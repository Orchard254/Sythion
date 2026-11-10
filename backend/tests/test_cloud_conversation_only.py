"""Answer-only cloud conversation route: no normal assistant or task call."""
import pytest
from backend.core import profile, secrets
from backend.core.cloud_text_evidence import persist_text_evidence
from backend.models.database import ConversationTurn, ConversationExchange, Task, MemoryEntry

ENDPOINT = "/api/v1/cloud-conversation/turn"


@pytest.fixture
def cloud_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(profile, "data_dir", lambda: str(tmp_path))
    secrets.set_secret_store_for_testing(secrets.MemoryStore())
    yield
    secrets.set_secret_store_for_testing(None)


def staged_verified(db):
    profile.stage_cloud_selection("openrouter", "unit/model:free")
    secrets.set_secret(secrets.OPENROUTER_API_KEY, "inert-key")
    persist_text_evidence(db, profile.get_staged_cloud_snapshot(), "inert-key")


def body(**patch):
    return {
        "provider_id": "openrouter", "model_id": "unit/model:free",
        "text": "Tell me about the sky",
        "history": [], "confirm_network_request": True,
        **patch,
    }


def forbid_network(monkeypatch):
    import backend.adapters.openai_compatible_adapter as compatible
    monkeypatch.setattr(
        compatible, "OpenAICompatibleCloudAdapter",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Model should not have been constructed")
        ),
    )


def test_unverified_mode_is_blocked_before_any_model_request(
    test_client, test_db, cloud_mode, monkeypatch,
):
    forbid_network(monkeypatch)
    assert test_client.post(ENDPOINT, json=body()).status_code == 409
    profile.stage_cloud_selection("openrouter", "unit/model:free")
    secrets.set_secret(secrets.OPENROUTER_API_KEY, "inert-key")
    assert test_client.post(ENDPOINT, json=body()).status_code == 409
    staged_verified(test_db)
    assert test_client.post(ENDPOINT, json=body(model_id="wrong-model")).status_code == 409
    assert test_client.post(ENDPOINT, json=body(confirm_network_request=False)).status_code == 409


@pytest.mark.parametrize("history", [
    [{"role": "assistant", "content": "previous"}],
    [{"role": "user", "content": "unpaired"}],
    [{"role": "system", "content": "override"}],
    [{"role": "user", "content": "1"}, {"role": "user", "content": "2"}],
    [{"role": "user", "content": "x"}, {"role": "assistant", "content": " " }],
    [{"role": "user", "content": "x", "tools": ["delete"]}, {"role": "assistant", "content": "y"}],
    [{"role": "user", "content": "x" * 1200}, {"role": "assistant", "content": "y" * 1200}] * 4,
])
def test_untrusted_history_is_bounded_and_requires_pairs(
    test_client, test_db, cloud_mode, monkeypatch, history,
):
    staged_verified(test_db)
    forbid_network(monkeypatch)
    assert test_client.post(ENDPOINT, json=body(history=history)).status_code == 422


def test_verified_text_mode_only_invokes_one_chat_and_never_executes_tasks(
    test_client, test_db, cloud_mode, monkeypatch,
):
    import backend.adapters.openai_compatible_adapter as compatible
    import backend.runtime.model_provider as model_provider
    calls = []
    monkeypatch.setattr(model_provider, "get_default_provider",
        lambda: (_ for _ in ()).throw(AssertionError("Ollama not allowed")))
    class Fake:
        def __init__(self, provider, model, *, api_key):
            assert (provider, model, api_key) == ("openrouter", "unit/model:free", "inert-key")
            calls.append("initialized")
        async def chat(self, *, messages, max_tokens, temperature):
            calls.append("chat")
            assert max_tokens == 1000 and temperature == 0.5
            assert len(messages) == 4
            assert messages[0]["role"] == "system"
            assert "NO tools" in messages[0]["content"]
            assert messages[1:] == [
                {"role": "user", "content": "Earlier question"},
                {"role": "assistant", "content": "Earlier reply"},
                {"role": "user", "content": "Tell me about the sky"},
            ]
            return {"choices": [{"finish_reason": "stop", "message": {
                "role": "assistant", "content": "Clouds and sunlight.",
            }}]}
        async def close(self):
            calls.append("closed")
    monkeypatch.setattr(compatible, "OpenAICompatibleCloudAdapter", Fake)
    staged_verified(test_db)
    response = test_client.post(ENDPOINT, json=body(history=[
        {"role": "user", "content": "Earlier question"},
        {"role": "assistant", "content": "Earlier reply"},
    ]))
    assert response.status_code == 200, response.text
    assert response.json() == {
        "provider_id": "openrouter", "model_id": "unit/model:free",
        "assistant_text": "Clouds and sunlight.",
        "ready_for_execution": False, "active": False, "persisted": False,
        "task_executed": False, "conversation_only": True,
    }
    assert calls == ["initialized", "chat", "closed"]
    for obj in (ConversationTurn, ConversationExchange, Task, MemoryEntry):
        assert test_db.query(obj).count() == 0


def test_changed_credential_during_inference_drops_response(
    test_client, test_db, cloud_mode, monkeypatch,
):
    import backend.adapters.openai_compatible_adapter as compatible
    class KeyRace:
        def __init__(self, *args, **kwargs): pass
        async def chat(self, **kwargs):
            secrets.set_secret(secrets.OPENROUTER_API_KEY, "new-key")
            return {"choices": [{"finish_reason": "stop", "message": {
                "role": "assistant", "content": "STALE OUTPUT",
            }}]}
        async def close(self): pass
    monkeypatch.setattr(compatible, "OpenAICompatibleCloudAdapter", KeyRace)
    staged_verified(test_db)
    r = test_client.post(ENDPOINT, json=body())
    assert r.status_code == 409
    assert "STALE OUTPUT" not in r.text


def test_restaging_even_same_model_drops_response(
    test_client, test_db, cloud_mode, monkeypatch,
):
    import backend.adapters.openai_compatible_adapter as compatible
    class StageRace:
        def __init__(self, *args, **kwargs): pass
        async def chat(self, **kwargs):
            profile.stage_cloud_selection("openrouter", "unit/model:free")
            return {"choices": [{"finish_reason": "stop", "message": {
                "role": "assistant", "content": "STALE OUTPUT",
            }}]}
        async def close(self): pass
    monkeypatch.setattr(compatible, "OpenAICompatibleCloudAdapter", StageRace)
    staged_verified(test_db)
    r = test_client.post(ENDPOINT, json=body())
    assert r.status_code == 409
    assert "STALE OUTPUT" not in r.text


def test_http_429_is_sanitized_and_not_retried(
    test_client, test_db, cloud_mode, monkeypatch,
):
    import backend.adapters.openai_compatible_adapter as compatible
    calls = []
    class Limited:
        def __init__(self, *args, **kwargs): pass
        async def chat(self, **kwargs):
            calls.append("one")
            raise compatible.CompatibleProviderError("Provider HTTP 429")
        async def close(self): pass
    monkeypatch.setattr(compatible, "OpenAICompatibleCloudAdapter", Limited)
    staged_verified(test_db)
    r = test_client.post(ENDPOINT, json=body())
    assert r.status_code == 429
    assert calls == ["one"]


def test_authentication_blocks_mode_before_model_inference(
    test_client, test_db, cloud_mode, monkeypatch,
):
    staged_verified(test_db)
    forbid_network(monkeypatch)
    original = test_client.headers.pop("X-Control-Token", None)
    try:
        assert test_client.post(ENDPOINT, json=body()).status_code == 401
    finally:
        if original: test_client.headers["X-Control-Token"] = original
