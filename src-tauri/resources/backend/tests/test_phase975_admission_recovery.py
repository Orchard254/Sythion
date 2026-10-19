"""Phase 9.75 — REAL process-kill DURING delegation admission (Windows, gated).

Runs ONLY when SYTHION_RUN_PROCESS_KILL=1:

    set SYTHION_RUN_PROCESS_KILL=1
    python -m pytest backend/tests/test_phase975_admission_recovery.py -q -m process_kill

Scenario:
  child A: proposes a delegation, then enters validate(). The atomic
           admission UPDATE has executed but NOT committed: the ledger
           append for the 'validated' stage is patched to sleep. The parent
           hard-kills the process exactly inside the admission transaction.
  parent:  asserts NO partial admission is visible (the uncommitted UPDATE
           rolled back with the process): row stays PROPOSED, zero admitted
           specialist slots (no phantom reservation), DB integrity intact.
  child B: reuses the EXISTING recovery machinery
           (DelegationService.recover_delegations — no second subsystem),
           then validates the same delegation and re-proposes the same
           objective.

Assertions:
  - no permanently reserved phantom slot;
  - no orphaned VALIDATED delegation from the killed writer;
  - duplicate idempotency still resolves to the SAME delegation;
  - admission succeeds after restart and parent state remains valid.
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

TASK_ID = "admission-crash-task"
OBJECTIVE = "crash during admission probe"

CHILD_TEMPLATE = r'''
import sys, json, time
sys.path.insert(0, r"{project}")

from backend.core.db_manager import DatabaseManager
from backend.models.database import set_db_manager

db_mgr = DatabaseManager(r"{db_path}")
set_db_manager(db_mgr)
session = db_mgr.new_session()

mode = "{mode}"

if mode == "admit_crash":
    # Kill window: INSIDE the admission transaction — after the atomic
    # conditional UPDATE ran, before its ledger event/commit.
    from backend.runtime.events import DbEventLedger
    _orig_append = DbEventLedger.append_event
    def _sleepy_append(self, scope, event_type, event_data):
        if isinstance(event_data, dict) and event_data.get("stage") == "validated":
            print(json.dumps({{"stage": "inside_admission"}}), flush=True)
            time.sleep(120)
        return _orig_append(self, scope, event_type, event_data)
    DbEventLedger.append_event = _sleepy_append

    from backend.agents.delegation import DelegationRequest, DelegationService
    svc = DelegationService(session)
    proposal = svc.propose(DelegationRequest(
        parent_agent_id="{main_id}",
        specialist_role="ANALYSIS",
        objective={objective!r},
        task_id="{task_id}",
    ))
    assert proposal["error"] is None
    d = proposal["delegation"]
    result = svc.validate(d.id)   # never returns; process is killed here
    print(json.dumps({{"unexpected": True, "result": str(result)}}), flush=True)

elif mode == "admit_recover":
    from sqlalchemy import text as _text
    from backend.agents.delegation import DelegationRequest, DelegationService
    from backend.agents.service import AgentService

    row = session.execute(_text(
        "SELECT id FROM delegations WHERE task_id = :t"),
        {{"t": "{task_id}"}}).fetchone()
    delegation_id = row[0]

    # Existing recovery pass (idempotent, reused — not a new mechanism).
    svc = DelegationService(session)
    recovery = svc.recover_delegations()

    validation = svc.validate(delegation_id)
    fresh = svc.get_delegation(delegation_id)
    session.refresh(fresh)

    dup = svc.propose(DelegationRequest(
        parent_agent_id="{main_id}",
        specialist_role="ANALYSIS",
        objective={objective!r},
        task_id="{task_id}",
    ))

    main = AgentService(session).get_or_create_main()
    print(json.dumps({{
        "recovery_entries": len(recovery),
        "first_validate_ok": bool(validation.get("ok")),
        "first_validate_reasons": list(validation.get("reasons") or []),
        "status_after": fresh.status,
        "dup_error": dup["error"],
        "dup_same_id": bool(dup["delegation"] is not None and
                            dup["delegation"].id == delegation_id),
        "parent_type": main.type,
        "parent_state": main.state,
    }}), flush=True)
'''


def _run_child(mode, db_path, main_id, delegation_id=""):
    script = CHILD_TEMPLATE.format(
        project=PROJECT, db_path=db_path, mode=mode, main_id=main_id,
        task_id=TASK_ID, objective=OBJECTIVE, delegation_id=delegation_id)
    return subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def _snapshot(db_path):
    from backend.core.db_manager import DatabaseManager
    from sqlalchemy import text

    mgr = DatabaseManager(db_path)
    s = mgr.new_session()
    try:
        rows = s.execute(text(
            "SELECT status FROM delegations WHERE task_id = :t"),
            {"t": TASK_ID}).fetchall()
        admitted = s.execute(text(
            "SELECT COUNT(*) FROM delegations WHERE task_id = :t AND status IN "
            "('VALIDATED','ASSIGNED','RUNNING','RESULT_READY','VERIFIED')"),
            {"t": TASK_ID}).scalar()
        integrity = s.execute(text("PRAGMA integrity_check")).scalar()
        return [r[0] for r in rows], int(admitted), integrity
    finally:
        s.close()
        mgr.engine.dispose()  # release the file lock for temp cleanup


def test_admission_survives_process_kill():
    import tempfile

    from backend.core.db_manager import DatabaseManager
    from backend.agents.service import AgentService

    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "phase975_kill.db")
        mgr = DatabaseManager(db_path)
        seed = mgr.new_session()
        agents = AgentService(seed)
        agents.ensure_definitions()
        main = agents.get_or_create_main()
        main_id = main.id
        seed.close()
        mgr.engine.dispose()

        # --- Process A: dies inside the admission transaction ---
        victim = _run_child("admit_crash", db_path, main_id)
        line = None
        deadline = time.time() + 60
        while time.time() < deadline:
            out_line = victim.stdout.readline()
            if out_line.strip().startswith("{"):
                line = out_line.strip()
                break
        assert line and "inside_admission" in line, (
            "child never reached the admission transaction")
        victim.kill()
        victim.wait(timeout=15)

        # No partial admission may be visible after the kill.
        statuses, admitted, integrity = _snapshot(db_path)
        assert statuses == ["PROPOSED"], statuses
        assert admitted == 0, "phantom specialist slot reserved by dead process"
        assert integrity == "ok"

        time.sleep(0.2)

        # --- Process B: existing recovery + clean re-admission ---
        child_b = _run_child("admit_recover", db_path, main_id)
        out, _ = child_b.communicate(timeout=120)
        start = out.find("{")
        assert start >= 0, f"child B produced no JSON: {out!r}"
        result = json.loads(out[start:out.rfind("}") + 1])

        assert result["first_validate_ok"] is True, result
        assert result["status_after"] == "VALIDATED"
        assert result["dup_error"] == "duplicate_delegation"
        assert result["dup_same_id"] is True
        assert result["parent_type"] == "MAIN"
        assert result["recovery_entries"] == 0  # nothing was RUNNING

        statuses, admitted, integrity = _snapshot(db_path)
        assert sorted(statuses) == ["VALIDATED"], statuses
        assert admitted == 1  # exactly one slot consumed by the survivor
        assert integrity == "ok"
