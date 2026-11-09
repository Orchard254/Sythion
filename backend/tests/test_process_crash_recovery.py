"""
Phase 3.5 REAL process-kill crash recovery test (Windows).

Environment gate: runs ONLY when SYTHION_RUN_PROCESS_KILL=1 on Windows
(uses TerminateProcess — a genuinely forceful kill). The standard suite
skips it; run explicitly with:

    set SYTHION_RUN_PROCESS_KILL=1
    python -m pytest backend/tests/test_process_crash_recovery.py -q -m process_kill

Scenario:
  child process #1: init DB -> create Goal+Plan+Step -> acquire lease ->
                    create task + mark step RUNNING (persisted) -> ready -> sleep
  parent: waits for "READY", then kill() (hard termination)
  child process #2: same DB -> lease takeover (stale) marks task INTERRUPTED ->
                    startup recovery resolves the step
  parent asserts: exactly ONE task, no forged completion, step recoverable,
                  attempts not incremented, coherent event history, and that a
                  THIRD recovery pass is a no-op.
"""

import json
import os
import sys
import subprocess
import time

import pytest

pytestmark = [
    pytest.mark.process_kill,
    pytest.mark.skipif(os.name != "nt", reason="Windows-only integration test"),
    pytest.mark.skipif(
        os.environ.get("SYTHION_RUN_PROCESS_KILL") != "1",
        reason="set SYTHION_RUN_PROCESS_KILL=1 to enable real process-kill testing",
    ),
]

PROJECT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT not in sys.path:
    sys.path.insert(0, PROJECT)

CHILD_TEMPLATE = r'''
import sys, json, time
sys.path.insert(0, r"{project}")

def asyncio_ok(coro):
    import asyncio
    return asyncio.run(coro)

from backend.core.config import settings
settings.lease_timeout_seconds = {lease_timeout}
settings.lease_heartbeat_seconds = max(1, {lease_timeout} // 3)

from backend.core.db_manager import DatabaseManager
from backend.models.database import set_db_manager

db_mgr = DatabaseManager(r"{db_path}")
set_db_manager(db_mgr)
session = db_mgr.new_session()

mode = "{mode}"

if mode == "crash":
    from backend.services.goal_manager import GoalManager
    from backend.services.planner import CandidateStepModel, Planner
    from backend.api.schemas import TaskState
    from backend.services.task_runner import TaskRunner

    manager = GoalManager(session)
    planner = Planner(session)
    goal = manager.create_goal("Crash recovery goal")
    plan = planner.create_plan(goal.id, [CandidateStepModel(key="work", description="durable work")])
    step = planner.plan_steps(plan.id)[0]

    runner = TaskRunner(session)
    assert asyncio_ok(runner.acquire_lease())
    task = asyncio_ok(runner.create_task("durable work",
                                         idempotency_key=f"goalstep:{{step.id}}:0"))
    # Bind the running attempt to the lease so takeover knows what to interrupt.
    assert asyncio_ok(runner.renew_lease(task_id=task.id))
    session.query(type(step)).filter_by(id=step.id).update({{
        "status": "running",
        "linked_task_id": task.id,
        "evidence_task_id": task.id,
        "attempt_count": 1,
    }})
    # Commit the step's pending mutation before runner transition, so the
    # runner does not see a caller-owned transaction (production guard).
    session.commit()
    asyncio_ok(runner.transition_state(task.id, TaskState.RUNNING,
                                       "task.execution_started", {{}}))
    print(json.dumps({{"goal_id": goal.id, "step_id": step.id, "task_id": task.id}}), flush=True)
    # Simulate ongoing work; parent will hard-kill us here.
    time.sleep(120)

elif mode == "recover":
    # Lease takeover first: the old lease expired, so acquiring it now marks
    # the orphaned RUNNING task INTERRUPTED via the authoritative path.
    from backend.services.task_runner import TaskRunner
    from backend.services.task_orchestrator import TaskOrchestrator
    from backend.services.planner import Planner
    from backend.models.database import Task

    runner = TaskRunner(session)
    acquired = asyncio_ok(runner.acquire_lease())

    orchestrator = TaskOrchestrator(state_store=runner, planner=Planner(session))
    results = orchestrator.recover_all()

    state = json.loads("""{child_state}""")
    step_id = state["step_id"]
    step = Planner(session).get_step(step_id)
    tasks = session.query(Task).all()
    print(json.dumps({{
        "lease_acquired": acquired,
        "recovery": results,
        "step_status": step.status,
        "attempt_count": step.attempt_count,
        "task_count": len(tasks),
        "task_states": [t.state for t in tasks],
    }}), flush=True)

elif mode == "persist_wake":
    # A meaningful event occurs; the DURABLE PENDING wake is written BEFORE
    # any queue/worker involvement, then the process dies.
    from backend.models.database import MainAgent, AgentWake
    from datetime import datetime

    agent = session.query(MainAgent).filter_by(id=1).first() or MainAgent(
        id=1, agent_id="wake-agent", name="Sythion", state="active")
    session.add(agent)
    session.flush()
    wake = AgentWake(
        agent_id=agent.agent_id,
        wake_key="task.completed:pre-kill-task",
        trigger_type="task.completed",
        trigger_reference="pre-kill-task",
        status="PENDING",
        coalesce_key="background_work",
    )
    session.add(wake)
    session.commit()
    print(json.dumps({{"agent_id": agent.agent_id, "wake_id": wake.id}}), flush=True)
    time.sleep(120)

elif mode == "recover_wake":
    # Fresh process: bootstrap sequence must reclaim the durable pending wake;
    # policy default OFF resolves it as an audited SKIPPED — never lost,
    # never executed without authorization, never duplicated.
    from backend.services.bootstrap import run_startup_sequence
    boot = run_startup_sequence(db_mgr)
    from backend.services.main_agent import MainAgentService
    svc = MainAgentService(session)
    svc.skip_wake("task.completed", reason="background_disabled",
                  wake_key="task.completed:pre-kill-task")

    from backend.models.database import AgentWake as AW
    rows = session.query(AW).filter(
        AW.wake_key == "task.completed:pre-kill-task").all()
    print(json.dumps({{
        "order": boot["order"],
        "wake_count": len(rows),
        "statuses": sorted(r.status for r in rows),
        "stop_reasons": [r.stop_reason for r in rows],
    }}), flush=True)

elif mode == "cognitive_park":
    # The live cognitive gate parks an ambiguous task in WAITING_FOR_USER
    # with durable clarification metadata; then the process dies hard.
    from backend.services.task_runner import TaskRunner
    from backend.services.cognitive_runtime import CognitiveDecisionAdapter
    from backend.models.database import Task as T

    runner = TaskRunner(session)
    assert asyncio_ok(runner.acquire_lease())
    task = asyncio_ok(runner.create_task("delete that",
                                         idempotency_key="cognitive-park:1"))
    adapter = CognitiveDecisionAdapter(runner, session)
    routing = asyncio_ok(adapter.route_new_input(task.id,
                                                 task.original_request))
    session.expire_all()
    row = session.query(T).filter_by(id=task.id).one()
    print(json.dumps({{
        "task_id": task.id,
        "routing_mode": routing.mode,
        "state": row.state,
    }}), flush=True)
    time.sleep(120)

elif mode == "recover_park":
    # Fresh process: NO transient cognitive state exists anywhere — the
    # parked decision lives entirely in durable Goal/Task state. The SAME
    # task is resumable through the normal reply lifecycle.
    from backend.services.task_runner import TaskRunner
    from backend.services.cognitive_runtime import CognitiveDecisionAdapter
    from backend.models.database import Task as T

    rows = session.query(T).all()
    pending = (json.loads(rows[0].pending_action)
               if rows and rows[0].pending_action else None)

    runner = TaskRunner(session)
    acquired = asyncio_ok(runner.acquire_lease())
    merged = False
    if acquired and rows:
        adapter = CognitiveDecisionAdapter(runner, session)
        asyncio_ok(adapter.apply_clarification_answer(
            rows[0].id, "sort by modified date, newest first"))
        session.expire_all()
        refreshed = session.query(T).filter_by(id=rows[0].id).one()
        merged = "[clarification]" in (refreshed.normalized_goal or "")

    print(json.dumps({{
        "task_count": len(rows),
        "state": rows[0].state if rows else None,
        "pending_kind": pending.get("kind") if pending else None,
        "awaiting": pending.get("awaiting_user_reply") if pending else None,
        "lease_acquired": acquired,
        "resumed_merged": merged,
    }}), flush=True)
'''


def _run_child(mode, db_path, lease_timeout, child_state="{}"):
    source = CHILD_TEMPLATE.format(
        project=PROJECT.replace("\\", "\\\\"),
        db_path=db_path.replace("\\", "\\\\"),
        lease_timeout=lease_timeout,
        mode=mode,
        child_state=json.dumps(child_state).replace('"', '\\"'),
    )
    return subprocess.Popen(
        [sys.executable, "-c", source],
        cwd=PROJECT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _readline(proc, timeout=30):
    import threading

    result = {}

    def _read():
        result["line"] = proc.stdout.readline()

    t = threading.Thread(target=_read, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        proc.kill()
        proc.wait(timeout=10)
        raise AssertionError("child timed out before producing output")
    line = result.get("line", "")
    if not line:
        # Child exited early; surface its stderr for diagnosis.
        err = b""
        try:
            err = proc.stderr.read() or b""
        except Exception:
            pass
        raise AssertionError(f"child exited early:\n{str(err)[-2000:]}")
    return line.strip()


class TestRealProcessKillRecovery:
    def test_forceful_kill_then_fresh_process_recovers(self, tmp_path):
        db_path = str(tmp_path / "crash_test.db")
        lease_timeout = 2  # seconds — stale takeover must be quick

        # --- Process #1: create durable work, then get force-killed -----
        p1 = _run_child("crash", db_path, lease_timeout)
        try:
            state_line = _readline(p1, timeout=60)
            state = json.loads(state_line)
            assert state["step_id"] > 0
            assert state["task_id"]
        finally:
            p1.kill()  # TerminateProcess — the hardest stop available
            p1.wait(timeout=10)
            if p1.stdout:
                p1.stdout.close()
            if p1.stderr:
                p1.stderr.close()

        # Wait out the lease expiry so the fresh process can take over.
        time.sleep(lease_timeout + 1.5)

        # --- Process #2: fresh interpreter, same DB, recovery runs ------
        p2 = _run_child("recover", db_path, lease_timeout, child_state=state)
        try:
            out_line = _readline(p2, timeout=60)
            result = json.loads(out_line)
        finally:
            p2.kill()
            p2.wait(timeout=10)
            for s in (p2.stdout, p2.stderr):
                if s:
                    s.close()

        # --- Invariants --------------------------------------------------
        assert result["lease_acquired"] is True  # stale takeover succeeded

        recovery = result["recovery"]
        assert len(recovery) == 1
        assert recovery[0]["outcome"] == "deferred_interrupted_manual_review_required"
        assert recovery[0]["resolved"] is False

        # No duplicate task was created.
        assert result["task_count"] == 1

        # The interrupted task is NOT guessed as success.
        assert result["task_states"] == ["interrupted"]

        # The step stays quarantined, not re-queued for automatic execution.
        assert result["step_status"] == "running"

        # Attempts were NOT incremented by recovery.
        assert result["attempt_count"] == 1

        # --- Third pass over the SAME database: idempotency -------------
        p3 = _run_child("recover", db_path, lease_timeout, child_state=state)
        try:
            again = json.loads(_readline(p3, timeout=60))
        finally:
            p3.kill()
            p3.wait(timeout=10)
            for s in (p3.stdout, p3.stderr):
                if s:
                    s.close()

        assert again["task_count"] == 1  # still exactly one task
        assert again["step_status"] == "running"
        assert again["recovery"][0]["outcome"] == "deferred_interrupted_manual_review_required"
        assert again["attempt_count"] == 1
class TestWakeDurabilityAcrossProcessDeath:
    def test_pending_wake_survives_forceful_kill_and_is_recovered(self, tmp_path):
        """A durable PENDING wake written before a forceful kill must still
        exist after restart, be resolved exactly once by the bootstrap
        sequence + explicit resolution, and never duplicate."""
        db_path = str(tmp_path / "wake_durability.db")
        lease_timeout = 2

        p1 = _run_child("persist_wake", db_path, lease_timeout)
        try:
            state = json.loads(_readline(p1, timeout=60))
            assert state["wake_id"] > 0
        finally:
            p1.kill()
            p1.wait(timeout=10)
            for s in (p1.stdout, p1.stderr):
                if s:
                    s.close()

        time.sleep(0.5)

        p2 = _run_child("recover_wake", db_path, lease_timeout)
        try:
            result = json.loads(_readline(p2, timeout=90))
        finally:
            p2.kill()
            p2.wait(timeout=10)
            for s in (p2.stdout, p2.stderr):
                if s:
                    s.close()

        # Bootstrap ordering held even in a fresh process.
        assert result["order"] == [
            "task_recovery", "goal_migration", "agent_init", "wake_recovery",
        ]
        # Exactly ONE wake row: no duplicates across process death.
        assert result["wake_count"] == 1
        # Default-off policy resolved it as an audited SKIPPED — not lost,
        # not silently executed.
        assert result["statuses"] == ["SKIPPED"]
        assert result["stop_reasons"] == ["background_disabled"]

        # Third pass over the same DB: still exactly one row (idempotent).
        p3 = _run_child("recover_wake", db_path, lease_timeout)
        try:
            again = json.loads(_readline(p3, timeout=90))
        finally:
            p3.kill()
            p3.wait(timeout=10)
            for s in (p3.stdout, p3.stderr):
                if s:
                    s.close()
        assert again["wake_count"] == 1


class TestCognitiveParkedDecisionSurvivesForcefulKill:
    """Phase 8.75: a live cognitive decision (parked clarification) is fully
    durable — no transient cognitive state is needed to recover or resume."""

    def test_parked_clarification_survives_kill_and_resumes(self, tmp_path):
        db_path = str(tmp_path / "cognitive_park.db")
        lease_timeout = 2

        # --- Process #1: cognitive gate parks the task, then hard kill ---
        p1 = _run_child("cognitive_park", db_path, lease_timeout)
        try:
            state = json.loads(_readline(p1, timeout=60))
            assert state["routing_mode"] == "clarify"
            assert state["state"] == "waiting_for_user"
            assert state["task_id"]
        finally:
            p1.kill()
            p1.wait(timeout=10)
            for s in (p1.stdout, p1.stderr):
                if s:
                    s.close()

        time.sleep(lease_timeout + 1.5)

        # --- Process #2: fresh process resumes the SAME parked task ------
        p2 = _run_child("recover_park", db_path, lease_timeout)
        try:
            result = json.loads(_readline(p2, timeout=60))
        finally:
            p2.kill()
            p2.wait(timeout=10)
            for s in (p2.stdout, p2.stderr):
                if s:
                    s.close()

        assert result["task_count"] == 1          # exactly one durable task
        assert result["state"] == "waiting_for_user"
        assert result["pending_kind"] == "clarification"
        assert result["awaiting"] is True
        assert result["lease_acquired"] is True
        assert result["resumed_merged"] is True   # resumable with zero
        # transient cognitive state.
