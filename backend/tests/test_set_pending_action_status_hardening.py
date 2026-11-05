"""Focused offline regressions for hardened TaskRunner.set_pending_action_status."""

import asyncio
import json

import pytest
from sqlalchemy import text

from backend.api.schemas import TaskState
from backend.models.database import Task
from backend.services.task_runner import TaskRunner


async def _make_runner(test_db):
    runner = TaskRunner(test_db)
    await runner.acquire_lease()
    return runner


def _task_with_pending(test_db, runner, pending, state=TaskState.WAITING_FOR_USER.value):
    task = Task(
        id="pending-task",
        original_request="pending task",
        state=state,
        version=1,
        pending_action=pending,
    )
    test_db.add(task)
    test_db.commit()
    return task


@pytest.mark.asyncio
async def test_set_pending_status_requires_lease(test_db):
    runner = TaskRunner(test_db)  # no lease
    pending = json.dumps({
        "kind": "permission",
        "status": "awaiting",
        "action_id": "action-1",
        "tool_name": "mutating_tool",
        "params": {},
    })
    _task_with_pending(test_db, runner, pending)

    with pytest.raises(RuntimeError, match="lease"):
        await runner.set_pending_action_status("pending-task", "approved")
    refreshed = test_db.query(Task).filter_by(id="pending-task").first()
    assert json.loads(refreshed.pending_action)["status"] == "awaiting"
    assert refreshed.state == TaskState.WAITING_FOR_USER.value


@pytest.mark.asyncio
async def test_set_pending_status_refuses_caller_owned_transaction(test_db):
    runner = await _make_runner(test_db)
    pending = json.dumps({
        "kind": "permission",
        "status": "awaiting",
        "action_id": "action-1",
        "tool_name": "mutating_tool",
        "params": {},
    })
    _task_with_pending(test_db, runner, pending)

    marker = Task(id="caller", original_request="caller", state=TaskState.CREATED.value, version=1)
    test_db.add(marker)
    with pytest.raises(RuntimeError, match="caller-owned"):
        await runner.set_pending_action_status("pending-task", "approved")
    assert marker in test_db.new
    test_db.rollback()


@pytest.mark.asyncio
async def test_manufactured_approval_without_binding_fails_closed(test_db):
    runner = await _make_runner(test_db)

    # No authenticated binding at all.
    pending = json.dumps({
        "kind": "permission",
        "status": "awaiting",
        "tool_name": "mutating_tool",
        "params": {},
    })
    _task_with_pending(test_db, runner, pending)
    with pytest.raises(RuntimeError, match="authenticated binding"):
        await runner.set_pending_action_status("pending-task", "approved")
    assert json.loads(test_db.query(Task).filter_by(id="pending-task").first().pending_action)["status"] == "awaiting"

    # Legacy/non-permission payload cannot be upgraded to approved.
    legacy = json.dumps({"status": "awaiting", "tool_name": "mutating_tool", "params": {}})
    _task = test_db.query(Task).filter_by(id="pending-task").first()
    _task.pending_action = legacy
    test_db.commit()
    with pytest.raises(RuntimeError, match="authenticated binding"):
        await runner.set_pending_action_status("pending-task", "approved")
    assert test_db.query(Task).filter_by(id="pending-task").first().pending_action == legacy

    # Invalid JSON is not replaced with an approved payload.
    _task.pending_action = "not-json"
    test_db.commit()
    with pytest.raises(RuntimeError, match="malformed"):
        await runner.set_pending_action_status("pending-task", "approved")
    assert test_db.query(Task).filter_by(id="pending-task").first().pending_action == "not-json"


@pytest.mark.asyncio
async def test_binding_mismatch_fails_closed(test_db):
    runner = await _make_runner(test_db)
    pending = json.dumps({
        "kind": "permission",
        "status": "awaiting",
        "action_id": "action-1",
        "tool_name": "mutating_tool",
        "params": {},
    })
    _task_with_pending(test_db, runner, pending)
    with pytest.raises(RuntimeError, match="authenticated binding"):
        await runner.set_pending_action_status("pending-task", "approved", binding="different")
    assert json.loads(test_db.query(Task).filter_by(id="pending-task").first().pending_action)["status"] == "awaiting"

    # Even an exactly matching identifier is not independently authenticated
    # consent, so the transition still fails closed.
    with pytest.raises(RuntimeError, match="authenticated binding"):
        await runner.set_pending_action_status("pending-task", "approved", binding="action-1")
    assert json.loads(test_db.query(Task).filter_by(id="pending-task").first().pending_action)["status"] == "awaiting"


@pytest.mark.asyncio
async def test_non_approved_statuses_do_not_change_state_or_enable_execution(test_db):
    runner = await _make_runner(test_db)
    pending = json.dumps({
        "kind": "permission",
        "status": "awaiting",
        "action_id": "action-1",
        "tool_name": "mutating_tool",
        "params": {},
    })
    # Cancelled and uncertain-ish evidence remain untouched as state.
    _task_with_pending(test_db, runner, pending, state=TaskState.CANCELLED.value)
    task_before = test_db.query(Task).filter_by(id="pending-task").first()
    assert task_before.state == TaskState.CANCELLED.value

    # Use denied, which is not an approval and must not transition or execute.
    await runner.set_pending_action_status("pending-task", "denied")
    task_after = test_db.query(Task).filter_by(id="pending-task").first()
    assert task_after.state == TaskState.CANCELLED.value
    assert json.loads(task_after.pending_action)["status"] == "denied"

    await runner.release_lease()


@pytest.mark.asyncio
async def test_approved_transition_fails_closed_even_with_matching_binding(test_db):
    runner = await _make_runner(test_db)
    pending = json.dumps({
        "kind": "permission",
        "status": "awaiting",
        "action_id": "action-1",
        "tool_name": "mutating_tool",
        "params": {},
    })
    _task_with_pending(test_db, runner, pending)
    with pytest.raises(RuntimeError, match="authenticated binding"):
        await runner.set_pending_action_status("pending-task", "approved")
    task = test_db.query(Task).filter_by(id="pending-task").first()
    data = json.loads(task.pending_action)
    assert data["status"] == "awaiting"
    assert data["action_id"] == "action-1"
    assert task.state == TaskState.WAITING_FOR_USER.value
    await runner.release_lease()


@pytest.mark.asyncio
async def test_missing_task_and_missing_pending_are_fail_safe(test_db):
    runner = await _make_runner(test_db)
    # Missing task raises (no silent success / no authentication invented).
    with pytest.raises(RuntimeError, match="not found"):
        await runner.set_pending_action_status("no-such-task", "approved")
    # Missing pending action is a safe no-op.
    task = Task(id="no-pending", original_request="x", state=TaskState.CREATED.value, version=1)
    test_db.add(task)
    test_db.commit()
    await runner.set_pending_action_status("no-pending", "approved")
    assert test_db.query(Task).filter_by(id="no-pending").first().pending_action is None
    await runner.release_lease()
