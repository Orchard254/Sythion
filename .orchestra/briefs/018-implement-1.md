# Brief: implement - 018 - attempt 1

- Item: 018 The vault-write guard must not be swallowed by the FastAPI global exception handler (R-026)
- Role: implementer
- Goal: Implement `plans/018.md` exactly - the vault-write-violation marker
  contract - so a blocked product-credential write through the HTTP layer
  surfaces as a documented 409 `VAULT_WRITE_BLOCKED` instead of an
  indistinguishable generic 500, while the generic 500 contract for genuine
  faults is preserved byte for byte.

## Read

- `C:\Sythion\sythion\.orchestra\plans\018.md` - the plan (read all of it; the
  "How it will be proven" and "Risk" sections are binding).
- `C:\Sythion\sythion\backend\main.py:265-290` - the handler.
- `C:\Sythion\sythion\backend\tests\conftest.py:622-742` - the guard.
- `C:\Sythion\sythion\backend\tests\test_vault_guard_root_scope.py` - the
  existing regression test that must keep passing unchanged.

## Branch / worktree

- Branch: `orchestra/018-vault-guard-surface`, from `master` (29bb243).
- Worktree: `C:\Users\orcha\AppData\Local\Temp\opencode\wt-018`.
- Small commits on the branch. Do NOT push. Do not touch master directly.

## Write (code) - exactly three files

1. `backend/main.py` - add `VAULT_WRITE_VIOLATION_ATTR = "sythion_vault_write_violation"`,
   `VAULT_WRITE_VIOLATION_STATUS = 409`, `VAULT_WRITE_VIOLATION_ERROR = "VAULT_WRITE_BLOCKED"`
   as module-level constants, and ONE branch in `global_exception_handler` BEFORE the
   existing 500 branch that answers a marker-carrying exception with the 409 body.
   The existing 500 branch stays byte-identical.
2. `backend/tests/conftest.py` - declare the marker attribute on
   `ProductCredentialWriteAttempt` (class-level), and replace the docstring
   paragraph that says the violation would be an indistinguishable 500 with the
   new contract. Change NOTHING else in the guard.
3. `backend/tests/test_vault_guard_exception_surfaces.py` (NEW) - the four tests
   the plan names.

## MANDATORY SAFETY REQUIREMENT (read twice)

The new test deliberately drives a write at the PRODUCT target
`Sythion (Synth)/ollama_api_key`. It is only safe because the guard blocks it
BEFORE the `win32cred` call. Therefore:

- While developing and running the new test, REPLACE `win32cred.CredWrite` and
  `win32cred.CredDelete` with sentinels BEFORE the conftest's guard is
  installed, so the guard's captured "original write" IS the sentinel and the
  real vault is unreachable. This is the exact technique the plan's author used.
  The sentinel must be reached ZERO times in your runs - report the count.
- Do NOT run the new test against the real vault even once, "just to check".
- Do not write to or delete from the real Windows Credential Manager, ever.
- The test must restore any monkeypatched state (monkeypatch handles `_store`;
  restore `backend.main.control_token` in a `finally`).

## Write (report)

- `C:\Sythion\sythion\.orchestra\reports\018-implement-1.md` - files changed,
  commit sha, test output pasted, sentinel-reached count, and the statement of
  what was NOT checked.

## Constraints

- `git diff --name-only` must list EXACTLY the three files above. No
  `backend/api/`, `backend/core/`, `backend/models/`, `backend/migrations/`,
  `src-tauri/`, `docs/` or `frontend/` file may appear.
- Do not update the vendored mirror `src-tauri/resources/backend/main.py` -
  criterion 4 fixes the file set. Record the drift in the report.
- Run: the new test file, `backend/tests/test_vault_guard_root_scope.py`,
  `backend/tests/acceptance/test_secret_storage.py`, and any test that imports
  `backend.main`. Paste the output.
- No destructive commands, no push, no deploy.

## Output contract

Report at `.orchestra/reports/018-implement-1.md` including the commit sha.
Reply with a short summary: what changed, test results, sentinel count, and
anything you could not determine. The FILE is the deliverable.