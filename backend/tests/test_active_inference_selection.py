"""Active reasoning-provider selection contract (offline; no provider requests)."""
import pytest

from backend.core import profile, secrets
from backend.core.cloud_text_evidence import persist_text_evidence
from backend.core.cloud_probe_evidence import persist_probe_evidence
from backend.runtime import active_inference as active
from backend.runtime import model_client
from backend.services.conversation_runtime import ModelClient


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(profile, "data_dir", lambda: str(tmp_path))
    secrets.set_secret_store_for_testing(secrets.MemoryStore())
    yield tmp_path
    secrets.set_secret_store_for_testing(None)


def verified(db):
    profile.stage_cloud_selection("openrouter", "unit/cloud:free")
    secrets.set_secret(secrets.OPENROUTER_API_KEY, "synthetic-not-real")
    snapshot = profile.get_staged_cloud_snapshot()
    persist_text_evidence(db, snapshot, "synthetic-not-real")
    persist_probe_evidence(db, snapshot, "synthetic-not-real")


def test_ollama_remains_default_and_staging_is_inert(isolated, monkeypatch):
    obj = object()
    monkeypatch.setattr(model_client, "get_adapter", lambda: obj)
    assert active.get_active_inference_selection().provider_id == "ollama-cloud"
    profile.stage_cloud_selection("openrouter", "unit/cloud:free")
    assert active.get_active_inference_selection().provider_id == "ollama-cloud"
    default = model_client.get_default_model_client()
    assert isinstance(default, model_client.OllamaCloudModelClient)
    assert default._adapter is obj


def test_cannot_activate_without_both_proofs(test_db, isolated):
    profile.stage_cloud_selection("openrouter", "unit/cloud:free")
    secrets.set_secret(secrets.OPENROUTER_API_KEY, "synthetic-not-real")
    with pytest.raises(active.ActiveInferenceUnavailable, match="qualification required"):
        active.activate_verified_cloud_selection(test_db, "openrouter", "unit/cloud:free")
    assert active.get_active_inference_selection().provider_id == "ollama-cloud"


def test_activates_only_matching_verified_identity(test_db, isolated):
    verified(test_db)
    with pytest.raises(active.ActiveInferenceUnavailable, match="exact model"):
        active.activate_verified_cloud_selection(test_db, "openrouter", "other/model")
    with pytest.raises(active.ActiveInferenceUnavailable, match="Invalid active"):
        active.activate_verified_cloud_selection(test_db, "not-a-provider", "unit/cloud:free")
    chosen = active.activate_verified_cloud_selection(test_db, "openrouter", "unit/cloud:free")
    assert chosen == active.ActiveInferenceSelection("openrouter", "unit/cloud:free")
    assert active.get_active_inference_selection() == chosen
    data = profile.load_profile()
    assert data["staged_cloud_selection"]["model_id"] == "unit/cloud:free"
    assert "synthetic-not-real" not in str(data)


def test_selected_provider_reaches_both_existing_model_seams(test_db, isolated):
    verified(test_db)
    active.activate_verified_cloud_selection(test_db, "openrouter", "unit/cloud:free")
    assert isinstance(ModelClient().provider, active.ActiveCompatibleChatProvider)
    decision = model_client.get_default_model_client()
    assert isinstance(decision, active.ActiveCompatibleDecisionClient)
    assert decision.provider_name() == "openrouter"


@pytest.mark.parametrize("change", ["rotate", "tamper", "corrupt"])
def test_active_binding_failure_never_falls_back_to_ollama(test_db, isolated, change):
    verified(test_db)
    active.activate_verified_cloud_selection(test_db, "openrouter", "unit/cloud:free")
    if change == "rotate":
        secrets.set_secret(secrets.OPENROUTER_API_KEY, "rotated-not-real")
    elif change == "tamper":
        profile.save_profile({"active_inference_selection": {
            "version": 1, "provider_id": "openrouter", "model_id": "unit/cloud:free",
            "nonce": "0" * 32, "mac": "0" * 64,
        }})
    else:
        (isolated / "profile.json").write_text("{ invalid json", encoding="utf-8")
    with pytest.raises(active.ActiveInferenceUnavailable):
        active.get_active_inference_selection()
    with pytest.raises(active.ActiveInferenceUnavailable):
        model_client.get_default_model_client()
    with pytest.raises(active.ActiveInferenceUnavailable):
        ModelClient()


def test_staging_new_candidate_does_not_replace_active_model(test_db, isolated):
    verified(test_db)
    active.activate_verified_cloud_selection(test_db, "openrouter", "unit/cloud:free")
    profile.stage_cloud_selection("groq", "other/model")
    assert active.get_active_inference_selection().provider_id == "openrouter"


@pytest.mark.asyncio
async def test_active_chat_uses_one_call_and_closes_socket(test_db, isolated, monkeypatch):
    verified(test_db)
    active.activate_verified_cloud_selection(test_db, "openrouter", "unit/cloud:free")
    from backend.runtime import compatible_chat_provider as compat
    actions = []
    class Fake:
        def __init__(self, provider, model):
            assert (provider, model) == ("openrouter", "unit/cloud:free")
        async def chat(self, **kwargs):
            actions.append("chat")
            return {"message": {"role": "assistant", "content": "Hello"}}
        async def close(self):
            actions.append("closed")
    monkeypatch.setattr(compat, "CompatibleCloudChatProvider", Fake)
    assert await ModelClient().chat([{"role": "user", "content": "hello"}]) == "Hello"
    assert actions == ["chat", "closed"]


@pytest.mark.asyncio
async def test_active_decision_uses_one_call_and_closes_socket(test_db, isolated, monkeypatch):
    verified(test_db)
    active.activate_verified_cloud_selection(test_db, "openrouter", "unit/cloud:free")
    actions = []
    class Fake:
        def __init__(self, provider, model):
            assert (provider, model) == ("openrouter", "unit/cloud:free")
        async def decide(self, messages, tools, max_tokens):
            actions.append("decide")
            return {"choices": []}
        async def close(self):
            actions.append("closed")
    monkeypatch.setattr(model_client, "CompatibleCloudModelClient", Fake)
    await model_client.get_default_model_client().decide([], [], 100)
    assert actions == ["decide", "closed"]


@pytest.mark.asyncio
async def test_provider_switch_during_chat_discards_stale_result(test_db, isolated, monkeypatch):
    verified(test_db)
    active.activate_verified_cloud_selection(test_db, "openrouter", "unit/cloud:free")
    from backend.runtime import compatible_chat_provider as compat
    actions = []
    class Fake:
        def __init__(self, provider, model):
            pass
        async def chat(self, **kwargs):
            profile.save_profile({"active_inference_selection": {
                "version": 1, "provider_id": "ollama-cloud",
            }})
            actions.append("chat")
            return {"message": {"content": "stale"}}
        async def close(self):
            actions.append("closed")
    monkeypatch.setattr(compat, "CompatibleCloudChatProvider", Fake)
    with pytest.raises(active.ActiveInferenceUnavailable, match="changed during"):
        await active.get_active_chat_provider().chat([{"role": "user", "content": "hey"}])
    assert actions == ["chat", "closed"]


@pytest.mark.asyncio
async def test_provider_switch_during_decision_discards_stale_result(test_db, isolated, monkeypatch):
    verified(test_db)
    active.activate_verified_cloud_selection(test_db, "openrouter", "unit/cloud:free")
    actions = []
    class Fake:
        def __init__(self, provider, model):
            pass
        async def decide(self, messages, tools, max_tokens):
            profile.save_profile({"active_inference_selection": {
                "version": 1, "provider_id": "ollama-cloud",
            }})
            actions.append("decide")
            return {"choices": []}
        async def close(self):
            actions.append("closed")
    monkeypatch.setattr(model_client, "CompatibleCloudModelClient", Fake)
    with pytest.raises(active.ActiveInferenceUnavailable, match="changed during"):
        await model_client.get_default_model_client().decide([], [], 100)
    assert actions == ["decide", "closed"]
