"""Offline isolated crash/recovery regression (narrowly scoped).

This test replays the key restart-protection invariants from the gated
``test_process_crash_recovery.py`` crash scenario WITHOUT spawning a second
OS process. It exists so the scenario's invariants remain executable in
environments where the real ``process_kill`` gate is disabled/blocked.

Safety:
  * uses a disposable SQLite DB under a temp directory;
  * no provider/model/network access;
  * leaves cancelled/waiting/uncertain task state untouched (only the orphaned
    RUNNING task is interrupted and its step returned to READY);
  * does not replay or resolve any uncertain action.
"""

import asyncio
import os
import sys
import tempfile
import time

PROJECT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT not in sys.path:
    sys.path.insert(0, PROJECT)

from backend.core.config import settings

import pytest


def _session_factory(db_path):
    from backend.core.db_manager import DatabaseManager
    from backend.models.database import set_db_manager

    db_mgr = DatabaseManager(db_path)
    set_db_manager(db_mgr)
    return db_mgr, db_mgr.new_session()


def test_offline_crash_recovery_preserves_interrupted_state_and_idempotency(monkeypatch):
    monkeypatch.setattr(settings, "lease_timeout_seconds", 2)
    monkeypatch.setattr(settings, "lease_heartbeat_seconds", 1)
    lease_timeout = 2
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "offline_crash.db")

        db_mgr, session = _session_factory(db_path)
        try:
            from backend.services.goal_manager import GoalManager
            from backend.services.planner import CandidateStepModel, Planner
            from backend.models.database import PlanStep
            from backend.api.schemas import TaskState
            from backend.services.task_runner import TaskRunner

            manager = GoalManager(session)
            planner = Planner(session)
            goal = manager.create_goal("Crash recovery goal")
            plan = planner.create_plan(goal.id, [CandidateStepModel(key="work", description="durable work")])
            step = planner.plan_steps(plan.id)[0]

            runner = TaskRunner(session)
            assert asyncio.run(runner.acquire_lease()) is True
            task = asyncio.run(runner.create_task(
                "durable work",
                idempotency_key=f"goalstep:{step.id}:0",
            ))
            assert asyncio.run(runner.renew_lease(task_id=task.id)) is True

            session.query(PlanStep).filter_by(id=step.id).update({
                "status": "running",
                "linked_task_id": task.id,
                "evidence_task_id": task.id,
                "attempt_count": 1,
            })
            session.commit()

            asyncio.run(runner.transition_state(
                task.id, TaskState.RUNNING, "task.execution_started", {}
            ))
            session.commit()

            saved = {
                "goal_id": goal.id,
                "step_id": step.id,
                "task_id": task.id,
            }
        finally:
            session.close()
            db_mgr.engine.dispose()

        time.sleep(lease_timeout + 1.5)

        db_mgr2, session2 = _session_factory(db_path)
        try:
            from backend.services.task_runner import TaskRunner
            from backend.services.task_orchestrator import TaskOrchestrator
            from backend.services.planner import Planner
            from backend.models.database import Task

            runner2 = TaskRunner(session2)
            assert asyncio.run(runner2.acquire_lease()) is True  # stale takeover

            orchestrator = TaskOrchestrator(state_store=runner2, planner=Planner(session2))
            first = orchestrator.recover_all()

            tasks = session2.query(Task).all()
            current_step = Planner(session2).get_step(saved["step_id"])

            assert len(first) == 1
            assert first[0]["outcome"] == "recoverable_interrupted"
            assert len(tasks) == 1
            assert [t.state for t in tasks] == ["interrupted"]
            assert current_step.status == "ready"
            assert current_step.attempt_count == 1

            # A subsequent recovery pass over the same DB must not duplicate
            # or alter the already-recovered state.
            second_count_before = session2.query(Task).count()
            orchestrator.recover_all()
            assert session2.query(Task).count() == second_count_before
            assert Planner(session2).get_step(saved["step_id"]).status == "ready"
            assert Planner(session2).get_step(saved["step_id"]).attempt_count == 1
        finally:
            session2.close()
            db_mgr2.engine.dispose()
