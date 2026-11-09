"""Pure goal projection tests, with no provider, database or tool access."""

import pytest

from backend.services.goal_reasoning_continuity import project_goal_reasoning


def task(tid, state, events=(), association="direct"):
    return {"task_id": tid, "state": state, "events": list(events), "association": association}


def ev(kind, job_id):
    return {"event_type": f"reasoning.{kind}", "event_data": {"job_id": job_id}}


def view(rows, *, total=None, status="active"):
    return project_goal_reasoning("goal-a", status, rows, total_tasks=len(rows) if total is None else total)


def test_empty_goal_is_not_claimed_complete():
    result = view([])
    assert result["task_window"] == {"total": 0, "returned": 0, "truncated": False}
    assert result["attention"] == "inspect_goal_plan"
    assert result["reasoning_receipts"]["started"] == 0


def test_completed_task_with_no_receipts_does_not_claim_no_calls():
    result = view([task("t", "completed")])
    assert result["tasks"][0]["reasoning"]["receipt_state"] == "not_recorded"
    assert result["reasoning_receipts"]["tasks_without_start_receipts"] == 1
    assert result["attention"] == "inspect_goal_plan"


def test_completed_goal_is_not_inferred_from_completed_tasks():
    result = view([task("t", "completed")], status="completed")
    assert result["attention"] == "goal_terminal"
    assert result["goal_status"] == "completed"


def test_unresolved_reasoning_does_not_authorize_replay():
    result = view([task("t", "running", [ev("started", "j1")])])
    assert result["attention"] == "inspect_unresolved_receipts"
    assert result["tasks"][0]["reasoning"]["unresolved_count"] == 1
    assert "Never replay" in result["note"]


def test_normal_receipt_does_not_claim_work_complete():
    result = view([task("t", "running", [ev("started", "j1"), ev("response_received", "j1")])])
    assert result["attention"] == "observe_running_work"
    assert result["tasks"][0]["reasoning"]["receipt_state"] == "recorded"
    assert result["reasoning_receipts"]["response_received"] == 1


@pytest.mark.parametrize("kind", ["failed", "timed_out", "cancelled"])
def test_terminal_provider_failure_is_not_unresolved(kind):
    result = view([task("t", "paused", [ev("started", "j1"), ev(kind, "j1")])])
    assert result["reasoning_receipts"][kind] == 1
    assert result["reasoning_receipts"]["unresolved_tasks"] == 0
    assert result["attention"] == "inspect_goal_plan"


def test_user_input_wait_has_priority():
    result = view([task("one", "running"), task("two", "waiting_for_user")])
    assert result["attention"] == "await_user"


def test_paused_goal_blocks_implicit_resumption():
    result = view([task("one", "running")], status="paused")
    assert result["attention"] == "goal_paused"


def test_failed_task_recommends_review_not_reexecution():
    result = view([task("one", "failed")])
    assert result["attention"] == "review_task_failure"


def test_truncation_disclosed_and_no_unwarranted_conclusion():
    result = view([task("a", "completed")], total=9)
    assert result["task_window"]["truncated"] is True
    assert result["attention"] == "more_tasks_not_inspected"
    assert result["task_window"]["total"] == 9


def test_unresolved_still_surfaces_in_truncated_window():
    result = view([task("a", "running", [ev("started", "x")])], total=99)
    assert result["attention"] == "inspect_unresolved_receipts"


def test_duplicate_task_state_counts_and_mixed_events():
    result = view([
        task("a", "running", [ev("started", "a"), ev("response_received", "a")]),
        task("b", "failed", [ev("started", "b"), ev("failed", "b")], "plan_evidence"),
    ])
    assert result["reasoning_receipts"]["started"] == 2
    assert result["reasoning_receipts"]["failed"] == 1
    assert result["task_states"] == {"failed": 1, "running": 1}
    assert result["tasks"][1]["association"] == "plan_evidence"


def test_malformed_events_cannot_create_false_call_counts():
    result = view([task("a", "running", [{"event_type": "reasoning.started", "event_data": "untrusted"},
                                                ev("started", " "), {"event_type": "tool.executed", "event_data": {"job_id": "x"}}])])
    assert result["reasoning_receipts"]["started"] == 0
    assert result["tasks"][0]["reasoning"]["receipt_state"] == "not_recorded"


@pytest.mark.parametrize("kwargs", [
    {"goal_id": "", "goal_status": "active", "task_snapshots": (), "total_tasks": 0},
    {"goal_id": "g", "goal_status": "", "task_snapshots": (), "total_tasks": 0},
    {"goal_id": "g", "goal_status": "active", "task_snapshots": (), "total_tasks": -1},
    {"goal_id": "g", "goal_status": "active", "task_snapshots": (task("t", "running"),), "total_tasks": 0},
])
def test_invalid_request_refused(kwargs):
    with pytest.raises(ValueError):
        project_goal_reasoning(**kwargs)


@pytest.mark.parametrize("bad", [None, {}, {"task_id": "", "state": "running"},
                                 {"task_id": "t", "state": None},
                                 {"task_id": "t", "state": "running", "events": "text"}])
def test_invalid_task_snapshot_refused(bad):
    with pytest.raises(ValueError):
        view([bad])


def test_paused_goal_does_not_implicitly_request_resume_from_waiting_task():
    result = view([task("t", "waiting_for_user")], status="paused")
    assert result["attention"] == "goal_paused"


def test_terminal_goal_does_not_implicitly_request_new_work():
    result = view([task("t", "waiting_for_user")], status="completed")
    assert result["attention"] == "goal_terminal"
