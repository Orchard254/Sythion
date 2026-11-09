"""Phase 12.5 final gate — capture-failure observability + fingerprint semantics.

Contract under test:
  1. Learning capture is best-effort (LEARNING_ONLY): capture failure must
     never affect task execution, verification, or runtime state.
  2. Capture failures must be OBSERVABLE (logged, deduplicated, never silent),
     including non-recorded store results (record() returns reason strings).
  3. No false REAL_EXECUTION record exists when capture fails.
  4. Capture failure never triggers retry/replan.
  5. Fingerprint semantics = PER_TERMINAL_OUTCOME (choice A): each distinct
     terminal observation of an attempt is its own immutable evidence record;
     retries create new attempt identities, and FAILED→SUCCESS_VERIFIED across
     attempts are separate evidence samples feeding retry-success metrics.
"""

import asyncio
import hashlib
import logging

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models.database import Base, PlanningOutcome
from backend.learning.capture import (
    CaptureRejected,
    log_capture_failure,
    report_capture_result,
)
from backend.learning.planning_store import PlanningOutcomeStore
from backend.learning.planning_taxonomy import PlanningProvenance, PlanningStrategy
from backend.services.goal_manager import GoalManager
from backend.services.planner import CandidateStepModel, Planner
from backend.services.task_orchestrator import TaskOrchestrator
from backend.services.task_runner import TaskRunner
from backend.api.schemas import TaskState


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine)
    s = S()
    yield s
    s.close()
    engine.dispose()


@pytest.fixture
def runner(db):
    from backend.services.task_runner import TaskRunner

    r = TaskRunner(db)
    assert asyncio.run(r.acquire_lease())
    return r


def _orch(db, runner):
    return TaskOrchestrator(state_store=runner, planner=Planner(db))


def _goal_with_plan(db, name="Obs gate goal"):
    mgr = GoalManager(db)
    planner = Planner(db)
    goal = mgr.create_goal(name, purpose="capture observability")
    plan = planner.create_plan(goal.id, [CandidateStepModel(key="a", description="step a work")], source="user")
    return goal


def _verifier(db):
    from backend.runtime.events import DbEventLedger
    from backend.services.execution_components import CompletionVerifier

    ledger = DbEventLedger(db)

    def fn(tid):
        return CompletionVerifier(db, ledger.event_history).precondition_error(tid)

    return fn


def _run(db, runner, orch, task_id, state, **kwargs):
    asyncio.run(runner.transition_state(task_id, TaskState.RUNNING, "task.execution_started", {}))
    event = {
        TaskState.COMPLETED: "task.completed",
        TaskState.FAILED: "task.failed",
    }[state]
    kwargs.setdefault("event_data", {})
    kwargs.setdefault("final_result" if state == TaskState.COMPLETED else "terminal_reason", "ok")
    asyncio.run(runner.transition_state(task_id, state, event, **kwargs))
    return asyncio.run(orch.apply_task_outcome(task_id, completion_precondition_error_fn=_verifier(db)))


@pytest.fixture(autouse=True)
def _reset_dedupe():
    from backend.learning import capture

    capture._CAPTURE_FAIL_COUNTS.clear()
    yield
    capture._CAPTURE_FAIL_COUNTS.clear()


# ---------------------------------------------------------------------------
# 1-4. PlanningOutcomeStore.record deliberately fails
# ---------------------------------------------------------------------------

def test_planning_record_failure_runtime_unaffected_and_observable(db, runner, caplog, monkeypatch):
    goal = _goal_with_plan(db)
    orch = _orch(db, runner)
    started = asyncio.run(orch.start_next_step(goal.id))
    task_id = started["task"].id

    def _boom(self, *a, **k):
        raise RuntimeError("deliberate capture failure")

    monkeypatch.setattr(PlanningOutcomeStore, "record", _boom)

    with caplog.at_level(logging.WARNING, logger="sythion.learning.capture"):
        result = _run(db, runner, orch, task_id, TaskState.COMPLETED)

    # runtime succeeded end-to-end
    assert result["outcome"] == "completed"
    assert result["updated"] is True
    from backend.models.database import PlanStep

    step = db.query(PlanStep).filter(PlanStep.objective_id == goal.id).first()
    assert step.status == "completed"
    assert GoalManager(db).get_goal(goal.id).status == "completed"

    # no false REAL_EXECUTION record was created
    rows = (
        db.query(PlanningOutcome)
        .filter(PlanningOutcome.goal_id == goal.id, PlanningOutcome.provenance == PlanningProvenance.REAL_EXECUTION.value)
        .all()
    )
    assert rows == []

    # failure is observable with diagnostic context
    hits = [r for r in caplog.records if "learning_capture_failed" in r.getMessage() and "planning_real_outcome" in r.getMessage()]
    assert len(hits) == 1
    msg = hits[0].getMessage()
    assert "deliberate capture failure" in msg
    assert task_id in msg


def test_capture_failure_does_not_trigger_retry_or_replan(db, runner, monkeypatch):
    goal = _goal_with_plan(db)
    orch = _orch(db, runner)
    started = asyncio.run(orch.start_next_step(goal.id))
    tid = started["task"].id

    def _boom(self, *a, **k):
        raise RuntimeError("capture down")

    monkeypatch.setattr(PlanningOutcomeStore, "record", _boom)
    result = _run(db, runner, orch, tid, TaskState.COMPLETED)
    assert result["outcome"] == "completed"
    # no retry machinery touched: step is completed, category None
    assert result["category"] is None

    # failure-side: capture fails on a FAILED execution too — runtime semantics preserved
    started2 = asyncio.run(orch.start_next_step(_goal_with_plan(db, "g2").id))
    tid2 = started2["task"].id
    result2 = _run(db, runner, orch, tid2, TaskState.FAILED, terminal_reason="boom")
    assert result2["outcome"] == "failed"
    assert result2["category"] is not None
    # retry decision derives from policy, unaffected by capture errors
    rinfo = orch.schedule_retry_if_allowed(started2["step"].id)
    assert rinfo is not None and rinfo["scheduled"] is True


def test_retry_capture_failure_observable(db, runner, caplog, monkeypatch):
    import backend.learning.capture as cap_mod

    goal = _goal_with_plan(db, "g3")
    orch = _orch(db, runner)
    started = asyncio.run(orch.start_next_step(goal.id))
    tid = started["task"].id

    def _boom(*a, **k):
        raise ValueError("retry capture down")

    monkeypatch.setattr(cap_mod, "record_retry_outcome", _boom)

    with caplog.at_level(logging.WARNING, logger="sythion.learning.capture"):
        result = _run(db, runner, orch, tid, TaskState.COMPLETED)

    assert result["outcome"] == "completed"
    hits = [r for r in caplog.records if "retry_outcome" in r.getMessage()]
    assert len(hits) == 1


def test_non_raising_rejection_is_observable(db, caplog):
    """store.record() swallows DB/validation problems into reason strings;
    those must surface too."""
    with caplog.at_level(logging.WARNING, logger="sythion.learning.capture"):
        report_capture_result(
            "planning_real_outcome",
            {"outcome": None, "eligible": False, "reason": "invalid outcome BOGUS"},
            task_id="t1",
            goal_id="g1",
        )
    msgs = [r.getMessage() for r in caplog.records]
    assert any("invalid outcome BOGUS" in m for m in msgs)


# ---------------------------------------------------------------------------
# Flood prevention: repeated identical failures do not flood logs
# ---------------------------------------------------------------------------

def test_no_flood_on_repeated_identical_capture_errors(db, runner, caplog, monkeypatch):
    goal = _goal_with_plan(db)
    orch = _orch(db, runner)

    def _boom(self, *a, **k):
        raise RuntimeError("persistent outage")

    monkeypatch.setattr(PlanningOutcomeStore, "record", _boom)

    with caplog.at_level(logging.WARNING, logger="sythion.learning.capture"):
        for i in range(6):
            g = _goal_with_plan(db, f"flood{i}")
            started = asyncio.run(orch.start_next_step(g.id))
            _run(db, runner, orch, started["task"].id, TaskState.COMPLETED)

    warnings = [r for r in caplog.records if "learning_capture_failed" in r.getMessage()]
    # first occurrence logged once; repeats counted silently until interval
    assert len(warnings) == 1

    # direct mechanism check: 1st + every 50th occurrence
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="sythion.learning.capture"):
        for i in range(101):
            log_capture_failure("unit_site", RuntimeError("same"), n=i)
    assert len([r for r in caplog.records]) == 3  # occurrences 1, 50, 100


# ---------------------------------------------------------------------------
# Fingerprint semantics: PER_TERMINAL_OUTCOME (choice A)
# ---------------------------------------------------------------------------

def _fp(goal, plan, task, attempt, outcome):
    raw = f"planning_real_task|{goal}|{plan}|{task}|{attempt}|{outcome}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def test_fingerprint_per_terminal_outcome_contract(db):
    """Choice A: each distinct terminal observation of an attempt is a separate
    evidence record. Same task+attempt+FAILED vs +SUCCESS_VERIFIED produce
    DIFFERENT fingerprints BY DESIGN — evidence rows are immutable observations;
    correction happens by appending new terminal observations (new attempts /
    recovery re-evaluations), never by mutating prior evidence."""
    store = PlanningOutcomeStore(db)
    fp_fail = _fp("gA", 1, "tA", 1, "FAILED")
    fp_ok = _fp("gA", 1, "tA", 1, "SUCCESS_VERIFIED")
    assert fp_fail != fp_ok  # per-terminal-outcome identity

    r1 = store.record(
        planning_strategy=PlanningStrategy.DIRECT_PLAN.value, task_class="goal",
        goal_id="gA", plan_id=1, outcome="FAILED", verified_success=False,
        evidence_fingerprint=fp_fail, provenance="REAL_EXECUTION",
    )
    r2 = store.record(
        planning_strategy=PlanningStrategy.DIRECT_PLAN.value, task_class="goal",
        goal_id="gA", plan_id=1, outcome="SUCCESS_VERIFIED", verified_success=True,
        evidence_fingerprint=fp_ok, provenance="REAL_EXECUTION",
    )
    assert r1["outcome"].id != r2["outcome"].id
    assert db.query(PlanningOutcome).filter(PlanningOutcome.goal_id == "gA").count() == 2


def test_duplicate_protection_within_same_terminal_observation(db):
    """Re-emission of the SAME terminal observation is idempotent."""
    store = PlanningOutcomeStore(db)
    fp = _fp("gB", 2, "tB", 1, "SUCCESS_VERIFIED")
    kw = dict(
        planning_strategy=PlanningStrategy.DIRECT_PLAN.value, task_class="goal",
        goal_id="gB", plan_id=2, outcome="SUCCESS_VERIFIED", verified_success=True,
        provenance="REAL_EXECUTION",
    )
    r1 = store.record(evidence_fingerprint=fp, **kw)
    r2 = store.record(evidence_fingerprint=fp, **kw)
    assert r1["outcome"].id == r2["outcome"].id
    assert r2["reason"] == "duplicate_fingerprint_idempotent"
    assert db.query(PlanningOutcome).filter(PlanningOutcome.goal_id == "gB").count() == 1


def test_fingerprint_truncation_convention(db):
    """:16 hex chars (64-bit) is the repo-wide convention (15 sites across all
    learning stores). At TTL-bounded evidence scale, birthday collision risk is
    negligible (<~1e-7 even at millions of rows); full-digest migration would
    invalidate all stored fingerprints' idempotency keys. Kept deliberately."""
    fp = _fp("gC", 3, "tC", 1, "SUCCESS_VERIFIED")
    assert len(fp) == 16
    int(fp, 16)  # valid hex


# ---------------------------------------------------------------------------
# LEARNING_ONLY preserved
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Production double-apply: kernel's PostTaskPipeline.apply_plan_outcome applies
# the outcome inside run_task; execute_session must tolerate the short-circuit
# re-application instead of raising KeyError('outcome').
# ---------------------------------------------------------------------------

def test_execute_session_tolerates_pipeline_double_apply(db):
    goal = _goal_with_plan(db, "double_apply")
    # fresh runner WITHOUT a pre-held lease: execute_session manages leasing
    orch = TaskOrchestrator(state_store=TaskRunner(db), planner=Planner(db))

    runner = orch.state_store

    async def fake_kernel(_db, task_id, _ex):
        """Mimic the real kernel: own lease + terminal transition + pipeline-side
        apply_plan_outcome (which already captures REAL_EXECUTION)."""
        from backend.api.schemas import TaskState as TS

        assert await runner.acquire_lease(task_id=task_id)
        try:
            await runner.transition_state(task_id, TS.RUNNING, "task.execution_started", {})
            await runner.transition_state(task_id, TS.COMPLETED, "task.completed", final_result="done", event_data={})
            await orch.apply_task_outcome(task_id)
        finally:
            await runner.release_lease()

    result = asyncio.run(orch.execute_session(goal.id, run_task_fn=fake_kernel))
    assert result["executed"], "session should record the executed step"
    assert result["executed"][0]["outcome"] == "completed"
    assert result["stopped_reason"] == "no_eligible_step"
    # evidence captured exactly once despite two apply calls
    rows = (
        db.query(PlanningOutcome)
        .filter(PlanningOutcome.goal_id == goal.id, PlanningOutcome.provenance == PlanningProvenance.REAL_EXECUTION.value)
        .all()
    )
    assert len(rows) == 1


def test_learning_only_preserved_through_capture_failure(db, runner, monkeypatch):
    from backend.core.config import settings

    assert settings.planning_learning_mode == "LEARNING_ONLY"
    goal = _goal_with_plan(db)
    orch = _orch(db, runner)
    started = asyncio.run(orch.start_next_step(goal.id))

    def _boom(self, *a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr(PlanningOutcomeStore, "record", _boom)
    result = _run(db, runner, orch, started["task"].id, TaskState.COMPLETED)
    assert result["outcome"] == "completed"
    assert settings.planning_learning_mode == "LEARNING_ONLY"
