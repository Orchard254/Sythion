"""Autonomous pending-activation sweep — closes the stuck-CREATED hole.

Zero external network calls: evidence is synthetic, secrets are in-memory, and
the profile lives under a temporary LOCALAPPDATA. The sweep only ever mutates
local backend state and never dispatches a task or sends an inference request.
"""
import asyncio
import threading

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


# --- (a) Queue writes share the apply lock; a queue cannot land in the apply
#         read -> validate -> clear window and be silently overwritten. --------


def _pending_write_kind(patch):
    """Which pending record a profile.save_profile patch would write, if any."""
    if isinstance(patch, dict):
        if isinstance(patch.get(ai._PENDING_ACTIVATION_KEY), dict):
            return "cloud"
        if isinstance(patch.get(ai._PENDING_OLLAMA_KEY), dict):
            return "ollama"
    return None


@pytest.mark.parametrize("kind", ["cloud", "ollama"])
def test_queue_write_is_serialized_with_apply_lock(kind, isolated, monkeypatch):
    """A queue cannot write pending state while an apply holds the lock.

    This is the exact window that used to lose the newest owner choice: apply
    reads pending, then (holding the lock) validates/checks/clears it. A queue
    landing there must wait for the lock so it lands AFTER the clear and
    survives for the next sweep instead of being silently overwritten.
    """
    started = threading.Event()
    wrote = threading.Event()
    errors = []
    original_save = profile.save_profile

    def guarded_save(patch, *a, **kw):
        if _pending_write_kind(patch) == kind:
            wrote.set()
        return original_save(patch, *a, **kw)

    monkeypatch.setattr(profile, "save_profile", guarded_save)

    if kind == "cloud":
        # Bypass evidence checks; only the serialized write is under test.
        binding = {
            "version": 1,
            "provider_id": "openrouter",
            "model_id": "unit/model:free",
            "nonce": "a" * 32,
            "mac": ai._active_mac(
                "openrouter", "unit/model:free", "a" * 32, "synthetic-key-not-real",
            ),
        }
        monkeypatch.setattr(ai, "_build_verified_binding", lambda *a, **kw: dict(binding))

    def run():
        try:
            started.set()
            if kind == "cloud":
                ai.queue_pending_activation(None, "openrouter", "unit/model:free")
            else:
                ai.queue_pending_ollama_activation(
                    None, model_id="gemma4:31b", kind="restore",
                )
        except BaseException as exc:  # surface thread failures in the assertion
            errors.append(exc)

    with ai._PENDING_APPLY_LOCK:
        thread = threading.Thread(target=run, name="queue-%s" % kind)
        thread.start()
        assert started.wait(5), "queue thread never started"
        assert not wrote.wait(0.2), (
            "queue wrote pending state while the apply lock was held"
        )
    assert wrote.wait(5), "queue never wrote after the lock was released"
    thread.join(5)
    assert not thread.is_alive()
    assert not errors, errors

    state = profile.load_profile()
    if kind == "cloud":
        assert state[ai._PENDING_ACTIVATION_KEY]["nonce"] == "a" * 32
    else:
        assert state[ai._PENDING_OLLAMA_KEY]["model_id"] == "gemma4:31b"


def test_apply_holds_lock_during_its_pending_clear(isolated, monkeypatch):
    """The apply side writes its pending clear while holding the same lock."""
    held = []
    original_save = profile.save_profile

    def guarded_save(patch, *a, **kw):
        if ("active_inference_selection" in patch
                and ai._PENDING_ACTIVATION_KEY in patch):
            held.append(ai._PENDING_APPLY_LOCK._is_owned())
        return original_save(patch, *a, **kw)

    monkeypatch.setattr(profile, "save_profile", guarded_save)
    profile.save_profile({
        ai._PENDING_ACTIVATION_KEY: {
            "version": 1, "provider_id": "openrouter", "model_id": "unit/model:free",
            "nonce": "a" * 32,
            "mac": ai._active_mac(
                "openrouter", "unit/model:free", "a" * 32, "synthetic-key-not-real",
            ),
        },
    })
    # A fake DB that reports no blocking task; the binding is valid against the
    # synthetic key stored below, so the apply path reaches its clear write.
    secrets.set_secret(secrets.OPENROUTER_API_KEY, "synthetic-key-not-real")

    class _IdleDb:
        def query(self, *a, **kw):
            class _Q:
                def filter(self, *a, **kw):
                    return self

                def first(self):
                    return None
            return _Q()

    assert ai.sweep_pending_activation(_IdleDb()) is True
    assert held and all(held), "apply cleared pending without holding the lock"


# --- (b) Credential rotation is observable and fails closed. -----------------


def test_rotated_credential_is_reported_blocked_not_applied(
    test_client, test_db, isolated,
):
    prepare(test_db)
    ai.queue_pending_activation(test_db, BODY["provider_id"], BODY["model_id"])
    assert ai.pending_activation_status(test_db) == "ready"
    record_before = profile.load_profile()[ai._PENDING_ACTIVATION_KEY]

    # The credential rotates between the queue and the apply.
    secrets.set_secret(secrets.OPENROUTER_API_KEY, "rotated-key")
    assert ai.pending_activation_status(test_db) == "blocked_credential"

    # Both apply triggers fail closed and keep the verified choice intact.
    assert ai.maybe_apply_pending_activation(test_db) is False
    assert ai.sweep_pending_activation(test_db) is False
    assert test_client.get(ENDPOINT).json()["provider_id"] == "ollama-cloud"
    assert profile.load_profile()[ai._PENDING_ACTIVATION_KEY] == record_before

    # Restoring the original key makes the SAME choice promotable again: it was
    # neither applied against the wrong key nor silently cleared.
    secrets.set_secret(secrets.OPENROUTER_API_KEY, "synthetic-key-not-real")
    assert ai.pending_activation_status(test_db) == "ready"
    assert ai.sweep_pending_activation(test_db) is True
    assert test_client.get(ENDPOINT).json() == {**BODY, "permissions_unchanged": True}


def test_pending_activation_status_states(test_client, test_db, isolated):
    assert ai.pending_activation_status(test_db) == "none"
    assert ai.pending_activation_status() == "none"

    ai.queue_pending_ollama_activation(None, model_id="gemma4:31b", kind="restore")
    assert ai.pending_activation_status(test_db) == "ready"
    # Ollama has no credential binding; no MAC or secret is ever surfaced.
    assert ai.pending_activation_status(test_db) in ai.PENDING_ACTIVATION_STATES

    # A structurally malformed cloud record is damage, not a credential block.
    profile.save_profile({
        ai._PENDING_ACTIVATION_KEY: {"version": 1, "mac": "0" * 64},
    })
    assert ai.pending_activation_status(test_db) == "damaged"


@pytest.mark.parametrize(
    "state,expected",
    [("running", "blocked_in_flight"), ("created", "ready")],
)
def test_status_uses_sweep_running_only_rule(
    test_client, test_db, isolated, state, expected,
):
    """A RUNNING task blocks; a CREATED one does not (the sweep can promote)."""
    prepare(test_db)
    _task(test_db, "t", state)
    assert test_client.post(ENDPOINT, json=BODY).json()["pending"] is True
    assert ai.pending_activation_status(test_db) == expected
