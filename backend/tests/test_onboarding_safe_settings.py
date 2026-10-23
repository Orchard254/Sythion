"""Honest safe onboarding: effective guarded authority and atomic 90-day config."""


def test_safe_settings_writes_guarded_90_day_config(test_client):
    response = test_client.post("/api/v1/setup/safe-settings")
    assert response.status_code == 200
    assert response.json() == {"ok": True}

    readiness = test_client.get("/api/v1/setup/readiness").json()
    assert readiness["checks"]["safe_settings_ok"] is True


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


def test_safe_settings_wrong_retention_is_not_ready(test_client, test_db):
    from backend.core.settings_store import set_setting

    set_setting(test_db, "setup_safe_initial_config_applied", "true")
    set_setting(test_db, "retention_days", "30")
    test_db.commit()

    readiness = test_client.get("/api/v1/setup/readiness").json()
    assert readiness["checks"]["safe_settings_ok"] is False


def test_safe_settings_malformed_retention_is_not_ready(test_client, test_db):
    from backend.core.settings_store import set_setting

    set_setting(test_db, "setup_safe_initial_config_applied", "true")
    set_setting(test_db, "retention_days", "not-a-number")
    test_db.commit()

    readiness = test_client.get("/api/v1/setup/readiness").json()
    assert readiness["checks"]["safe_settings_ok"] is False