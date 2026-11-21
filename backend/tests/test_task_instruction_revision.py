"""File-backed revision/admission races. No provider or external OS actions."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from backend.api.routes import assistant_turn
from backend.api.schemas import AssistantTurnRequest
from backend.models.database import (
    Base, Task, TaskEvent, TaskDecisionWindow, ConversationExchange, ConversationTurn,
)
from backend.runtime.task_ownership import TaskOwnershipRow
from backend.services.conversation_runtime import ConversationRuntime
from backend.services.conversation_turn_engine import ConversationTurnEngine, ConversationTurnValidationError
from backend.services.task_runner import TaskRunner
from backend.services.task_instruction_revision import TaskInstructionRevision, RevisionUnavailable
from backend.services.task_execution_loop import TaskExecutionLoop
from backend.services.model_engine import DecisionType, ModelDecisionError
from backend.tools.executor import ToolOutput


@pytest.fixture
def db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'revisions.db'}", connect_args={"timeout": 10})

    @event.listens_for(engine, "connect")
    def setup(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")

    Base.metadata.create_all(engine)
    monkeypatch.setattr("backend.core.session._EXPLICIT_SESSION_ID", "revision-test")
    monkeypatch.setattr("backend.services.conversation_state.update_state", lambda *a, **k: None)
    with sessionmaker(bind=engine)() as session:
        yield session
    engine.dispose()


async def seed(db, *, acquire=True):
    db.add(Task(id="original", original_request="Create a chess study plan", state="running", version=2))
    db.commit()
    runtime = ConversationRuntime(db)
    identity = await runtime._persist_conversation("Create a chess study plan", "Work accepted.", "source", task_id="original")
    runner = TaskRunner(db)
    if acquire:
        assert (await runner.acquire_task_ownership("original", worker_id="test-worker")).acquired
    return runner, runtime, identity


async def append(runner, runtime, *, version=2, text="Maximum fifteen minutes, please", exchange="followup"):
    return await TaskInstructionRevision(runner).append("original", version, text, runtime, exchange, "source")


def appended_events(db):
    return db.query(TaskEvent).filter_by(task_id="original", event_type="task.instruction_appended").all()


@pytest.mark.asyncio
async def test_exact_human_instruction_and_exchange_commit_before_old_decision_is_discarded(db):
    runner, runtime, _ = await seed(db)
    revision = TaskInstructionRevision(runner)
    snapshot = revision.open("original")
    text = "  Maximum 15 minutes; do not send or delete anything.  "
    identity = await append(runner, runtime, text=text)
    assert not revision.claim("original", snapshot.revision)
    fresh = revision.open("original")
    assert fresh.revision == snapshot.revision + 1
    assert fresh.normalized_goal.endswith(text)
    assert fresh.original_request == "Create a chess study plan"
    row = db.get(Task, "original")
    assert (row.state, row.version) == ("running", 3)
    evidence = json.loads(appended_events(db)[0].event_data)
    assert evidence["instruction"] == text
    assert evidence["turn_ids"] == list(identity.turn_ids)
    assert evidence["source_exchange_id"] == "source"
    assert db.get(ConversationExchange, "followup").user_text == text
    assert db.query(Task).count() == 1
    assert revision.claim("original", fresh.revision)
    assert not revision.claim("original", fresh.revision)  # no response replay


@pytest.mark.asyncio
async def test_admitted_decision_refuses_append_without_touching_work(db):
    runner, runtime, _ = await seed(db)
    revision = TaskInstructionRevision(runner)
    snapshot = revision.open("original")
    assert revision.claim("original", snapshot.revision)
    with pytest.raises(RevisionUnavailable, match="not at a cooperative safe point"):
        await append(runner, runtime)
    assert db.get(Task, "original").normalized_goal is None
    assert not appended_events(db)
    assert db.get(ConversationExchange, "followup") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["completed", "cancelled", "interrupted", "paused", "waiting_for_user"])
async def test_completion_and_cancellation_races_never_reopen_or_duplicate(db, state):
    runner, runtime, _ = await seed(db)
    revision = TaskInstructionRevision(runner)
    snapshot = revision.open("original")
    with sessionmaker(bind=db.bind)() as writer:
        target = writer.get(Task, "original")
        target.state = state
        target.version += 1
        writer.commit()
    with pytest.raises(RevisionUnavailable, match="changed or stopped"):
        await append(runner, runtime)
    assert not revision.claim("original", snapshot.revision)
    assert db.query(Task).count() == 1
    assert db.get(Task, "original").state == state
    assert not appended_events(db)


@pytest.mark.asyncio
@pytest.mark.parametrize("evidence", ["pending", "active", "uncertain", "expired", "wrong_owner", "missing_window"])
async def test_permission_action_and_owner_evidence_refuse_append_unchanged(db, evidence):
    runner, runtime, _ = await seed(db)
    revision = TaskInstructionRevision(runner)
    revision.open("original")
    target = db.get(Task, "original")
    owner = db.get(TaskOwnershipRow, "original")
    if evidence == "pending":
        target.pending_action = json.dumps({"kind": "permission", "status": "approved", "action_id": "consent"})
    elif evidence == "active":
        target.active_action_id = owner.active_action_id = "already-started"
    elif evidence == "uncertain":
        owner.uncertain_action_ids = json.dumps(["unknown-effect"])
    elif evidence == "expired":
        owner.expires_at = datetime.utcnow() - timedelta(seconds=10)
    elif evidence == "wrong_owner":
        db.get(TaskDecisionWindow, "original").owner_nonce = "different-worker"
    else:
        db.query(TaskDecisionWindow).delete()
    db.commit()
    before = (target.pending_action, target.active_action_id, owner.uncertain_action_ids, target.version)
    with pytest.raises(RevisionUnavailable):
        await append(runner, runtime, text="Yes, and change it to fifteen minutes")
    db.refresh(target)
    db.refresh(owner)
    assert (target.pending_action, target.active_action_id, owner.uncertain_action_ids, target.version) == before
    assert not appended_events(db)
    assert db.query(ConversationExchange).count() == 1


@pytest.mark.asyncio
async def test_commit_failure_rolls_back_goal_revision_exchange_and_audit(db, monkeypatch):
    runner, runtime, _ = await seed(db)
    revision = TaskInstructionRevision(runner)
    snapshot = revision.open("original")

    def fail():
        raise RuntimeError("commit failed")

    with monkeypatch.context() as patch:
        patch.setattr(db, "commit", fail)
        with pytest.raises(RuntimeError, match="commit failed"):
            await append(runner, runtime)
    assert db.get(Task, "original").normalized_goal is None
    assert db.get(Task, "original").version == 2
    assert db.get(TaskDecisionWindow, "original").revision == snapshot.revision
    assert not appended_events(db)
    assert db.query(ConversationExchange).count() == 1
    assert db.query(ConversationTurn).count() == 2
    assert revision.claim("original", snapshot.revision)


@pytest.mark.asyncio
async def test_cross_session_and_duplicate_exchange_cannot_append(db):
    runner, runtime, _ = await seed(db)
    revision = TaskInstructionRevision(runner)
    revision.open("original")
    runtime.session_id = "other-session"
    with pytest.raises(RevisionUnavailable):
        await append(runner, runtime)
    runtime.session_id = "revision-test"
    await append(runner, runtime)
    with pytest.raises(RevisionUnavailable):
        await append(runner, runtime)  # stale version cannot duplicate instruction
    with pytest.raises(RuntimeError, match="Conversation persistence failed"):
        await append(runner, runtime, version=3)  # duplicate exact exchange rolls back
    assert len(appended_events(db)) == 1
    assert db.get(Task, "original").version == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("race", ["two_appends", "claim_append"])
async def test_concurrent_revision_and_admission_have_one_winner(db, race):
    runner, runtime, _ = await seed(db)
    snapshot = TaskInstructionRevision(runner).open("original")
    nonce, fence = runner.task_ownership_nonce, runner._task_ownership_fence
    barrier = Barrier(2)

    def contender(index):
        with sessionmaker(bind=db.bind)() as writer:
            other = TaskRunner(writer)
            other_runtime = ConversationRuntime(writer)
            if race == "claim_append" and index == 0:
                other._task_ownership_nonce = nonce
                other._task_ownership_task_id = "original"
                other._task_ownership_fence = fence
                barrier.wait(timeout=10)
                return TaskInstructionRevision(other).claim("original", snapshot.revision)
            barrier.wait(timeout=10)
            try:
                asyncio.run(append(other, other_runtime, exchange=f"followup-{index}"))
                return True
            except RevisionUnavailable:
                return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(contender, range(2)))
    assert sum(results) == 1
    assert len(appended_events(db)) == (1 if race == "two_appends" or results[1] else 0)
    assert db.query(Task).count() == 1


class FakeExecutor:
    def list_tools(self):
        return []

    def get_tool(self, name):
        return type("Tool", (), {"get_policy_requirements": lambda self: {"read_only": True}})()

    async def execute_tool(self, *args, **kwargs):
        pytest.fail("A stale decision must not reach the executor")


@pytest.mark.asyncio
@pytest.mark.parametrize("old_type", [DecisionType.EXECUTE_TOOL, DecisionType.COMPLETE_TASK, DecisionType.RUN_SKILL, "provider_error"])
async def test_real_worker_discards_stale_decision_and_entire_pending_batch(db, old_type):
    _, _, _ = await seed(db, acquire=False)
    runner = TaskRunner(db)
    loop = TaskExecutionLoop(db, runner, FakeExecutor())
    entered, release = asyncio.Event(), asyncio.Event()
    seen = []

    class Model:
        calls = 0
        stale_drained = 0

        async def make_decision(self, **kwargs):
            self.calls += 1
            seen.append(kwargs["normalized_goal"])
            if self.calls == 1:
                entered.set()
                await release.wait()
                if old_type == "provider_error":
                    raise ModelDecisionError("MODEL_UNAVAILABLE", "Synthetic old-inference failure")
                return old_type, {"tool_name": "old-tool", "result": "old result", "skill_id": "old-skill"}
            return DecisionType.COMPLETE_TASK, {"result": "Revised fifteen minute plan"}

        def pop_pending_tool_calls(self):
            if self.calls == 1:
                self.stale_drained += 1
                return [{"decision_type": DecisionType.EXECUTE_TOOL, "args": {"tool_name": "stale-batch"}}]
            return []

    model = Model()
    loop.model_engine = model
    work = asyncio.create_task(loop.execute_task("original"))
    await asyncio.wait_for(entered.wait(), timeout=5)
    try:
        with sessionmaker(bind=db.bind)() as request_db:
            await append(TaskRunner(request_db), ConversationRuntime(request_db))
        release.set()
        await asyncio.wait_for(work, timeout=10)
    finally:
        if not work.done():
            work.cancel()
            await asyncio.gather(work, return_exceptions=True)
    assert model.calls == 2 and model.stale_drained == 1
    assert seen[0] is None and seen[1].endswith("Maximum fifteen minutes, please")
    db.expire_all()
    assert db.get(Task, "original").final_result == "Revised fifteen minute plan"
    assert db.get(Task, "original").state == "completed"
    assert db.query(Task).count() == 1


@pytest.mark.asyncio
async def test_actual_inflight_mutation_is_not_interrupted_replayed_or_revised(db, monkeypatch):
    monkeypatch.setenv("SYTHION_AUTONOMY_LEVEL", "autonomous")
    await seed(db, acquire=False)
    entered, release = asyncio.Event(), asyncio.Event()

    class Executor(FakeExecutor):
        calls = 0

        def get_tool(self, name):
            return type("Tool", (), {"get_policy_requirements": lambda self: {"mutating": True}})()

        async def execute_tool(self, *args, **kwargs):
            self.calls += 1
            entered.set()
            await release.wait()
            return ToolOutput(success=True, data={"done": True}, model_summary="Observed result")

    class Model:
        calls = 0

        async def make_decision(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return DecisionType.EXECUTE_TOOL, {"tool_name": "mutator", "params": {}}
            return DecisionType.COMPLETE_TASK, {"result": "Original work finished"}

        def pop_pending_tool_calls(self):
            return []

    executor = Executor()
    loop = TaskExecutionLoop(db, TaskRunner(db), executor)
    loop.model_engine = Model()
    work = asyncio.create_task(loop.execute_task("original"))
    await asyncio.wait_for(entered.wait(), timeout=5)
    try:
        with sessionmaker(bind=db.bind)() as request_db:
            current = request_db.get(Task, "original")
            action = current.active_action_id
            assert action
            with pytest.raises(RevisionUnavailable, match="effect may be unknown"):
                await append(TaskRunner(request_db), ConversationRuntime(request_db), version=current.version)
            request_db.expire_all()
            assert request_db.get(Task, "original").active_action_id == action
        assert not work.done()
        release.set()
        await asyncio.wait_for(work, timeout=10)
    finally:
        if not work.done():
            work.cancel()
            await asyncio.gather(work, return_exceptions=True)
    assert executor.calls == 1
    assert not appended_events(db)
    assert db.get(Task, "original").normalized_goal is None


def engine_reply(monkeypatch, command, text="I paused and updated your task.", hook=None):
    class Engine:
        def __init__(self, *args):
            pass

        async def run_turn(self, user_text, turns, descriptors, discussion_contexts=None, **kwargs):
            assert any(d["task_id"] == "original" and d["kind"] == "task_context" for d in descriptors)
            if hook:
                hook()
            return {"assistant_text": text, "commands": command}

    monkeypatch.setattr("backend.services.conversation_turn_engine.ConversationTurnEngine", Engine)
    monkeypatch.setattr("backend.services.conversation_runtime.ModelClient", lambda: object())

    async def no_worker(*args, **kwargs):
        pytest.fail("Composer revision must not schedule/restart a worker")

    monkeypatch.setattr("backend.api.routes.run_task", no_worker)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["append", "ambiguous", "chat", "independent", "completed_race", "busy"])
async def test_composer_followup_independent_ambiguity_and_races(db, monkeypatch, mode):
    runner, _, _ = await seed(db)
    revision = TaskInstructionRevision(runner)
    snapshot = revision.open("original")
    commands = [{"type": "append_task_instruction", "task_id": "original", "expected_version": 2}]
    human = "Limit that to fifteen minutes"
    hook = None
    if mode in {"ambiguous", "chat"}:
        commands = []
    if mode == "independent":
        commands = [{"type": "start_task", "request": "Model-expanded request must not replace human text"}]
        human = "Check weather in Oslo"
    if mode == "busy":
        assert revision.claim("original", snapshot.revision)
    if mode == "completed_race":
        def hook():
            with sessionmaker(bind=db.bind)() as writer:
                target = writer.get(Task, "original")
                target.state = "completed"
                target.version += 1
                writer.commit()
    text = "Which task do you mean?" if mode == "ambiguous" else "Hello, happy to help!" if mode == "chat" else "I paused and updated your task."
    engine_reply(monkeypatch, commands, text=text, hook=hook)
    bg = BackgroundTasks()
    try:
        await runner.release_lease()
    except Exception:
        pass
    response = await assistant_turn(AssistantTurnRequest(text=human, client_request_id="instruction-revision-" + str(mode)), bg, db, None)
    assert len(bg.tasks) == (1 if mode == "independent" else 0)
    assert db.query(Task).count() == (2 if mode == "independent" else 1)
    if mode == "append":
        assert response.assistant_text == ""
        assert response.status_text == (
            "Your instruction was queued for this task and has not yet "
            "been applied. Whether to apply it will be decided at a safe point. "
            "No task was restarted "
            "or duplicated, and no action permission was granted."
        )
        assert response.status == "queued"
        assert db.get(Task, "original").normalized_goal is None
        assert not response.background_task_ids
    elif mode in {"busy", "completed_race"}:
        assert response.status == "failed"
        assert response.assistant_text == ""
        assert response.status_text in {revision.BUSY, revision.CHANGED}
        assert not appended_events(db)
    elif mode == "ambiguous":
        assert response.assistant_text == "Which task do you mean?"
        assert not appended_events(db)
    elif mode == "chat":
        assert response.assistant_text == "Hello, happy to help!"
        assert not appended_events(db)
    else:
        assert db.get(Task, "original").normalized_goal is None
        assert len(response.background_task_ids) == 1
        assert db.get(Task, response.background_task_ids[0]).original_request == human
        assert response.assistant_text == ""
        assert response.status_text == (
            "Your separate background request was accepted. Existing "
            "tasks were not modified or restarted."
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("command", [
    {"type": "append_task_instruction", "task_id": "unknown", "expected_version": 2},
    {"type": "append_task_instruction", "task_id": "original", "expected_version": True},
    {"type": "append_task_instruction", "task_id": "original", "expected_version": 1},
    {"type": "append_task_instruction", "task_id": "original", "expected_version": 2, "instruction": "injected rewrite"},
    {"type": "answer_clarification", "task_id": "original", "expected_version": 2, "answer": "permission"},
])
async def test_strict_model_targets_never_rewrite_human_instruction_or_infer_permission(command):
    descriptor = {"task_id": "original", "kind": "task_context", "expected_version": 2,
                  "question": "Current state: running", "request": "Create chess plan"}
    engine = ConversationTurnEngine(object())
    with pytest.raises(ConversationTurnValidationError):
        engine._validate_output(json.dumps({"assistant_text": "ok", "commands": [command]}), engine._normalize_descriptors([descriptor]))


@pytest.mark.asyncio
async def test_later_typed_clarification_preserves_all_accepted_constraints(db):
    runner, runtime, _ = await seed(db)
    revision = TaskInstructionRevision(runner)
    revision.open("original")
    text = "x" * 3000 + "\nMaximum fifteen minutes; do not delete anything."
    await append(runner, runtime, text=text)
    target = db.get(Task, "original")
    target.state = "waiting_for_user"
    target.version += 1
    target.pending_question = "Which days?"
    target.pending_action = json.dumps({"kind": "clarification"})
    db.commit()
    task_version = target.version
    result = await runner.reply_to_waiting("original", "Weekdays", task_version)
    assert text in result.normalized_goal
    assert result.normalized_goal.endswith("[clarification] Weekdays")
    assert len(result.normalized_goal) > 3000


@pytest.mark.asyncio
async def test_route_persistence_failure_never_returns_or_persists_update_claim(db, monkeypatch):
    runner, _, _ = await seed(db)
    TaskInstructionRevision(runner).open("original")
    engine_reply(monkeypatch, [{"type": "append_task_instruction", "task_id": "original", "expected_version": 2}])

    async def fail(*args, **kwargs):
        raise RuntimeError("conversation storage failed")

    monkeypatch.setattr(ConversationRuntime, "_persist_conversation", fail)
    bg = BackgroundTasks()
    with pytest.raises(HTTPException) as error:
        await assistant_turn(AssistantTurnRequest(text="Only fifteen minutes", client_request_id="instruction-inflight"), bg, db, None)
    assert error.value.status_code == 500
    assert not bg.tasks and not appended_events(db)
    assert db.query(Task).count() == 1
    assert db.get(Task, "original").normalized_goal is None
    assert db.query(ConversationExchange).count() == 1


@pytest.mark.asyncio
async def test_revision_does_not_grant_permission_for_new_consequential_proposal(db, monkeypatch):
    await seed(db, acquire=False)
    entered, release = asyncio.Event(), asyncio.Event()

    class Executor(FakeExecutor):
        def get_tool(self, name):
            return type("Tool", (), {"get_policy_requirements": lambda self: {
                "mutating": True, "destructive": True, "risk_level": "high",
            }})()

    class Model:
        calls = 0

        async def make_decision(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                entered.set()
                await release.wait()
                return DecisionType.COMPLETE_TASK, {"result": "Old result"}
            return DecisionType.EXECUTE_TOOL, {"tool_name": "file_delete", "params": {"path": "synthetic-only"}}

        def pop_pending_tool_calls(self):
            return []

    monkeypatch.setenv("SYTHION_AUTONOMY_LEVEL", "guarded")
    loop = TaskExecutionLoop(db, TaskRunner(db), Executor())
    loop.model_engine = Model()
    work = asyncio.create_task(loop.execute_task("original"))
    await asyncio.wait_for(entered.wait(), timeout=5)
    try:
        with sessionmaker(bind=db.bind)() as request_db:
            await append(TaskRunner(request_db), ConversationRuntime(request_db), text="Make it fifteen minutes")
        release.set()
        await asyncio.wait_for(work, timeout=10)
    finally:
        if not work.done():
            work.cancel()
            await asyncio.gather(work, return_exceptions=True)
    db.expire_all()
    target = db.get(Task, "original")
    assert target.state == "waiting_for_user"
    pending = json.loads(target.pending_action)
    assert pending["kind"] == "permission" and pending["status"] == "awaiting"
    assert target.final_result is None
    assert db.query(Task).count() == 1


@pytest.mark.asyncio
async def test_retention_removes_revision_window_without_affecting_other_tasks(db):
    from backend.services.retention import purge_task

    runner, _, _ = await seed(db)
    TaskInstructionRevision(runner).open("original")
    db.add(Task(id="independent", original_request="Other work", state="running", version=1))
    db.flush()
    db.add(TaskDecisionWindow(task_id="independent", owner_nonce="other", phase="thinking", revision=0))
    db.commit()
    assert purge_task(db, "original") is not None
    assert db.query(TaskDecisionWindow).filter_by(task_id="original").first() is None
    assert db.query(TaskDecisionWindow).filter_by(task_id="independent").one().phase == "thinking"
    assert db.get(Task, "independent").state == "running"
