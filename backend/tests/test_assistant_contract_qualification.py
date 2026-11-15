"""The real Assistant contract gate uses one inert provider request, never executes tasks."""
import pytest

from backend.core import profile, secrets
from backend.core.cloud_text_evidence import persist_text_evidence
from backend.core.cloud_probe_evidence import persist_probe_evidence
from backend.core.cloud_assistant_evidence import is_assistant_contract_evidence_current
from backend.models.database import Task

URL = "/api/v1/inference-preview/assistant-contract"
ACTIVE = "/api/v1/inference/active"
REQUEST = {
    "provider_id": "openrouter", "model_id": "unit/model:free",
    "confirm_network_request": True,
}


@pytest.fixture
def candidate(tmp_path, monkeypatch, test_db):
    monkeypatch.setattr(profile, "data_dir", lambda: str(tmp_path))
    secrets.set_secret_store_for_testing(secrets.MemoryStore())
    profile.stage_cloud_selection("openrouter", "unit/model:free")
    secrets.set_secret(secrets.OPENROUTER_API_KEY, "synthetic-not-real")
    snapshot = profile.get_staged_cloud_snapshot()
    persist_text_evidence(test_db, snapshot, "synthetic-not-real")
    persist_probe_evidence(test_db, snapshot, "synthetic-not-real")
    yield
    secrets.set_secret_store_for_testing(None)


def provider_response(content):
    return {
        "choices": [{
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": content},
        }],
    }


def fake_adapter(monkeypatch, content):
    import backend.adapters.openai_compatible_adapter as module
    called = []
    class Adapter:
        def __init__(self, provider_id, model_id, *, api_key):
            assert (provider_id, model_id, api_key) == (
                "openrouter", "unit/model:free", "synthetic-not-real",
            )
        async def chat(self, **kwargs):
            called.append(("chat", kwargs))
            assert len(kwargs["messages"]) == 2
            assert kwargs["messages"][0]["role"] == "system"
            assert "assistant_text" in kwargs["messages"][0]["content"]
            return provider_response(content)
        async def close(self):
            called.append(("close", None))
    monkeypatch.setattr(module, "OpenAICompatibleCloudAdapter", Adapter)
    return called


def test_assistant_contract_proved_once_before_actual_activation(
    test_client, test_db, candidate, monkeypatch,
):
    calls = fake_adapter(monkeypatch, '{"assistant_text":"Hello","commands":[]}')
    assert not is_assistant_contract_evidence_current(test_db)
    response = test_client.post(URL, json=REQUEST)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "provider_id": "openrouter", "model_id": "unit/model:free",
        "assistant_contract_verified": True, "evidence_persisted": True,
        "active": False, "task_executed": False,
    }
    assert [entry[0] for entry in calls] == ["chat", "close"]
    assert is_assistant_contract_evidence_current(test_db)
    assert test_db.query(Task).count() == 0
    activated = test_client.post(ACTIVE, json={
        "provider_id": "openrouter", "model_id": "unit/model:free",
    })
    assert activated.status_code == 200, activated.text


@pytest.mark.parametrize("text", [
    "Hello from the model",
    '{"assistant_text":"Hello","commands":"not a list"}',
    '{"assistant_text":"Hello","commands":[],"extra":true}',
    '{"assistant_text":"Hello","commands":[{"type":"start_task","request":"open app"}]}',
])
def test_plain_text_or_unsafe_schema_does_not_admit_provider(
    test_client, test_db, candidate, monkeypatch, text,
):
    calls = fake_adapter(monkeypatch, text)
    response = test_client.post(URL, json=REQUEST)
    assert response.status_code == 422, response.text
    assert "model" in response.text.lower()
    assert [entry[0] for entry in calls] == ["chat", "close"]
    assert not is_assistant_contract_evidence_current(test_db)
    assert test_client.post(ACTIVE, json={
        "provider_id": "openrouter", "model_id": "unit/model:free",
    }).status_code == 409
    assert test_db.query(Task).count() == 0


def test_contract_evidence_ends_after_credential_rotation(
    test_client, test_db, candidate, monkeypatch,
):
    fake_adapter(monkeypatch, '{"assistant_text":"Hello","commands":[]}')
    assert test_client.post(URL, json=REQUEST).status_code == 200
    secrets.set_secret(secrets.OPENROUTER_API_KEY, "rotated-credential")
    assert not is_assistant_contract_evidence_current(test_db)
    assert test_client.post(ACTIVE, json={
        "provider_id": "openrouter", "model_id": "unit/model:free",
    }).status_code == 409


def test_no_network_without_explicit_confirmation(test_client, candidate, monkeypatch):
    import backend.adapters.openai_compatible_adapter as module
    monkeypatch.setattr(module, "OpenAICompatibleCloudAdapter",
        lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("No provider request expected"),
        ))
    response = test_client.post(URL, json={**REQUEST, "confirm_network_request": False})
    assert response.status_code == 409
