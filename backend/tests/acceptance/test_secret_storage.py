"""
Acceptance tests for the write-only secret storage policy.

Maps to README.md SEC-2 (No secrets in SQLite; Secrets in
Credential Manager mock) and the architecture rule that secrets never enter
.env, SQLite, logs, task events, exports, or model context.

Most of these tests run against the in-memory secret backend. The exception is
``test_credential_manager_round_trip``, which drives the REAL Windows
Credential Manager store - but against a TEST-SCOPED target
(``Sythion (Synth) [TEST]/...``), never the product's own
``Sythion (Synth)/ollama_api_key``. ``conftest.py`` installs a guard that
fails any acceptance test attempting to write to or delete a product-prefixed
credential target.
"""

import pytest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.core import secrets as secrets_module
from backend.core.secrets import MemoryStore
from backend.core.config import settings as app_settings
from backend.adapters.ollama_adapter import OllamaCloudAdapter

from .conftest import ACCEPTANCE_CONTROL_TOKEN

SECRET_NAME = "ollama_api_key"

# Snapshot of the PRODUCT credential target prefix, taken at import time. The
# round-trip test below monkeypatches ``secrets_module._CM_TARGET_PREFIX``, so
# this MUST be a snapshot: comparing a composed target against the *patched*
# attribute would be a tautology.
_PRODUCT_TARGET_PREFIX = secrets_module._CM_TARGET_PREFIX

# Test-scoped prefix. It is deliberately NOT under the product prefix - the
# character after "Sythion (Synth)" is a space, not a "/" - and it is visibly
# test-scoped to a human browsing the Windows Credential Manager.
_TEST_TARGET_PREFIX = "Sythion (Synth) [TEST]/"


@pytest.fixture
def memory_secret_store():
    """Force the in-memory secret backend for every test."""
    store = MemoryStore()
    secrets_module.set_secret_store_for_testing(store)
    yield store
    secrets_module.set_secret_store_for_testing(None)


def _headers(**extra):
    return {"X-Control-Token": ACCEPTANCE_CONTROL_TOKEN, **extra}


# --- Write-only contract ----------------------------------------------------


def test_secret_put_never_returns_value(client, memory_secret_store):
    resp = client.put(
        f"/api/v1/secrets/{SECRET_NAME}",
        json={"value": "sk-test-1234567890"},
        headers=_headers(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is True
    assert body["name"] == SECRET_NAME
    # Write-only: the response must not echo the stored value.
    assert "value" not in body
    assert "sk-test" not in str(body)


def test_secret_value_visible_in_store_only(client, memory_secret_store):
    client.put(
        f"/api/v1/secrets/{SECRET_NAME}",
        json={"value": "sk-keep-me-secret"},
        headers=_headers(),
    )
    # The value is present in the secret store...
    assert memory_secret_store.get(SECRET_NAME) == "sk-keep-me-secret"
    # ...but the list endpoint reports only names, never values.
    resp = client.get("/api/v1/secrets", headers=_headers())
    assert resp.status_code == 200
    assert resp.json() == {"configured": [SECRET_NAME]}
    assert "sk-keep-me-secret" not in str(resp.json())


def test_secret_delete_removes_and_reports_unconfigured(client, memory_secret_store):
    client.put(
        f"/api/v1/secrets/{SECRET_NAME}",
        json={"value": "sk-to-delete"},
        headers=_headers(),
    )
    resp = client.delete(f"/api/v1/secrets/{SECRET_NAME}", headers=_headers())
    assert resp.status_code == 200
    assert resp.json() == {"configured": False, "name": SECRET_NAME}
    assert memory_secret_store.get(SECRET_NAME) == ""
    assert client.get("/api/v1/secrets", headers=_headers()).json() == {"configured": []}


def test_unknown_secret_name_rejected(client, memory_secret_store):
    resp = client.put(
        "/api/v1/secrets/database_password",
        json={"value": "hunter2"},
        headers=_headers(),
    )
    assert resp.status_code == 404


def test_secret_endpoints_require_auth(client, memory_secret_store):
    resp = client.put(
        f"/api/v1/secrets/{SECRET_NAME}", json={"value": "sk-x"}, headers={}
    )
    assert resp.status_code == 401
    resp = client.get("/api/v1/secrets", headers={})
    assert resp.status_code == 401


# --- Key resolution from the secret store -----------------------------------


def test_adapter_resolves_key_from_secret_store(memory_secret_store):
    memory_secret_store.set(SECRET_NAME, "sk-from-vault")
    with patch.object(app_settings, "ollama_api_key", ""):
        adapter = OllamaCloudAdapter()
    assert adapter.api_key == "sk-from-vault"


def test_environment_override_wins_over_store(memory_secret_store):
    memory_secret_store.set(SECRET_NAME, "sk-from-vault")
    with patch.object(app_settings, "ollama_api_key", "sk-from-env"):
        adapter = OllamaCloudAdapter()
    assert adapter.api_key == "sk-from-env"


def test_resolve_returns_empty_when_unset(memory_secret_store):
    assert secrets_module.resolve_ollama_api_key("") == ""


# --- Real Credential Manager round trip (Windows only) -----------------------


def test_credential_manager_round_trip(monkeypatch):
    """The production backend writes/reads via win32cred when available.

    Skipped automatically on machines without pywin32; on Windows this verifies
    the real vault round trip against a TEST-SCOPED target
    (``Sythion (Synth) [TEST]/ollama_api_key``) - never the product's own
    ``Sythion (Synth)/ollama_api_key`` - then cleans up after itself.
    """
    try:
        import win32cred
    except ImportError:
        pytest.skip("pywin32 not available")

    # Pin the target prefix for the duration of this test. ``_cm_target()``
    # reads the module global at call time, so the REAL win32cred store is
    # still exercised - only the target NAME changes.
    store = secrets_module.CredentialManagerStore(_TEST_TARGET_PREFIX)
    # Pin the active store so ``has_secret()`` / ``list_secret_names()`` answer
    # from the real Credential Manager instead of a MemoryStore that another
    # test may have left behind. ``monkeypatch`` restores the previous value.
    monkeypatch.setattr(secrets_module, "_store", store)

    # The composed target must be the test-scoped one AND must not sit under
    # the PRODUCT prefix (asserted against the import-time snapshot, not
    # against any mutable module global).
    target = secrets_module._cm_target(SECRET_NAME, _TEST_TARGET_PREFIX)
    assert target == "Sythion (Synth) [TEST]/ollama_api_key"
    assert not target.startswith(_PRODUCT_TARGET_PREFIX)

    store.set(SECRET_NAME, "sk-roundtrip-zz")
    try:
        assert store.get(SECRET_NAME) == "sk-roundtrip-zz"
        assert secrets_module.has_secret(SECRET_NAME) is True
        assert SECRET_NAME in secrets_module.list_secret_names()

        # Read-only self-check (anti-mock tripwire): the credential must really
        # be in the REAL vault. A MemoryStore, a mocked ``win32cred`` or a
        # write that never happened all fail here. Only target NAMES are
        # inspected - no credential blob is read.
        #
        # The enumeration is deliberately UNFILTERED and filtered here.
        # Measured on this machine: a PARTIAL prefix - a directory-style
        # prefix of a credential that demonstrably exists included - makes
        # ``CredEnumerate(prefix, 0)`` return winerror 1168, whereas the
        # EXACT-MATCH form ``CredEnumerate(<exact target name>, 0)`` works
        # for every existing credential. The unfiltered form is used because
        # it cannot be defeated by either quirk, and it really does see the
        # entry, which is what makes an "absent" observation non-vacuous.
        try:
            enumerated = win32cred.CredEnumerate(None, 0)
        except Exception as exc:  # pywintypes.error 1168 when nothing matches
            pytest.fail(
                "the test-scoped credential was not found in the real Windows "
                f"Credential Manager: {exc!r}"
            )
        assert target in [entry.get("TargetName") for entry in enumerated]
    finally:
        store.delete(SECRET_NAME)
    assert store.get(SECRET_NAME) == ""
    assert secrets_module.has_secret(SECRET_NAME) is False


# --- Secrets never enter the database ---------------------------------------


def test_secret_not_persisted_in_database(client, memory_secret_store, db_session):
    client.put(
        f"/api/v1/secrets/{SECRET_NAME}",
        json={"value": "sk-no-sqlite"},
        headers=_headers(),
    )
    # Scan every text column of every table for the secret value.
    from sqlalchemy import inspect, text
    from backend.models.database import Base

    bind = db_session.get_bind()
    inspector = inspect(bind)
    for table in inspector.get_table_names():
        cols = [c["name"] for c in inspector.get_columns(table)
                if c["type"].__class__.__name__.lower() in ("varchar", "text", "string", "char")]
        if not cols:
            continue
        for col in cols:
            rows = db_session.execute(text(f'SELECT "{col}" FROM "{table}"')).fetchall()
            for row in rows:
                assert "sk-no-sqlite" not in str(row), (
                    f"secret leaked into {table}.{col}"
                )
