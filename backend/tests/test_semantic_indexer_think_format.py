import json

from backend.services.grounded_discussion_semantic_index import (
    SemanticIndexSnapshot,
    SemanticDiscussionIndexer,
)


def _snapshot():
    return SemanticIndexSnapshot(
        session_id="session-1",
        exchange_id="exchange-1",
        user_turn_id=1,
        assistant_turn_id=2,
        source_user_text="Hello",
        source_assistant_text="Hi",
        candidate_discussion_ids=(),
        candidate_labels={},
        max_candidate_count=5,
        max_source_text=100,
        max_source_bytes=1000,
        max_payload_bytes=10000,
        max_proposals=5,
        eligible_correction_target_ids=(),
    )


def test_semantic_indexer_disables_thinking_and_requests_json(monkeypatch):
    recorded = {}

    class FakeClient:
        async def chat(self, messages, temperature=None, max_tokens=None, timeout=None, think=None, format=None):
            recorded["kwargs"] = {
                "temperature": temperature,
                "max_tokens": max_tokens,
                "timeout": timeout,
                "think": think,
                "format": format,
            }
            return json.dumps(
                {
                    "status": "insufficient",
                    "proposals": [],
                    "clarification": "No evidence",
                    "reason": "no candidate discussions",
                }
            )

    indexer = SemanticDiscussionIndexer(FakeClient())
    result = __import__("asyncio").run(indexer.index(_snapshot()))
    assert result.status == "insufficient"
    assert recorded["kwargs"]["think"] is False
    assert recorded["kwargs"]["format"] == "json"


def test_model_client_forwards_think_and_format_to_provider():
    import asyncio
    from backend.services.conversation_runtime import ModelClient

    recorded = {}

    class FakeProvider:
        async def chat(self, messages, think=None, format=None, **kwargs):
            recorded["think"] = think
            recorded["format"] = format
            return {"message": {"content": "ok"}}

    client = ModelClient(provider=FakeProvider())
    result = asyncio.run(client.chat(messages=[{"role": "user", "content": "hi"}], think=False, format="json"))
    assert result == "ok"
    assert recorded["think"] is False
    assert recorded["format"] == "json"