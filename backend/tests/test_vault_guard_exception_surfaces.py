"""Item 018: a blocked product-vault write must not be swallowed by the
FastAPI global exception handler (R-026).

The contract under test - the "vault-write-violation marker contract":

    ``backend/main.py`` defines ``VAULT_WRITE_VIOLATION_ATTR`` and treats an
    exception as a vault-write violation exactly when it exposes a truthy
    attribute of that name. The global exception handler maps such an
    exception to HTTP 409 with the body
    ``{"error": "VAULT_WRITE_BLOCKED", "detail": <message>, "code": "409"}``.
    Every other exception keeps the generic 500 response, byte for byte.
    ``backend/tests/conftest.py``'s ``ProductCredentialWriteAttempt`` declares
    that attribute. Neither side imports the other.

Safety
-------
These tests deliberately drive a write at the PRODUCT target
``Sythion (Synth)/ollama_api_key``. That is safe because the conftest guard
blocks it BEFORE the ``win32cred`` call. Three independent proofs are
asserted, so the test cannot pass vacuously:

* the ``CredWrite`` spy records the attempted target and the guard's message
  names the product target (the write really was attempted, and it was
  blocked);
* a read-only fingerprint of the REAL product credential (presence plus a
  SHA-256 of the blob, never the blob itself) is identical before and after;
* the sentinel mode below makes the real Windows Credential Manager
  completely unreachable for the run.

Sentinel mode
-------------
Set ``SYTHION_VAULT_SENTINEL=1`` to replace ``win32cred.CredWrite`` and
``win32cred.CredDelete`` with recording sentinels AT IMPORT TIME of this
module - i.e. BEFORE the conftest guard fixture is installed, so the guard's
captured "original write" IS the sentinel and the real vault is unreachable.
The sentinel must be reached ZERO times; the autouse fixture below asserts
that after every test.

Sentinel mode is opt-in because a session that also contains the real-vault
round trip (``backend/tests/acceptance/test_secret_storage.py``) legitimately
needs the real API. Run the sentinel-armed session on this file alone.
"""

import contextlib
import hashlib
import os

import pytest

try:
    import win32cred
except ImportError:  # pragma: no cover - non-Windows hosts
    win32cred = None

from fastapi.testclient import TestClient

import backend.main as main_module
from backend.core import secrets as secrets_module
from backend.tests.conftest import ProductCredentialWriteAttempt


_PRODUCT_TARGET = "Sythion (Synth)/ollama_api_key"
_SECRET_NAME = "ollama_api_key"
_CONTROL_TOKEN = "vault-guard-surface-control-token"

# Every sentinel hit is recorded here. The list must stay EMPTY: a hit means
# the guard failed to block a write before the win32cred call.
_SENTINEL_HITS = []
_SENTINELS_ARMED = False


def _target_of(cred):
    """The target name of a credential dict, or None if it cannot be read."""
    getter = getattr(cred, "get", None)
    if getter is None:
        return None
    try:
        target = getter("TargetName", None)
    except Exception:
        return None
    return target if isinstance(target, str) else None


def _install_vault_sentinels():
    """Replace the real win32cred write/delete API with recording sentinels.

    Called at import time (before any fixture runs), so the conftest guard
    captures the SENTINEL as the "original" API it would delegate to. The
    real Windows Credential Manager is therefore unreachable for the run.
    """
    global _SENTINELS_ARMED

    def sentinel_write(cred, flags=0):
        target = _target_of(cred)
        _SENTINEL_HITS.append(("write", target))
        raise AssertionError(
            "vault sentinel reached: win32cred.CredWrite was called with "
            f"target {target!r}. The guard must block every "
            "product-credential write BEFORE the win32cred call."
        )

    def sentinel_delete(target, *args, **kwargs):
        _SENTINEL_HITS.append(("delete", target))
        raise AssertionError(
            "vault sentinel reached: win32cred.CredDelete was called with "
            f"target {target!r}. The guard must block every "
            "product-credential delete BEFORE the win32cred call."
        )

    win32cred.CredWrite = sentinel_write
    win32cred.CredDelete = sentinel_delete
    _SENTINELS_ARMED = True


if win32cred is not None and os.environ.get("SYTHION_VAULT_SENTINEL", "").strip() == "1":
    _install_vault_sentinels()


@pytest.fixture(autouse=True)
def _sentinel_never_reached():
    """Fail the test if the vault sentinel was reached during it."""
    baseline = list(_SENTINEL_HITS)
    yield
    reached = _SENTINEL_HITS[len(baseline):]
    print(
        f"[vault-sentinel] armed={_SENTINELS_ARMED} reached={len(reached)} "
        f"hits={reached!r}"
    )
    assert reached == [], (
        "the vault sentinel standing in for the real Windows Credential "
        f"Manager was reached {len(reached)} time(s): {reached!r}"
    )


def _vault_fingerprint(target):
    """Read-only fingerprint of a REAL credential: (present?, sha256 of blob).

    ``CredRead`` is deliberately NOT intercepted by the guard, so this is a
    legal, non-destructive observation. The blob itself is never returned -
    only its digest.
    """
    if win32cred is None:
        return None
    try:
        cred = win32cred.CredRead(target, win32cred.CRED_TYPE_GENERIC)
    except Exception:
        # No such credential (or it cannot be read): "absent" is the answer.
        return ("absent", None)
    blob = cred.get("CredentialBlob", b"") or b""
    if isinstance(blob, str):
        blob = blob.encode("utf-16-le", errors="replace")
    return ("present", hashlib.sha256(bytes(blob)).hexdigest())


@contextlib.contextmanager
def _control_token_installed():
    """Give the app a known control token, and put the old one back."""
    previous = main_module.control_token
    main_module.control_token = _CONTROL_TOKEN
    try:
        yield
    finally:
        main_module.control_token = previous


def _client(raise_server_exceptions):
    """A TestClient over the REAL app, with the control-boundary headers.

    The client is used WITHOUT ``with``, so the app's lifespan never runs:
    no profile lock is taken and no database is opened.
    """
    return TestClient(
        main_module.app,
        raise_server_exceptions=raise_server_exceptions,
        headers={
            "Origin": "tauri://localhost",
            "X-Control-Token": _CONTROL_TOKEN,
        },
    )


def _put_secret(client):
    """Drive the EXISTING production route (no route is added or changed)."""
    return client.put(
        f"/api/v1/secrets/{_SECRET_NAME}",
        json={"value": "sk-blocked-product-write"},
    )


def test_product_vault_write_through_http_surfaces_as_blocked_not_generic_500(monkeypatch):
    """A blocked product-credential write must surface as 409, not as a 500."""
    if win32cred is None:
        pytest.skip("pywin32 not available")

    before = _vault_fingerprint(_PRODUCT_TARGET)

    # Pin the active store to the REAL CredentialManagerStore, so the composed
    # target really is "Sythion (Synth)/ollama_api_key" and the guard really
    # is engaged.
    monkeypatch.setattr(
        secrets_module, "_store", secrets_module.CredentialManagerStore()
    )

    # Spy around the ALREADY-GUARDED win32cred.CredWrite: it records every
    # attempted target, then delegates to the guard. The guard raises before
    # the sentinel/real API is reached.
    attempted = []
    guarded_write = win32cred.CredWrite

    def spy_write(cred, flags=0):
        attempted.append(_target_of(cred))
        return guarded_write(cred, flags)

    monkeypatch.setattr(win32cred, "CredWrite", spy_write)

    with _control_token_installed():
        client = _client(raise_server_exceptions=False)
        response = _put_secret(client)

    body = response.json()
    assert response.status_code == 409, (
        "expected the blocked vault write to surface as 409 "
        f"VAULT_WRITE_BLOCKED, got {response.status_code} "
        f"{body.get('error')!r}"
    )
    assert body["error"] == "VAULT_WRITE_BLOCKED"
    assert body["code"] == "409"
    assert _PRODUCT_TARGET in body["detail"]
    assert response.status_code != 500
    assert not (200 <= response.status_code < 300)

    # The request really did try to write the product target ...
    assert attempted == [_PRODUCT_TARGET]
    # ... and the real vault was not modified.
    assert _vault_fingerprint(_PRODUCT_TARGET) == before


def test_genuine_fault_on_the_same_route_is_still_a_generic_500(monkeypatch):
    """The 500 contract is preserved: a genuine fault is still a generic 500."""

    class _FailingStore:
        """A store whose ``set()`` fails - a genuine, unmarked fault."""

        def get(self, name):
            return ""

        def set(self, name, value):
            raise RuntimeError("simulated vault failure")

        def delete(self, name):
            return None

    monkeypatch.setattr(secrets_module, "_store", _FailingStore())

    with _control_token_installed():
        client = _client(raise_server_exceptions=False)
        response = _put_secret(client)

    body = response.json()
    assert response.status_code == 500
    assert body["error"] == "INTERNAL_ERROR"
    assert body["code"] == "500"
    assert body["detail"] == "simulated vault failure"


def test_vault_write_violation_still_raises_out_of_a_default_test_client(monkeypatch):
    """The guard keeps its teeth: the violation still propagates."""
    if win32cred is None:
        pytest.skip("pywin32 not available")

    monkeypatch.setattr(
        secrets_module, "_store", secrets_module.CredentialManagerStore()
    )

    with _control_token_installed():
        client = _client(raise_server_exceptions=True)
        with pytest.raises(ProductCredentialWriteAttempt):
            _put_secret(client)


def test_violation_marker_contract_is_shared_by_handler_and_guard():
    """The drift pin: both sides spell the marker the same way."""
    assert main_module.VAULT_WRITE_VIOLATION_ATTR == "sythion_vault_write_violation"
    assert main_module.VAULT_WRITE_VIOLATION_STATUS == 409
    assert main_module.VAULT_WRITE_VIOLATION_ERROR == "VAULT_WRITE_BLOCKED"

    # The guard declares EXACTLY that attribute name, at class level.
    assert main_module.VAULT_WRITE_VIOLATION_ATTR in vars(ProductCredentialWriteAttempt)
    assert getattr(ProductCredentialWriteAttempt, main_module.VAULT_WRITE_VIOLATION_ATTR) is True

    violation = ProductCredentialWriteAttempt("blocked write")
    assert getattr(violation, main_module.VAULT_WRITE_VIOLATION_ATTR, False) is True

    # An ordinary exception does NOT carry the marker, so the handler's
    # violation branch can never fire by accident.
    assert getattr(RuntimeError("ordinary"), main_module.VAULT_WRITE_VIOLATION_ATTR, False) is False
    assert getattr(ValueError("ordinary"), main_module.VAULT_WRITE_VIOLATION_ATTR, False) is False
