"""S179 acceptance: scoped, attributed, fresh, cancellable model context."""

import pytest

from backend.runtime.context_working_set import (
    ContextItem,
    ContextScope,
    ContextSource,
    EpistemicStatus,
    assemble_working_set,
)
from backend.services.reasoning_dispatch import get_reasoning_metrics


def _item(scope, ref="m1", status=EpistemicStatus.REMEMBERED, relevant=True, tokens=10):
    return ContextItem(
        source=ContextSource.MEMORY, ref=ref, content="remembered value",
        owner_scope=scope, provenance="memory_service:read", status=status,
        relevant=relevant, token_estimate=tokens,
    )


@pytest.fixture(autouse=True)
def clean_metrics():
    metrics = get_reasoning_metrics()
    metrics.reset()
    yield
    metrics.reset()


def test_scope_requires_exact_identity_and_rejects_foreign():
    task_scope = ContextScope(task_id="task-1", goal_id="goal-1")
    assert task_scope.contains(ContextScope(task_id="task-1"))
    assert not task_scope.contains(ContextScope(task_id="task-2"))
    assert not task_scope.contains(ContextScope(goal_id="goal-2"))
    with pytest.raises(ValueError):
        ContextScope()
    with pytest.raises(ValueError):
        ContextScope(task_id=" task-1 ")


def test_assembly_drops_foreign_items():
    scope = ContextScope(task_id="task-1")
    own = _item(scope)
    foreign = _item(ContextScope(task_id="task-2"), ref="m2")
    working = assemble_working_set(scope, [own, foreign], now=100.0)
    assert [item.ref for item in working.items] == ["m1"]


def test_messages_carry_provenance_and_uncertainty_labels():
    scope = ContextScope(task_id="task-1", session_id="session-1")
    known = ContextItem(
        source=ContextSource.TASK_STATE, ref="m1", content="task is running",
        owner_scope=scope, provenance="task_store:read",
        status=EpistemicStatus.KNOWN, relevant=True, token_estimate=10)
    assumed = _item(scope, "m2", status=EpistemicStatus.ASSUMED)
    working = assemble_working_set(scope, [known, assumed], now=100.0)
    messages = working.for_messages(now=100.0)
    assert len(messages) == 3
    assert "provenance=task_store:read" in messages[0]["content"]
    assert "status=KNOWN" in messages[0]["content"]
    assert "provenance=memory_service:read" in messages[1]["content"]
    assert "status=ASSUMED" in messages[1]["content"]
    assert messages[-1]["role"] == "user"


def test_cancelled_and_stale_sets_produce_no_model_messages():
    scope = ContextScope(task_id="task-1")
    working = assemble_working_set(scope, [_item(scope)], now=100.0,
                                   max_age_seconds=10.0)
    assert working.for_messages(now=105.0)
    assert working.cancel().for_messages(now=105.0) == []
    assert working.for_messages(now=200.0) == []


def test_relevance_and_token_measurement_reject_activity_only():
    scope = ContextScope(task_id="task-1")
    working = assemble_working_set(
        scope, [_item(scope, "m1", relevant=True, tokens=12),
                _item(scope, "m2", relevant=False, tokens=8)],
        now=100.0)
    assert working.relevance() == 0.5
    assert working.token_estimate() == 20
    metrics = get_reasoning_metrics()
    metrics.record_context(working)
    report = metrics.report()
    assert report.context_records == 1
    assert report.context_items == 2
    assert report.estimated_context_tokens == 20
    assert report.avg_context_relevance == 0.5
    assert report.effective is False   # context measurement alone is not success


def test_item_rejects_oversized_or_unattributed_content():
    scope = ContextScope(task_id="task-1")
    with pytest.raises(ValueError):
        ContextItem(ContextSource.MEMORY, "m", "x" * 8001, scope, "p")
    with pytest.raises(ValueError):
        ContextItem(ContextSource.MEMORY, "m", "x", scope, "")


def test_empty_working_set_is_honest():
    scope = ContextScope(task_id="task-1")
    working = assemble_working_set(scope, [], now=100.0)
    assert working.relevance() == 0.0
    assert working.for_messages(now=100.0) == []
