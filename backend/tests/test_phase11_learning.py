"""Phase 11 — Learned Delegation, Strategy Selection & Outcome Learning (functional + adversarial)."""

import json
import hashlib
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models.database import Base, StrategyOutcome
from backend.learning.taxonomy import StrategyType, OutcomeClass, EvidenceStrength, UserFeedbackKind
from backend.learning.store import StrategyOutcomeStore
from backend.learning.selector import StrategySelector
from backend.learning.feedback import classify_user_feedback


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = Session()
    yield s
    s.close()
    engine.dispose()


# -- Evidence ---------------------------------------------------------------

def test_record_success_verified_requires_evidence(db):
    store = StrategyOutcomeStore(db)
    # SUCCESS_VERIFIED without evidence_refs -> ineligible
    r = store.record(task_class="command", strategy_type=StrategyType.DIRECT_EXECUTION.value, outcome=OutcomeClass.SUCCESS_VERIFIED.value, evidence_refs=[])
    assert r["eligible"] is False
    assert "evidence" in r["reason"].lower()
    # with evidence -> eligible
    r2 = store.record(task_class="command", strategy_type=StrategyType.DIRECT_EXECUTION.value, outcome=OutcomeClass.SUCCESS_VERIFIED.value, evidence_refs=["task:123"], task_id="t1")
    assert r2["eligible"] is True
    assert r2["outcome"].outcome == OutcomeClass.SUCCESS_VERIFIED.value


def test_record_failure_no_evidence_ok(db):
    store = StrategyOutcomeStore(db)
    r = store.record(task_class="command", strategy_type=StrategyType.DIRECT_EXECUTION.value, outcome=OutcomeClass.FAILED.value, task_id="t2")
    assert r["eligible"] is True


def test_incomplete_outcome_rejected(db):
    store = StrategyOutcomeStore(db)
    r = store.record(task_class="", strategy_type="DIRECT_EXECUTION", outcome="UNKNOWN_OUTCOME")
    assert r["eligible"] is False


def test_provenance_roundtrip(db):
    store = StrategyOutcomeStore(db)
    r = store.record(task_class="informational", strategy_type=StrategyType.DELEGATE_ANALYSIS.value, outcome=OutcomeClass.SUCCESS_VERIFIED.value, role="ANALYSIS", evidence_refs=["task:1","delegation:2"], cost={"total": 1.2}, latency_ms=1200, task_id="prov1")
    prov = store.provenance(r["outcome"].id)
    assert prov["task_class"] == "informational"
    assert prov["role"] == "ANALYSIS"
    assert prov["evidence_refs"] == ["task:1","delegation:2"]


def test_duplicate_fingerprint_idempotent(db):
    store = StrategyOutcomeStore(db)
    fp = "abc123dup001"
    r1 = store.record(task_class="command", strategy_type=StrategyType.DIRECT_ANSWER.value, outcome=OutcomeClass.SUCCESS_PARTIAL.value, evidence_fingerprint=fp, task_id="a", evidence_refs=["task:a"])
    r2 = store.record(task_class="command", strategy_type=StrategyType.DIRECT_ANSWER.value, outcome=OutcomeClass.SUCCESS_PARTIAL.value, evidence_fingerprint=fp, task_id="a", evidence_refs=["task:a"])
    assert r1["outcome"].id == r2["outcome"].id
    assert r2["reason"] == "duplicate_fingerprint_idempotent"


def test_user_correction_evidence(db):
    store = StrategyOutcomeStore(db)
    r = store.record(task_class="command", strategy_type=StrategyType.DELEGATE_ANALYSIS.value, outcome=OutcomeClass.USER_CORRECTED.value, user_feedback=UserFeedbackKind.STRATEGY_ERROR.value, task_id="u1", evidence_refs=["task:u1"])
    assert r["eligible"] is True
    assert r["outcome"].user_feedback == UserFeedbackKind.STRATEGY_ERROR.value


# -- Statistics -------------------------------------------------------------

def test_statistics_counts_and_rates(db):
    store = StrategyOutcomeStore(db)
    for i in range(3):
        store.record(task_class="command", strategy_type=StrategyType.DIRECT_EXECUTION.value, outcome=OutcomeClass.SUCCESS_VERIFIED.value, evidence_refs=[f"task:s{i}"], task_id=f"s{i}", latency_ms=100, cost={"total": 0.5})
    store.record(task_class="command", strategy_type=StrategyType.DIRECT_EXECUTION.value, outcome=OutcomeClass.FAILED.value, task_id="f1", evidence_refs=["task:f1"])
    stats = store.statistics(task_class="command", strategy_type=StrategyType.DIRECT_EXECUTION.value)
    assert stats.sample_count == 4
    assert stats.success_count == 3
    assert stats.failure_count == 1
    assert stats.success_rate == pytest.approx(0.75)
    assert stats.verified_success_rate == pytest.approx(0.75)


def test_decay_and_recent_counts(db):
    store = StrategyOutcomeStore(db)
    old = datetime.utcnow() - timedelta(days=100)
    store.record(task_class="general", strategy_type=StrategyType.DIRECT_ANSWER.value, outcome=OutcomeClass.SUCCESS_PARTIAL.value, task_id="old1", created_at=old, evidence_refs=["task:old"])
    store.record(task_class="general", strategy_type=StrategyType.DIRECT_ANSWER.value, outcome=OutcomeClass.SUCCESS_PARTIAL.value, task_id="new1", evidence_refs=["task:new"])
    stats_all = store.statistics(task_class="general", strategy_type=StrategyType.DIRECT_ANSWER.value, include_expired=True)
    assert stats_all.recent_count >= 1
    assert stats_all.decayed_count >= 1
    # default excludes expired
    stats = store.statistics(task_class="general", strategy_type=StrategyType.DIRECT_ANSWER.value)
    assert stats.sample_count == 1


def test_conflicting_evidence_preserved(db):
    store = StrategyOutcomeStore(db)
    for i in range(8):
        store.record(task_class="command", strategy_type=StrategyType.DELEGATE_ANALYSIS.value, outcome=OutcomeClass.SUCCESS_VERIFIED.value, task_id=f"da{i}", evidence_refs=[f"task:da{i}"])
    for i in range(9):
        store.record(task_class="command", strategy_type=StrategyType.DIRECT_EXECUTION.value, outcome=OutcomeClass.SUCCESS_VERIFIED.value, task_id=f"de{i}", evidence_refs=[f"task:de{i}"])
    # Both exist
    stats_a = store.statistics(task_class="command", strategy_type=StrategyType.DELEGATE_ANALYSIS.value)
    stats_b = store.statistics(task_class="command", strategy_type=StrategyType.DIRECT_EXECUTION.value)
    assert stats_a.sample_count == 8
    assert stats_b.sample_count == 9
    # conflict not overwritten


def test_small_sample_protection(db):
    store = StrategyOutcomeStore(db)
    store.record(task_class="general", strategy_type=StrategyType.DELEGATE_RESEARCH.value, outcome=OutcomeClass.SUCCESS_VERIFIED.value, task_id="one", evidence_refs=["task:one"])
    stats = store.statistics(task_class="general", strategy_type=StrategyType.DELEGATE_RESEARCH.value)
    assert stats.evidence_strength in (EvidenceStrength.LOW_SAMPLE.value, EvidenceStrength.UNRATED.value)
    # 5 samples -> SUPPORTED
    for i in range(4):
        store.record(task_class="general", strategy_type=StrategyType.DELEGATE_RESEARCH.value, outcome=OutcomeClass.SUCCESS_VERIFIED.value, task_id=f"m{i}", evidence_refs=[f"task:m{i}"])
    stats2 = store.statistics(task_class="general", strategy_type=StrategyType.DELEGATE_RESEARCH.value)
    assert stats2.evidence_strength == EvidenceStrength.SUPPORTED.value


def test_cost_aware_scoring_prefers_efficient(db):
    store = StrategyOutcomeStore(db)
    # same success rate but different cost/latency
    for i in range(5):
        store.record(task_class="command", strategy_type=StrategyType.DIRECT_EXECUTION.value, outcome=OutcomeClass.SUCCESS_VERIFIED.value, task_id=f"cheap{i}", evidence_refs=[f"task:cheap{i}"], cost={"total": 0.1}, latency_ms=100)
    for i in range(5):
        store.record(task_class="command", strategy_type=StrategyType.MULTI_SPECIALIST.value, outcome=OutcomeClass.SUCCESS_VERIFIED.value, task_id=f"exp{i}", evidence_refs=[f"task:exp{i}"], cost={"total": 5.0}, latency_ms=5000)
    selector = StrategySelector(db)
    result = selector.select(task_context={"task_class": "command"}, candidates=[StrategyType.DIRECT_EXECUTION.value, StrategyType.MULTI_SPECIALIST.value])
    # cheap direct should win despite multi also succeeds (cost-aware)
    assert result.strategy == StrategyType.DIRECT_EXECUTION.value


# -- Selection --------------------------------------------------------------

def test_direct_vs_delegated_selection(db):
    store = StrategyOutcomeStore(db)
    for i in range(6):
        store.record(task_class="command", strategy_type=StrategyType.DIRECT_EXECUTION.value, outcome=OutcomeClass.SUCCESS_VERIFIED.value, task_id=f"d{i}", evidence_refs=[f"task:d{i}"])
    for i in range(6):
        store.record(task_class="command", strategy_type=StrategyType.DELEGATE_ANALYSIS.value, outcome=OutcomeClass.FAILED.value, task_id=f"fa{i}", evidence_refs=[f"task:fa{i}"])
    selector = StrategySelector(db)
    result = selector.select(task_context={"task_class": "command"}, candidates=[StrategyType.DIRECT_EXECUTION.value, StrategyType.DELEGATE_ANALYSIS.value])
    assert result.strategy == StrategyType.DIRECT_EXECUTION.value
    assert result.evidence_strength in ("SUPPORTED", "ESTABLISHED")


def test_role_choice_via_evidence(db):
    store = StrategyOutcomeStore(db)
    for i in range(6):
        store.record(task_class="informational", strategy_type=StrategyType.DELEGATE_ANALYSIS.value, outcome=OutcomeClass.SUCCESS_VERIFIED.value, role="ANALYSIS", task_id=f"a{i}", evidence_refs=[f"task:a{i}"])
    for i in range(6):
        store.record(task_class="informational", strategy_type=StrategyType.DELEGATE_RESEARCH.value, outcome=OutcomeClass.FAILED.value, role="RESEARCH", task_id=f"r{i}", evidence_refs=[f"task:r{i}"])
    selector = StrategySelector(db)
    result = selector.select(task_context={"task_class": "informational"}, candidates=[StrategyType.DELEGATE_ANALYSIS.value, StrategyType.DELEGATE_RESEARCH.value])
    assert result.strategy == StrategyType.DELEGATE_ANALYSIS.value


def test_insufficient_evidence_unrated(db):
    store = StrategyOutcomeStore(db)
    selector = StrategySelector(db)
    result = selector.select(task_context={"task_class": "unknown_class_xyz"}, candidates=[StrategyType.DIRECT_EXECUTION.value, StrategyType.DELEGATE_ANALYSIS.value])
    assert result.evidence_strength == EvidenceStrength.UNRATED.value
    assert result.confidence is None


def test_incompatible_strategy_vetoed(db):
    store = StrategyOutcomeStore(db)
    selector = StrategySelector(db)
    # make DELEGATE unavailable via availability map
    result = selector.select(
        task_context={"task_class": "command"},
        candidates=[StrategyType.DIRECT_EXECUTION.value, StrategyType.DELEGATE_ANALYSIS.value],
        strategy_availability={StrategyType.DELEGATE_ANALYSIS.value: False},
    )
    assert result.strategy == StrategyType.DIRECT_EXECUTION.value
    assert StrategyType.DELEGATE_ANALYSIS.value in result.evidence_summary.get("vetoed", {})


def test_capability_mismatch_veto(db):
    store = StrategyOutcomeStore(db)
    selector = StrategySelector(db)
    result = selector.select(
        task_context={"task_class": "command"},
        candidates=[
            {"strategy": StrategyType.DELEGATE_CODING.value, "requires_capabilities": ["file_operations"]},
            {"strategy": StrategyType.DIRECT_ANSWER.value, "requires_capabilities": []},
        ],
        available_capabilities=[],
    )
    assert result.strategy == StrategyType.DIRECT_ANSWER.value


def test_exploration_blocked_high_risk(db, monkeypatch):
    from backend.core.config import settings
    monkeypatch.setattr(settings, "strategy_exploration_rate", 1.0)  # try to force explore
    store = StrategyOutcomeStore(db)
    selector = StrategySelector(db)
    result = selector.select(
        task_context={"task_class": "command", "risk_level": "HIGH"},
        candidates=[StrategyType.DIRECT_EXECUTION.value, StrategyType.DELEGATE_ANALYSIS.value],
    )
    assert result.mode == "EXPLOIT"  # high risk blocks explore


def test_strategy_availability_respected_over_evidence(db):
    store = StrategyOutcomeStore(db)
    for i in range(10):
        store.record(task_class="command", strategy_type=StrategyType.DELEGATE_CODING.value, outcome=OutcomeClass.SUCCESS_VERIFIED.value, task_id=f"c{i}", evidence_refs=[f"task:c{i}"])
    selector = StrategySelector(db)
    result = selector.select(
        task_context={"task_class": "command"},
        candidates=[StrategyType.DELEGATE_CODING.value, StrategyType.DIRECT_EXECUTION.value],
        strategy_availability={StrategyType.DELEGATE_CODING.value: False},
    )
    assert result.strategy != StrategyType.DELEGATE_CODING.value


# -- User Feedback ----------------------------------------------------------

def test_feedback_classification():
    assert classify_user_feedback("You misunderstood me") == UserFeedbackKind.INTERPRETATION_ERROR.value
    assert classify_user_feedback("That's not what I wanted, bad output") == UserFeedbackKind.OUTPUT_QUALITY_ERROR.value
    assert classify_user_feedback("Wrong strategy, should have done direct") == UserFeedbackKind.STRATEGY_ERROR.value
    assert classify_user_feedback("Do it this way next time") == UserFeedbackKind.PREFERENCE_FEEDBACK.value


# -- Persistence ------------------------------------------------------------

def test_persistence_survives_reload():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = S()
    store = StrategyOutcomeStore(s)
    store.record(task_class="command", strategy_type=StrategyType.DIRECT_EXECUTION.value, outcome=OutcomeClass.SUCCESS_VERIFIED.value, task_id="persist1", evidence_refs=["task:persist1"])
    s2 = S()
    rows = s2.query(StrategyOutcome).filter_by(task_id="persist1").all()
    assert len(rows) == 1
    s.close(); s2.close(); engine.dispose()


# -- Security invariants ----------------------------------------------------

def test_learning_cannot_bypass_hard_constraint(db):
    store = StrategyOutcomeStore(db)
    for i in range(20):
        store.record(task_class="command", strategy_type=StrategyType.DELEGATE_ANALYSIS.value, outcome=OutcomeClass.SUCCESS_VERIFIED.value, task_id=f"sec{i}", evidence_refs=[f"task:sec{i}"])
    selector = StrategySelector(db)
    result = selector.select(
        task_context={"task_class": "command"},
        candidates=[StrategyType.DIRECT_EXECUTION.value, StrategyType.DELEGATE_ANALYSIS.value],
        hard_constraints=[{"strategy": StrategyType.DELEGATE_ANALYSIS.value, "violated": True, "reason": "hard policy"}],
    )
    assert result.strategy != StrategyType.DELEGATE_ANALYSIS.value


def test_low_sample_cannot_override_hard_policy(db):
    store = StrategyOutcomeStore(db)
    store.record(task_class="command", strategy_type=StrategyType.DELEGATE_CODING.value, outcome=OutcomeClass.SUCCESS_VERIFIED.value, task_id="low1", evidence_refs=["task:low1"])
    selector = StrategySelector(db)
    result = selector.select(
        task_context={"task_class": "command"},
        candidates=[StrategyType.DIRECT_EXECUTION.value, StrategyType.DELEGATE_CODING.value],
        hard_constraints=[{"strategy": StrategyType.DELEGATE_CODING.value, "violated": True, "reason": "hard"}],
    )
    assert result.strategy == StrategyType.DIRECT_EXECUTION.value


def test_malicious_strategy_record_rejected():
    # Injection attempt via strategy_type field must be rejected by taxonomy
    assert StrategyType.values().count("DROP TABLE strategy_outcomes") == 0
    assert "DROP TABLE strategy_outcomes" not in StrategyType.values()


def test_stale_evidence_not_dominating(db):
    store = StrategyOutcomeStore(db)
    ancient = datetime.utcnow() - timedelta(days=200)
    for i in range(10):
        store.record(task_class="general", strategy_type=StrategyType.DIRECT_ANSWER.value, outcome=OutcomeClass.SUCCESS_VERIFIED.value, task_id=f"anc{i}", evidence_refs=[f"task:anc{i}"], created_at=ancient)
    for i in range(3):
        store.record(task_class="general", strategy_type=StrategyType.DELEGATE_ANALYSIS.value, outcome=OutcomeClass.SUCCESS_VERIFIED.value, task_id=f"fresh{i}", evidence_refs=[f"task:fresh{i}"])
    # Default stats exclude expired (200 days > 90 TTL) so ancient should not dominate
    stats_direct = store.statistics(task_class="general", strategy_type=StrategyType.DIRECT_ANSWER.value)
    assert stats_direct.sample_count == 0


def test_provider_specific_model_id_not_selected_by_selector(db):
    # StrategySelector never returns model ids
    store = StrategyOutcomeStore(db)
    store.record(task_class="command", strategy_type=StrategyType.DIRECT_EXECUTION.value, outcome=OutcomeClass.SUCCESS_VERIFIED.value, task_id="m1", evidence_refs=["task:m1"], model_id="provider-specific-model-xyz")
    selector = StrategySelector(db)
    result = selector.select(task_context={"task_class": "command"}, candidates=[StrategyType.DIRECT_EXECUTION.value])
    assert "provider" not in result.strategy.lower()
    assert result.strategy in StrategyType.values()
