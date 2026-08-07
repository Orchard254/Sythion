"""
Tests for the activity_recall tool: cross-task activity history by time range.

Contract: read-only recall of Sythion's own past activity (task_events +
conversation_turns) within a time window. Only the bounded model_summary
(output fallback) reaches the model -- raw params/data/output never appear in
the returned data. Rows referencing purged/missing tasks must not error.
"""

import json
from datetime import datetime, timedelta

import pytest

from backend.core.db_manager import DatabaseManager
from backend.models.database import set_db_manager, Task, TaskEvent, ConversationTurn
from backend.tools.memory_tools import ActivityRecallTool
from backend.api.schemas import ActivityRecallInput


@pytest.fixture
def activity_db(tmp_path):
    db_path = str(tmp_path / "test_activity.db")
    db_mgr = DatabaseManager(db_path)
    set_db_manager(db_mgr)
    with db_mgr.get_session() as session:
        yield session


def _now():
    return datetime.utcnow()


def _add_event(session, task_id, created_at, tool_name="web_search", summary=None, event_type="tool.executed", params=None):
    ev = TaskEvent(
        task_id=task_id,
        event_type=event_type,
        event_data=json.dumps({
            "tool_name": tool_name,
            "params": params or {"q": "SECRET_PARAM_VALUE"},
            "model_summary": summary or f"ran {tool_name}",
            "output": {"raw": "RAW_OUTPUT_PAYLOAD"},
            "success": True,
        }),
        sequence_num=1,
    )
    ev.created_at = created_at
    session.add(ev)
    return ev


def _add_turn(session, task_id, created_at, role="user", summary="a conversation turn"):
    t = ConversationTurn(
        session_id="sess_1",
        task_id=task_id,
        role=role,
        summary=summary,
    )
    t.created_at = created_at
    session.add(t)
    return t


@pytest.mark.asyncio
async def test_activity_recall_range_excludes_outside_events(activity_db):
    session, = (activity_db,)
    _add_event(session, "t1", _now() - timedelta(hours=3), summary="inside window")
    _add_event(session, "t2", _now() - timedelta(hours=20), summary="too old")
    _add_event(session, "t3", _now() + timedelta(hours=1), summary="too new")
    session.commit()

    tool = ActivityRecallTool()
    out = await tool.execute(ActivityRecallInput(
        start_time=_now() - timedelta(hours=6),
        end_time=_now(),
    ))
    assert out.success is True
    assert out.data["count"] == 1
    assert out.data["events"][0]["summary"] == "inside window"


@pytest.mark.asyncio
async def test_activity_recall_default_end_is_now(activity_db):
    session = activity_db
    _add_event(session, "t1", _now() - timedelta(hours=2), summary="past event")
    _add_event(session, "t2", _now() + timedelta(hours=2), summary="future event")
    session.commit()

    tool = ActivityRecallTool()
    out = await tool.execute(ActivityRecallInput(start_time=_now() - timedelta(hours=5)))
    assert out.success is True
    summaries = [e["summary"] for e in out.data["events"]]
    assert "past event" in summaries
    assert "future event" not in summaries


@pytest.mark.asyncio
async def test_activity_recall_tool_name_filter(activity_db):
    session = activity_db
    _add_event(session, "t1", _now() - timedelta(hours=1), tool_name="web_search", summary="searched")
    _add_event(session, "t2", _now() - timedelta(hours=1), tool_name="file_list", summary="listed")
    session.commit()

    tool = ActivityRecallTool()
    out = await tool.execute(ActivityRecallInput(
        start_time=_now() - timedelta(hours=2),
        tool_name="web_search",
    ))
    assert out.success is True
    assert out.data["count"] == 1
    assert out.data["events"][0]["tool_name"] == "web_search"
    assert "listed" not in [e["summary"] for e in out.data["events"]]


@pytest.mark.asyncio
async def test_activity_recall_max_results_clamps(activity_db):
    session = activity_db
    for i in range(5):
        _add_event(session, f"t{i}", _now() - timedelta(minutes=5 - i), summary=f"event {i}")
    session.commit()

    tool = ActivityRecallTool()
    out = await tool.execute(ActivityRecallInput(
        start_time=_now() - timedelta(hours=1),
        max_results=3,
    ))
    assert out.success is True
    assert out.data["count"] == 3


@pytest.mark.asyncio
async def test_activity_recall_max_results_boundary_returns_25(activity_db):
    session = activity_db
    for i in range(30):
        _add_event(session, f"t{i}", _now() - timedelta(minutes=30 - i), summary=f"event {i}")
    session.commit()

    tool = ActivityRecallTool()
    out = await tool.execute(ActivityRecallInput(
        start_time=_now() - timedelta(hours=1),
        max_results=25,  # schema upper bound
    ))
    assert out.success is True
    assert out.data["count"] == 25


@pytest.mark.asyncio
async def test_activity_recall_tool_hard_clamps_beyond_schema(activity_db):
    """The schema already caps max_results at 25; the tool independently
    hard-clamps too (model_construct bypasses validation) so a raw executor
    call can never return more than the cap."""
    session = activity_db
    for i in range(30):
        _add_event(session, f"t{i}", _now() - timedelta(minutes=30 - i), summary=f"event {i}")
    session.commit()

    tool = ActivityRecallTool()
    raw_input = ActivityRecallInput.model_construct(
        start_time=_now() - timedelta(hours=1),
        max_results=999,
    )
    out = await tool.execute(raw_input)
    assert out.success is True
    assert out.data["count"] == 25


@pytest.mark.asyncio
async def test_activity_recall_purged_task_id_does_not_crash(activity_db):
    session = activity_db
    # No Task row exists for this task_id (purged by retention) -- the event
    # row remains; the query must not error.
    _add_event(session, "purged_task", _now() - timedelta(hours=1), summary="orphan event")
    session.commit()

    tool = ActivityRecallTool()
    out = await tool.execute(ActivityRecallInput(start_time=_now() - timedelta(hours=2)))
    assert out.success is True
    assert out.data["count"] == 1
    assert out.data["events"][0]["task_id"] == "purged_task"


@pytest.mark.asyncio
async def test_activity_recall_never_leaks_raw_params_or_output(activity_db):
    session = activity_db
    _add_event(
        session,
        "t1",
        _now() - timedelta(hours=1),
        tool_name="web_search",
        summary="bounded summary text",
        params={"q": "TOP_SECRET_QUERY"},
    )
    session.commit()

    tool = ActivityRecallTool()
    out = await tool.execute(ActivityRecallInput(start_time=_now() - timedelta(hours=2)))
    assert out.success is True
    assert out.data["count"] == 1
    event = out.data["events"][0]
    # Only the bounded model_summary is surfaced.
    assert event["summary"] == "bounded summary text"
    # Raw params and raw output payloads must never reach the model.
    blob = json.dumps(out.data)
    assert "TOP_SECRET_QUERY" not in blob
    assert "RAW_OUTPUT_PAYLOAD" not in blob
    assert "SECRET_PARAM_VALUE" not in blob
    assert "params" not in event


@pytest.mark.asyncio
async def test_activity_recall_output_fallback_when_no_summary(activity_db):
    session = activity_db
    ev = TaskEvent(
        task_id="t1",
        event_type="tool.executed",
        event_data=json.dumps({
            "tool_name": "system_time",
            "params": {},
            "output": "2026-08-06 08:00:00 UTC",
            "success": True,
        }),
        sequence_num=1,
    )
    ev.created_at = _now() - timedelta(hours=1)
    session.add(ev)
    session.commit()

    tool = ActivityRecallTool()
    out = await tool.execute(ActivityRecallInput(start_time=_now() - timedelta(hours=2)))
    assert out.success is True
    assert out.data["events"][0]["summary"] == "2026-08-06 08:00:00 UTC"


@pytest.mark.asyncio
async def test_activity_recall_includes_conversation_turns(activity_db):
    session = activity_db
    _add_event(session, "t1", _now() - timedelta(hours=1), summary="ran a tool")
    _add_turn(session, "t1", _now() - timedelta(minutes=30), role="user", summary="user asked something")
    session.commit()

    tool = ActivityRecallTool()
    out = await tool.execute(ActivityRecallInput(start_time=_now() - timedelta(hours=2)))
    assert out.success is True
    kinds = {e["kind"] for e in out.data["events"]}
    assert kinds == {"tool", "turn"}
    summaries = [e["summary"] for e in out.data["events"]]
    assert "ran a tool" in summaries
    assert "user asked something" in summaries


@pytest.mark.asyncio
async def test_activity_recall_invalid_range_rejected(activity_db):
    tool = ActivityRecallTool()
    out = await tool.execute(ActivityRecallInput(
        start_time=_now(),
        end_time=_now() - timedelta(hours=1),
    ))
    assert out.success is False
    assert out.error["code"] == "INVALID_TIME_RANGE"


def test_activity_recall_policy_read_only():
    reqs = ActivityRecallTool().get_policy_requirements()
    assert reqs["risk_level"] == "none"
    assert reqs["requires_confirmation"] is False
    assert reqs["mutating"] is False


def test_activity_recall_schema_exposes_fields_to_model():
    from backend.adapters.ollama_adapter import format_tools_for_ollama
    formatted = format_tools_for_ollama([
        {
            "name": "activity_recall",
            "description": "recall activity",
            "input_schema": ActivityRecallInput,
        }
    ])
    props = formatted[0]["function"]["parameters"]["properties"]
    assert "start_time" in props
    assert "end_time" in props
    assert "tool_name" in props
    assert "exclude_task_ids" in props
    assert "max_results" in props


def test_activity_recall_created_at_indexes_exist(tmp_path):
    db_path = str(tmp_path / "test_idx.db")
    db_mgr = DatabaseManager(db_path)
    set_db_manager(db_mgr)
    with db_mgr.get_session() as session:
        from sqlalchemy import text
        rows = session.execute(text(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name IN ('ix_task_events_created_at', 'ix_conversation_turns_created_at')"
        )).fetchall()
        names = {r[0] for r in rows}
        assert "ix_task_events_created_at" in names
        assert "ix_conversation_turns_created_at" in names


@pytest.mark.asyncio
async def test_activity_recall_exclude_task_ids_filters_events(activity_db):
    """Events from excluded task_ids are omitted from the result."""
    session = activity_db
    _add_event(session, "t_current", _now() - timedelta(minutes=10), summary="current task event")
    _add_event(session, "t_prior", _now() - timedelta(minutes=30), summary="prior task event")
    _add_event(session, "t_other", _now() - timedelta(minutes=20), summary="other task event")
    session.commit()

    tool = ActivityRecallTool()
    out = await tool.execute(ActivityRecallInput(
        start_time=_now() - timedelta(hours=1),
        exclude_task_ids=["t_current"],
    ))
    assert out.success is True
    summaries = [e["summary"] for e in out.data["events"]]
    assert "current task event" not in summaries
    assert "prior task event" in summaries
    assert "other task event" in summaries


@pytest.mark.asyncio
async def test_activity_recall_exclude_multiple_task_ids(activity_db):
    """Multiple excluded task_ids are all filtered out."""
    session = activity_db
    _add_event(session, "t1", _now() - timedelta(minutes=10), summary="excluded 1")
    _add_event(session, "t2", _now() - timedelta(minutes=9), summary="excluded 2")
    _add_event(session, "t3", _now() - timedelta(minutes=8), summary="kept")
    session.commit()

    tool = ActivityRecallTool()
    out = await tool.execute(ActivityRecallInput(
        start_time=_now() - timedelta(hours=1),
        exclude_task_ids=["t1", "t2"],
    ))
    assert out.success is True
    summaries = [e["summary"] for e in out.data["events"]]
    assert "excluded 1" not in summaries
    assert "excluded 2" not in summaries
    assert "kept" in summaries
