"""Provider-scoped staged cloud evidence; synthetic credentials, offline."""
import json

import pytest

from backend.core import profile, secrets
from backend.core.cloud_probe_evidence import (
    SETTING_KEY, MAX_AGE_SECONDS, make_evidence,
    is_probe_evidence_current, persist_probe_evidence,
)
from backend.core.settings_store import set_setting


@pytest.fixture
def isolated_cloud_evidence(tmp_path, monkeypatch):
    data_dir = tmp_path / "cloud-evidence-prototype"
    data_dir.mkdir()
    monkeypatch.setattr(profile, "data_dir", lambda: str(data_dir))
    secrets.set_secret_store_for_testing(secrets.MemoryStore())
    yield
    secrets.set_secret_store_for_testing(None)


def _record(db, provider="openrouter", model="test/tool:free", credential="inert-v1"):
    profile.stage_cloud_selection(provider, model)
    selection = profile.get_staged_cloud_snapshot()
    assert len(selection["generation"]) == 32
    name = {"openrouter": secrets.OPENROUTER_API_KEY,
            "nvidia": secrets.NVIDIA_API_KEY,
            "groq": secrets.GROQ_API_KEY}[provider]
    secrets.set_secret(name, credential)
    persist_probe_evidence(db, selection, credential)
    return selection


def test_real_synthetic_proof_is_valid_only_for_current_stage_and_vault(test_db, isolated_cloud_evidence):
    selected = _record(test_db)
    assert is_probe_evidence_current(test_db)
    import time
    assert is_probe_evidence_current(test_db, now=int(time.time()) + MAX_AGE_SECONDS + 3) is False
    assert selected == profile.get_staged_cloud_snapshot()


def test_restage_exact_same_provider_model_invalidates_previous_probe(test_db, isolated_cloud_evidence):
    previous = _record(test_db)
    profile.stage_cloud_selection("openrouter", "test/tool:free")
    latest = profile.get_staged_cloud_snapshot()
    assert previous["model_id"] == latest["model_id"]
    assert previous["generation"] != latest["generation"]
    assert not is_probe_evidence_current(test_db)


def test_swapping_to_another_provider_and_returning_invalidates_probe(test_db, isolated_cloud_evidence):
    _record(test_db)
    profile.stage_cloud_selection("groq", "test/tool:free")
    assert not is_probe_evidence_current(test_db)
    profile.stage_cloud_selection("openrouter", "test/tool:free")
    assert not is_probe_evidence_current(test_db)


def test_rotating_key_or_deleting_key_invalidates_probe(test_db, isolated_cloud_evidence):
    _record(test_db)
    secrets.set_secret(secrets.OPENROUTER_API_KEY, "inert-v2")
    assert not is_probe_evidence_current(test_db)
    secrets.delete_secret(secrets.OPENROUTER_API_KEY)
    assert not is_probe_evidence_current(test_db)


def test_proof_is_not_tamperable_through_profile_or_db(test_db, isolated_cloud_evidence):
    _record(test_db)
    from backend.core.settings_store import get_setting
    record = json.loads(get_setting(test_db, SETTING_KEY))
    for key, value in (("model_id", "fake/model:free"), ("mac", "0" * 64),
                       ("issued_at", True), ("version", False)):
        modified = {**record, key: value}
        set_setting(test_db, SETTING_KEY, json.dumps(modified))
        test_db.commit()
        assert not is_probe_evidence_current(test_db)
    set_setting(test_db, SETTING_KEY, json.dumps(record))
    test_db.commit()
    assert is_probe_evidence_current(test_db)


@pytest.mark.parametrize("offset", [-2, MAX_AGE_SECONDS + 1, MAX_AGE_SECONDS])
def test_clock_bounds_fail_closed(test_db, isolated_cloud_evidence, offset):
    _record(test_db)
    import time
    from backend.core.settings_store import get_setting
    recorded = json.loads(get_setting(test_db, SETTING_KEY))
    now = recorded["issued_at"] + offset
    assert is_probe_evidence_current(test_db, now=now) is (0 <= offset <= MAX_AGE_SECONDS)


def test_evidence_persists_only_safe_fields(test_db, isolated_cloud_evidence):
    _record(test_db, credential="super-sensitive-inert-secret")
    from backend.core.settings_store import get_setting
    stored = get_setting(test_db, SETTING_KEY)
    assert "super-sensitive-inert-secret" not in stored
    assert "test/tool:free" in stored
    assert "openrouter" in stored
    assert "mac" in stored


def test_older_staged_profiles_without_generation_are_not_accepted(test_db, isolated_cloud_evidence):
    p = profile.profile_path()
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"staged_cloud_selection": {"provider_id": "openrouter", "model_id": "old/model"}}, f)
    assert profile.get_staged_cloud_selection() == {"provider_id": "openrouter", "model_id": "old/model"}
    assert profile.get_staged_cloud_snapshot() == {}
    assert not is_probe_evidence_current(test_db)


def test_no_fake_proof_when_db_is_unavailable(isolated_cloud_evidence):
    profile.stage_cloud_selection("openrouter", "test/tool:free")
    secrets.set_secret(secrets.OPENROUTER_API_KEY, "inert")
    assert is_probe_evidence_current(None) is False


def test_make_evidence_requires_nonempty_key(isolated_cloud_evidence):
    profile.stage_cloud_selection("groq", "unit-model")
    with pytest.raises(ValueError):
        make_evidence(profile.get_staged_cloud_snapshot(), "")
