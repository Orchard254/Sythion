# Brief: implement - 020 - round 4 (scrub the legacy override in the child environment too)

- Item: 020 The `.env` isolation must be measured, not assumed, and cover the process environment
- Role: implementer (round 4)
- Branch: `orchestra/020-r3-probe-scrub` (EXISTING - continue on it)
- Base of this round: `e993976` (the current tip of that branch)
- Goal: close the residual the round-3 implementer MEASURED and reported rather
  than fixed, because the round-3 brief forbade touching `_child_environment`.
  That restriction was too tight: the root cause is the SAME in both places.

## The residual (measured, not fixed, in round 3)

> The **nested runs** have a second manifestation of the same root cause: with
> the legacy override set, `_child_environment` sets `SYTHION_DATA_DIR` *and*
> inherits the legacy one, so `resolve_data_dir()` raises
> `ValueError: conflicting profile directories` at import and
> `test_the_local_env_isolation_is_measured_not_assumed` is **RED** on such a
> machine (nothing written outside the scratch tree - it fails before
> `_ensure_private_dir`).

So on a developer machine whose shell carries `Sythion (Synth)_DATA_DIR`:
- the **migration probe** was fixed in round 3 (its child env is scrubbed);
- the **three nested runs** are NOT - `_child_environment` still inherits the
  legacy override, so the main isolation test goes RED for a reason that has
  nothing to do with what the item is testing.

Nothing is written outside the scratch tree in this state - the failure is
SAFE. But a RED test on a machine that is merely configured differently is a
false alarm, and the isolation's whole job is to neutralise ambient
environment. The legacy override IS ambient environment.

## The change

1. **Scrub the legacy override in `_child_environment` as well**, so the
   three nested runs cannot inherit it. Use the SAME production constants
   the round-3 fix used - `DATA_DIR_ENV` and `LEGACY_DATA_DIR_ENV` imported
   from `backend/core/storage.py` - and scrub case-insensitively. The round-3
   implementer found that the legacy name upper-cases to
   `SYTHION (SYNTH)_DATA_DIR` (a SPACE, not an underscore), which is exactly
   why the existing `SYTHION_*` sweep never saw it. Reuse that knowledge.
   **Factor the scrub into ONE helper** used by both the migration probe and
   `_child_environment`, so the two places cannot drift apart again.
2. **Prove it.** Add a test that shows the three nested runs stay GREEN with
   the legacy override set in the PARENT environment - i.e. the main
   isolation test still passes on a machine that merely has the legacy
   override configured. The test must FAIL against the pre-fix code.
3. **Do not change anything else.** The signal line, the three-run
   structure, the timeout, the negative control's shape, the fence, the
   redaction helper, the leak-check ordering and the round-3 probe test are
   all verified correct - do not touch them. The `_child_environment` change
   must remove ONLY the legacy/canonical data-dir overrides that would
   otherwise make the child resolve a profile directory outside the scratch
   tree; do not remove anything else the child legitimately needs.

## The round-3 finding you must not regress

Round 3's own test - `test_the_migration_probe_stays_in_the_scratch_tree_when_the_legacy_override_is_set`
- must keep passing, with its three independently-teethed assertions intact
(the resolved target, `migrated=False`, and the override's directory being
empty). The round-3 implementer showed all three fail without the scrub.

## Do NOT do

- Do not change `backend/core/storage.py` or any other production file.
- Do not weaken any existing test.
- `git diff e558991..HEAD` must touch ONLY
  `backend/tests/test_env_isolation.py`.

## Verify (paste all of it)

- `python -m pytest backend/tests/test_env_isolation.py -q` - passes; report
  the count and the wall time.
- `python -m pytest backend/tests/test_setup_readiness.py -q` - passes.
- `python -m pytest backend/tests --collect-only -q` - report the count (expect
  1632).
- **THE TEETH**: in a TEMP copy, confirm the NEW test FAILS without the
  `_child_environment` scrub, and that the round-3 probe test still fails
  without its own scrub. Paste both. Delete the TEMP copy afterwards.
- **The negative control still works**: in a THROWAWAY worktree, remove
  `autouse=True` from `_isolate_local_env_settings` in
  `backend/tests/conftest.py` and confirm the measurement test still goes RED
  for the right reason ("the exported process-environment sentinel reached the
  effective settings"). Delete the throwaway worktree afterwards.
- The full backend suite: `python -m pytest backend/tests -q` - run it in the
  BACKGROUND and poll to completion. NOTE: other agents may be running suites
  concurrently, which makes the known intermittent native crash (`0xC0000005`,
  reproducible on the pre-change base) MORE likely - if your run dies
  natively, re-run once and report BOTH. The
  `test_phase975_admission_multiproc.py` contention flakes are known and
  pre-existing (they pass in isolation). Report what you actually see;
  attribute neither to this change.

## Write

- `C:\Sythion\sythion\.orchestra\reports/020-implement-4.md` - commit sha, the
  exact diff, every command's output, the teeth proof, and a statement that no
  production code changed. **Include the full suite's actual output** and
  make sure every path you cite exists.

## Constraints

- Commit on the EXISTING branch `orchestra/020-r3-probe-scrub`. Do not push.
  Do not touch master. Do not modify `orchestra/020-env-isolation` (already
  merged).
- Never copy, read or print the developer's real `backend/.env`, and never let
  a real `~/.sythion` be copied anywhere. Never write outside the scratch
  tree in any test.
- No destructive commands.

## Output contract

Report at `.orchestra/reports/020-implement-4.md`. Reply with a short summary
and the commit sha. The FILE is the deliverable.
