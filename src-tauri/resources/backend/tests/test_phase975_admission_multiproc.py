"""Phase 9.75 — REAL multi-process delegation admission contention tests.

Unlike the thread-based Phase 9.5 admission tests, these spawn INDEPENDENT
OS processes that contend at the SQLite write boundary of a shared file
database. This is the environment the atomic admission invariant must hold
in: separate Python interpreters, separate sessions, no shared locks.

Run explicitly (also runs as part of the normal suite on Windows):

    python -m pytest backend/tests/test_phase975_admission_multiproc.py -q

Invariant under test (Phase 9.75):

    N concurrent callers → database admission →
    active ADMITTED delegations <= configured max_specialists_per_task,
    with no visible over-limit VALIDATED state and no corruption.
"""

import json
import os
import subprocess
import sys

import pytest

pytestmark = [
    pytest.mark.skipif(
        os.name != "nt",
        reason="multi-process contention convention is exercised on Windows CI",
    ),
]

PROJECT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT not in sys.path:
    sys.path.insert(0, PROJECT)

TASK_ID = "multiproc-parent-task"

CHILD_TEMPLATE = r'''
import sys, json
sys.path.insert(0, r"{project}")

from backend.core.db_manager import DatabaseManager
from backend.models.database import set_db_manager

db_mgr = DatabaseManager(r"{db_path}")
set_db_manager(db_mgr)
session = db_mgr.new_session()

from backend.agents.delegation import DelegationRequest, DelegationService

svc = DelegationService(session)
proposal = svc.propose(DelegationRequest(
    parent_agent_id="{main_id}",
    specialist_role="ANALYSIS",
    objective={objective!r},
    task_id="{task_id}",
))
out = {{"proposed_error": proposal["error"]}}
d = proposal["delegation"]
if d is not None:
    out["delegation_id"] = d.id
    try:
        validation = svc.validate(d.id)
        out["ok"] = bool(validation.get("ok"))
        out["reasons"] = list(validation.get("reasons") or [])
        fresh = svc.get_delegation(d.id)
        session.refresh(fresh)
        out["status"] = fresh.status
        out["failure_reason"] = fresh.failure_reason
    except Exception as exc:  # surfaced, never swallowed silently
        out["ok"] = False
        out["reasons"] = ["child_exception: %s: %s" % (type(exc).__name__, exc)]
print(json.dumps(out), flush=True)
'''


def _run_child(db_path, main_id, objective):
    script = CHILD_TEMPLATE.format(
        project=PROJECT, db_path=db_path, main_id=main_id,
        task_id=TASK_ID, objective=objective)
    return subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def _collect(children):
    results = []
    for c in children:
        stdout, _ = c.communicate(timeout=180)
        assert c.returncode == 0, f"child exited {c.returncode}: {stdout}"
        line = [l for l in stdout.splitlines() if l.strip().startswith("{")]
        assert line, f"no JSON from child: {stdout}"
        results.append(json.loads(line[-1]))
    return results


@pytest.fixture
def seeded_db(tmp_path):
    """Fresh shared file DB with definitions + Main Agent pre-seeded so the
    children contend ONLY on admission, not on seeding."""
    from backend.core.db_manager import DatabaseManager
    from backend.agents.service import AgentService

    db_path = os.path.join(str(tmp_path), "phase975_admission.db")
    mgr = DatabaseManager(db_path)
    session = mgr.new_session()
    agents = AgentService(session)
    agents.ensure_definitions()
    main = agents.get_or_create_main()
    main_id = main.id
    session.close()
    return db_path, main_id


ADMITTED_STATES = ("VALIDATED", "ASSIGNED", "RUNNING",
                   "RESULT_READY", "VERIFIED")


def _snapshot(db_path):
    """Authoritative cross-process snapshot: statuses + SQLite integrity."""
    from backend.core.db_manager import DatabaseManager
    from sqlalchemy import text

    mgr = DatabaseManager(db_path)
    s = mgr.new_session()
    try:
        rows = s.execute(text(
            "SELECT id, status, failure_reason FROM delegations "
            "WHERE task_id = :t"), {"t": TASK_ID}).fetchall()
        integrity = s.execute(text("PRAGMA integrity_check")).scalar()
        return [(r[0], r[1], r[2]) for r in rows], integrity
    finally:
        s.close()


class TestMultiProcessAdmission:

    def test_exact_boundary_two_admit(self, seeded_db):
        """limit=2, two concurrent processes: BOTH admit — exact boundary."""
        db_path, main_id = seeded_db
        children = [
            _run_child(db_path, main_id, f"boundary job {i}")
            for i in range(2)
        ]
        results = _collect(children)

        assert all(r["ok"] for r in results), results
        assert all(r["status"] == "VALIDATED" for r in results), results

        rows, integrity = _snapshot(db_path)
        admitted = [r for r in rows if r[1] in ADMITTED_STATES]
        assert len(admitted) == 2
        assert integrity == "ok"

    def test_above_boundary_contention_never_exceeds_limit(self, seeded_db):
        """Six concurrent processes, limit=2: at most two admitted, refusals
        are deterministic FAILED rows, no over-limit VALIDATED, no damage."""
        db_path, main_id = seeded_db
        children = [
            _run_child(db_path, main_id, f"contended job {i}")
            for i in range(6)
        ]
        results = _collect(children)

        oks = [r for r in results if r["ok"]]
        assert len(oks) <= 2, f"fan-out exceeded: {results}"

        rows, integrity = _snapshot(db_path)
        admitted = [r for r in rows if r[1] in ADMITTED_STATES]
        assert len(admitted) <= 2, rows
        # No invalid VALIDATED remains: every admitted row really is within
        # the invariant, every refusal is terminal and diagnosed.
        assert len([r for r in rows if r[1] == "PROPOSED"]) == 0, rows
        for r in rows:
            if r[1] == "FAILED":
                assert r[2] and "max_specialists_per_task" in r[2], rows
        assert len(rows) == 6, rows  # nothing lost
        assert integrity == "ok"

    def test_duplicate_objective_across_processes(self, seeded_db):
        """Same parent+role+objective from three processes: exactly ONE
        active delegation survives; duplicates are rejected deterministically."""
        db_path, main_id = seeded_db
        children = [
            _run_child(db_path, main_id, "identical duplicate objective")
            for _ in range(3)
        ]
        results = _collect(children)

        proposed_ok = [r for r in results if r["proposed_error"] is None]
        duplicates = [r for r in results if r["proposed_error"] ==
                      "duplicate_delegation"]
        assert len(proposed_ok) >= 1, results
        assert len(proposed_ok) + len(duplicates) == 3, results

        rows, integrity = _snapshot(db_path)
        active_dupes = [r for r in rows if r[1] in (
            "PROPOSED",) + ADMITTED_STATES]
        assert len(active_dupes) == 1, rows
        assert integrity == "ok"

    def test_no_orphaned_proposed_after_contention(self, seeded_db):
        """Every proposal ends either admitted or explicitly FAILED — a crash
        of the invariant would leave PROPOSED rows holding phantom intent."""
        db_path, main_id = seeded_db
        children = [
            _run_child(db_path, main_id, f"terminal job {i}")
            for i in range(4)
        ]
        _collect(children)
        rows, _ = _snapshot(db_path)
        for _, status, _reason in rows:
            assert status != "PROPOSED", rows
