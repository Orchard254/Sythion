# Brief: implement - 018 - round 2 (documentation-only correction)

- Item: 018 The vault-write guard must not be swallowed by the FastAPI global exception handler
- Role: implementer (round 2)
- Branch: `orchestra/018-vault-guard-surface` (EXISTING - continue on it)
- Base of this round: `70010b3` (the commit the reviewer reviewed)
- Goal: fix the reviewer's two documentation findings. NO code, contract, test or
  behaviour change is required or permitted in this round.

## Read

- `C:\Sythion\sythion\.orchestra\reviews\018-r1.md` - F1 and F2, with the exact
  replacement text. This is the whole job.
- `C:\Sythion\sythion\backend\tests\conftest.py:685-700` - the "Known constraint"
  paragraph.
- `C:\Sythion\sythion\backend\tests\test_vault_guard_exception_surfaces.py:15-28` -
  the module docstring.

## Worktree

- `C:\Users\orcha\AppData\Local\Temp\opencode\wt-018` already exists on the branch
  (re-create it if it is gone). Work there.

## Changes (exactly two, both prose)

1. **F1 - `backend/tests/conftest.py`, the "Known constraint" paragraph.** It
   currently claims a 409 response AND a propagating exception that never
   co-occur. Replace the last two sentences with the reviewer's exact text:

   > A test that drives a **blocked** vault write through ``backend/main.py.app``
   > sees one of two outcomes, depending on the client: a default ``TestClient``
   > raises this exception out of the request, so the test goes red; a client
   > built with ``raise_server_exceptions=False`` - or a real HTTP client -
   > receives the 409 ``VAULT_WRITE_BLOCKED`` response and the exception does
   > **not** propagate, so such a test must assert on the status to stay
   > meaningful.

   Keep the rest of the paragraph (the first sentences about
   `@app.exception_handler(Exception)`) intact. Change nothing else in the file -
   not the marker attribute, not the guard logic, not any other docstring.

2. **F2 - `backend/tests/test_vault_guard_exception_surfaces.py`, the module
   docstring.** Qualify the sentinel bullet so it is not presented as an
   always-on proof. The reviewer's suggested wording:

   > * in sentinel mode (``SYTHION_VAULT_SENTINEL=1``) the real Windows
   > Credential Manager is completely unreachable for the run - that proof is
   > opt-in, because the acceptance round trip legitimately needs the real API.

   Change nothing else in that file.

## Do NOT do

- Do not change any Python logic, any test assertion, `backend/main.py`, the
  marker contract, the sentinel behaviour, or the file set.
- `git diff --name-only master..HEAD` must still list EXACTLY the same three
  files.
- Do not update the vendored mirror `src-tauri/resources/backend/main.py` - the
  orchestrator records that as a follow-up.

## Verify

- Run the new test file with the sentinel armed
  (`$env:SYTHION_VAULT_SENTINEL='1'`) - it must still pass.
- Run `backend/tests/test_vault_guard_root_scope.py` - it must still pass.
- Run `python -m pytest backend/tests/acceptance/test_secret_storage.py -q` - 10
  passed (this one needs the real API, so run it WITHOUT the sentinel).
- Paste the output.

## Write

- `C:\Sythion\sythion\.orchestra\reports\018-implement-2.md` - round-2 report:
  the commit sha, the exact diff (it should be prose only), the test output, and
  a statement that no logic changed.

## Constraints

- Commit on the SAME branch. Do not push. Do not touch master.
- Do not write to or delete from the real Windows Credential Manager.

## Output contract

Report at `.orchestra/reports/018-implement-2.md`. Reply with a short summary
and the commit sha. The FILE is the deliverable.