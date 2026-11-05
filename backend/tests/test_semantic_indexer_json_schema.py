"""Offline tests for optional JSON Schema output constraints in semantic indexing."""

import asyncio
import json

import pytest

from backend.services.grounded_discussion_semantic_index import (
    SEMANTIC_INDEX_JSON_SCHEMA,
    SemanticDiscussionIndexer,
    SemanticIndexingValidationError,
    SemanticIndexSnapshot,
    validate_semantic_index_output,
)


def _snapshot():
    return SemanticIndexSnapshot(
        session_id="session-1",
        exchange_id="exchange-1",
        user_turn_id=11,
        assistant_turn_id=12,
        candidate_discussion_ids=("disc-existing",),
        candidate_labels={"disc-existing": "Existing Lantern"},
        max_proposals=3,
    )


def test_defaults_to_json_mode():
    recorded = {}

    class FakeClient:
        async def chat(self, messages, temperature=None, max_tokens=None, timeout=None,
                      think=None, format=None):
            recorded["format"] = format
            return json.dumps(
                {
                    "status": "insufficient",
                    "proposals": [],
                    "clarification": "No evidence",
                    "reason": "no candidate discussions",
                }
            )

    async def run():
        indexer = SemanticDiscussionIndexer(FakeClient())
        result = await indexer.index(_snapshot())
        assert result.status == "insufficient"
        assert recorded["format"] == "json"

    asyncio.run(run())


def test_no_format_regression_when_provider_lacks_format():
    received = {}

    class FakeClient:
        async def chat(self, messages, temperature=None, max_tokens=None, timeout=None, think=None):
            received["keys"] = set(locals().keys()) - {"self"}
            return json.dumps(
                {
                    "status": "insufficient",
                    "proposals": [],
                    "clarification": "No evidence",
                    "reason": "no candidate discussions",
                }
            )

    async def run():
        indexer = SemanticDiscussionIndexer(FakeClient())
        result = await indexer.index(_snapshot())
        assert result.status == "insufficient"
        assert "format" not in received["keys"]

    asyncio.run(run())


def test_invalid_proposal_with_existing_discussion_and_label_rejected():
    snap = _snapshot()
    payload = {
        "status": "proposals",
        "proposals": [
            {"discussion_id": "disc-existing", "label": "Mixed Mode", "session_id": "session-1"},
        ],
        "clarification": None,
        "reason": "mixed",
    }
    with pytest.raises(SemanticIndexingValidationError):
        validate_semantic_index_output(payload, snap)


def test_indexer_rejects_invalid_schema_output_without_retry():
    snap = _snapshot()
    response = {
        "status": "proposals",
        "proposals": [
            {"discussion_id": "disc-existing", "label": "Mixed Mode"},
        ],
        "clarification": None,
        "reason": "mixed",
    }

    class FakeClient:
        def __init__(self):
            self.calls = 0

        async def chat(self, **kwargs):
            self.calls += 1
            return json.dumps(response)

    client = FakeClient()

    async def run():
        indexer = SemanticDiscussionIndexer(client, max_provider_calls=1)
        with pytest.raises(SemanticIndexingValidationError):
            await indexer.index(snap)
        assert client.calls == 1

    asyncio.run(run())
