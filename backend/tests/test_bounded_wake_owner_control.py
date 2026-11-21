"""Owner-facing control surface for the bounded time-driven wake loop.

The loop must remain default-off and require BOTH owner switches, but the owner
also needs an in-app way to enable it. These tests cover the settings whitelist,
the persisted service override, the idempotent live reconciler, and the API.
"""

import pytest

from backend.services.bounded_wake_scheduler import WakePolicy
from backend.services.wake_worker import WakeWorker


def test_bounded_wake_is_an_allowed_owner_setting(test_db):
    from backend.core.settings_store import get_settings, update_settings

    result = update_settings(test_db, {"bounded_wake_enabled": True})
    assert result["bounded_wake_enabled"] is True
    result = update_settings(test_db, {"bounded_wake_enabled": "false"})
    assert result["bounded_wake_enabled"] is False


def test_service_defaults_off_and_persisted_override_wins(test_db, monkeypatch):
    from backend.core.config import settings
    from backend.services.main_agent import MainAgentService

    monkeypatch.setattr(settings, "bounded_wake_enabled", False)
    svc = MainAgentService(test_db)
    assert svc.bounded_wake_enabled() is False

    assert svc.set_bounded_wake_enabled(True) is True
    assert svc.bounded_wake_enabled() is True
    assert svc.set_bounded_wake_enabled(False) is False
    assert svc.bounded_wake_enabled() is False


def test_config_default_enables_when_owner_sets_it(test_db, monkeypatch):
    from backend.core.config import settings
    from backend.services.main_agent import MainAgentService

    monkeypatch.setattr(settings, "bounded_wake_enabled", True)
    assert MainAgentService(test_db).bounded_wake_enabled() is True


def test_apply_owner_policy_requires_both_switches(monkeypatch):
    import backend.services.main_agent as main_agent
    from backend.core.config import settings

    switches = {"background": False, "bounded": False}

    class FakeService:
        def __init__(self, db):
            pass

        def background_enabled(self):
            return switches["background"]

        def may_execute_background(self):
            # apply_owner_policy gates on this (allowed, reason) policy check;
            # keep the fake in sync with the real MainAgentService contract.
            return (switches["background"],
                    "ok" if switches["background"] else "background_disabled")

        def bounded_wake_enabled(self):
            return switches["bounded"]

    monkeypatch.setattr(main_agent, "MainAgentService", FakeService)
    monkeypatch.setattr(settings, "bounded_wake_interval_seconds", 7.5)
    monkeypatch.setattr(settings, "bounded_wake_max_cycles", 4)

    worker = WakeWorker(db_manager=object())
    started = []
    stopped = []

    def fake_start(policy):
        started.append(policy)
        return True

    monkeypatch.setattr(worker, "start_bounded_loop", fake_start)
    monkeypatch.setattr(worker, "stop_bounded_loop", lambda: stopped.append(True))

    # Both off: nothing runs, loop stopped.
    assert worker.apply_owner_policy(object()) is False
    assert started == []
    assert stopped == [True]

    # Background on, bounded off: still inert.
    switches["background"] = True
    assert worker.apply_owner_policy(object()) is False
    assert started == []

    # Both on: loop started with the configured bounds.
    switches["bounded"] = True
    assert worker.apply_owner_policy(object()) is True
    assert len(started) == 1
    assert isinstance(started[0], WakePolicy)
    assert started[0].enabled is True
    assert started[0].interval_seconds == 7.5
    assert started[0].max_cycles == 4


def test_bounded_wake_api_default_off(test_client, test_db, monkeypatch):
    from backend.core.config import settings

    monkeypatch.setattr(settings, "bounded_wake_enabled", False)
    response = test_client.get("/api/v1/agent/bounded-wake")
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is False
    assert body["default"] is False
    assert body["requires_background"] is False


def test_bounded_wake_api_toggle_persists(test_client, test_db):
    from backend.core.settings_store import get_setting

    response = test_client.put("/api/v1/agent/bounded-wake", json={"enabled": True})
    assert response.status_code == 200
    assert response.json()["enabled"] is True
    assert get_setting(test_db, "bounded_wake_enabled") == "true"

    response = test_client.get("/api/v1/agent/bounded-wake")
    assert response.json()["enabled"] is True

    test_client.put("/api/v1/agent/bounded-wake", json={"enabled": False})
    assert get_setting(test_db, "bounded_wake_enabled") == "false"


def test_settings_put_accepts_bounded_wake_and_applies_live(test_client, test_db, monkeypatch):
    from backend.services import wake_worker as wake_worker_module

    applied = []
    fake_worker = type("W", (), {
        "apply_owner_policy": lambda self, db: applied.append(db),
    })()
    monkeypatch.setattr(wake_worker_module, "get_wake_worker", lambda: fake_worker)

    response = test_client.put("/api/v1/settings", json={"bounded_wake_enabled": True})
    assert response.status_code == 200
    assert applied  # live reconcile was invoked after the persisted change
