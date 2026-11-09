"""Full Sythion ORM regression tests for read-only reasoning recovery preflight.

No model, tool, scheduler, lease recovery, or mutating API calls are made.
"""

import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models.database import Base, Objective, Task, TaskEvent
from backend.runtime.task_ownership import TaskOwnershipRow
from backend.services.goal_reasoning_continuity import read_goal_reasoning_continuity


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([
        Objective(id="goal-a", title="Goal A", status="active", priority=2),
        Objective(id="goal-b", title="Goal B", status="active", priority=2),
    ])
    session.commit()
    yield session
    session.close()
    engine.dispose()


def setup_task(db, task_id="owned", goal_id="goal-a", state="running", *, event=True):
    db.add(Task(id=task_id, objective_id=goal_id, original_request="test", state=state, version=1))
    if event:
        db.add(TaskEvent(task_id=task_id, event_type="reasoning.started",
                         event_data=json.dumps({"job_id": f"call-{task_id}", "purpose": "task_decision"}),
                         sequence_num=1))
    db.commit()


def setup_owner(db, task_id="owned", status="ACTIVE", delta=30, *, action=None, uncertain=None):
    now = datetime.utcnow()
    db.add(TaskOwnershipRow(
        task_id=task_id, owner_nonce="test-owner", worker_id="test-worker", status=status,
        expires_at=now + timedelta(seconds=delta), active_action_id=action,
        uncertain_action_ids=uncertain,
    ))
    db.commit()


def test_current_owner_may_still_be_running(db):
    setup_task(db)
    setup_owner(db, delta=120)
    result = read_goal_reasoning_continuity(db, "goal-a")
    assert result["tasks"][0]["recovery"]["state"] == "possibly_running"
    assert result["recovery_preflight"]["possibly_running_tasks"] == 1
    assert result["recovery_preflight"]["review_required_tasks"] == 0


def test_expired_owner_is_review_required_not_auto_resumed(db):
    setup_task(db)
    setup_owner(db, delta=-120)
    result = read_goal_reasoning_continuity(db, "goal-a")
    assert result["tasks"][0]["recovery"]["state"] == "stale_reasoning_review"
    assert result["recovery_preflight"]["review_required_tasks"] == 1
    assert result["recovery_preflight"]["automatic_replay_allowed"] is False


def test_released_owner_with_unresolved_reasoning_is_reviewed(db):
    setup_task(db)
    setup_owner(db, status="RELEASED")
    result = read_goal_reasoning_continuity(db, "goal-a")
    assert result["tasks"][0]["recovery"]["owner_lease"] == "released"
    assert result["tasks"][0]["recovery"]["state"] == "unresolved_reasoning_review"


@pytest.mark.parametrize("raw", ['["action-secret"]', '{invalid', 'null'])
def test_uncertain_or_malformed_external_action_is_reviewed_without_leaking_ids(db, raw):
    setup_task(db, event=False)
    setup_owner(db, uncertain=raw)
    result = read_goal_reasoning_continuity(db, "goal-a")
    assert result["recovery_preflight"]["external_effect_uncertain_tasks"] == 1
    assert result["tasks"][0]["recovery"]["state"] == "external_effect_review"
    assert "action-secret" not in repr(result)
    assert "test-owner" not in repr(result)
    assert "test-worker" not in repr(result)


def test_other_goals_ownership_and_receipts_are_excluded(db):
    setup_task(db, "ours", "goal-a")
    setup_task(db, "foreign", "goal-b")
    setup_owner(db, "ours", delta=120)
    setup_owner(db, "foreign", delta=-120, uncertain='["hidden"]')
    result = read_goal_reasoning_continuity(db, "goal-a")
    assert result["task_window"]["total"] == 1
    assert [task["task_id"] for task in result["tasks"]] == ["ours"]
    assert result["recovery_preflight"]["external_effect_uncertain_tasks"] == 0
    assert "foreign" not in repr(result)
    assert "hidden" not in repr(result)


def test_preflight_does_not_modify_task_owner_or_event_rows(db):
    setup_task(db)
    setup_owner(db, delta=-120)
    before_tasks = [(row.id, row.state, row.version) for row in db.query(Task).all()]
    before_owner = [(row.task_id, row.status, row.owner_nonce)
                    for row in db.query(TaskOwnershipRow).all()]
    before_events = db.query(TaskEvent).count()
    read_goal_reasoning_continuity(db, "goal-a")
    assert [(row.id, row.state, row.version) for row in db.query(Task).all()] == before_tasks
    assert [(row.task_id, row.status, row.owner_nonce)
            for row in db.query(TaskOwnershipRow).all()] == before_owner
    assert db.query(TaskEvent).count() == before_events


def test_partial_window_cannot_be_claimed_fully_reconciled(db):
    for index in range(3):
        setup_task(db, f"t-{index}", "goal-a", event=False)
    result = read_goal_reasoning_continuity(db, "goal-a", limit=2)
    assert result["task_window"]["truncated"] is True
    assert result["recovery_preflight"]["window_complete"] is False


def test_task_active_action_without_owner_row_is_not_mistaken_for_clean_work(db):
    setup_task(db, event=False)
    row = db.query(Task).filter_by(id="owned").one()
    row.active_action_id = "external-action-id-do-not-disclose"
    db.commit()
    result = read_goal_reasoning_continuity(db, "goal-a")
    assert result["tasks"][0]["recovery"]["state"] == "external_effect_review"
    assert result["recovery_preflight"]["external_effect_uncertain_tasks"] == 1
    assert "external-action-id-do-not-disclose" not in repr(result)
