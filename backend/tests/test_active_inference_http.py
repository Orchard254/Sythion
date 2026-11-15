"""Production provider API acceptance; zero external requests and no task dispatch."""
import pytest

from backend.core import profile, secrets
from backend.core.cloud_text_evidence import persist_text_evidence
from backend.core.cloud_probe_evidence import persist_probe_evidence
from backend.core.settings_store import set_setting
from backend.models.database import Task

ENDPOINT = "/api/v1/inference/active"
BODY = {"provider_id": "openrouter", "model_id": "unit/model:free"}


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(profile, "data_dir", lambda: str(tmp_path))
    secrets.set_secret_store_for_testing(secrets.MemoryStore())
    yield
    secrets.set_secret_store_for_testing(None)


def prepare(db):
    profile.stage_cloud_selection("openrouter", "unit/model:free")
    secrets.set_secret(secrets.OPENROUTER_API_KEY, "synthetic-key-not-real")
    snap = profile.get_staged_cloud_snapshot()
    persist_text_evidence(db, snap, "synthetic-key-not-real")
    persist_probe_evidence(db, snap, "synthetic-key-not-real")


def test_default_active_selection_does_not_use_staged_preview(test_client, isolated):
    r = test_client.get(ENDPOINT)
    assert r.status_code == 200, r.text
    assert r.json() == {
        "provider_id": "ollama-cloud", "model_id": "", "permissions_unchanged": True,
    }
    profile.stage_cloud_selection("openrouter", "unit/model:free")
    assert test_client.get(ENDPOINT).json()["provider_id"] == "ollama-cloud"
    assert test_client.post(ENDPOINT, json=BODY).status_code == 409


def test_activates_verified_model_without_network_or_tasks(
    test_client, test_db, isolated, monkeypatch,
):
    import backend.adapters.openai_compatible_adapter as adapter
    monkeypatch.setattr(adapter, "OpenAICompatibleCloudAdapter",
        lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("Activation must be local-only")
        ))
    prepare(test_db)
    r = test_client.post(ENDPOINT, json=BODY)
    assert r.status_code == 200, r.text
    assert r.json() == {**BODY, "permissions_unchanged": True}
    assert test_client.get(ENDPOINT).json() == r.json()
    assert test_db.query(Task).count() == 0


def test_credential_rotation_invalidates_active_runtime_without_fallback(
    test_client, test_db, isolated,
):
    prepare(test_db)
    assert test_client.post(ENDPOINT, json=BODY).status_code == 200
    secrets.set_secret(secrets.OPENROUTER_API_KEY, "changed-key")
    r = test_client.get(ENDPOINT)
    assert r.status_code == 409
    assert "changed-key" not in r.text
    assert test_client.post(ENDPOINT, json=BODY).status_code == 409


@pytest.mark.parametrize("state", [
    "created", "running", "waiting_for_user", "paused", "interrupted",
])
def test_switch_refused_while_unfinished_tasks_exist(
    test_client, test_db, isolated, state,
):
    prepare(test_db)
    test_db.add(Task(id="pending-provider-switch", original_request="Existing task",
                     state=state))
    test_db.commit()
    r = test_client.post(ENDPOINT, json=BODY)
    assert r.status_code == 409
    assert "unfinished" in r.text
    assert test_client.get(ENDPOINT).json()["provider_id"] == "ollama-cloud"


def test_cannot_switch_back_to_unverified_ollama(
    test_client, test_db, isolated,
):
    prepare(test_db)
    assert test_client.post(ENDPOINT, json=BODY).status_code == 200
    r = test_client.delete(ENDPOINT)
    assert r.status_code == 409
    assert test_client.get(ENDPOINT).json()["provider_id"] == "openrouter"


def test_restores_verified_ollama_without_changing_permissions(
    test_client, test_db, isolated,
):
    prepare(test_db)
    assert test_client.post(ENDPOINT, json=BODY).status_code == 200
    profile.set_selected_model("unit/ollama")
    secrets.set_secret(secrets.OLLAMA_API_KEY, "inert-ollama-key")
    set_setting(test_db, "ollama_inference_verified_model", "unit/ollama")
    test_db.commit()
    r = test_client.delete(ENDPOINT)
    assert r.status_code == 200, r.text
    assert r.json() == {
        "provider_id": "ollama-cloud", "model_id": "unit/ollama",
        "permissions_unchanged": True,
    }
    assert test_client.get(ENDPOINT).json()["provider_id"] == "ollama-cloud"


def test_control_token_is_required_for_provider_switch(test_client, test_db, isolated):
    prepare(test_db)
    old = test_client.headers.pop("X-Control-Token", None)
    try:
        assert test_client.post(ENDPOINT, json=BODY).status_code == 401
        assert test_client.delete(ENDPOINT).status_code == 401
    finally:
        if old:
            test_client.headers["X-Control-Token"] = old
    assert test_client.get(ENDPOINT).json()["provider_id"] == "ollama-cloud"
