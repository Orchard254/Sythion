"""Honest safe onboarding: effective guarded authority and atomic 90-day config."""

import pytest


def test_safe_settings_writes_guarded_90_day_config(test_client, test_db):
    from backend.core.settings_store import get_setting

    response = test_client.post("/api/v1/setup/safe-settings")
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert get_setting(test_db, "retention_days") == "90"
    assert get_setting(test_db, "setup_safe_initial_config_applied") == "true"

    readiness = test_client.get("/api/v1/setup/readiness").json()
    assert readiness["checks"]["safe_settings_ok"] is True


@pytest.mark.parametrize("days", [1, 30, 91, 365])
def test_safe_settings_preserves_owner_retention(test_client, test_db, days):
    from backend.core.settings_store import get_setting, set_setting

    set_setting(test_db, "retention_days", str(days))
    test_db.commit()
    for _ in range(2):
        assert test_client.post("/api/v1/setup/safe-settings").status_code == 200
        assert get_setting(test_db, "retention_days") == str(days)
        assert get_setting(test_db, "setup_safe_initial_config_applied") == "true"


@pytest.mark.parametrize("retention", ["not-a-number", "0", "-1", "", "1.5"])
def test_safe_settings_repairs_invalid_retention(test_client, test_db, retention):
    from backend.core.settings_store import get_setting, set_setting

    set_setting(test_db, "retention_days", retention)
    test_db.commit()
    assert test_client.post("/api/v1/setup/safe-settings").status_code == 200
    assert get_setting(test_db, "retention_days") == "90"
    assert get_setting(test_db, "setup_safe_initial_config_applied") == "true"


def test_safe_settings_rolls_back_retention_when_marker_write_fails(test_client, test_db, monkeypatch):
    from backend.core import settings_store

    settings_store.set_setting(test_db, "retention_days", "0")
    test_db.commit()
    original = settings_store.set_setting

    def fail_marker(db, key, value):
        if key == "setup_safe_initial_config_applied":
            raise RuntimeError("injected failure")
        original(db, key, value)

    monkeypatch.setattr(settings_store, "set_setting", fail_marker)
    assert test_client.post("/api/v1/setup/safe-settings").status_code == 500
    test_db.expire_all()
    assert settings_store.get_setting(test_db, "retention_days") == "0"
    assert settings_store.get_setting(test_db, "setup_safe_initial_config_applied") is None


def test_safe_settings_rejects_autonomous_runtime_environment(test_client, monkeypatch):
    monkeypatch.setenv("SYTHION_AUTONOMY_LEVEL", "autonomous")
    response = test_client.post("/api/v1/setup/safe-settings")
    assert response.status_code == 409
    assert "guarded" in response.json()["detail"].lower()


def test_safe_settings_respects_environment_precedence_over_settings(test_client, monkeypatch):
    import backend.core.config as core_config

    monkeypatch.setenv("SYTHION_AUTONOMY_LEVEL", "guarded")
    monkeypatch.setattr(core_config.settings, "autonomy_level", "autonomous")
    response = test_client.post("/api/v1/setup/safe-settings")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_safe_settings_rejects_autonomous_settings_without_env_override(test_client, monkeypatch):
    import backend.core.config as core_config

    monkeypatch.delenv("SYTHION_AUTONOMY_LEVEL", raising=False)
    monkeypatch.setattr(core_config.settings, "autonomy_level", "autonomous")
    response = test_client.post("/api/v1/setup/safe-settings")
    assert response.status_code == 409


def test_safe_settings_rejects_unknown_or_empty_effective_autonomy(test_client, monkeypatch):
    for value in ("", "unknown"):
        monkeypatch.setenv("SYTHION_AUTONOMY_LEVEL", value)
        response = test_client.post("/api/v1/setup/safe-settings")
        assert response.status_code == 409


def test_safe_settings_marker_only_is_not_ready(test_client, test_db):
    from backend.core.settings_store import set_setting

    set_setting(test_db, "setup_safe_initial_config_applied", "true")
    test_db.commit()

    readiness = test_client.get("/api/v1/setup/readiness").json()
    assert readiness["checks"]["safe_settings_ok"] is False


@pytest.mark.parametrize("days", [91, 30])
def test_safe_settings_remains_ready_after_public_retention_update(test_client, test_db, days):
    from backend.core.settings_store import get_setting

    assert test_client.post("/api/v1/setup/safe-settings").status_code == 200
    response = test_client.put("/api/v1/settings", json={"retention_days": days})
    assert response.status_code == 200
    assert get_setting(test_db, "retention_days") == str(days)
    assert test_client.get("/api/v1/setup/readiness").json()["checks"]["safe_settings_ok"] is True


@pytest.mark.parametrize("retention", ["not-a-number", "0", "-1", "", "1.5"])
def test_safe_settings_invalid_retention_is_not_ready(test_client, test_db, retention):
    from backend.core.settings_store import set_setting

    set_setting(test_db, "setup_safe_initial_config_applied", "true")
    set_setting(test_db, "retention_days", retention)
    test_db.commit()

    readiness = test_client.get("/api/v1/setup/readiness").json()
    assert readiness["checks"]["safe_settings_ok"] is False


def test_safe_settings_valid_retention_without_marker_is_not_ready(test_client, test_db):
    from backend.core.settings_store import set_setting

    set_setting(test_db, "retention_days", "91")
    test_db.commit()

    readiness = test_client.get("/api/v1/setup/readiness").json()
    assert readiness["checks"]["safe_settings_ok"] is False


def test_safe_settings_loses_readiness_when_autonomy_changes(test_client, monkeypatch):
    assert test_client.post("/api/v1/setup/safe-settings").status_code == 200
    monkeypatch.setenv("SYTHION_AUTONOMY_LEVEL", "autonomous")
    assert test_client.get("/api/v1/setup/readiness").json()["checks"]["safe_settings_ok"] is False
