"""Regression: the vault write-guard protects every test, not just acceptance/.

Lives outside ``backend/tests/acceptance/`` so it is only covered by the root
conftest autouse fixture. If the guard were (still) acceptance-only, the
product-prefixed write below would reach ``win32cred`` instead of raising.
"""

import pytest

try:
    import win32cred
except ImportError:  # pragma: no cover - non-Windows hosts
    win32cred = None

from backend.tests.conftest import ProductCredentialWriteAttempt

_PRODUCT = "Sythion (Synth)/ollama_api_key"


def test_vault_guard_blocks_product_write_at_root_scope():
    if win32cred is None:
        pytest.skip("pywin32 not available")

    cred = {
        "Type": win32cred.CRED_TYPE_GENERIC,
        "TargetName": _PRODUCT,
        "CredentialBlob": b"x",
        "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE,
        "UserName": "",
        "Comment": "root-scope probe",
    }

    try:
        with pytest.raises(ProductCredentialWriteAttempt):
            win32cred.CredWrite(cred, 0)
        # Case-insensitive: the lower-cased spelling addresses the same slot.
        cred["TargetName"] = "sythion (synth)/ollama_api_key"
        with pytest.raises(ProductCredentialWriteAttempt):
            win32cred.CredWrite(cred, 0)
    finally:
        # Defensive cleanup; the guard prevents any real write, so this is a
        # no-op in the happy path, but it must never mask the test result.
        try:
            win32cred.CredDelete(_PRODUCT, win32cred.CRED_TYPE_GENERIC, 0)
        except Exception:
            pass
    # Delete is guarded too.
    with pytest.raises(ProductCredentialWriteAttempt):
        win32cred.CredDelete(_PRODUCT, win32cred.CRED_TYPE_GENERIC, 0)
