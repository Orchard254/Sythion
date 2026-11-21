"""Focused wiring tests for the per-action consent flow.

Connects the previously orphaned proposal/receipt system to the live approval
handoff: a consequential action stages a real proposal, the owner UI decides it,
and only a verified durable receipt may release the pending action.  Every
unverified path stays fail-closed.
"""

import json

import pytest

from backend.api.schemas import TaskState, ToolOutput
from backend.models.database import (
    PermissionDecisionReceiptRow,
    PermissionProposalRow,
    TaskEvent,
)
from backend.services.task_execution_loop import TaskExecutionLoop
from backend.tools.executor import ToolExecutor

PARAMS = {"path": "C:/tmp/consent_flow_test.txt", "content": "hello"}
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
    # execute_task normally initializes these; tests call _execute_tool directly.
    loop._ownership_lost = False
    loop.cancellation._state_store = runner
    return loop


async def _running_task(runner):
    task = await runner.create_task("consent flow")
    acquired = await runner.acquire_task_ownership(task.id, worker_id="consent-test")
    assert acquired.acquired
    await runner.transition_state(task.id, TaskState.RUNNING, "task.execution_started", {})
    return task


async def _stage(loop, task_id):
    assert await loop._execute_tool(task_id, {"tool_name": TOOL, "params": dict(PARAMS)}) is False
    task = await loop.runner.get_task(task_id)
    assert task.state == TaskState.WAITING_FOR_USER.value
    return json.loads(task.pending_action)


def _decide(client, proposal_id, decision_id, decision):
    return client.post(
        "/api/v1/approvals/decide",
        json={
            "proposal_id": proposal_id,
            "decision_id": decision_id,
            "decision": decision,
        },
    )


@pytest.mark.asyncio
async def test_consequential_action_stages_real_proposal_that_is_listed(
    db_session, task_runner, test_client
):
    task = await _running_task(task_runner)
    fake = _FakeEffect()
    loop = _loop(db_session, task_runner, fake)

    pending = await _stage(loop, task.id)
    assert pending["status"] == "awaiting"
    assert pending["tool_name"] == TOOL
    assert pending["action_id"]
    assert pending["proposal_id"]

    proposal = db_session.query(PermissionProposalRow).filter_by(task_id=task.id).one()
    assert proposal.proposal_id == pending["proposal_id"]
    assert proposal.status == "awaiting"
    assert proposal.action_id == pending["action_id"]

    listed = test_client.get("/api/v1/approvals/pending")
    assert listed.status_code == 200
    assert pending["proposal_id"] in [p["proposal_id"] for p in listed.json()["proposals"]]
    assert fake.calls == 0


@pytest.mark.asyncio
async def test_deny_marks_proposal_appends_action_denied_and_stops_relisting(
    db_session, task_runner, test_client
):
    task = await _running_task(task_runner)
    fake = _FakeEffect()
    loop = _loop(db_session, task_runner, fake)
    pending = await _stage(loop, task.id)

    resp = _decide(test_client, pending["proposal_id"], "deny-1", "deny")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "denied"
    assert body["approved"] is False
    assert body["authorization_issued"] is False

    proposal = db_session.query(PermissionProposalRow).filter_by(
        proposal_id=pending["proposal_id"]
    ).one()
    assert proposal.status == "denied"
    assert pending["proposal_id"] not in [
        p["proposal_id"] for p in test_client.get("/api/v1/approvals/pending").json()["proposals"]
    ]

    parked = await task_runner.get_task(task.id)
    assert json.loads(parked.pending_action)["status"] == "denied"
    events = db_session.query(TaskEvent).filter_by(task_id=task.id).all()
    assert any(
        e.event_type == "action.denied"
        and json.loads(e.event_data).get("tool_name") == TOOL
        for e in events
    )
    assert fake.calls == 0


@pytest.mark.asyncio
async def test_verified_receipt_releases_action_and_records_trusted_provenance(
    db_session, task_runner, test_client
):
    task = await _running_task(task_runner)
    fake = _FakeEffect()
    loop = _loop(db_session, task_runner, fake)
    pending = await _stage(loop, task.id)

    resp = _decide(test_client, pending["proposal_id"], "approve-1", "approve")
    assert resp.status_code == 200, resp.text
    assert resp.json()["approved"] is True
    assert resp.json()["authorization_issued"] is False

    receipt = db_session.query(PermissionDecisionReceiptRow).filter_by(
        decision_id="approve-1"
    ).one()
    assert receipt.status == "approved"
    assert receipt.principal_id == "loopback_control_boundary"
    assert receipt.auth_method == "loopback_control_token"

    # Resume the parked task, then attempt the gated handoff.
    await task_runner.transition_state(
        task.id, TaskState.RUNNING, "action.approved",
        {"action_id": pending["action_id"]},
    )
    ok = await loop._execute_tool(
        task.id,
        {"tool_name": TOOL, "params": dict(PARAMS)},
        approved_by_user=True,
        action_id=pending["action_id"],
    )
    assert ok is True
    assert fake.calls == 1
    done = await task_runner.get_task(task.id)
    assert done.state == TaskState.RUNNING.value
    assert done.pending_action is None


@pytest.mark.asyncio
async def test_bare_flag_unverified_and_forged_decision_are_refused(
    db_session, task_runner, test_client
):
    from backend.runtime.consequential_approval import ConsequentialApprovalService

    task = await _running_task(task_runner)
    fake = _FakeEffect()
    loop = _loop(db_session, task_runner, fake)
    pending = await _stage(loop, task.id)

    # Bare approved flag with no receipt is refused at the execution gate.
    assert await loop._execute_tool(
        task.id, {"tool_name": TOOL, "params": dict(PARAMS)},
        approved_by_user=True, action_id=pending["action_id"],
    ) is False
    assert fake.calls == 0

    # A bare set_pending_action_status("approved") is refused.
    with pytest.raises(RuntimeError):
        await task_runner.set_pending_action_status(task.id, "approved")

    # A forged / unverified decision_id does not verify.
    svc = ConsequentialApprovalService(db=db_session)
    forged = svc.verify(
        task_id=task.id,
        action_id=pending["action_id"],
        decision_id="does-not-exist",
        proposal_id=pending["proposal_id"],
    )
    assert forged.ok is False

    # Even a self-supplied "approved" status plus unknown decision_id cannot
    # release the action: the gate still requires a verified receipt.
    parked = await task_runner.get_task(task.id)
    data = json.loads(parked.pending_action)
    data["status"] = "approved"
    data["decision_id"] = "does-not-exist"
    parked.pending_action = json.dumps(data)
    db_session.commit()
    assert await loop._execute_tool(
        task.id, {"tool_name": TOOL, "params": dict(PARAMS)},
        approved_by_user=True, action_id=pending["action_id"],
    ) is False
    assert fake.calls == 0


@pytest.mark.asyncio
async def test_decision_replay_cannot_reauthorize(db_session, task_runner, test_client):
    task = await _running_task(task_runner)
    fake = _FakeEffect()
    loop = _loop(db_session, task_runner, fake)
    pending = await _stage(loop, task.id)

    first = _decide(test_client, pending["proposal_id"], "replay-1", "approve")
    assert first.status_code == 200, first.text
    assert db_session.query(PermissionDecisionReceiptRow).filter_by(
        decision_id="replay-1"
    ).count() == 1

    # An exact replay of the same decision_id is idempotent (200) and still
    # cannot mint a second receipt. A different decision_id on the decided
    # proposal is refused, so replay cannot re-authorize.
    replay = _decide(test_client, pending["proposal_id"], "replay-1", "approve")
    assert replay.status_code == 200
    assert replay.json()["approved"] is True
    second = _decide(test_client, pending["proposal_id"], "replay-2", "approve")
    assert second.status_code == 409
    assert db_session.query(PermissionDecisionReceiptRow).filter_by(
        action_id=pending["action_id"]
    ).count() == 1
