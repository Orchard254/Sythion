# Brief: review - 018 - round 1

- Item: 018 The vault-write guard must not be swallowed by the FastAPI global exception handler
- Role: reviewer (review only - do not modify code)
- Reviewed commit: `70010b3` (`orchestra/018-vault-guard-surface`)
- Goal: judge the diff against `plans/018.md`: correctness, quality, scope, and
  whether each acceptance criterion is met. Work in your OWN worktree checked out
  at the reviewed commit - never the implementer's tree and never master's working copy.

## Read

- `C:\Sythion\sythion\.orchestra\plans\018.md` (binding)
- `C:\Sythion\sythion\.orchestra\backlog.md` item 018
- `C:\Sythion\sythion\.orchestra\reports\018-implement-1.md`
- The diff: `git diff master..70010b3` (or `git show 70010b3` in your worktree)
- `backend/main.py`, `backend/tests/conftest.py`,
  `backend/tests/test_vault_guard_exception_surfaces.py`,
  `backend/tests/test_vault_guard_root_scope.py` (must keep passing unchanged)

## Worktree

- Create `C:\Users\orcha\AppData\Local\Temp\opencode\wt-018-review`, detached at
  `70010b3`. Do not reuse wt-018.

## Write

- `C:\Sythion\sythion\.orchestra\reviews\018-r1.md` - the only file you may write.
  No source edits.

## What to check (be adversarial)

1. **Scope**: `git diff --name-only master..70010b3` must be EXACTLY
   `backend/main.py`, `backend/tests/conftest.py`,
   `backend/tests/test_vault_guard_exception_surfaces.py`. Nothing else.
2. **The 500 branch is untouched**: read the handler in the diff and confirm the
   pre-existing 500 response is byte-identical.
3. **The marker contract is symmetric**: the attribute name on
   `ProductCredentialWriteAttempt` and the constant in `backend/main.py` are the
   same string; no import in either direction (grep `backend/` excluding
   `backend/tests/` for `backend.tests`).
4. **The new test is not vacuous**: does it actually reach a write attempt at the
   product target? Does the guard really block it? Would the test still pass if
   the guard did NOT block (i.e. is the assertion on the response enough, or
   could a broken guard still yield 409)? Say precisely.
5. **The 500 contract test is real**: does `test_genuine_fault_on_the_same_route_is_still_a_generic_500`
   genuinely exercise the same route with a stub store, or does it accidentally
   pass for another reason?
6. **The sentinel is opt-in**: the report says the sentinel is armed only with
   `SYTHION_VAULT_SENTINEL=1`. In a NORMAL run (CI, a developer) the new test
   file runs against the REAL `win32cred`, relying on the guard alone. Judge
   whether that is acceptable and whether it matches the precedent of the
   already-merged `backend/tests/test_vault_guard_root_scope.py` (which also aims
   a write at the product target and relies on the guard). If you think it is a
   problem, say what the fix shape is.
7. **Does the change weaken anything?** A 409 on a path that used to be 500 -
   could any real (non-test) exception accidentally carry the marker attribute
   and be diverted? Consider `AttributeError`, `ValidationError` and any
   exception class in the repo that has an attribute with that name.
8. **The new test's isolation**: does it mutate global state (`backend.main.control_token`,
   `secrets_module._store`) and restore it? Does it need the app's lifespan
   (profile lock, DB)?
9. **The docstring edit** in `backend/tests/conftest.py` must now be TRUE. Read it.

You may run read-only commands and targeted test runs in your worktree. If you run
the new test file, ARM THE SENTINEL (`$env:SYTHION_VAULT_SENTINEL='1'`) - do not
run it against the real vault.

## Output contract

`.orchestra/reviews/018-r1.md` with at least:
- `- Reviewed commit: 70010b3`
- `- Tier: <NORMAL|EXTERNAL|SENSITIVE|SENSITIVE+EXTERNAL>` (run the skill's
  classifier if it is available; otherwise state your own and why)
- Scope check result
- Per-acceptance-criterion judgement (018's five criteria)
- Findings and risks, each with a concrete fix shape
- `VERDICT: ACCEPT` / `IMPROVE` / `REJECT` with the reason

Reply with a short summary. The FILE is the deliverable.