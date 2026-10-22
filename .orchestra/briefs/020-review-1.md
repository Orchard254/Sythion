# Brief: review - 020 - round 1

- Item: 020 The `.env` isolation must be measured, not assumed, and cover the process environment
- Role: reviewer (review only - no source edits)
- Reviewed commit: `2fdc842` (branch `orchestra/020-env-isolation`, based on `f1ad188`)
- Goal: judge the diff against `plans/020.md` (round 2) and the six acceptance
  criteria in backlog item 020. Work in your OWN worktree at the reviewed commit.

## Read

- `C:\Sythion\sythion\.orchestra\plans\020.md` (binding)
- `C:\Sythion\sythion\.orchestra\backlog.md` item 020 (the six criteria)
- `C:\Sythion\sythion\.orchestra\reports\020-implement-1.md`
- The diff: `git diff f1ad188..2fdc842`

## Worktree

- Create `C:\Users\orcha\AppData\Local\Temp\opencode\wt-020-review`, detached at
  `2fdc842`. (Do not reuse wt-020 - another agent may still own it.)

## What to check (be adversarial)

1. **Scope**: `git diff --name-only f1ad188..2fdc842` must list ONLY files
   under `backend/tests/`. No `backend/core/` file, no `backend/api/` file, no
   `scripts/` file unless the plan justified one, no production file.
2. **The probe template must NOT be collected** by the repository's own pytest
   run. The plan requires the collected count to move by exactly +1 (the one
   new test). Confirm with `python -m pytest backend/tests --collect-only -q`.
3. **The check has teeth - run the negative control yourself.** This is the
   whole point of the item, so do not take the report's word for it. In a
   THROWAWAY worktree (or a throwaway COPY of the scratch tree), delete the
   isolation from `backend/tests/conftest.py` - remove `autouse=True` from
   `_isolate_local_env_settings` - and run the new test. It MUST go RED, and
   the reason must be the sentinel reaching the effective settings, not some
   unrelated error. A control that fails for the wrong reason proves nothing.
   Also confirm the control cannot be silently skipped: if the anchor text is
   not found, the test must FAIL LOUDLY, not pass.
4. **The anti-vacuity argument holds.** The positive signal is "the sentinel is
   NOT in effect", which a broken scratch tree would satisfy trivially. Verify
   that the negative control actually closes that hole: with the isolation off
   and the sentinel `.env` present, the probe MUST go red. If the scratch
   `.env` were not being read at all, the control would pass and the outer
   test would have to go red - check that it does.
5. **No real credential is ever touched.** The developer's `backend/.env` must
   never be copied, read, printed or diffed - the scratch copy excludes it and
   the scratch tree gets a GENERATED `.env`. Confirm by reading the copy
   logic. Confirm the probe never RENDERS a sentinel value (boolean comparisons
   only) and that the outer test asserts neither sentinel string appears in any
   captured stdout/stderr.
6. **The child environment is safe**: the child must not inherit the developer's
   `SYTHION_*` variables, `PYTHONPATH` must be scrubbed so the scratch tree
   shadows the repository, and the profile dir / DB path must resolve INSIDE
   the scratch tree. Verify the probe's own plumbing uses non-`SYTHION_*` names
   (the isolation deletes every `SYTHION_*` var for the duration of a test).
7. **The two implementation details beyond the plan** - the loopback exemption
   in the probe's socket guard, and the placeholder in the scratch profile dir
   so the legacy `~/.sythion` migration cannot copy real user data - are
   justified, not accidental. Read the code and say whether each is correct and
   whether it weakens the check.
8. **Criterion 4**: `backend/tests/test_setup_readiness.py` must import
   `_LOCAL_ENV_FIELDS` from `backend.tests.conftest` and no longer hard-code the
   three field names.
9. **Criteria 2 and 3** are marked ALREADY MET in the plan (the process-env
   coverage at `conftest.py:544-560` / `:602-612`, and the `file_roots` swap
   comment at `:585-590`). Verify those file:line claims yourself - they must
   still be true in the current tree.
10. **The 019 tautology guard**: the new test file must not contain `or True`,
    `and False`, or a bare `assert True`.
11. **Timeout behaviour**: each nested run must be bounded and must FAIL (not
    hang) on a timeout.

## Write

- `C:\Sythion\sythion\.orchestra\reviews\020-r1.md` (the only file you may write)

## Output contract

- `- Reviewed commit: 2fdc842`
- `- Tier: <NORMAL|EXTERNAL|SENSITIVE|SENSITIVE+EXTERNAL>`
- Scope check result
- Per-acceptance-criterion judgement (020's six criteria)
- Findings, each with a concrete fix shape
- `VERDICT: ACCEPT` / `IMPROVE` / `REJECT` with the reason

## Constraints

- No source edits. Probe scripts in TEMP only, deleted after.
- The full suite is the Verifier's job - you do not need to run it.
- Do not push. Do not write to the real credential vault. Never copy or read
  the developer's `backend/.env`.

Reply with a short summary. The FILE is the deliverable.