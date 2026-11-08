"""Acceptance tests for fail-closed profile handling during model selection."""

import httpx

import pytest
from unittest.mock import patch

from backend.adapters.ollama_adapter import get_adapter
from backend.core import profile as profile_module
from backend.core.config import settings as app_settings

from .conftest import ACCEPTANCE_CONTROL_TOKEN
from .conftest import _headers, _models_transport, stubbed_models_adapter, isolated_profile


@pytest.fixture
def invalid_profile(isolated_profile):
    (isolated_profile / "profile.json").write_bytes(b"\xff\xfe\x00")
    return isolated_profile


def test_select_model_profile_storage_failure_is_sanitized(
    client, invalid_profile, stubbed_models_adapter
):
    original = (invalid_profile / "profile.json").read_bytes()

    with patch.object(app_settings, "selected_model", ""):
        resp = client.post(
            "/api/v1/onboarding/models",
            json={"model_id": "minimax-m3"},
            headers=_headers(),
        )

    assert resp.status_code == 500
    body = resp.json()
    assert body == {"detail": "Profile storage unavailable"}
    text = resp.text
    assert "profile.json" not in text
    assert str(invalid_profile) not in text
    assert (invalid_profile / "profile.json").read_bytes() == original
