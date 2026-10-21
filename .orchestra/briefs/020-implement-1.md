# Brief: implement - 020 - attempt 1

- Item: 020 The `.env` isolation must be measured, not assumed, and cover the process environment
- Role: implementer
- Goal: implement `plans/020.md` exactly. The deliverable is a check that goes
  RED when the autouse `.env` isolation is deleted - on a machine with no
  `backend/.env`, i.e. on CI - which is the property the 006 verifier could
  not demonstrate.

## Read

- `C:\Sythion\sythion\.orchestra\plans\020.md` - the binding plan. Read it in
  full before writing anything; every number in it is a measurement, not an
  estimate, and the mechanism is already validated.
- `C:\Sythion\sythion\backend\tests\conftest.py:495-620` - the isolation under test.
- `C:\Sythion\sythion\backend\tests\test_setup_readiness.py:320-365` - the file
  that re-hardcodes the three field names (criterion 4).
- `C:\Sythion\sythion\.orchestra\verifications\006-v1.md` - the finding this item closes.

## Branch / worktree

- Branch: `orchestra/020-env-isolation`, from master (df75bb6 + the state commits).
- Worktree: `C:\Users\orcha\AppData\Local\Temp\opencode\wt-020`.
- Small commits on the branch. Do NOT push. Do not touch master directly.

## Write (code) - only files under backend/tests/

1. `backend/tests/test_env_isolation.py` (NEW) - the one test
   `test_the_local_env_isolation_is_measured_not_assumed`: build the scratch
   tree, run the three nested pytest subprocesses, assert the exact signal line
   in both directions, assert run 2's readiness outcome set equals run 1's
   item-by-item (from the JUnit XML), assert the negative control (run 3)
   failed the probe AND the repository's own
   `test_the_suite_does_not_inherit_the_local_env_file`, assert neither sentinel
   string appears in any captured stdout/stderr, assert no blocked network
   attempt, and remove the scratch tree in a `finally`.
2. `backend/tests/isolation_probe_template.py` (NEW) - the probe TEMPLATE. It is
   a template, NOT a collected test: it must not be collected by the repository's
   own pytest run (name it so it is not a `test_*.py` module, or otherwise keep
   it out of collection - the plan's approach). It is copied into the scratch
   tree at run time as a collected probe.
3. `backend/tests/test_setup_readiness.py` (MODIFIED) - criterion 4: import
   `_LOCAL_ENV_FIELDS` from `backend.tests.conftest` and iterate it instead of
   re-hardcoding `("ollama_api_key", "selected_model", "file_roots")`.
   Nothing else in that file changes.

NO production file may change. `backend/core/config.py` must be untouched.

## MANDATORY safety rules

- The developer's REAL `backend/.env` must NEVER be copied, read, printed or
  diffed. The scratch copy excludes it; the scratch tree gets a GENERATED `.env`
  with a fake sentinel. Assert in the test that the developer's `.env` was not
  copied.
- The probe must never RENDER a sentinel value - boolean comparisons only, and
  the failure messages name the property, not the value. The outer test asserts
  neither sentinel string appears in any captured output.
- No network I/O. The profile dir and the database must resolve inside the
  scratch tree (`SYTHION_DATA_DIR`, `LOCALAPPDATA` redirected). The child
  environment is built EXPLICITLY - every `SYTHION_*` removed, `PYTHONPATH`
  scrubbed so the scratch tree shadows the repository.
- The child's probe plumbing uses NON-`SYTHION_*` variable names, because the
  isolation deletes every `SYTHION_*` variable for the duration of a test.
- The scratch tree is removed in a `finally`; a removal that still fails FAILS
  the test (debris must not accumulate silently).

## Verify (paste all of it)

- `python -m pytest backend/tests/test_env_isolation.py -q` - passes, and report
  the wall time.
- `python -m pytest backend/tests/test_setup_readiness.py -q` - passes.
- `python -m pytest backend/tests --collect-only -q` - report the count and
  explain the delta against the pre-change count (the plan says the tree
  collects 1628 today).
- The FULL suite: `python -m pytest backend/tests -q` from the repository root
  in the worktree. Run it in the BACKGROUND and poll to completion - it takes
  4-9 minutes; do not report until it has actually finished. If it dies
  natively, re-run once and report BOTH outcomes.
- The NEGATIVE control in the REPOSITORY: in a throwaway worktree (NOT the
  branch), delete the isolation from the repository's `backend/tests/conftest.py`
  and run the new test - it must go RED. Paste the failure. This is the
  property the whole item exists for. Do NOT make that deletion in the branch.

## Write (report)

- `C:\Sythion\sythion\.orchestra\reports\020-implement-1.md` - commit sha, files
  changed, every command's output, the wall times, the repository-level
  negative-control result, which criteria were ALREADY MET (with the file:line
  that meets them), and what was NOT checked.

## Constraints

- No push, no deploy, no destructive commands.
- The new test must not be flaky-by-construction: it has a 120 s per-run
  timeout and fails rather than hangs.
- Do not weaken the check to make it pass. If the negative control does NOT
  fail, fix the cause or report it - never relax the assertion.

## Output contract

Report at `.orchestra/reports/020-implement-1.md` including the commit sha.
Reply with a short summary: the signal line, the negative-control result (both
levels), the wall times, the collected-count delta, and anything you could not
determine. The FILE is the deliverable.