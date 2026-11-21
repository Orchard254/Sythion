"""Post-consent continuation regression tests.

A verified owner approval must finish the work: the task parked by the
execution loop in WAITING_FOR_USER for THAT permission proposal is reopened to
RUNNING and its ordinary worker is scheduled once. No new authority path is
created - the executor still verifies the durable receipt before any tool
effect, and every fail-closed / replay / deny case must leave the task parked
with no dispatch.
"""

import json
from types import SimpleNamespace

import pytest

from backend.api.schemas import TaskState, ToolOutput
from backend.models.database import Task
from backend.services.task_execution_loop import TaskExecutionLoop
from backend.tools.executor import ToolExecutor

PARAMS = {"path": "C:/tmp/consent_continuation_test.txt", "content": "hello"}
TOOL = "file_create"


class _FakeEffect:
    """Mutating, confirmation-required tool whose effect is only counted."""

    def __init__(self):
        self.calls = 0

    def get_policy_requirements(self):
        return {"mutating": True, "requires_confirmation": True}

    async def execute_tool(self, tool_name, input_data, policy_context=None):
        self.calls += 1
        return ToolOutput(success=True, data={"ok": True}, model_summary="done")


def _loop(db, runner, fake):
    ex = ToolExecutor()
    ex.get_tool = lambda name: fake
    ex.execute_tool = fake.execute_tool
    loop = TaskExecutionLoop(db, runner, ex)
    loop._ownership_lost = False
    loop.cancellation._state_store = runner
    return loop


async def _park_for_approval(runner, db, fake):
    """Drive a real consequential action into the WAITING_FOR_USER park."""
    task = await runner.create_task("consent continuation")
    acquired = await runner.acquire_task_ownership(task.id, worker_id="consent-test")
    assert acquired.acquired
    await runner.transition_state(task.id, TaskState.RUNNING, "task.execution_started", {})

    loop = _loop(db, runner, fake)
    assert await loop._execute_tool(
        task.id, {"tool_name": TOOL, "params": dict(PARAMS)}
    ) is False

    parked = await runner.get_task(task.id)
    assert parked.state == TaskState.WAITING_FOR_USER.value
    pending = json.loads(parked.pending_action)
    assert pending["kind"] == "permission"
    assert pending["status"] == "awaiting"
    assert pending["proposal_id"] and pending["action_id"]
    # The production loop releases task ownership when it parks (execute_task
    # finally). Mirror that so the route is the only contender for ownership.
    await runner.release_task_ownership(task_id=task.id, outcome="execute_task_finished")
    return task, pending


@pytest.fixture
def scheduled(monkeypatch):
    """Record run_task scheduling deterministically, without running the worker.

    A plain function returning a coroutine appends synchronously when the route
    calls it, so the count is observable immediately after the HTTP response
    regardless of how the TestClient event loop schedules background tasks.
    """
    calls = []

    def _worker(db_, task_id, tool_executor):
        calls.append(task_id)

        async def _noop():
            return None

        return _noop()

    monkeypatch.setattr("backend.api.routes.run_task", _worker)
    return calls


def _decide(client, proposal_id, decision_id, decision):
    return client.post(
        "/api/v1/approvals/decide",
        json={
            "proposal_id": proposal_id,
            "decision_id": decision_id,
            "decision": decision,
        },
    )


def _task(db, task_id):
    return db.query(Task).filter_by(id=task_id).one()


@pytest.mark.asyncio
async def test_approved_decision_resumes_parked_task_and_schedules_once(
    db_session, task_runner, test_client, scheduled
):
    task, pending = await _park_for_approval(task_runner, db_session, _FakeEffect())
    # Staging used the fixture's lease; the route must take the lease itself.
    await task_runner.release_lease()

    resp = _decide(test_client, pending["proposal_id"], "approve-1", "approve")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["approved"] is True
    assert body["receipt_verified"] is True
    assert body["continuation_scheduled"] is True
    assert body["authorization_issued"] is False  # no second authority path

    resumed = _task(db_session, task.id)
    assert resumed.state == TaskState.RUNNING.value
    assert resumed.pending_question is None
    assert scheduled == [task.id]  # scheduled exactly once


@pytest.mark.asyncio
async def test_replay_does_not_double_dispatch(
    db_session, task_runner, test_client, scheduled
):
    task, pending = await _park_for_approval(task_runner, db_session, _FakeEffect())
    await task_runner.release_lease()

    first = _decide(test_client, pending["proposal_id"], "approve-replay", "approve")
    assert first.status_code == 200, first.text
    assert first.json()["continuation_scheduled"] is True
    assert scheduled == [task.id]

    replay = _decide(test_client, pending["proposal_id"], "approve-replay", "approve")
    assert replay.status_code == 200
    assert replay.json()["approved"] is True
    assert replay.json()["continuation_scheduled"] is False
    assert scheduled == [task.id]  # no second dispatch

    # A different decision_id on the already-decided proposal is refused and
    # cannot mint a second receipt or a second continuation.
    conflict = _decide(test_client, pending["proposal_id"], "approve-other", "approve")
    assert conflict.status_code == 409
    assert scheduled == [task.id]


@pytest.mark.asyncio
async def test_denied_decision_does_not_dispatch(
    db_session, task_runner, test_client, scheduled
):
    task, pending = await _park_for_approval(task_runner, db_session, _FakeEffect())
    await task_runner.release_lease()

    resp = _decide(test_client, pending["proposal_id"], "deny-1", "deny")
    assert resp.status_code == 200, resp.text
    assert resp.json()["approved"] is False
    assert resp.json()["continuation_scheduled"] is False
    assert scheduled == []
    assert _task(db_session, task.id).state == TaskState.WAITING_FOR_USER.value


@pytest.mark.asyncio
async def test_unverified_or_malformed_receipt_never_dispatches(
    db_session, task_runner, test_client, scheduled, monkeypatch
):
    from backend.runtime.consequential_approval import (
        ConsequentialApprovalResult,
        ConsequentialApprovalService,
    )

    task, pending = await _park_for_approval(task_runner, db_session, _FakeEffect())
    await task_runner.release_lease()

    # A receipt that LOOKS approved but the service did not verify. The route's
    # gate must refuse the continuation even though the receipt object claims
    # approval (authority never comes from the object alone).
    def _unverified(self, *args, **kwargs):
        fake_receipt = SimpleNamespace(
            approved=True,
            status="approved",
            decision_id=kwargs.get("decision_id"),
            action_id=kwargs.get("action_id"),
        )
        return ConsequentialApprovalResult(
            allowed=False,
            approved=False,
            reason="safeguard denied",
            receipt=fake_receipt,
        )

    monkeypatch.setattr(ConsequentialApprovalService, "record_decision", _unverified)
    resp = _decide(test_client, pending["proposal_id"], "unverified-1", "approve")
    assert resp.status_code == 200, resp.text
    assert resp.json()["approved"] is False
    assert resp.json()["continuation_scheduled"] is False
    assert scheduled == []
    assert _task(db_session, task.id).state == TaskState.WAITING_FOR_USER.value

    # A verified flag with a missing/malformed receipt is equally refused.
    def _no_receipt(self, *args, **kwargs):
        return ConsequentialApprovalResult(
            allowed=True, approved=True, receipt=None
        )

    monkeypatch.setattr(ConsequentialApprovalService, "record_decision", _no_receipt)
    await task_runner.acquire_lease()
    task2, pending2 = await _park_for_approval(task_runner, db_session, _FakeEffect())
    await task_runner.release_lease()
    resp2 = _decide(test_client, pending2["proposal_id"], "malformed-1", "approve")
    assert resp2.status_code == 200, resp2.text
    assert resp2.json()["continuation_scheduled"] is False
    assert scheduled == []
    assert _task(db_session, task2.id).state == TaskState.WAITING_FOR_USER.value


@pytest.mark.asyncio
async def test_non_approval_parked_task_is_left_untouched(
    db_session, task_runner, test_client, scheduled
):
    from backend.services.consequential_approval import ConsequentialApprovalService

    # A task parked for a genuine clarification (not staged by this approval)
    # must not be resumed, and resume_admission's conservative gates are intact.
    task = await task_runner.create_task("clarification wait")
    await task_runner.acquire_task_ownership(task.id, worker_id="consent-test")
    await task_runner.transition_state(task.id, TaskState.RUNNING, "task.execution_started", {})
    await task_runner.transition_state(
        task.id,
        TaskState.WAITING_FOR_USER,
        "task.waiting_for_user",
        {"question": "Which file?"},
        pending_question="Which file?",
    )
    version = _task(db_session, task.id).version
    svc = ConsequentialApprovalService(db=db_session)
    proposal = svc.stage_proposal(
        task_id=task.id,
        action_id="clarification-action",
        tool_name="file_create",
        task_version=version,
        params=PARAMS,
    )
    db_session.commit()
    await task_runner.release_lease()

    resp = _decide(test_client, proposal.proposal_id, "clarify-approve", "approve")
    assert resp.status_code == 200, resp.text
    assert resp.json()["approved"] is True
    # No parked "approved" permission action bound to this decision -> no resume.
    assert resp.json()["continuation_scheduled"] is False
    assert scheduled == []
    untouched = _task(db_session, task.id)
    assert untouched.state == TaskState.WAITING_FOR_USER.value
    assert untouched.pending_question == "Which file?"
