"""Production provider API acceptance; zero external requests and no task dispatch."""
import pytest

from backend.core import profile, secrets
from backend.core.cloud_text_evidence import persist_text_evidence
from backend.core.cloud_probe_evidence import persist_probe_evidence
from backend.core.cloud_assistant_evidence import persist_assistant_contract_evidence
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
    persist_assistant_contract_evidence(db, snap, "synthetic-key-not-real")


def prepare_ollama_cloud(db):
    profile.stage_cloud_selection("ollama-cloud", "gemma4:31b")
    secrets.set_secret(secrets.OLLAMA_API_KEY, "synthetic-key-not-real")
    snap = profile.get_staged_cloud_snapshot()
    persist_text_evidence(db, snap, "synthetic-key-not-real")
    persist_probe_evidence(db, snap, "synthetic-key-not-real")
    persist_assistant_contract_evidence(db, snap, "synthetic-key-not-real")


def test_ollama_cloud_activates_through_the_same_endpoint(test_client, test_db, isolated):
    prepare_ollama_cloud(test_db)
    body = {"provider_id": "ollama-cloud", "model_id": "gemma4:31b"}
    r = test_client.post(ENDPOINT, json=body)
    assert r.status_code == 200, r.text
    assert r.json() == {**body, "permissions_unchanged": True}
    assert test_client.get(ENDPOINT).json() == r.json()


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


@pytest.mark.parametrize("state", ["created", "running"])
def test_switch_queued_while_a_task_has_a_model_call_in_flight(
    test_client, test_db, isolated, state,
):
    """Lazy-user: a verified choice requested during in-flight work is queued,
    accepted with 200, and applied automatically later — no 409, no retry."""
    prepare(test_db)
    task = Task(id="pending-provider-switch", original_request="Existing task",
                state=state)
    test_db.add(task)
    test_db.commit()
    r = test_client.post(ENDPOINT, json=BODY)
    assert r.status_code == 200, r.text
    assert r.json()["pending"] is True
    assert r.json()["permissions_unchanged"] is True
    assert "automatically" in r.json()["message"]
    # The active model still honestly reports the OLD selection for now.
    assert test_client.get(ENDPOINT).json()["provider_id"] == "ollama-cloud"


def test_pending_activation_applies_when_work_settles(test_client, test_db, isolated):
    from backend.runtime.active_inference import maybe_apply_pending_activation

    prepare(test_db)
    task = Task(id="busy", original_request="Existing task", state="running")
    test_db.add(task)
    test_db.commit()
    assert test_client.post(ENDPOINT, json=BODY).json()["pending"] is True

    # Task reaches a terminal state (no model call can hold the old binding).
    task.state = "completed"
    test_db.commit()

    assert maybe_apply_pending_activation(test_db) is True
    assert test_client.get(ENDPOINT).json() == {**BODY, "permissions_unchanged": True}
    # Applying clears the pending choice — it is not applied twice.
    assert maybe_apply_pending_activation(test_db) is False


def test_pending_activation_not_applied_while_task_still_running(
    test_client, test_db, isolated,
):
    from backend.runtime.active_inference import maybe_apply_pending_activation

    prepare(test_db)
    test_db.add(Task(id="still-busy", original_request="Existing task", state="running"))
    test_db.commit()
    assert test_client.post(ENDPOINT, json=BODY).json()["pending"] is True

    # A different task is still running: the switch must stay deferred.
    assert maybe_apply_pending_activation(test_db) is False
    assert test_client.get(ENDPOINT).json()["provider_id"] == "ollama-cloud"


@pytest.mark.asyncio
async def test_task_terminal_transition_applies_pending_activation(
    test_client, test_db, isolated, task_runner,
):
    """The task_runner terminal hook — not just the helper — promotes a queued
    choice, so the owner never has to return after work settles."""
    from backend.api.schemas import TaskState

    prepare(test_db)
    task = await task_runner.create_task("In-flight task")
    await task_runner.transition_state(task.id, TaskState.RUNNING, "task.execution_started", {})

    # Owner picks a new verified model while the task holds the old binding.
    r = test_client.post(ENDPOINT, json=BODY)
    assert r.status_code == 200 and r.json()["pending"] is True
    assert test_client.get(ENDPOINT).json()["provider_id"] == "ollama-cloud"

    # The task finishes through the real state machine.
    await task_runner.transition_state(
        task.id, TaskState.COMPLETED, "task.execution_completed", {},
    )
    assert test_client.get(ENDPOINT).json() == {**BODY, "permissions_unchanged": True}


def test_direct_activation_clears_any_pending_choice(test_client, test_db, isolated):
    from backend.runtime.active_inference import (
        queue_pending_activation, maybe_apply_pending_activation,
    )

    prepare(test_db)
    test_db.add(Task(id="busy", original_request="Existing task", state="running"))
    test_db.commit()
    queue_pending_activation(test_db, BODY["provider_id"], BODY["model_id"])

    # An explicit activation (idle path) promotes the choice and clears pending.
    task = test_db.query(Task).filter_by(id="busy").one()
    task.state = "completed"
    test_db.commit()
    r = test_client.post(ENDPOINT, json=BODY)
    assert r.status_code == 200, r.text
    assert "pending" not in r.json()
    assert test_client.get(ENDPOINT).json() == {**BODY, "permissions_unchanged": True}
    assert maybe_apply_pending_activation(test_db) is False


@pytest.mark.parametrize("state", ["waiting_for_user", "paused", "interrupted"])
def test_switch_allowed_while_task_has_no_active_model_call(
    test_client, test_db, isolated, state,
):
    """A waiting/paused/interrupted task has no in-flight model call, so it
    must not silently block a legitimate model change."""
    prepare(test_db)
    test_db.add(Task(id="idle-task", original_request="Existing task", state=state))
    test_db.commit()
    r = test_client.post(ENDPOINT, json=BODY)
    assert r.status_code == 200, r.text
    assert r.json() == {**BODY, "permissions_unchanged": True}


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


def test_cloud_onboarding_readiness_uses_active_provider_not_ollama_key(
    test_client, test_db, isolated, monkeypatch,
):
    # Old onboarding required the Ollama subscription even when a cloud model
    # had been selected. The real active model now determines chat readiness.
    from backend.core import config
    monkeypatch.setattr(config.settings, "ollama_api_key", "")
    prepare(test_db)
    assert test_client.post(ENDPOINT, json=BODY).status_code == 200
    safe = test_client.post("/api/v1/setup/safe-settings")
    assert safe.status_code == 200, safe.text
    r = test_client.get("/api/v1/setup/readiness")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["active_provider_id"] == "openrouter"
    assert body["active_provider_ready_ok"] is True
    assert body["checks"]["selected_model_ok"] is True
    assert body["checks"]["inference_verified_ok"] is True
    assert body["checks"]["safe_settings_ok"] is True
    assert body["checks"]["ollama_api_key_ok"] is False
    assert body["selected_model"] == "unit/model:free"
    assert body["inference_verified_model"] == "unit/model:free"


def test_rotated_cloud_key_is_not_reinterpreted_as_ollama_readiness(
    test_client, test_db, isolated,
):
    prepare(test_db)
    assert test_client.post(ENDPOINT, json=BODY).status_code == 200
    secrets.set_secret(secrets.OPENROUTER_API_KEY, "invalidated-key")
    r = test_client.get("/api/v1/setup/readiness")
    assert r.status_code == 200, r.text
    assert r.json()["active_provider_id"] == "unavailable"
    assert r.json()["active_provider_ready_ok"] is False
    assert r.json()["checks"]["selected_model_ok"] is False
    assert r.json()["checks"]["inference_verified_ok"] is False
    assert r.json()["full_ready"] is False


def test_changing_ollama_model_is_deferred_while_a_task_runs(
    test_client, test_db, isolated, monkeypatch,
):
    """Lazy-user: /onboarding/models also mutates the active Ollama model, so a
    running task defers it (200 + pending) instead of a cancel/refresh hand-back.
    """
    import backend.runtime.model_provider as provider_module

    class Provider:
        def list_models(self):
            return [{"name": "ollama/another-model"}]

    monkeypatch.setattr(provider_module, "get_default_provider", lambda: Provider())
    test_db.add(Task(
        id="active-ollama-settings-switch", original_request="In progress",
        state="running",
    ))
    test_db.commit()
    response = test_client.post(
        "/api/v1/onboarding/models", json={"model_id": "ollama/another-model"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["pending"] is True
    assert body["selected_model"] == "ollama/another-model"
    assert "automatically" in body["message"]
    # The live selection is unchanged until work settles.
    from backend.core import profile
    assert profile.get_selected_model() != "ollama/another-model"


def test_deferred_ollama_model_selection_applies_when_idle(
    test_client, test_db, isolated, monkeypatch,
):
    from backend.runtime.active_inference import maybe_apply_pending_activation
    import backend.runtime.model_provider as provider_module

    class Provider:
        def list_models(self):
            return [{"name": "ollama/another-model"}]

    monkeypatch.setattr(provider_module, "get_default_provider", lambda: Provider())
    task = Task(id="busy-select", original_request="In progress", state="running")
    test_db.add(task)
    test_db.commit()
    assert test_client.post(
        "/api/v1/onboarding/models", json={"model_id": "ollama/another-model"},
    ).json()["pending"] is True

    task.state = "completed"
    test_db.commit()
    assert maybe_apply_pending_activation(test_db) is True
    from backend.core import profile
    assert profile.get_selected_model() == "ollama/another-model"
    assert maybe_apply_pending_activation(test_db) is False


# --- Deferred Ollama restore (DELETE /inference/active) ---------------------


def _active_cloud_with_verified_ollama(test_client, test_db):
    """Activate a cloud model, then set up a verified local Ollama fallback."""
    prepare(test_db)
    assert test_client.post(ENDPOINT, json=BODY).status_code == 200
    profile.set_selected_model("unit/ollama")
    secrets.set_secret(secrets.OLLAMA_API_KEY, "inert-ollama-key")
    set_setting(test_db, "ollama_inference_verified_model", "unit/ollama")
    test_db.commit()


@pytest.mark.parametrize("state", ["created", "running"])
def test_ollama_restore_deferred_while_task_holds_old_binding(
    test_client, test_db, isolated, state,
):
    """DELETE /inference/active queues the restore instead of returning 409."""
    from backend.runtime.active_inference import maybe_apply_pending_activation

    _active_cloud_with_verified_ollama(test_client, test_db)
    task = Task(id="busy-restore", original_request="Existing task", state=state)
    test_db.add(task)
    test_db.commit()

    r = test_client.delete(ENDPOINT)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["provider_id"] == "ollama-cloud"
    assert body["model_id"] == "unit/ollama"
    assert body["permissions_unchanged"] is True
    assert body["pending"] is True
    assert "automatically" in body["message"]
    # The old cloud binding is still active for now.
    assert test_client.get(ENDPOINT).json()["provider_id"] == "openrouter"

    # Still busy: no promotion.
    assert maybe_apply_pending_activation(test_db) is False
    assert test_client.get(ENDPOINT).json()["provider_id"] == "openrouter"

    task.state = "completed"
    test_db.commit()
    assert maybe_apply_pending_activation(test_db) is True
    active = test_client.get(ENDPOINT).json()
    assert active["provider_id"] == "ollama-cloud"
    assert active["model_id"] == "unit/ollama"
    # Cleared after apply; not applied twice.
    assert maybe_apply_pending_activation(test_db) is False


def test_ollama_restore_does_not_apply_while_another_task_still_runs(
    test_client, test_db, isolated,
):
    from backend.runtime.active_inference import maybe_apply_pending_activation

    _active_cloud_with_verified_ollama(test_client, test_db)
    finishing = Task(id="finishing", original_request="Existing task", state="running")
    other = Task(id="other", original_request="Existing task", state="running")
    test_db.add_all([finishing, other])
    test_db.commit()
    assert test_client.delete(ENDPOINT).json()["pending"] is True

    finishing.state = "completed"
    test_db.commit()
    assert maybe_apply_pending_activation(test_db) is False
    assert test_client.get(ENDPOINT).json()["provider_id"] == "openrouter"


def test_ollama_restore_immediate_path_unchanged(test_client, test_db, isolated):
    """With no in-flight task the restore is committed synchronously."""
    _active_cloud_with_verified_ollama(test_client, test_db)
    r = test_client.delete(ENDPOINT)
    assert r.status_code == 200, r.text
    assert r.json() == {
        "provider_id": "ollama-cloud", "model_id": "unit/ollama",
        "permissions_unchanged": True,
    }
    assert test_client.get(ENDPOINT).json() == r.json()
    from backend.runtime.active_inference import maybe_apply_pending_activation
    assert maybe_apply_pending_activation(test_db) is False


# --- Health reports the authoritative active binding ------------------------


def test_health_reports_authoritative_active_provider_and_model(
    test_client, test_db, isolated,
):
    prepare(test_db)
    assert test_client.post(ENDPOINT, json=BODY).status_code == 200
    active = test_client.get(ENDPOINT).json()
    health = test_client.get("/api/v1/health")
    assert health.status_code == 200, health.text
    body = health.json()
    assert body["active_provider_id"] == active["provider_id"] == "openrouter"
    assert body["active_model_id"] == active["model_id"] == "unit/model:free"
    # Legacy fields still describe the local/default provider.
    assert "selected_model" in body and "cloud_configured" in body


def test_health_active_fields_fail_closed_on_credential_rotation(
    test_client, test_db, isolated,
):
    prepare(test_db)
    assert test_client.post(ENDPOINT, json=BODY).status_code == 200
    secrets.set_secret(secrets.OPENROUTER_API_KEY, "rotated-key")
    health = test_client.get("/api/v1/health")
    assert health.status_code == 200, health.text
    body = health.json()
    assert body["active_provider_id"] == "unavailable"
    assert body["active_model_id"] == ""
    # The authoritative endpoint stays honestly unavailable too (no crash).
    assert test_client.get(ENDPOINT).status_code == 409
    assert "rotated-key" not in health.text


def test_registry_and_self_model_expose_authoritative_active_binding(
    test_client, test_db, isolated,
):
    """Other provider/model reporters carry clearly-named active fields while
    their legacy default-provider fields stay unchanged."""
    prepare(test_db)
    assert test_client.post(ENDPOINT, json=BODY).status_code == 200

    registry = test_client.get("/api/v1/models/registry")
    assert registry.status_code == 200, registry.text
    assert registry.json()["active_provider_id"] == "openrouter"
    assert registry.json()["active_model_id"] == "unit/model:free"

    self_model = test_client.get("/api/v1/agent/self-model")
    assert self_model.status_code == 200, self_model.text
    models = self_model.json()["models"]
    assert models["active_provider_id"] == "openrouter"
    assert models["active_model_id"] == "unit/model:free"
    # Legacy local-provider fields are still present.
    assert "provider_id" in models and "selected_model" in models
