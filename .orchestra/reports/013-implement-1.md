VERDICT: ACCEPT

# 013 Implement: relocate vault write-guard into root conftest

## Discovery
- Guard block (ProductCredentialWriteAttempt, `_PRODUCT_TARGET_PREFIX` / `_PRODUCT_TARGET_PREFIX_LOWER`, `_forbid_product_credential_writes` and helpers `_reject`, `_target_of`, `guarded_write`, `guarded_delete`) moved verbatim from `backend/tests/acceptance/conftest.py` to `backend/tests/conftest.py`, with a guarded `secrets` import added at root (try `backend.core` then `core`).
- Removed the guard and the now-unused `from backend.core import secrets as secrets_module` from `backend/tests/acceptance/conftest.py`; acceptance-only fixtures (`db_session`, `app`, `client`, `acceptance_control_token`, etc.) remain.
- Added `backend/tests/test_vault_guard_root_scope.py` (outside acceptance/) proving root-scope coverage, including the case-insensitive product-prefix variant and a guarded delete.

## Behavior
- Import-time snapshot of `secrets_module._CM_TARGET_PREFIX` preserved, so the round-trip test's monkeypatch does not disarm the guard.
- `CredRead` remains unintercepted; only `CredWrite`/`CredDelete` are wrapped. Fail-closed on unreadable targets.
- `import win32cred` stays guarded by try/except ImportError inside the fixture.

## Evidence
- `python -m pytest --collect-only -q` => 1616 tests collected (baseline 1615; +1 new guard test).
- `python -m pytest backend/tests/test_vault_guard_root_scope.py -q` => 1 passed (vault operations gated).
- `python -m pytest -q -k 'vault or guard or readiness'` => 23 passed, 1593 deselected.
- Negative-control (without acceptance conftest loaded): `pytest backend/tests/test_vault_guard_root_scope.py -q --ignore=backend/tests/acceptance` => 1 passed.
- classify.py => SENSITIVE (exit 2) as expected for a guard relocation touching root conftest.

## Diff (tracked)
```
 backend/tests/acceptance/conftest.py | 124 ---------------------------------
 backend/tests/conftest.py            | 129 +++++++++++++++++++++++++++++++++++
 2 files changed, 129 insertions(+), 124 deletions(-)
```
Plus new files: `backend/tests/test_vault_guard_root_scope.py`, `.orchestra/plans/013-plan-1.md`, this report.

## Scope check
Change is S-sized (test/conftest only; no production code). Allowed paths only.
