# Brief: verify - 018 - round 1

- Item: 018 The vault-write guard must not be swallowed by the FastAPI global exception handler
- Role: verifier (judge the OUTCOME; do not edit source)
- Reviewed commit: `3359610` (branch `orchestra/018-vault-guard-surface`)
- Goal: decide whether the reported problem is actually gone - a BLOCKED
  product-credential write through the HTTP layer must be observable as a
  documented 409, not as an indistinguishable generic 500 - and whether anything
  else broke.

## Read

- `C:\Sythion\sythion\.orchestra\plans\018.md` (the acceptance criteria, binding)
- `C:\Sythion\sythion\.orchestra\backlog.md` item 018 (the five criteria)
- `C:\Sythion\sythion\.orchestra\reviews\018-r1.md` and `018-r2.md`
- `C:\Sythion\sythion\.orchestra\reports\018-implement-1.md` and `018-implement-2.md`

## Worktree

- Create `C:\Users\orcha\AppData\Local\Temp\opencode\wt-018-verify`, detached at `3359610`.
- A second throwaway worktree at `master` (0e14168) for the base reproduction is
  allowed; remove it when done.

## MANDATORY SAFETY

The new test deliberately aims a write at the PRODUCT credential target
`Sythion (Synth)/ollama_api_key`. It is only safe because the guard blocks it
before delegation. Therefore:

- ARM THE SENTINEL for every run of the new test file:
  `$env:SYTHION_VAULT_SENTINEL='1'`. Report the sentinel-reached count (must be 0).
- Never run the new test file against the real vault.
- Never write to or delete from the real Windows Credential Manager.
- The pre-existing acceptance round trip
  (`backend/tests/acceptance/test_secret_storage.py::test_credential_manager_round_trip`)
  legitimately uses the real API against the TEST-SCOPED prefix
  `Sythion (Synth) [TEST]/` - that one is allowed to run WITHOUT the sentinel,
  and it is deselected from sentinel-armed full-suite runs.

## What to verify

1. **Reproduction, fail on base / pass on branch.** Copy the new test file into
   a throwaway worktree at `master` and run it there with the sentinel armed:
   it must FAIL (the 409 assertion, and the drift pin). Run it in your own
   worktree at `3359610`: it must PASS.
2. **The pinned invariant, by name**: *a blocked product-credential write is
   never delegated to `win32cred` and is never reported as a generic 500.*
   - the 409 body, the exact error slug, the exact code, the product target in
     `detail`, `!= 500`, `not 2xx`;
   - the `CredWrite` spy shows exactly one attempt at the product target;
   - the read-only fingerprint of the real product credential is unchanged
     before/after (presence + SHA-256, never the blob).
3. **The 500 contract**: a genuine fault on the SAME route is still
   `500` + `INTERNAL_ERROR` + `"500"`.
4. **The 500 branch is byte-identical** to base (`git diff` shows 0 deletions in
   `backend/main.py`; the 500 `JSONResponse` block is context, not changed lines).
5. **The marker contract is symmetric and import-free**: the attribute name in
   `backend/main.py` and on `ProductCredentialWriteAttempt` is the same string;
   grep over `backend/` excluding `backend/tests/` for `backend.tests` -> 0 hits.
6. **Scope**: `git diff --name-only master..3359610` is exactly the three files.
   `backend/core/secrets.py` byte-identical to base.
7. **Non-vacuity by mutation** (throwaway probes, deleted afterwards): with the
   marker suppressed the same blocked write must give 500; with the guard not
   blocking (a store whose `set()` succeeds) it must give 2xx. Both mutations
   must make the new test FAIL.
8. **No regression**: full suite on the branch, sentinel armed, with the one
   real-vault acceptance round trip deselected. Compare the per-test outcome
   with the base (use `--junitxml` and compare PER TEST, not totals). The
   expected delta is exactly the four new tests, all passing, and the skipped
   count unchanged at 45.
9. **The corrected documentation is TRUE**: read the "Known constraint" paragraph
   in `backend/tests/conftest.py` and check each claim against Starlette 0.35.1's
   behaviour - a default `TestClient` raises, a `raise_server_exceptions=False`
   client gets the 409 and the exception does not propagate. If you can, check
   the "real HTTP client" half too (a loopback socket is allowed; it is a local
   connection, not an external action).

## Write

- `C:\Sythion\sythion\.orchestra\verifications\018-v1.md` (the only file you may
  write; no source edits)

## Output contract

- `- Reviewed commit: 3359610`
- `VERDICT: FIXED` / `NOT FIXED` / `REGRESSED` as the FIRST line
- Reproduction result with command + output
- Each acceptance criterion (the five from the backlog) checked against reality
- The pinned invariant checked BY NAME
- The regression check with numbers
- What you did NOT check

## Constraints

- No source edits to the committed branch. No push. No destructive commands.
- Arm the sentinel; never touch the real credential store.
- Do not ask questions; if something cannot be determined, say so in the report.

Reply with a short summary. The FILE is the deliverable.