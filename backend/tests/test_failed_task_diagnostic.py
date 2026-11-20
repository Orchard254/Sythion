import asyncio
import json

import pytest

from backend.api.schemas import TaskState
from backend.models.database import (
    ConversationExchange,
    ConversationTaskAssociation,
    Task,
    TaskEvent,
)
from backend.services.execution_components import PostTaskPipeline
from backend.services.post_task_intelligence import diagnose_failed_task


def _associate_task(db, task_id: str) -> None:
    db.add(ConversationExchange(id=f"ex-{task_id}", session_id="sess", user_text="hi", assistant_text="hello"))
    db.add(ConversationTaskAssociation(
        exchange_id=f"ex-{task_id}",
        task_id=task_id,
        user_turn_id=1,
        assistant_turn_id=2,
    ))
    db.commit()


@pytest.mark.asyncio
async def test_failed_task_diagnostic_persists_once(test_db):
    task = Task(
        id="ft1",
        original_request="rename file x",
        state=TaskState.FAILED.value,
        terminal_reason="FILE_NOT_FOUND",
    )
    test_db.add(task)
    test_db.commit()
    _associate_task(test_db, "ft1")

    calls = {"count": 0}

    async def fake_model(messages):
        calls["count"] += 1
        return {"message": {"content": "The requested file was not found; check its path. Next step: verify the file exists."}}

    result = await diagnose_failed_task(test_db, "ft1", model_call_fn=fake_model)
    assert "not found" in (result or "").lower()

    task = test_db.query(Task).filter_by(id="ft1").first()
    assert task.final_result and "Diagnostic:" in task.final_result

    events = test_db.query(TaskEvent).filter_by(task_id="ft1").all()
    assert any(e.event_type == "task.diagnostic" for e in events)

    calls_before = calls["count"]
    result2 = await diagnose_failed_task(test_db, "ft1", model_call_fn=fake_model)
    assert calls["count"] == calls_before
    assert result2 == result


@pytest.mark.asyncio
async def test_diagnostic_commit_failure_reports_unavailable(test_db, monkeypatch):
    """A failed persistence commit must not return the diagnosis as if it were
    saved — the rollback removes the evidence event, claiming success hides it.
    """
    task = Task(
        id="ft-commit",
        original_request="do thing",
        state=TaskState.FAILED.value,
        terminal_reason="BOOM",
    )
    test_db.add(task)
    test_db.commit()
    _associate_task(test_db, "ft-commit")

    state = {"armed": False}
    real_commit = test_db.commit

    def flaky_commit():
        if state["armed"]:
            raise RuntimeError("disk full")
        return real_commit()

    monkeypatch.setattr(test_db, "commit", flaky_commit)

    calls = {"count": 0}

    async def fake_model(messages):
        calls["count"] += 1
        state["armed"] = True  # arm failure for the diagnostic's final commit
        return {"message": {"content": "The requested file was missing; check the path."}}

    result = await diagnose_failed_task(test_db, "ft-commit", model_call_fn=fake_model)

    assert calls["count"] == 1
    assert "unavailable" in (result or "").lower()
    assert "check the path" not in (result or "").lower()
    # The rollback removed the diagnostic event, so nothing was persisted.
    events = test_db.query(TaskEvent).filter_by(task_id="ft-commit").all()
    assert not any(e.event_type == "task.diagnostic" for e in events)


@pytest.mark.asyncio
async def test_failed_task_diagnostic_unavailable_without_model(test_db):
    task = Task(
        id="ft2",
        original_request="do thing",
        state=TaskState.FAILED.value,
        terminal_reason="BOOM",
    )
    test_db.add(task)
    test_db.commit()
    _associate_task(test_db, "ft2")

    result = await diagnose_failed_task(test_db, "ft2")
    assert result and "unavailable" in result.lower()

    task = test_db.query(Task).filter_by(id="ft2").first()
    assert task.final_result and "Diagnostic:" in task.final_result


@pytest.mark.asyncio
async def test_non_associated_task_not_diagnosed(test_db):
    task = Task(
        id="ftNA",
        original_request="do thing",
        state=TaskState.FAILED.value,
        terminal_reason="BOOM",
    )
    test_db.add(task)
    test_db.commit()

    result = await diagnose_failed_task(test_db, "ftNA", model_call_fn=AsyncMock(return_value="x") if False else None)
    assert result is None


@pytest.mark.asyncio
async def test_post_task_pipeline_triggers_diagnostic(test_db):
    task = Task(
        id="ft3",
        original_request="do thing 3",
        state=TaskState.FAILED.value,
        terminal_reason="NOPE",
    )
    test_db.add(task)
    test_db.commit()
    _associate_task(test_db, "ft3")

    async def fake_model(messages):
        return "Evidence suggests a permission block. Next step: inspect permissions."

    from backend.models.database import set_db_manager
    from backend.services.task_runner import TaskRunner

    class _Manager:
        def get_session(self):
            return test_db

    set_db_manager(_Manager())

    runner = TaskRunner(test_db)
    pipeline = PostTaskPipeline(test_db, runner, None, model_call_fn=fake_model)

    await pipeline.apply_plan_outcome("ft3")
    # Finalization returns before diagnostics complete; wait explicitly here.
    await pipeline.drain_pending_diagnostics()

    task = test_db.query(Task).filter_by(id="ft3").first()
    assert task.final_result and "permission" in task.final_result.lower()

    event_count = test_db.query(TaskEvent).filter_by(task_id="ft3", event_type="task.diagnostic").count()
    await pipeline.apply_plan_outcome("ft3")
    await pipeline.drain_pending_diagnostics()
    assert test_db.query(TaskEvent).filter_by(task_id="ft3", event_type="task.diagnostic").count() == event_count


@pytest.mark.asyncio
async def test_production_entry_point_isolates_provider_failure(test_db):
    task = Task(
        id="ft4",
        original_request="do thing 4",
        state=TaskState.FAILED.value,
        terminal_reason="NOPE",
    )
    test_db.add(task)
    test_db.commit()
    _associate_task(test_db, "ft4")

    async def failing_model(messages):
        raise RuntimeError("provider down")

    class _Manager:
        def get_session(self):
            return test_db

    from backend.models.database import set_db_manager

    set_db_manager(_Manager())

    from backend.services.task_runner import TaskRunner

    runner = TaskRunner(test_db)
    pipeline = PostTaskPipeline(test_db, runner, None, model_call_fn=failing_model)

    await pipeline.apply_plan_outcome("ft4")
    await pipeline.drain_pending_diagnostics()

    task = test_db.query(Task).filter_by(id="ft4").first()
    assert task.final_result and "Diagnostic:" in task.final_result


@pytest.mark.asyncio
async def test_concurrent_scheduling_one_model_call(test_db):
    task = Task(
        id="ftc",
        original_request="do thing c",
        state=TaskState.FAILED.value,
        terminal_reason="NOPE",
    )
    test_db.add(task)
    test_db.commit()
    _associate_task(test_db, "ftc")

    calls = {"count": 0}

    async def fake_model(messages):
        calls["count"] += 1
        await asyncio.sleep(0.05)
        return {"response": "permission block found; next step inspect permission"}

    class _Manager:
        def get_session(self):
            return test_db

    from backend.models.database import set_db_manager

    set_db_manager(_Manager())

    from backend.services.task_runner import TaskRunner

    runner = TaskRunner(test_db)
    pipeline = PostTaskPipeline(test_db, runner, None, model_call_fn=fake_model)

    await asyncio.gather(
        pipeline.apply_plan_outcome("ftc"),
        pipeline.apply_plan_outcome("ftc"),
    )
    await pipeline.drain_pending_diagnostics()

    assert calls["count"] == 1
    task = test_db.query(Task).filter_by(id="ftc").first()
    assert task.final_result and "permission" in task.final_result.lower()
    event = test_db.query(TaskEvent).filter_by(task_id="ftc", event_type="task.diagnostic").first()
    assert event is not None
    payload = json.loads(event.event_data) if isinstance(event.event_data, str) else event.event_data
    assert "evidence_event_types" in payload
    assert "evidence_event_codes" in payload


@pytest.mark.asyncio
async def test_redacts_secret_and_preserves_failed_event_code(test_db):
    task = Task(
        id="fts2",
        original_request="use api_key=SECRET123 to connect",
        normalized_goal="secret test",
        state=TaskState.FAILED.value,
        terminal_reason="password=hunter2",
    )
    test_db.add(task)
    test_db.add(TaskEvent(
        task_id="fts2",
        event_type="task.failed",
        event_data='{"code": "AUTH_FAILED", "message": "password=hunter2"}',
        sequence_num=1,
    ))
    test_db.commit()
    _associate_task(test_db, "fts2")

    captured = {}

    async def fake_model(messages):
        captured["content"] = messages[1]["content"]
        return {"message": {"content": "secret not exposed; next step check config"}}

    from backend.models.database import set_db_manager

    class _Manager:
        def get_session(self):
            return test_db

    set_db_manager(_Manager())

    result = await diagnose_failed_task(test_db, "fts2", model_call_fn=fake_model)
    assert result and "secret not exposed" in result
    assert "SECRET123" not in captured["content"]
    assert "hunter2" not in captured["content"]
    assert "[REDACTED]" in captured["content"]
    event = test_db.query(TaskEvent).filter_by(task_id="fts2", event_type="task.diagnostic").first()
    payload = json.loads(event.event_data) if isinstance(event.event_data, str) else event.event_data
    assert payload["evidence_event_codes"] == ["AUTH_FAILED"]


@pytest.mark.asyncio
async def test_save_conversation_turn_does_not_block_or_diagnose(test_db):
    task = Task(
        id="fts",
        original_request="secret token",
        state=TaskState.FAILED.value,
        terminal_reason="NOPE",
    )
    test_db.add(task)
    test_db.commit()
    _associate_task(test_db, "fts")

    calls = {"count": 0}

    async def fake_model(messages):
        calls["count"] += 1
        return {"message": {"content": "diagnosis"}}

    class _Manager:
        def get_session(self):
            return test_db

    from backend.models.database import set_db_manager

    set_db_manager(_Manager())

    from backend.services.task_runner import TaskRunner

    runner = TaskRunner(test_db)
    pipeline = PostTaskPipeline(test_db, runner, None, model_call_fn=fake_model)

    await pipeline.save_conversation_turn("fts")

    assert calls["count"] == 0


@pytest.mark.asyncio
async def test_real_context_manager_scheduler(tmp_path, monkeypatch):
    from backend.core.db_manager import DatabaseManager
    from backend.models.database import set_db_manager, Task, ConversationExchange, ConversationTaskAssociation
    import backend.services.failed_task_diagnostic as diag

    db_path = str(tmp_path / "real_diag.db")
    previous = None
    try:
        from backend.models.database import get_db_manager as _gdm

        previous = _gdm()
    except Exception:
        previous = None

    mgr = DatabaseManager(db_path)
    set_db_manager(mgr)
    # Avoid live configured-provider calls during the test.
    monkeypatch.setattr(diag, "_provider_is_configured", lambda: False)

    try:
        async def _seed():
            with mgr.get_session() as db:
                db.add(Task(id="ftr", original_request="r", state=TaskState.FAILED.value, terminal_reason="boom"))
                db.add(ConversationExchange(id="e", session_id="s", user_text="u", assistant_text="a"))
                db.add(ConversationTaskAssociation(exchange_id="e", task_id="ftr", user_turn_id=1, assistant_turn_id=2))
                db.commit()

        await _seed()

        from backend.services.task_runner import TaskRunner

        with mgr.get_session() as db:
            runner = TaskRunner(db)
            pipeline = PostTaskPipeline(db, runner, None)

        await pipeline.apply_plan_outcome("ftr")
        await pipeline.drain_pending_diagnostics()

        with mgr.get_session() as db:
            task = db.query(Task).filter_by(id="ftr").first()
            assert task is not None
            assert task.final_result and "Diagnostic:" in task.final_result
    finally:
        if previous is not None:
            set_db_manager(previous)