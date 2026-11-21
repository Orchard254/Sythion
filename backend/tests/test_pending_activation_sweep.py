"""Autonomous pending-activation sweep — closes the stuck-CREATED hole.

Zero external network calls: evidence is synthetic, secrets are in-memory, and
the profile lives under a temporary LOCALAPPDATA. The sweep only ever mutates
local backend state and never dispatches a task or sends an inference request.
"""
import asyncio

import pytest

from backend.core import profile, secrets
from backend.core.cloud_text_evidence import persist_text_evidence
from backend.core.cloud_probe_evidence import persist_probe_evidence
from backend.core.cloud_assistant_evidence import persist_assistant_contract_evidence
from backend.models.database import Task
from backend.runtime import active_inference as ai

ENDPOINT = "/api/v1/inference/active"
BODY = {"provider_id": "openrouter", "model_id": "unit/model:free"}


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Temp LOCALAPPDATA + in-memory secret store, mirroring the HTTP suite."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(profile, "data_dir", lambda: str(tmp_path))
    secrets.set_secret_store_for_testing(secrets.MemoryStore())
    yield tmp_path
    secrets.set_secret_store_for_testing(None)


def prepare(db):
    profile.stage_cloud_selection("openrouter", "unit/model:free")
    secrets.set_secret(secrets.OPENROUTER_API_KEY, "synthetic-key-not-real")
    snap = profile.get_staged_cloud_snapshot()
    persist_text_evidence(db, snap, "synthetic-key-not-real")
    persist_probe_evidence(db, snap, "synthetic-key-not-real")
    persist_assistant_contract_evidence(db, snap, "synthetic-key-not-real")


def _task(db, task_id, state):
    db.add(Task(id=task_id, original_request="Existing task", state=state))
    db.commit()


class _SharedSessionProxy:
    """Delegates DB work to the shared test session but ignores close().

    The sweep owns its session lifecycle; the test session must stay open for
    assertions after the cycle. Only cloud-pending promotion is exercised here,
    so no commit/rollback beyond read queries is needed.
    """

    def __init__(self, db):
        self._db = db

    def close(self):
        pass

    def __getattr__(self, name):
        return getattr(self._db, name)


class _SharedSessionManager:
    def __init__(self, db):
        self._proxy = _SharedSessionProxy(db)

    def new_session(self):
        return self._proxy


# (a) The hole: a CREATED task that never advances must not strand the choice.


def test_sweep_promotes_pending_while_created_task_is_stuck(
    test_client, test_db, isolated,
):
    prepare(test_db)
    _task(test_db, "stuck-created", "created")
    r = test_client.post(ENDPOINT, json=BODY)
    assert r.status_code == 200, r.text
    assert r.json()["pending"] is True

    # No owner action and no terminal transition ever happens; the task stays
    # CREATED forever. The old terminal-only apply path could never fire here.
    assert test_db.query(Task).filter_by(id="stuck-created").one().state == "created"
    assert test_client.get(ENDPOINT).json()["provider_id"] == "ollama-cloud"

    # The autonomous sweep promotes it using the RUNNING-only safety check.
    assert ai.sweep_pending_activation(test_db) is True
    assert test_client.get(ENDPOINT).json() == {**BODY, "permissions_unchanged": True}


# (b) A genuinely RUNNING task still holds the old binding — defer.


def test_sweep_does_not_promote_while_task_is_running(
    test_client, test_db, isolated,
):
    prepare(test_db)
    _task(test_db, "genuinely-running", "running")
    assert test_client.post(ENDPOINT, json=BODY).json()["pending"] is True

    assert ai.sweep_pending_activation(test_db) is False
    assert test_client.get(ENDPOINT).json()["provider_id"] == "ollama-cloud"


def test_sweep_promotes_once_running_task_finishes(
    test_client, test_db, isolated,
):
    prepare(test_db)
    task = Task(id="was-running", original_request="Existing task", state="running")
    test_db.add(task)
    test_db.commit()
    assert test_client.post(ENDPOINT, json=BODY).json()["pending"] is True
    assert ai.sweep_pending_activation(test_db) is False

    task.state = "completed"
    test_db.commit()
    assert ai.sweep_pending_activation(test_db) is True
    assert test_client.get(ENDPOINT).json() == {**BODY, "permissions_unchanged": True}


# (c) One-shot and fail-closed.


def test_sweep_never_applies_twice(test_client, test_db, isolated):
    prepare(test_db)
    _task(test_db, "stuck-created", "created")
    assert test_client.post(ENDPOINT, json=BODY).json()["pending"] is True

    assert ai.sweep_pending_activation(test_db) is True
    # Clearing the pending record makes a second promotion a no-op.
    assert ai.sweep_pending_activation(test_db) is False
    assert test_client.get(ENDPOINT).json() == {**BODY, "permissions_unchanged": True}


def test_sweep_fails_closed_on_damaged_pending_record(
    test_client, test_db, isolated,
):
    prepare(test_db)
    _task(test_db, "stuck-created", "created")
    assert test_client.post(ENDPOINT, json=BODY).json()["pending"] is True

    # Corrupt the credential binding after it was queued.
    state = profile.load_profile()
    state[ai._PENDING_ACTIVATION_KEY]["mac"] = "0" * 64
    profile.save_profile(state)

    assert ai.sweep_pending_activation(test_db) is False
    # The terminal path fails closed on the same damaged record too.
    assert ai.maybe_apply_pending_activation(test_db) is False
    assert test_client.get(ENDPOINT).json()["provider_id"] == "ollama-cloud"


def test_sweep_fails_closed_on_ambiguous_records(test_client, test_db, isolated):
    prepare(test_db)
    _task(test_db, "stuck-created", "created")
    assert test_client.post(ENDPOINT, json=BODY).json()["pending"] is True

    # Both kinds present at once = external damage; refuse to guess intent.
    profile.save_profile({
        ai._PENDING_OLLAMA_KEY: {
            "version": 1, "model_id": "gemma4:31b", "kind": "restore",
        },
    })
    assert ai.sweep_pending_activation(test_db) is False
    assert test_client.get(ENDPOINT).json()["provider_id"] == "ollama-cloud"


# The autonomous, app-lifespan path: one db-manager-driven cycle offloaded to a
# worker thread, and the bounded loop itself — both with no owner action.


@pytest.mark.asyncio
async def test_manager_driven_sweep_cycle_promotes_without_owner_action(
    test_client, test_db, isolated,
):
    prepare(test_db)
    _task(test_db, "stuck-created", "created")
    assert test_client.post(ENDPOINT, json=BODY).json()["pending"] is True

    promoted = await ai.sweep_pending_activation_once(
        _SharedSessionManager(test_db),
    )
    assert promoted is True
    assert test_client.get(ENDPOINT).json() == {**BODY, "permissions_unchanged": True}


@pytest.mark.asyncio
async def test_background_loop_promotes_without_owner_action(
    test_client, test_db, isolated,
):
    prepare(test_db)
    _task(test_db, "stuck-created", "created")
    assert test_client.post(ENDPOINT, json=BODY).json()["pending"] is True

    loop = asyncio.create_task(
        ai.pending_activation_sweeper(
            _SharedSessionManager(test_db), interval_seconds=0.01,
        ),
        name="test-pending-activation-sweep",
    )
    try:
        promoted = False
        for _ in range(100):
            try:
                if ai.get_active_inference_selection().provider_id == "openrouter":
                    promoted = True
                    break
            except Exception:
                pass
            await asyncio.sleep(0.02)
        assert promoted, "the background sweep never promoted the pending choice"
    finally:
        loop.cancel()
        with pytest.raises(asyncio.CancelledError):
            await loop

    assert test_client.get(ENDPOINT).json() == {**BODY, "permissions_unchanged": True}


# (d) The original terminal-transition apply path is still intact.


@pytest.mark.asyncio
async def test_terminal_transition_still_applies_pending_activation(
    test_client, test_db, isolated, task_runner,
):
    from backend.api.schemas import TaskState

    prepare(test_db)
    task = await task_runner.create_task("In-flight task")
    await task_runner.transition_state(
        task.id, TaskState.RUNNING, "task.execution_started", {},
    )
    assert test_client.post(ENDPOINT, json=BODY).json()["pending"] is True
    assert test_client.get(ENDPOINT).json()["provider_id"] == "ollama-cloud"

    await task_runner.transition_state(
        task.id, TaskState.COMPLETED, "task.execution_completed", {},
    )
    assert test_client.get(ENDPOINT).json() == {**BODY, "permissions_unchanged": True}
