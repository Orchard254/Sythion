# Brief: implement - 020 - round 2 (two safety findings the review refuted)

- Item: 020 The `.env` isolation must be measured, not assumed, and cover the process environment
- Role: implementer (round 2)
- Branch: `orchestra/020-env-isolation` (EXISTING - continue on it)
- Base of this round: `2fdc842`
- Goal: the review ACCEPTed the item but **refuted a safety property the
  implementer reported as true**, and found one leak in the failure path. Both
  are test-only fixes. **No production code changes.**

## F1 - the placeholder fences the WRONG directory (the reported safety claim is false)

The implementer reported: *"the scratch profile dir carries a placeholder so the
legacy `~/.sythion` migration cannot copy real user data in"*. The reviewer
tested that claim and **refuted it**:

> in the isolation-present shape the migration target is
> `<scratch>/localappdata/Sythion (Synth)`; today nothing is copied only
> because the app lifespan never runs (`TestClient` without a `with`), which I
> proved by showing a fake `~/.sythion` *would* be copied.

So the property does not hold by construction - it holds only by accident, and
it would stop holding the moment the nested run uses the app lifespan.

**Fix**: put the placeholder where the migration actually looks. Read the
migration code (the legacy `~/.sythion` -> profile migration in
`backend/core/storage.py` or wherever it lives - find it, do not guess) and
make the scratch tree's **actual** migration target contain the placeholder, so
that a copy of the user's real data into the scratch tree is impossible
regardless of whether the lifespan runs. Then prove it: the report must show
that a fake `~/.sythion` in the scratch tree is NOT copied into the scratch
profile - both with the current shape (lifespan not run) AND with the lifespan
run. If the migration's target is derived from an env var, redirect that env
var in the child instead of relying on a placeholder.

## F2 - raw child output is rendered in a failure message BEFORE the leak assertion

The outer test renders the nested run's raw output in a failure message. The
whole point of the sentinel is that it never appears in any captured
stdout/stderr - and the test asserts that. If the test fails, the failure
message can print the very string the test exists to keep out of the output.

**Fix**: do not render the raw child output in a failure message before the
sentinel-leak assertion has been made. Either assert the leak FIRST and only
then include output, or redact/omit the output from the message and report the
sentinel-leak check as its own failure. The report must say which shape you
chose and why it still leaves the failure diagnosable.

## Do NOT do

- Do not change the check's signal line, the three-run structure, the exact
  expected signal, the timeout, the child environment, the negative control's
  shape, or the "fails loudly when the anchor is missing" behaviour - the
  Reviewer verified all of those and they are correct.
- Do not weaken any test.
- The file set stays `backend/tests/` only. No production file, no
  `backend/core/` file.
- `git diff --name-only f1ad188..HEAD` must still list exactly the same three
  files.

## Verify (paste all of it)

- `python -m pytest backend/tests/test_env_isolation.py -q` - passes; report the
  wall time.
- `python -m pytest backend/tests/test_setup_readiness.py -q` - passes.
- The collected count: `python -m pytest backend/tests --collect-only -q`.
- **The negative control still works**: in a THROWAWAY worktree, remove
  `autouse=True` from `_isolate_local_env_settings` in `backend/tests/conftest.py`
  and confirm the new test still goes RED for the right reason ("the exported
  process-environment sentinel reached the effective settings"). Paste the
  failure. Delete the throwaway worktree afterwards.
- **The F1 proof**: show that a fake `~/.sythion` in the scratch tree is NOT
  copied into the scratch profile - with the current shape AND with the app
  lifespan running. Paste the evidence.
- The full backend suite: `python -m pytest backend/tests -q` - run it in the
  BACKGROUND and poll to completion. NOTE: this machine has TWO known
  pre-existing failures (`test_phase975_admission_multiproc.py` contention
  flakes, which pass in isolation) and a known intermittent native crash
  (`0xC0000005` in `test_phase95_hardening.py`, reproducible on pre-change
  master). Report what you actually see; attribute neither to this change.
  Another agent may be running a suite concurrently - that makes the native
  crash more likely.

## Write

- `C:\Sythion\sythion\.orchestra\reports/020-implement-2.md` - commit sha, the
  exact diff, every command's output, the F1 proof, and a statement that no
  production code changed.

## Constraints

- Commit on the SAME branch. Do not push. Do not touch master.
- Never copy, read or print the developer's real `backend/.env`, and never let
  a real `~/.sythion` be copied anywhere.
- No destructive commands.

## Output contract

Report at `.orchestra/reports/020-implement-2.md`. Reply with a short summary
and the commit sha. The FILE is the deliverable.