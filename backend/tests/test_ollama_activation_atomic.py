"""Changing Ollama models must not corrupt a working active selection.

These tests never contact Ollama or any paid provider. The candidate HTTP
request is replaced by an exact fake; model state changes only after success.
"""
from types import SimpleNamespace

import pytest

from backend.core import profile
from backend.core.settings_store import get_setting, set_setting
from backend.models.database import Task

URL = "/api/v1/inference/ollama/activate"


@pytest.fixture
def configured(tmp_path, monkeypatch, test_db):
    from backend.core import config, secrets
    import backend.runtime.model_provider as provider_module
    import backend.adapters.ollama_adapter as ollama_module

    monkeypatch.setattr(profile, "data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(config.settings, "selected_model", "")
    monkeypatch.delenv("SYTHION_SELECTED_MODEL", raising=False)
    monkeypatch.setattr(secrets, "resolve_secret_context",
                        lambda: ("Sythion (Synth)", True, False, "synthetic"))
    # Simulate an existing verified Ollama model.
    profile.save_profile({"selected_model": "previous/model"})
    set_setting(test_db, "ollama_inference_verified_model", "previous/model")
    test_db.commit()

    adapter = SimpleNamespace(selected_model="previous/model", _capability_profile="old-profile")
    async def candidate_chat(*, messages, model_id, max_tokens, timeout):
        if model_id == "broken/model":
            raise RuntimeError("synthetic model unavailable")
        return {"message": {"role": "assistant", "content": "OK"}}
    adapter.chat = candidate_chat

    class Provider:
        def list_models(self):
            return [
                {"name": "previous/model"}, {"name": "next/model"},
                {"name": "broken/model"},
            ]
    monkeypatch.setattr(provider_module, "get_default_provider", lambda: Provider())
    monkeypatch.setattr(ollama_module, "get_adapter", lambda: adapter)
    return adapter


def test_rejected_candidate_does_not_change_previous_working_model(
    test_client, test_db, configured,
):
    response = test_client.post(URL, json={"model_id": "broken/model"})
    assert response.status_code == 502, response.text
    assert profile.get_selected_model() == "previous/model"
    assert configured.selected_model == "previous/model"
    assert get_setting(test_db, "ollama_inference_verified_model") == "previous/model"
    assert configured._capability_profile == "old-profile"


def test_successful_candidate_commits_verified_model_and_invalidates_adapter_cache(
    test_client, test_db, configured,
):
    response = test_client.post(URL, json={"model_id": "next/model"})
    assert response.status_code == 200, response.text
    assert response.json() == {
        "provider_id": "ollama-cloud", "model_id": "next/model",
        "verified": True, "permissions_unchanged": True,
    }
    assert profile.get_selected_model() == "next/model"
    assert configured.selected_model == "next/model"
    assert configured._capability_profile is None
    assert get_setting(test_db, "ollama_inference_verified_model") == "next/model"
    assert test_client.get("/api/v1/inference/active").json()["model_id"] == "next/model"


def test_inactive_or_unlisted_candidate_never_mutates_selected_model(
    test_client, test_db, configured,
):
    response = test_client.post(URL, json={"model_id": "unknown/model"})
    assert response.status_code == 400
    assert profile.get_selected_model() == "previous/model"
    assert configured.selected_model == "previous/model"


def test_unfinished_task_blocks_ollama_switch_without_provider_request(
    test_client, test_db, configured, monkeypatch,
):
    test_db.add(Task(id="busy", original_request="In flight", state="running"))
    test_db.commit()
    response = test_client.post(URL, json={"model_id": "next/model"})
    assert response.status_code == 409
    assert profile.get_selected_model() == "previous/model"
    assert configured.selected_model == "previous/model"
