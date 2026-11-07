"""
Acceptance gate: Cloud onboarding (Phase B).

Onboarding lists authenticated Cloud model IDs and selects one exact returned
ID. Only the non-secret model ID is persisted to the profile; an arbitrary,
stale, or retyped ID is rejected so the sidecar never runs against a model it
cannot use.
"""

import json
import os

import httpx
import pytest
from unittest.mock import patch

from backend.adapters.ollama_adapter import get_adapter
from backend.core.config import settings as app_settings
from backend.core import profile as profile_module

from .conftest import ACCEPTANCE_CONTROL_TOKEN


def _headers():
    return {"X-Control-Token": ACCEPTANCE_CONTROL_TOKEN}


def _models_transport():
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={
                "models": [
                    {"name": "minimax-m3"},
                    {"name": "deepseek-r2"},
                ]
            })
        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.fixture
def isolated_profile(tmp_path, monkeypatch):
    """Point the profile file at a temp path so tests never touch the real one."""
    monkeypatch.setenv("SYTHION_DATA_DIR", str(tmp_path))
    profile_module.save_profile.__globals__["data_dir"] = lambda: str(tmp_path)
    profile_module.profile_path.__globals__["data_dir"] = lambda: str(tmp_path)
    return tmp_path


@pytest.fixture
def stubbed_models_adapter(isolated_profile):
    """Adapter that resolves an authenticated model list without the network."""
    adapter = get_adapter()
    adapter.api_key = "test-key"
    adapter.selected_model = ""
    adapter._client = httpx.AsyncClient(
        base_url="https://ollama.com",
        headers={"Authorization": "Bearer test-key"},
        transport=_models_transport(),
    )
    # _client_loop will be set on first async call to _get_client
    yield adapter
    import asyncio
    try:
        asyncio.get_event_loop().run_until_complete(adapter.close())
    except Exception:
        pass


# --- Onboarding status -------------------------------------------------------


def test_onboarding_status_reports_state(client, isolated_profile):
    with patch.object(app_settings, "ollama_api_key", ""), \
         patch.object(app_settings, "selected_model", ""):
        resp = client.get("/api/v1/onboarding", headers=_headers())
    assert resp.status_code == 200
    assert resp.json()["onboarded"] is False
    assert resp.json()["selected_model"] == ""


# --- Selecting an exact returned ID ------------------------------------------


def test_select_model_persists_exact_returned_id(client, isolated_profile, stubbed_models_adapter):
    resp = client.post(
        "/api/v1/onboarding/models",
        json={"model_id": "minimax-m3"},
        headers=_headers(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"selected_model": "minimax-m3", "persisted": True}

    # Only the non-secret model ID is persisted to the profile file.
    with open(profile_module.profile_path(), encoding="utf-8") as f:
        stored = json.load(f)
    assert stored == {"selected_model": "minimax-m3"}


def test_select_unknown_model_rejected(client, isolated_profile, stubbed_models_adapter):
    resp = client.post(
        "/api/v1/onboarding/models",
        json={"model_id": "not-a-real-model"},
        headers=_headers(),
    )
    assert resp.status_code == 400
    assert "not in the authenticated model list" in resp.json()["detail"]
    # Nothing persisted.
    assert profile_module.get_selected_model() == ""


def test_onboarding_requires_auth(client, isolated_profile):
    resp = client.post("/api/v1/onboarding/models", json={"model_id": "minimax-m3"})
    assert resp.status_code == 401


# --- Resolution order ---------------------------------------------------------


def test_env_override_wins_over_persisted_model(client, isolated_profile, monkeypatch):
    profile_module.set_selected_model("persisted-model")
    # A real, case-sensitive environment override must take precedence over
    # both persisted profile metadata and the imported settings singleton.
    monkeypatch.setattr(app_settings, "selected_model", "")
    monkeypatch.setenv("SYTHION_SELECTED_MODEL", "env-model")
    from backend.core.config import resolve_selected_model
    assert resolve_selected_model() == "env-model"


def test_persisted_model_used_without_env(client, isolated_profile):
    profile_module.set_selected_model("persisted-model")
    with patch.object(app_settings, "selected_model", ""):
        from backend.core.config import resolve_selected_model
        assert resolve_selected_model() == "persisted-model"
