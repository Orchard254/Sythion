"""Offline tests of independent text-only cloud readiness evidence."""
import json
import time

import pytest

from backend.core import profile, secrets
from backend.core.cloud_text_evidence import (
    MAX_AGE_SECONDS, SETTING_KEY, is_text_evidence_current, persist_text_evidence,
)
from backend.core.cloud_probe_evidence import is_probe_evidence_current
from backend.core.settings_store import get_setting, set_setting


@pytest.fixture
def isolated_text_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(profile, "data_dir", lambda: str(tmp_path))
    secrets.set_secret_store_for_testing(secrets.MemoryStore())
    yield
    secrets.set_secret_store_for_testing(None)


def create_evidence(db, provider="openrouter", model="unit/model:free", key="synthetic-key"):
    profile.stage_cloud_selection(provider, model)
    code = {
        "openrouter": secrets.OPENROUTER_API_KEY,
        "groq": secrets.GROQ_API_KEY,
        "nvidia": secrets.NVIDIA_API_KEY,
    }[provider]
    secrets.set_secret(code, key)
    snap = profile.get_staged_cloud_snapshot()
    persist_text_evidence(db, snap, key)
    return snap


def test_text_success_is_current_but_is_not_tool_success(test_db, isolated_text_evidence):
    create_evidence(test_db)
    assert is_text_evidence_current(test_db)
    assert is_probe_evidence_current(test_db) is False


def test_text_success_expires_and_clock_rollback_fails_closed(test_db, isolated_text_evidence):
    create_evidence(test_db)
    record = json.loads(get_setting(test_db, SETTING_KEY))
    assert is_text_evidence_current(test_db, now=record["issued_at"] + MAX_AGE_SECONDS)
    assert not is_text_evidence_current(test_db, now=record["issued_at"] + MAX_AGE_SECONDS + 1)
    assert not is_text_evidence_current(test_db, now=record["issued_at"] - 1)


def test_key_rotation_and_deletion_revoke_text_readiness(test_db, isolated_text_evidence):
    create_evidence(test_db)
    secrets.set_secret(secrets.OPENROUTER_API_KEY, "different")
    assert not is_text_evidence_current(test_db)
    secrets.delete_secret(secrets.OPENROUTER_API_KEY)
    assert not is_text_evidence_current(test_db)


def test_restage_same_exact_model_invalidates_text_readiness(test_db, isolated_text_evidence):
    previous = create_evidence(test_db)
    profile.stage_cloud_selection(previous["provider_id"], previous["model_id"])
    assert not is_text_evidence_current(test_db)


def test_switch_provider_then_back_invalidates_text_readiness(test_db, isolated_text_evidence):
    create_evidence(test_db)
    profile.stage_cloud_selection("groq", "unit/model:free")
    assert not is_text_evidence_current(test_db)
    profile.stage_cloud_selection("openrouter", "unit/model:free")
    assert not is_text_evidence_current(test_db)


def test_can_verify_text_only_on_other_allowlisted_providers(test_db, isolated_text_evidence):
    for name in ("groq", "nvidia", "openrouter"):
        create_evidence(test_db, provider=name)
        assert is_text_evidence_current(test_db)


def test_signed_record_has_no_credentials_or_provider_content(test_db, isolated_text_evidence):
    create_evidence(test_db, key="secret-should-not-be-stored")
    stored = get_setting(test_db, SETTING_KEY)
    assert "secret-should-not-be-stored" not in stored
    assert "unit/model:free" in stored
    assert "mac" in stored


def test_tampered_text_evidence_fails_closed(test_db, isolated_text_evidence):
    create_evidence(test_db)
    original = json.loads(get_setting(test_db, SETTING_KEY))
    for name, value in (
        ("mac", "0" * 64),
        ("model_id", "other/model"),
        ("generation", "f" * 32),
        ("issued_at", True),
        ("version", True),
    ):
        set_setting(test_db, SETTING_KEY, json.dumps({**original, name: value}))
        test_db.commit()
        assert not is_text_evidence_current(test_db)
    set_setting(test_db, SETTING_KEY, json.dumps(original))
    test_db.commit()
    assert is_text_evidence_current(test_db)


def test_missing_db_or_candidate_never_claims_text_readiness(test_db, isolated_text_evidence):
    assert not is_text_evidence_current(test_db)
    create_evidence(test_db)
    assert not is_text_evidence_current(None)


def test_invalid_staged_revision_cannot_be_proven(test_db, isolated_text_evidence):
    with pytest.raises(ValueError):
        persist_text_evidence(test_db, {"provider_id": "openrouter", "model_id": "unit/model:free", "generation": "0"*32}, "synthetic")
