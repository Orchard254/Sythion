"""Focused truthful scheduling response tests for composer turns."""

import pytest

from fastapi import BackgroundTasks

try:
    from backend.api.routes import assistant_turn, _composer_scheduling_established
    from backend.api.schemas import AssistantTurnRequest
    from backend.models.database import Task, TaskEvent
except ImportError:  # pragma: no cover
    from api.routes import assistant_turn, _composer_scheduling_established
    from api.schemas import AssistantTurnRequest
    from models.database import Task, TaskEvent


class _FakeEngine:
    def __init__(self, *args, **kwargs):
        pass

    async def run_turn(self, *args, **kwargs):
        return {
            "assistant_text": "Requested background work.",
            "commands": [{"type": "start_task", "request": "same request"}],
        }


def _plain_engine_factory(assistant_text="Hello from Synth.", commands=None):
    class _Engine:
        def __init__(self, *args, **kwargs):
            pass

        async def run_turn(self, *args, **kwargs):
            return {"assistant_text": assistant_text, "commands": list(commands or [])}

    return _Engine


@pytest.fixture(autouse=True)
def _patch(monkeypatch):
    import backend.core.session as session_mod

    session_mod.set_current_session_id("assistant-turn-test-session")
    monkeypatch.setattr("backend.services.conversation_turn_engine.ConversationTurnEngine", _FakeEngine)
    monkeypatch.setattr("backend.services.conversation_runtime.ModelClient", lambda *a, **k: object())

    async def _worker(*a, **k):
        return None

    monkeypatch.setattr("backend.api.routes.run_task", _worker)
    yield


@pytest.mark.asyncio
async def test_start_task_fresh_reports_unknown_scheduling_until_worker_starts(request):
    db = request.getfixturevalue("test_db")
    from fastapi import BackgroundTasks

    bg = BackgroundTasks()
    resp = await assistant_turn(
        AssistantTurnRequest(client_request_id="fresh-sched-1", text="do work"),
        bg,
        db,
        None,
    )

    assert resp.background_task_ids, resp
    assert resp.scheduling_established is False, resp
    assert resp.scheduling_status == "unknown", resp
    assert resp.dispatch_status == "unknown", resp
    assert resp.background_task_scheduling_status == "unknown", resp
    assert resp.task_scheduling_status == "unknown", resp


@pytest.mark.asyncio
async def test_pure_conversation_fresh_reports_no_pending_scheduling(request, monkeypatch):
    db = request.getfixturevalue("test_db")
    monkeypatch.setattr(
        "backend.services.conversation_turn_engine.ConversationTurnEngine",
        _plain_engine_factory(),
    )
    from fastapi import BackgroundTasks

    bg = BackgroundTasks()
    resp = await assistant_turn(
        AssistantTurnRequest(client_request_id="fresh-conv-1", text="hello"),
        bg,
        db,
        None,
    )

    assert resp.background_task_ids == []
    assert resp.scheduling_established is True, resp
    assert resp.scheduling_status == "established", resp
    assert bg.tasks == []


@pytest.mark.asyncio
async def test_exact_replay_aligns_scheduling_fields_and_does_not_rerun(request):
    db = request.getfixturevalue("test_db")
    from fastapi import BackgroundTasks

    first = await assistant_turn(
        AssistantTurnRequest(client_request_id="replay-align-1", text="do work"),
        BackgroundTasks(),
        db,
        None,
    )
    worker_ids = first.background_task_ids

    replay_bg = BackgroundTasks()
    replay = await assistant_turn(
        AssistantTurnRequest(client_request_id="replay-align-1", text="do work"),
        replay_bg,
        db,
        None,
    )

    assert replay.background_task_ids == worker_ids, (replay, first)
    assert replay_bg.tasks == [], replay
    assert replay.scheduling_established is False, replay
    assert replay.scheduling_status == "unknown", replay
    assert set(
        [
            replay.background_task_scheduling_status,
            replay.scheduling_status,
            replay.task_scheduling_status,
            replay.dispatch_status,
        ]
    ) == {"unknown"}, replay


def test_empty_work_list_is_false(test_db):
    assert _composer_scheduling_established(test_db, None) is False
    assert _composer_scheduling_established(test_db, []) is False
    assert _composer_scheduling_established(test_db, ()) is False


def test_malformed_nonempty_work_list_is_unproven(test_db):
    assert _composer_scheduling_established(test_db, [None]) is False
    assert _composer_scheduling_established(test_db, [123]) is False
    assert _composer_scheduling_established(test_db, [""]) is False
    assert _composer_scheduling_established(test_db, ["   "]) is False
    assert _composer_scheduling_established(test_db, ["ok", None]) is False
    assert _composer_scheduling_established(test_db, "not-a-list") is False


def test_helper_requires_durable_worker_started_entry(test_db):
    task = Task(
        id="helper-task",
        original_request="helper request",
        state="running",
        version=1,
        idempotency_key="helper-key",
        created_at=__import__("datetime").datetime.utcnow(),
        updated_at=__import__("datetime").datetime.utcnow(),
    )
    test_db.add(task)
    test_db.commit()

    assert _composer_scheduling_established(test_db, [task.id]) is False

    test_db.add(
        TaskEvent(
            task_id=task.id,
            event_type="task.worker_started",
            event_data='{"task_idempotency_key": "helper-key"}',
            sequence_num=1,
            created_at=__import__("datetime").datetime.utcnow(),
        )
    )
    test_db.commit()

    assert _composer_scheduling_established(test_db, [task.id]) is True
