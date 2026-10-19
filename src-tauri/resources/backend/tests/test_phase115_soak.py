"""Gated soak test — realistic synthetic workload for learning substrate."""

import random
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models.database import Base
from backend.learning.store import StrategyOutcomeStore
from backend.learning.selector import StrategySelector
from backend.learning.taxonomy import StrategyType, OutcomeClass

pytestmark = pytest.mark.gated_soak


def test_learning_soak_mixed_workload():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = S()
    store = StrategyOutcomeStore(db)
    rng = random.Random(42)

    # Mix: simple direct, informational, goal, delegation, specialist, retry, failure, correction, exploration
    task_classes = ["command", "informational", "goal", "general"]
    strategies = [
        StrategyType.DIRECT_EXECUTION.value,
        StrategyType.DIRECT_ANSWER.value,
        StrategyType.DELEGATE_ANALYSIS.value,
        StrategyType.DELEGATE_RESEARCH.value,
        StrategyType.DELEGATE_CODING.value,
        StrategyType.MULTI_SPECIALIST.value,
    ]
    outcomes = [
        OutcomeClass.SUCCESS_VERIFIED.value,
        OutcomeClass.SUCCESS_PARTIAL.value,
        OutcomeClass.FAILED.value,
        OutcomeClass.FAILED_VERIFICATION.value,
        OutcomeClass.USER_CORRECTED.value,
    ]
    # Generate 200 mixed trials
    for i in range(200):
        tc = rng.choice(task_classes)
        st = rng.choice(strategies)
        oc = rng.choices(outcomes, weights=[0.5, 0.1, 0.2, 0.05, 0.15])[0]
        ev = [f"task:soak{i}"] if oc != OutcomeClass.SUCCESS_VERIFIED.value or rng.random() > 0.1 else []
        # ensure SUCCESS_VERIFIED has evidence
        if oc == OutcomeClass.SUCCESS_VERIFIED.value and not ev:
            ev = [f"task:soak{i}"]
        store.record(
            task_class=tc,
            strategy_type=st,
            outcome=oc,
            task_id=f"soak{i}",
            evidence_refs=ev or [f"task:soak{i}"],
            cost={"total": rng.uniform(0.1, 3.0)},
            latency_ms=rng.randint(100, 4000),
        )

    # Inspect
    selector = StrategySelector(db)
    # Stability check: repeat selection
    r1 = selector.select(task_context={"task_class": "command"}, candidates=[StrategyType.DIRECT_EXECUTION.value, StrategyType.DELEGATE_ANALYSIS.value])
    r2 = selector.select(task_context={"task_class": "command"}, candidates=[StrategyType.DIRECT_EXECUTION.value, StrategyType.DELEGATE_ANALYSIS.value])
    assert r1.strategy == r2.strategy

    # Evidence strength distribution
    stats_all = store.statistics(task_class="command")
    total = sum(s.sample_count for s in stats_all.values())
    assert total > 0
    # At least some SUPPORTED/ESTABLISHED
    established = [s for s in stats_all.values() if s.evidence_strength in ("SUPPORTED", "ESTABLISHED")]
    assert len(established) >= 1 or total < 20  # soak may be sparse per strategy, but not zero

    db.close()
    engine.dispose()
