"""Isolated provider text-preview endpoint; no real network/model/task calls.

The preview is NOT normal Sythion conversation onboarding and does not
store conversation turns, tasks, or memories. Synthetic credentials only.
"""
import pytest

from backend.core import profile, secrets
from backend.models.database import ConversationTurn, Task, MemoryEntry


@pytest.fixture
def text_preview(tmp_path, monkeypatch):
    monkeypatch.setattr(profile, "data_dir", lambda: str(tmp_path))
    secrets.set_secret_store_for_testing(secrets.MemoryStore())
    yield
    secrets.set_secret_store_for_testing(None)


def payload(**patch):
    return {
        "provider_id": "openrouter",
        "model_id": "unit/test:free",
        "text": "Hello, how are you?",
        "confirm_network_request": True,
        **patch,
    }


def staged():
    profile.stage_cloud_selection("openrouter", "unit/test:free")
    secrets.set_secret(secrets.OPENROUTER_API_KEY, "inert-unit-key")


def protect_adapter(monkeypatch):
    import backend.adapters.openai_compatible_adapter as compatible
    monkeypatch.setattr(compatible, "OpenAICompatibleCloudAdapter",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("Network call forbidden")))


def test_requires_staged_model_credential_and_network_consent(
    test_client, text_preview, monkeypatch,
):
    protect_adapter(monkeypatch)
    assert test_client.post("/api/v1/inference-preview/chat", json=payload()).status_code == 409
    profile.stage_cloud_selection("openrouter", "unit/test:free")
    assert test_client.post("/api/v1/inference-preview/chat", json=payload()).status_code == 409
    secrets.set_secret(secrets.OPENROUTER_API_KEY, "inert-unit-key")
    for case in (
        payload(confirm_network_request=False),
        payload(model_id="other/model:free"),
        payload(text="   "),
        payload(extra_field="unknown"),
    ):
        response = test_client.post("/api/v1/inference-preview/chat", json=case)
        assert response.status_code in (409, 422)


def test_rejected_authentication_does_not_call_provider(test_client, text_preview, monkeypatch):
    staged()
    protect_adapter(monkeypatch)
    token = test_client.headers.pop("X-Control-Token", None)
    try:
        response = test_client.post("/api/v1/inference-preview/chat", json=payload())
        assert response.status_code == 401
    finally:
        if token:
            test_client.headers["X-Control-Token"] = token


def test_text_preview_requires_no_tool_proof_and_never_executes_or_persists(
    test_client, test_db, text_preview, monkeypatch,
):
    from backend.core.cloud_probe_evidence import is_probe_evidence_current
    from backend.core.cloud_text_evidence import is_text_evidence_current
    import backend.adapters.openai_compatible_adapter as compatible
    import backend.runtime.model_provider as default_provider
    monkeypatch.setattr(
        default_provider, "get_default_provider",
        lambda: (_ for _ in ()).throw(AssertionError("Unexpected Ollama call")),
    )
    calls = []
    class PlainText:
        def __init__(self, provider, model, *, api_key):
            assert (provider, model, api_key) == ("openrouter", "unit/test:free", "inert-unit-key")
            calls.append(("init",))
        async def chat(self, *, messages, max_tokens, temperature):
            assert len(messages) == 2
            assert all("tools" not in msg for msg in messages)
            assert "no tools" in messages[0]["content"]
            assert messages[1] == {"role": "user", "content": "Hello, how are you?"}
            assert max_tokens == 400 and temperature == 0.2
            calls.append(("chat",))
            return {"choices": [{"finish_reason": "stop", "message": {
                "role": "assistant", "content": "Hello from the preview!",
            }}]}
        async def close(self):
            calls.append(("close",))
    monkeypatch.setattr(compatible, "OpenAICompatibleCloudAdapter", PlainText)
    staged()
    assert is_probe_evidence_current(test_db) is False
    assert is_text_evidence_current(test_db) is False
    response = test_client.post("/api/v1/inference-preview/chat", json=payload())
    assert response.status_code == 200, response.text
    assert response.json() == {
        "provider_id": "openrouter", "model_id": "unit/test:free",
        "assistant_text": "Hello from the preview!", "active": False,
        "ready_for_execution": False, "persisted": False, "task_executed": False,
    }
    assert calls == [("init",), ("chat",), ("close",)]
    assert test_db.query(Task).count() == 0
    assert test_db.query(ConversationTurn).count() == 0
    assert test_db.query(MemoryEntry).count() == 0
    assert is_probe_evidence_current(test_db) is False
    assert is_text_evidence_current(test_db) is True
    response = test_client.get("/api/v1/inference-preview/providers")
    assert response.json()["text_probe_verified"] is True
    assert response.json()["tool_probe_verified"] is False
    assert response.json()["ready_for_execution"] is False
    assert profile.get_staged_cloud_selection()["provider_id"] == "openrouter"


@pytest.mark.parametrize("problem,status", [
    ("Provider HTTP 429", 429),
    ("Provider HTTP 401", 502),
    ("Provider HTTP 400", 502),
    ("Provider transport outcome unknown", 503),
])
def test_cloud_errors_sanitized_no_retry(
    test_client, text_preview, monkeypatch, problem, status,
):
    import backend.adapters.openai_compatible_adapter as compatible
    calls = []
    class Rejected:
        def __init__(self, *args, **kwargs):
            calls.append("init")
        async def chat(self, **kwargs):
            calls.append("chat")
            raise compatible.CompatibleProviderError(problem)
        async def close(self):
            calls.append("close")
    monkeypatch.setattr(compatible, "OpenAICompatibleCloudAdapter", Rejected)
    staged()
    response = test_client.post("/api/v1/inference-preview/chat", json=payload())
    assert response.status_code == status
    assert response.json()["detail"] not in (problem, "inert-unit-key")
    assert "inert-unit-key" not in response.text
    assert calls == ["init", "chat", "close"]


@pytest.mark.parametrize("reply", [
    {"choices": [{"finish_reason": "length", "message": {"role": "assistant", "content": "unfinished"}}]},
    {"choices": [{"finish_reason": "tool_calls", "message": {
        "role": "assistant", "tool_calls": [{"function": {"name": "do_something"}}]}}]},
    {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": ""}}]},
])
def test_malformed_or_action_bearing_text_cannot_be_delivered(
    test_client, text_preview, monkeypatch, reply,
):
    import backend.adapters.openai_compatible_adapter as compatible
    class BadResponse:
        def __init__(self, *args, **kwargs):
            pass
        async def chat(self, **kwargs):
            return reply
        async def close(self):
            pass
    monkeypatch.setattr(compatible, "OpenAICompatibleCloudAdapter", BadResponse)
    staged()
    response = test_client.post("/api/v1/inference-preview/chat", json=payload())
    assert response.status_code == 422
    assert "unfinished" not in response.text
    assert "do_something" not in response.text


def test_model_restaged_midcall_refuses_to_return_stale_response(
    test_client, text_preview, monkeypatch,
):
    import backend.adapters.openai_compatible_adapter as compatible
    class Changed:
        def __init__(self, *args, **kwargs):
            pass
        async def chat(self, **kwargs):
            profile.stage_cloud_selection("openrouter", "unit/test:free")
            return {"choices": [{"finish_reason": "stop", "message": {
                "role": "assistant", "content": "stale",
            }}]}
        async def close(self):
            pass
    monkeypatch.setattr(compatible, "OpenAICompatibleCloudAdapter", Changed)
    staged()
    response = test_client.post("/api/v1/inference-preview/chat", json=payload())
    assert response.status_code == 409
    assert "stale" not in response.text


def test_credential_rotated_midcall_refuses_stale_response(
    test_client, text_preview, monkeypatch,
):
    import backend.adapters.openai_compatible_adapter as compatible
    class Changed:
        def __init__(self, *args, **kwargs):
            pass
        async def chat(self, **kwargs):
            secrets.set_secret(secrets.OPENROUTER_API_KEY, "new-key")
            return {"choices": [{"finish_reason": "stop", "message": {
                "role": "assistant", "content": "stale",
            }}]}
        async def close(self):
            pass
    monkeypatch.setattr(compatible, "OpenAICompatibleCloudAdapter", Changed)
    staged()
    response = test_client.post("/api/v1/inference-preview/chat", json=payload())
    assert response.status_code == 409
    assert "stale" not in response.text
