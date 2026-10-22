# Brief: implement - 020 - round 3 (scrub the legacy data-dir override in the probe)

- Item: 020 The `.env` isolation must be measured, not assumed, and cover the process environment
- Role: implementer (round 3)
- Branch: `orchestra/020-r3-probe-scrub` (NEW - create it from `e558991`)
- Base of this round: `e558991` (the tip of `orchestra/020-env-isolation`)
- Goal: close the one finding the round-2 review left open. **Test-only** -
  no production code changes.

## The finding

The round-2 review ACCEPTed the item and recorded one LOW finding:

> the new migration probe does not scrub `Sythion (Synth)_DATA_DIR`, so with
> that override set the probe resolves `data_dir()` outside the scratch tree
> and copies the fake marker there before failing loudly - measured.

**Why it matters even though it is LOW.** The probe's whole safety property is
that it only ever writes INSIDE the scratch tree. `Sythion (Synth)_DATA_DIR`
is the LEGACY data-dir override that `backend/core/storage.py` still honours
(alongside the canonical `SYTHION_DATA_DIR`). The isolation deletes every
`SYTHION_*` variable, but **not** the legacy `Sythion (Synth)_DATA_DIR` - it
does not start with `SYTHION_`. So on a developer machine where that legacy
override happens to be set, the probe's `data_dir()` resolves OUTSIDE the
scratch tree, the fake legacy marker is copied there, and only afterwards does
the test fail loudly. The failure is correct; the write is not.

## The change

1. **Scrub the legacy override in the probe's child environment.** In
   `_run_the_migration_probe` in `backend/tests/test_env_isolation.py`, the
   environment already deletes every variable whose name starts with
   `SYTHION_`. Delete the legacy override as well. **Read
   `backend/core/storage.py` first** and use the EXACT variable name(s) it
   honours - there may be more than one legacy spelling, and there may be a
   canonical one too. Do not guess the spelling.
2. **Prove it.** Add a test that shows the probe is contained even when the
   legacy override IS set in the parent environment. The shape:
   - set the legacy override (to a directory inside the scratch tree, so the
     test itself never writes outside the scratch) in the parent process;
   - run the probe;
   - assert the probe's resolved target is the expected scratch-internal
     directory, and that nothing was written to the override's directory.
   The test must FAIL without the fix and pass with it. Verify that by
   running it against the pre-fix code in a TEMP copy.
3. **Do not change anything else.** The signal line, the three-run structure,
   the timeout, the child environment for the OTHER runs, the negative
   control's shape, the fence, the redaction helper and the leak-check
   ordering are all verified correct by the round-2 review. Do not touch
   them.

## Do NOT do

- Do not change `backend/core/storage.py` or any other production file.
- Do not weaken any existing test.
- `git diff e558991..HEAD` must touch ONLY
  `backend/tests/test_env_isolation.py`.

## Verify (paste all of it)

- `python -m pytest backend/tests/test_env_isolation.py -q` - passes; report
  the count and the wall time.
- `python -m pytest backend/tests/test_setup_readiness.py -q` - passes.
- `python -m pytest backend/tests --collect-only -q` - report the count (the
  round-2 count was 1630; yours should be 1631).
- **The teeth of the new test**: in a TEMP copy, confirm the new test FAILS
  without the scrub and PASSES with it. Paste both. The TEMP copy must be
  deleted afterwards.
- **The negative control still works**: in a THROWAWAY worktree, remove
  `autouse=True` from `_isolate_local_env_settings` in
  `backend/tests/conftest.py` and confirm the new test still goes RED for the
  right reason ("the exported process-environment sentinel reached the
  effective settings"). Delete the throwaway worktree afterwards.
- The full backend suite: `python -m pytest backend/tests -q` - run it in the
  BACKGROUND and poll to completion. NOTE: another agent may be running a
  suite concurrently, which makes the known intermittent native crash
  (`0xC0000005`, reproducible on the pre-change base) MORE likely - if your
  run dies natively, re-run once and report BOTH. The
  `test_phase975_admission_multiproc.py` contention flakes are also known and
  pre-existing (they pass in isolation). Report what you actually see;
  attribute neither to this change.

## Write

- `C:\Sythion\sythion\.orchestra\reports/020-implement-3.md` - commit sha, the
  exact diff, every command's output, the teeth proof, and a statement that no
  production code changed.

## Constraints

- Commit on the NEW branch `orchestra/020-r3-probe-scrub`. Do not push. Do not
  touch master. Do not modify `orchestra/020-env-isolation` - that branch is
  being committed concurrently.
- Never copy, read or print the developer's real `backend/.env`, and never let
  a real `~/.sythion` be copied anywhere. Never write outside the scratch
  tree in any test.
- No destructive commands.

## Output contract

Report at `.orchestra/reports/020-implement-3.md`. Reply with a short summary
and the commit sha. The FILE is the deliverable.
