"""Phase 9 REAL process-kill delegation recovery test (Windows, gated).

Runs ONLY when SYTHION_RUN_PROCESS_KILL=1 on Windows (TerminateProcess).
Run explicitly:

    set SYTHION_RUN_PROCESS_KILL=1
    python -m pytest backend/tests/test_phase9_process_kill.py -q -m process_kill

Scenario:
  child #1: seed specialist definitions -> propose+validate+assign a
            delegation -> create the specialist kernel task (idempotency key
            delegation:<id>:1) and mark it RUNNING with one audited action
            event -> ready -> sleep.
  parent:   hard-kills the process.
  child #2: stale lease takeover marks the orphaned RUNNING task INTERRUPTED
            (existing machinery) -> DelegationService.recover_delegations()
            resolves the delegation from task evidence.
  parent asserts: exactly ONE specialist task (no duplicate execution),
            no forged completion, delegation recoverable to ASSIGNED,
            second recovery pass is a no-op.
"""

import json
import os
import subprocess
import sys
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

if mode == "delegate_crash":
    import asyncio
    from backend.agents.delegation import DelegationRequest, DelegationService
    from backend.agents.service import AgentService
    from backend.api.schemas import TaskState
    from backend.services.task_runner import TaskRunner

    agents = AgentService(session)
    agents.ensure_definitions()
    main = agents.get_or_create_main()
    svc = DelegationService(session)
    proposal = svc.propose(DelegationRequest(
        parent_agent_id=main.id, specialist_role="ANALYSIS",
        objective="crash recovery probe"))
    assert proposal["error"] is None
    d = proposal["delegation"]
    assert svc.validate(d.id)["ok"]
    assigned = svc.assign(d.id)
    spec_id = assigned["specialist"].id

    runner = TaskRunner(session)
    assert asyncio_ok(runner.acquire_lease())
    d.attempt = 1
    d.status = "RUNNING"
    session.commit()
    task = asyncio_ok(runner.create_task(
        "[specialist:ANALYSIS] crash recovery probe",
        idempotency_key=f"delegation:%s:1" % d.id))
    d.specialist_task_id = task.id
    session.commit()
    # Ensure the lease knows which task to mark interrupted on takeover
    try:
        runner._leases.touch_task(task.id)
    except Exception:
        pass
    asyncio_ok(runner.transition_state(task.id, TaskState.RUNNING,
                                       "task.execution_started",
                                       {{"specialist_delegation": d.id}}))
    agents.transition(spec_id, "RUNNING", reason="delegation_started")
    runner.ledger.append_event(task.id, "specialist.action", {{
        "delegation_id": d.id, "tool": "system_time",
        "success": True, "seq": 1}})
    session.commit()
    print(json.dumps({{"delegation_id": d.id, "task_id": task.id,
                       "specialist_agent_id": spec_id}}), flush=True)
    time.sleep(120)

elif mode == "delegate_recover":
    from backend.agents.delegation import DelegationService
    from backend.models.database import Task
    from backend.services.task_runner import TaskRunner

    runner = TaskRunner(session)
    acquired = asyncio_ok(runner.acquire_lease())  # stale takeover interrupts

    state = json.loads("""{child_state}""")
    svc = DelegationService(session)
    first = svc.recover_delegations()
    second = svc.recover_delegations()

    tasks = session.query(Task).filter(
        Task.original_request.like("[specialist:%")).all()
    delegation = svc.get_delegation(state["delegation_id"])
    from backend.agents.service import AgentService
    specialist = AgentService(session).get_agent(state["specialist_agent_id"])
    print(json.dumps({{
        "lease_acquired": acquired,
        "first_recovery": first,
        "second_recovery_count": len(second),
        "task_count": len(tasks),
        "task_states": [t.state for t in tasks],
        "delegation_status": delegation.status,
        "integration_outcome": delegation.integration_outcome,
        "specialist_state": specialist.state if specialist else None,
    }}), flush=True)
'''


def _run_child(mode, db_path, lease_timeout, state=None):
    script = CHILD_TEMPLATE.format(
        project=PROJECT, db_path=db_path, mode=mode,
        lease_timeout=lease_timeout,
        child_state=json.dumps(state or {}))
    return subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def test_delegation_survives_process_kill():
    import tempfile

    lease_timeout = 2
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "phase9_kill.db")

        child = _run_child("delegate_crash", db_path, lease_timeout)
        line = None
        deadline = time.time() + 60
        while time.time() < deadline:
            output_line = child.stdout.readline()
            if output_line.strip().startswith("{"):
                line = output_line.strip()
                break
        assert line, "child #1 never reached the delegating state"
        state = json.loads(line)
        child.kill()
        child.wait(timeout=15)
        # Allow lease to expire (2s) plus a buffer for dead-pid takeover
        time.sleep(3)

        child2 = _run_child("delegate_recover", db_path, lease_timeout,
                            state=state)
        out, _ = child2.communicate(timeout=120)
        start = out.find("{")
        assert start >= 0, f"child #2 produced no JSON: {out!r}"
        result = json.loads(out[start:out.rfind("}") + 1])

        # No duplicate specialist execution.
        assert result["task_count"] == 1
        # Lease takeover marked the orphaned work interrupted (existing
        # machinery — not a second recovery mechanism).
        assert result["task_states"] == ["interrupted"]
        # Recovery resolved the delegation honestly: recoverable, NOT done.
        assert result["first_recovery"][0]["outcome"] == \
            "recoverable_interrupted"
        assert result["delegation_status"] == "ASSIGNED"
        assert result["integration_outcome"] is None  # no forged completion
        assert result["specialist_state"] == "CANCELLED"
        # Idempotent: second pass found nothing RUNNING.
        assert result["second_recovery_count"] == 0
