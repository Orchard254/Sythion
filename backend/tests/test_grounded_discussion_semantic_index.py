"""Offline tests for the isolated semantic discussion-indexing contract."""

import asyncio
from typing import Any

import pytest

from backend.services.grounded_discussion_semantic_index import (
    SemanticIndexBudget,
    SemanticIndexResult,
    SemanticIndexSnapshot,
    SemanticIndexingBudgetExhausted,
    SemanticIndexingError,
    SemanticIndexingValidationError,
    SemanticDiscussionIndexer,
    normalize_semantic_index_proposal,
    validate_semantic_index_output,
)


class FakeClient:
    def __init__(self, response: Any = None, exc: Exception | None = None, delay: float = 0.0):
        self.response = response
        self.exc = exc
        self.delay = delay
        self.calls = 0

    async def chat(self, **kwargs):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.exc is not None:
            raise self.exc
        return self.response


def snapshot(**overrides):
    base = {
        "session_id": "session-1",
        "exchange_id": "exchange-1",
        "user_turn_id": 11,
        "assistant_turn_id": 12,
        "candidate_discussion_ids": ("disc-existing",),
        "candidate_labels": {"disc-existing": "Existing Lantern"},
        "max_proposals": 3,
    }
    base.update(overrides)
    return SemanticIndexSnapshot(**base)


def proposals_envelope(proposals, *, status="proposals", clarification=None, reason="ok"):
    return {
        "status": status,
        "proposals": proposals,
        "clarification": clarification,
        "reason": reason,
    }


def test_snapshot_rejects_non_exact_identity():
    with pytest.raises(SemanticIndexingValidationError):
        snapshot(session_id=" session-1 ")
    with pytest.raises(SemanticIndexingValidationError):
        snapshot(user_turn_id=0)
    with pytest.raises(SemanticIndexingValidationError):
        snapshot(source_user_text="x" * 1001)


def test_normalize_new_and_existing_proposals_bind_to_snapshot():
    snap = snapshot()
    new_prop = normalize_semantic_index_proposal(
        {
            "discussion_id": None,
            "label": "New Museum Budget",
            "correction_text": "Budget corrected",
            "session_id": "session-1",
        },
        snap,
    )
    assert new_prop["label"] == "New Museum Budget"
    assert new_prop["provenance"] == "model_inferred_unverified"
    assert new_prop["label_provenance"] == "model_inferred_unverified"
    assert new_prop["target_exchange_id"] == "exchange-1"
    assert new_prop["target_user_turn_id"] == 11

    eligible_snap = snapshot(eligible_correction_target_ids=("disc-existing",))
    existing_prop = normalize_semantic_index_proposal(
        {
            "discussion_id": "disc-existing",
            "conflict_text": "Conflict",
            "session_id": "session-1",
        },
        eligible_snap,
    )
    assert existing_prop["discussion_id"] == "disc-existing"
    assert existing_prop["target_assistant_turn_id"] == 12


def test_normalize_rejects_unknown_fields_invented_ids_and_mixed_modes():
    snap = snapshot()
    with pytest.raises(SemanticIndexingValidationError):
        normalize_semantic_index_proposal({"foo": "bar"}, snap)
    with pytest.raises(SemanticIndexingValidationError):
        normalize_semantic_index_proposal({"discussion_id": "invented", "label": None}, snap)
    with pytest.raises(SemanticIndexingValidationError):
        normalize_semantic_index_proposal({"discussion_id": "disc-existing", "label": "Nope"}, snap)
    with pytest.raises(SemanticIndexingValidationError):
        normalize_semantic_index_proposal({"discussion_id": None, "label": None}, snap)


def test_normalize_rejects_anchor_mismatch():
    snap = snapshot()
    with pytest.raises(SemanticIndexingValidationError):
        normalize_semantic_index_proposal(
            {"discussion_id": "disc-existing", "session_id": "other"}, snap
        )
    with pytest.raises(SemanticIndexingValidationError):
        normalize_semantic_index_proposal(
            {"discussion_id": "disc-existing", "target_exchange_id": "other"}, snap
        )
    with pytest.raises(SemanticIndexingValidationError):
        normalize_semantic_index_proposal(
            {"discussion_id": "disc-existing", "target_user_turn_id": 99}, snap
        )


def test_normalize_rejects_duplicate_proposals():
    snap = snapshot()
    env = proposals_envelope(
        [
            {"discussion_id": None, "label": "Same Topic"},
            {"discussion_id": None, "label": "Same Topic"},
        ],
        reason="duplicate",
    )
    with pytest.raises(SemanticIndexingValidationError):
        validate_semantic_index_output(env, snap)


def test_normalize_requires_pre_existing_correction_target_proof():
    snap = snapshot()
    with pytest.raises(SemanticIndexingValidationError):
        normalize_semantic_index_proposal(
            {
                "discussion_id": "disc-existing",
                "correction_text": "Correction without proof",
                "session_id": "session-1",
            },
            snap,
        )
    allowed = snapshot(eligible_correction_target_ids=("disc-existing",))
    accepted = normalize_semantic_index_proposal(
        {
            "discussion_id": "disc-existing",
            "correction_text": "Correction with proof",
            "session_id": "session-1",
        },
        allowed,
    )
    assert accepted["correction_text"] == "Correction with proof"


def test_validate_semantic_index_output_shapes():
    snap = snapshot()
    valid = validate_semantic_index_output(
        proposals_envelope(
            [
                {
                    "discussion_id": None,
                    "label": "Topic Two",
                    "provenance": "model_inferred_unverified",
                }
            ],
            reason="two topics",
        ),
        snap,
    )
    assert isinstance(valid, SemanticIndexResult)
    assert valid.status == "proposals"
    assert len(valid.proposals) == 1
    assert valid.untrusted is True

    ambiguous = validate_semantic_index_output(
        proposals_envelope([], status="ambiguous", clarification="Which museum?", reason="ambiguous"),
        snap,
    )
    assert ambiguous.status == "ambiguous"
    assert ambiguous.clarification == "Which museum?"

    insufficient = validate_semantic_index_output(
        proposals_envelope([], status="insufficient", clarification="No evidence", reason="insufficient"),
        snap,
    )
    assert insufficient.status == "insufficient"


def test_validate_rejects_closed_shape_violations():
    snap = snapshot()
    with pytest.raises(SemanticIndexingValidationError):
        validate_semantic_index_output({"status": "proposals", "proposals": [] , "clarification": None, "reason": "x", "extra": 1}, snap)
    with pytest.raises(SemanticIndexingValidationError):
        validate_semantic_index_output(proposals_envelope([]), snap)
    with pytest.raises(SemanticIndexingValidationError):
        validate_semantic_index_output(
            proposals_envelope(
                [{"discussion_id": None, "label": "x"}],
                status="insufficient",
                clarification="no",
                reason="x",
            ),
            snap,
        )
    with pytest.raises(SemanticIndexingValidationError):
        validate_semantic_index_output(
            proposals_envelope(
                [],
                status="proposals",
                clarification=None,
                reason="x",
            ),
            snap,
        )


def test_indexer_uses_injected_provider_once_without_retry():
    snap = snapshot()
    response = proposals_envelope(
        [{"discussion_id": None, "label": "Semantic Topic", "provenance": "model_inferred_unverified"}],
        reason="indexed",
    )
    client = FakeClient(response=__import__("json").dumps(response))

    async def run():
        indexer = SemanticDiscussionIndexer(client, max_provider_calls=1)
        result = await indexer.index(snap)
        assert result.status == "proposals"
        assert len(result.proposals) == 1
        assert client.calls == 1
        assert indexer.call_count == 1
        with pytest.raises(SemanticIndexingBudgetExhausted):
            await indexer.index(snap)
        assert client.calls == 1

    asyncio.run(run())


def test_indexer_does_not_retry_uncertain_provider_outcome():
    snap = snapshot()
    client = FakeClient(exc=RuntimeError("provider unavailable"))

    async def run():
        indexer = SemanticDiscussionIndexer(client, max_provider_calls=3)
        with pytest.raises(SemanticIndexingError):
            await indexer.index(snap)
        assert client.calls == 1
        assert indexer.call_count == 1

    asyncio.run(run())


def test_indexer_propagates_cancellation():
    snap = snapshot()

    class CancellingClient:
        def __init__(self):
            self.calls = 0

        async def chat(self, **kwargs):
            self.calls += 1
            raise asyncio.CancelledError()

    client = CancellingClient()

    async def run():
        indexer = SemanticDiscussionIndexer(client, max_provider_calls=1)
        with pytest.raises(asyncio.CancelledError):
            await indexer.index(snap)
        assert client.calls == 1

    asyncio.run(run())


def test_indexer_rejects_invalid_model_output_without_call_increment():
    # Name retained for compatibility, but corrected doctrine charges the call.
    snap = snapshot()
    client = FakeClient(response="{not-json")

    async def run():
        indexer = SemanticDiscussionIndexer(client, max_provider_calls=1)
        with pytest.raises(SemanticIndexingValidationError):
            await indexer.index(snap)
        assert client.calls == 1
        assert indexer.call_count == 1

    asyncio.run(run())


def test_shared_budget_is_atomic_across_indexers():
    snap = snapshot()
    response = proposals_envelope(
        [{"discussion_id": None, "label": "Shared Budget Topic"}],
        reason="indexed",
    )
    client = FakeClient(response=__import__("json").dumps(response), delay=0.05)
    shared_budget = SemanticIndexBudget(max_provider_calls=1)

    async def run():
        first = SemanticDiscussionIndexer(client, budget=shared_budget)
        second = SemanticDiscussionIndexer(client, budget=shared_budget)
        results = await asyncio.gather(
            first.index(snap), second.index(snap), return_exceptions=True
        )
        successes = [r for r in results if not isinstance(r, Exception)]
        failures = [r for r in results if isinstance(r, SemanticIndexingBudgetExhausted)]
        assert len(successes) == 1
        assert len(failures) == 1
        assert client.calls == 1
        assert shared_budget.consumed_calls == 1

    asyncio.run(run())


def test_invalid_snapshot_does_not_charge_budget():
    client = FakeClient(response="{}")
    budget = SemanticIndexBudget(max_provider_calls=1)

    async def run():
        indexer = SemanticDiscussionIndexer(client, budget=budget)
        with pytest.raises(SemanticIndexingValidationError):
            await indexer.index({"session_id": " session-1 ", "exchange_id": "exchange-1", "user_turn_id": 11, "assistant_turn_id": 12})
        assert client.calls == 0
        assert budget.consumed_calls == 0

    asyncio.run(run())


def test_payload_byte_bound_rejects_before_provider_call():
    snap = snapshot(max_payload_bytes=20)
    client = FakeClient(response=__import__("json").dumps(proposals_envelope([], status="insufficient", clarification="x", reason="x")))

    async def run():
        indexer = SemanticDiscussionIndexer(client, max_provider_calls=1)
        with pytest.raises(SemanticIndexingValidationError):
            await indexer.index(snap)
        assert client.calls == 0
        assert indexer.call_count == 0

    asyncio.run(run())


def test_indexer_builds_bounded_payload_without_full_transcript():
    snap = snapshot(source_user_text="short user text", source_assistant_text="short assistant text")
    client = FakeClient(response=__import__("json").dumps(proposals_envelope([], status="insufficient", clarification="need more", reason="sparse")))

    async def run():
        indexer = SemanticDiscussionIndexer(client, max_provider_calls=1)
        await indexer.index(snap)
        assert client.calls == 1

    asyncio.run(run())


def test_build_messages_prompt_distinguishes_json_null_from_empty_string():
    snap = snapshot()
    indexer = SemanticDiscussionIndexer(FakeClient())
    messages = indexer._build_messages(snap)
    system = messages[0]["content"]
    assert "JSON null" in system
    assert 'NEVER an empty string' in system
    assert 'never as ""' in system or 'never as \\"\\"' in system


def test_validate_rejects_empty_clarification_not_json_null():
    snap = snapshot()
    env = proposals_envelope(
        [{"discussion_id": None, "label": "Topic"}],
        clarification="",
        reason="x",
    )
    with pytest.raises(SemanticIndexingValidationError):
        validate_semantic_index_output(env, snap)


def test_indexer_rejects_model_output_with_empty_clarification():
    snap = snapshot()
    response = proposals_envelope(
        [{"discussion_id": None, "label": "Topic"}],
        clarification="",
        reason="x",
    )
    client = FakeClient(response=__import__("json").dumps(response))

    async def run():
        indexer = SemanticDiscussionIndexer(client, max_provider_calls=1)
        with pytest.raises(SemanticIndexingValidationError):
            await indexer.index(snap)
        assert client.calls == 1

    asyncio.run(run())


def test_build_messages_prompt_requires_reason():
    snap = snapshot()
    indexer = SemanticDiscussionIndexer(FakeClient())
    messages = indexer._build_messages(snap)
    system = messages[0]["content"]
    assert "reason is MANDATORY" in system
    assert "never omit reason" in system.lower() or "Never omit reason" in system
    assert "All four top-level keys are required" in system


def test_validate_rejects_missing_reason_key():
    snap = snapshot()
    payload = {
        "status": "proposals",
        "proposals": [{"discussion_id": None, "label": "Topic"}],
        "clarification": None,
    }
    with pytest.raises(SemanticIndexingValidationError):
        validate_semantic_index_output(payload, snap)


def test_indexer_rejects_model_output_missing_reason():
    snap = snapshot()
    payload = {
        "status": "proposals",
        "proposals": [{"discussion_id": None, "label": "Topic"}],
        "clarification": None,
    }
    client = FakeClient(response=__import__("json").dumps(payload))

    async def run():
        indexer = SemanticDiscussionIndexer(client, max_provider_calls=1)
        with pytest.raises(SemanticIndexingValidationError):
            await indexer.index(snap)
        assert client.calls == 1

    asyncio.run(run())
