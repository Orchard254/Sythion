"""Blocked-work briefing: deterministic, bounded, read-only, non-authorizing."""

from datetime import datetime

from backend.models.database import MainAgent, Objective, PlanStep, Task
from backend.runtime.permission_proposal import PermissionProposalRow
from backend.services.user_briefing import build_user_briefing


def _objective(db, oid="obj-1"):
    db.add(Objective(id=oid, title="Goal", status="active",
                     purpose="do the thing"))
    db.flush()
    return oid


def _task(db, tid, state="waiting_for_user", question=None, reason=None):
    db.add(Task(
        id=tid, original_request="request", state=state,
        pending_question=question, terminal_reason=reason,
    ))
    db.flush()


def test_empty_state_has_no_blockers(test_db):
    result = build_user_briefing(test_db)
    assert result["has_blockers"] is False
    assert result["items"] == []
    assert "Nothing is blocked" in result["headline"]
    assert result["authorized"] is False
    assert result["approval_issued"] is False
    assert result["execution_started"] is False


def test_blocked_items_are_collected_and_ordered(test_db):
    _task(test_db, "t-wait", "waiting_for_user", question="Which file?")
    _task(test_db, "t-held", "interrupted", reason="uncertain external effect")
    oid = _objective(test_db)
    db_step = PlanStep(
        objective_id=oid, step_key="s1", step_order=1,
        description="step", status="blocked", blocker_reason="needs input",
    )
    test_db.add(db_step)
    test_db.add(PermissionProposalRow(
        proposal_id="p-1", task_id="t-wait", action_id="a-1",
        tool_name="file_write", task_version=1, schema_version=1,
        params_digest="d", proposal_digest="d", target_canonical="x",
        content_sha256="h", content_bytes=0, target_must_not_exist=True,
        status="awaiting", created_at=datetime.utcnow(),
    ))
    test_db.add(MainAgent(id=1, agent_id="agent-1", state="paused"))
    test_db.commit()

    result = build_user_briefing(test_db)
    kinds = [item["kind"] for item in result["items"]]
    assert kinds == [
        "task_held", "task_needs_input", "goal_step_blocked",
        "permission_awaiting", "agent_not_active",
    ]
    assert result["has_blockers"] is True
    needs_input = next(i for i in result["items"] if i["kind"] == "task_needs_input")
    assert needs_input["reason"] == "Which file?"
    assert all(item["authority"] == "none" for item in result["items"])
    assert all(item["approved"] is False for item in result["items"])
    assert all(
        result[key] is False
        for key in ("authorized", "approval_issued", "execution_started")
    )
    assert "not an approval" in result["note"]


def test_untrusted_text_is_bounded_and_sanitized(test_db):
    long_question = "line1\nline2\t" + "x" * 500
    _task(test_db, "t-long", "waiting_for_user", question=long_question)
    test_db.commit()

    result = build_user_briefing(test_db)
    reason = result["items"][0]["reason"]
    assert len(reason) <= 160
    assert "\n" not in reason
    assert "\t" not in reason
    assert "approval" not in reason.lower()
    assert "resume" not in reason.lower()


def test_terminal_and_active_tasks_are_not_briefed(test_db):
    _task(test_db, "t-done", "completed")
    _task(test_db, "t-run", "running")
    test_db.commit()
    assert build_user_briefing(test_db)["items"] == []


def test_briefing_endpoint_is_read_only(test_client, test_db):
    _task(test_db, "t-wait", "waiting_for_user", question="Which file?")
    test_db.commit()

    response = test_client.get("/api/v1/briefing")
    assert response.status_code == 200
    body = response.json()
    assert body["has_blockers"] is True
    assert body["authorized"] is False
    assert body["approval_issued"] is False
    assert any(i["ref"] == "t-wait" for i in body["items"])


def test_briefing_creates_no_state_changes(test_db):
    _task(test_db, "t-wait", "waiting_for_user", question="ok?")
    test_db.commit()
    before = (test_db.query(Task).get("t-wait").state,
              test_db.query(Task).get("t-wait").pending_question)
    build_user_briefing(test_db)
    after = (test_db.query(Task).get("t-wait").state,
             test_db.query(Task).get("t-wait").pending_question)
    assert before == after
