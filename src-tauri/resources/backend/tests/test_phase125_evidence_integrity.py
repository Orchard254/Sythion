"""Evidence integrity audit tests — legacy/REAL isolation, lifecycle, promotion semantics."""

import tempfile, os
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from backend.models.database import Base, PlanningOutcome
from backend.learning.planning_taxonomy import PlanningStrategy, PlanningProvenance
from backend.learning.planning_store import PlanningOutcomeStore, PlanningPolicyStore


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = S()
    yield s
    s.close()
    engine.dispose()


def test_legacy_not_counted_as_real(db):
    store = PlanningOutcomeStore(db)
    for i in range(10):
        store.record(planning_strategy=PlanningStrategy.DIRECT_PLAN.value, task_class="goal", goal_id=f"leg{i}", outcome="SUCCESS_VERIFIED", verified_success=True, provenance="LEGACY", evidence_refs=[f"goal:leg{i}"])
    stats = store.statistics(task_class="goal", planning_strategy=PlanningStrategy.DIRECT_PLAN.value)
    assert stats.sample_count == 10
    assert stats.real_sample_count == 0
    assert stats.real_verified_success_count == 0
    # promotion requires real -> blocked
    pstore = PlanningPolicyStore(db)
    pol = pstore.create_observed("goal", PlanningStrategy.DIRECT_PLAN.value, evidence_summary={"sample_count": 10, "shadow_validated": True})
    r = pstore.promote(pol.id, target_status="SUPPORTED")
    assert r["ok"] is False
    assert "real" in r["reason"].lower()


def test_legacy_vs_real_isolation_coverage(db):
    store = PlanningOutcomeStore(db)
    store.record(planning_strategy=PlanningStrategy.DIRECT_PLAN.value, task_class="iso", goal_id="r1", outcome="SUCCESS_VERIFIED", verified_success=True, provenance="REAL_EXECUTION", evidence_refs=["goal:r1"])
    store.record(planning_strategy=PlanningStrategy.DIRECT_PLAN.value, task_class="iso", goal_id="l1", outcome="SUCCESS_VERIFIED", verified_success=True, provenance="LEGACY", evidence_refs=["goal:l1"])
    store.record(planning_strategy=PlanningStrategy.DIRECT_PLAN.value, task_class="iso", goal_id="s1", outcome="SUCCESS_VERIFIED", verified_success=True, provenance="SYNTHETIC_TEST", evidence_refs=["goal:s1"])
    store.record(planning_strategy=PlanningStrategy.DIRECT_PLAN.value, task_class="iso", goal_id="i1", outcome="SUCCESS_VERIFIED", verified_success=True, provenance="IMPORTED", evidence_refs=["goal:i1"])
    stats_all = store.statistics(task_class="iso", planning_strategy=PlanningStrategy.DIRECT_PLAN.value, include_synthetic=True)
    stats_real = store.statistics(task_class="iso", planning_strategy=PlanningStrategy.DIRECT_PLAN.value, include_synthetic=False)
    assert stats_all.sample_count == 4
    assert stats_real.sample_count == 1
    assert stats_real.real_sample_count == 1
    # coverage report isolates
    cov = store.coverage_report()
    assert cov["real_sample_count"] == 1
    assert cov["synthetic_sample_count"] == 3  # LEGACY+SYNTHETIC+IMPORTED counted as non-real


def test_planner_create_plan_is_legacy_not_real(db):
    from backend.models.database import Objective
    import datetime
    obj = Objective(id="audit_obj", title="audit", status="active", priority=3, created_at=datetime.datetime.utcnow(), updated_at=datetime.datetime.utcnow())
    db.add(obj); db.commit()
    from backend.services.planner import Planner, CandidateStepModel
    planner = Planner(db)
    cands = [CandidateStepModel(key="a", description="step a", priority=1, depends_on=[])]
    plan = planner.create_plan("audit_obj", cands, source="user")
    # create a second plan to trigger replan record
    cands2 = [CandidateStepModel(key="a", description="step a2", priority=1, depends_on=[])]
    plan2 = planner.create_plan("audit_obj", cands2, source="user", reason="test")
    # the replan planning_outcome should be LEGACY, not REAL_EXECUTION
    rows = db.query(PlanningOutcome).filter(PlanningOutcome.goal_id == "audit_obj").all()
    # at least one replan row exists
    assert len(rows) >= 1
    for r in rows:
        assert r.provenance == "LEGACY"
        assert r.provenance != "REAL_EXECUTION"


def test_real_execution_only_after_verified_success_counts(db):
    store = PlanningOutcomeStore(db)
    # Simulate verified execution: after goal completed with verified steps
    store.record(planning_strategy=PlanningStrategy.DIRECT_PLAN.value, task_class="goal", goal_id="verified1", outcome="SUCCESS_VERIFIED", verified_success=True, provenance="REAL_EXECUTION", evidence_refs=["goal:verified1"])
    stats = store.statistics(task_class="goal", planning_strategy=PlanningStrategy.DIRECT_PLAN.value)
    assert stats.real_verified_success_count == 1
    # LEGACY replan record from plan creation must not count
    store.record(planning_strategy=PlanningStrategy.REPLAN_AFTER_FAILURE.value, task_class="goal", goal_id="verified1", outcome="SUCCESS_PARTIAL", verified_success=None, provenance="LEGACY", evidence_refs=["goal:verified1"])
    stats2 = store.statistics(task_class="goal", planning_strategy=PlanningStrategy.REPLAN_AFTER_FAILURE.value)
    assert stats2.real_verified_success_count == 0


def test_shadow_does_not_use_legacy_for_advantage(db):
    store = PlanningOutcomeStore(db)
    # LEGACY success should not create expected advantage
    for i in range(5):
        store.record(planning_strategy=PlanningStrategy.DEPENDENCY_AWARE_PLAN.value, task_class="goal", goal_id=f"leg_adv{i}", outcome="SUCCESS_VERIFIED", verified_success=True, provenance="LEGACY", evidence_refs=[f"goal:leg_adv{i}"])
    # No REAL evidence, shadow should not claim advantage
    from backend.learning.planning_shadow import evaluate_shadow
    evaluate_shadow(db, task_context={"task_class": "goal"}, failure_context=None, actual_policy="DIRECT_PLAN")
    report = store.shadow_quality_report()
    # expected_advantage should be 0 or None because shadow has no REAL SUPPORTED evidence
    assert report["expected_advantage"] is None or report["expected_advantage"] == 0


def test_promotion_semantics_5plus3_is_minimum_not_proven_superior(db):
    # Verify promotion_readiness level REAL_SUPPORTED is eligibility, not superiority claim
    pstore = PlanningPolicyStore(db)
    ostore = PlanningOutcomeStore(db)
    for i in range(5):
        ostore.record(planning_strategy=PlanningStrategy.DIRECT_PLAN.value, task_class="goal", goal_id=f"sem{i}", outcome="SUCCESS_VERIFIED", verified_success=True, provenance="REAL_EXECUTION", evidence_refs=[f"goal:sem{i}"])
    pol = pstore.create_observed("goal", PlanningStrategy.DIRECT_PLAN.value, evidence_summary={"sample_count": 5})
    readiness = ostore.promotion_readiness(pol)
    assert readiness["readiness_level"] == "REAL_SUPPORTED"
    # doc says this is minimum for review, not proven superior — check eligible_next indicates still needs shadow
    assert "shadow" in (readiness["eligible_next"] or "").lower()


def test_migration_legacy_idempotent_and_backup_restore():
    import tempfile, os
    from backend.core.db_manager import DatabaseManager
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test.db")
        mgr = DatabaseManager(db_path)
        # insert a planning outcome with LEGACY via API
        with mgr.get_session() as s:
            store = PlanningOutcomeStore(s)
            store.record(planning_strategy=PlanningStrategy.DIRECT_PLAN.value, task_class="goal", goal_id="mig1", outcome="SUCCESS_VERIFIED", verified_success=True, provenance="LEGACY", evidence_refs=["goal:mig1"])
        mgr.engine.dispose()
        # backup copy (include wal/shm if present)
        backup = os.path.join(tmp, "backup.db")
        import shutil
        shutil.copy(db_path, backup)
        for suffix in ("-wal", "-shm"):
            src = db_path + suffix
            if os.path.exists(src):
                shutil.copy(src, backup + suffix)
        # restore via new manager
        mgr2 = DatabaseManager(backup)
        with mgr2.get_session() as s2:
            rows = s2.query(PlanningOutcome).filter_by(goal_id="mig1").all()
            assert len(rows) == 1
            assert rows[0].provenance == "LEGACY"
        mgr2.engine.dispose()
        # repeated startup idempotent
        mgr3 = DatabaseManager(db_path)
        with mgr3.get_session() as s3:
            rows2 = s3.query(PlanningOutcome).filter_by(goal_id="mig1").all()
            assert rows2[0].provenance == "LEGACY"
        mgr3.engine.dispose()


def test_specialist_metrics_is_limitation_not_claim(db):
    # specialist_count derived from total_cost_json; if no specialist_calls, metric is None, not fabricated zero
    store = PlanningOutcomeStore(db)
    store.record(planning_strategy=PlanningStrategy.DIRECT_PLAN.value, task_class="goal", goal_id="spec_none", outcome="SUCCESS_VERIFIED", verified_success=True, provenance="REAL_EXECUTION", total_cost={"total": 0.5}, evidence_refs=["goal:spec_none"])
    m = store.quality_metrics(task_class="goal")
    # specialist_count should be None when no specialist data, not 0
    assert m.specialist_count is None
    # with specialist data, it appears
    store.record(planning_strategy=PlanningStrategy.DIRECT_PLAN.value, task_class="goal", goal_id="spec_yes", outcome="SUCCESS_VERIFIED", verified_success=True, provenance="REAL_EXECUTION", total_cost={"total": 0.5, "specialist_calls": 2}, evidence_refs=["goal:spec_yes"])
    m2 = store.quality_metrics(task_class="goal")
    assert m2.specialist_count is not None
