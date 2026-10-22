# Brief: review - 020 - round 2

- Item: 020 The `.env` isolation must be measured, not assumed, and cover the process environment
- Role: reviewer (review only - no source edits)
- Reviewed commit: **the tip of `orchestra/020-env-isolation`** - resolve it
  with `git rev-parse orchestra/020-env-isolation` and record the sha. The
  previous reviewed commit was `2fdc842`; this round is `2fdc842` + one or
  more round-2 commits.
- Goal: judge ONLY the round-2 delta against
  `.orchestra/briefs/020-implement-2.md`, and confirm that the round-1
  findings F1 and F2 are actually closed.

## Read

- `C:\Sythion\sythion\.orchestra\briefs\020-implement-2.md` (binding)
- `C:\Sythion\sythion\.orchestra\reviews\020-r1.md` - your predecessor's
  review, in particular findings **F1** and **F2**
- `C:\Sythion\sythion\.orchestra\reports\020-implement-2.md`
- The diff: `git diff 2fdc842..<reviewed sha>`

## Worktree

- Create `C:\Users\orcha\AppData\Local\Temp\opencode\wt-020-review2`,
  detached at the reviewed sha. (Do not reuse `wt-020` or `wt-020-review` -
  another agent may still own them.)

## What to check

1. **Scope**: the round-2 diff touches ONLY files under `backend/tests/`. The
   file set must still be exactly the three files
   `backend/tests/isolation_probe_template.py`,
   `backend/tests/test_env_isolation.py`,
   `backend/tests/test_setup_readiness.py`. **No production file and no
   `backend/core/` file may change.**
2. **F1 is closed - and this is the finding that matters.** The implementer
   REPORTED that "the scratch profile dir carries a placeholder so the legacy
   `~/.sythion` migration cannot copy real user data in". Your predecessor
   REFUTED that: the placeholder fenced the wrong directory - the migration's
   actual target in the scratch tree is `<scratch>/localappdata/Sythion
   (Synth)` - and nothing was copied only because the app lifespan never runs
   (`TestClient` without a `with`). A reported safety property that does not
   hold is worse than no claim, because the next agent relies on it.
   **Verify the fix by construction, not by the report's word:**
   - Find the migration code yourself. Read it. Do not take the report's
     characterisation of where it looks.
   - Confirm the placeholder (or whatever mechanism) is at the directory the
     migration actually reads.
   - **Prove it yourself:** plant a fake `~/.sythion` in the scratch tree and
     show that it is NOT copied into the scratch profile - **both** with the
     current shape (lifespan not run) **and** with the app lifespan running.
     A fix that only holds when the lifespan does not run is not a fix.
   - If the mechanism is "redirect the migration target with an env var",
     confirm the env var is actually honoured by the migration code.
3. **F2 is closed**: the outer test must not render the raw child output in a
   failure message BEFORE the sentinel-leak assertion has been made. Confirm
   the ordering by reading the code: the leak check must come first, or the
   output must be redacted/omitted. Confirm the failure is still diagnosable
   (the report should say what shape was chosen and why).
   **Prove it**: force a failure and confirm the sentinel VALUE does not
   appear in the failure output.
4. **Nothing was weakened**: no existing assertion may have been relaxed,
   deleted, renamed or skipped to make the suite pass. Diff the test bodies,
   not just the file names. In particular, the round-1 reviewer verified these
   behaviours and they must still hold:
   - the signal line is exactly
     `SENTINEL-PROBE file_neutralised=True env_neutralised=True env_cleared=True profile_isolated=True network_attempts=0`
     for runs 1 and 2, and
     `file_neutralised=False env_neutralised=False env_cleared=False profile_isolated=True network_attempts=0`
     for run 3 (the negative control);
   - deleting `autouse=True` from `_isolate_local_env_settings` makes the test
     go RED **for the right reason**;
   - renaming the fixture so the anchor is missing makes the test **FAIL
     LOUDLY**, never skip;
   - the three-run structure, the timeout, the child environment, and the
     "fails rather than hangs" behaviour are unchanged.
5. **The negative control still works** - run it yourself in a THROWAWAY
   worktree: remove `autouse=True` from `_isolate_local_env_settings` in
   `backend/tests/conftest.py` and confirm the test goes RED with the
   sentinel reason, not some unrelated error. Delete the throwaway worktree
   afterwards.
6. **The 019 tautology guard**: the modified test files must not contain
   `or True`, `and False`, or a bare `assert True`.
7. **The collected count** must still be exactly +1 over the branch base
   (1628 -> 1629) - the probe template must still not be collected. Run
   `python -m pytest backend/tests --collect-only -q`.
8. **No credential was ever touched**: the developer's real `backend/.env` is
   never copied, read, printed or diffed, and no real `~/.sythion` is copied
   anywhere. Confirm by reading the copy logic and by grepping the diff.
   The probe must never RENDER a sentinel value - boolean comparisons only.
9. **CI facts - check them yourself.** Do not inherit a CI claim from the
   brief or from a predecessor's report. Read `.github/workflows/ci.yml` and
   report what it actually contains.

## Write

- `C:\Sythion\sythion\.orchestra\reviews\020-r2.md` (the only file you may write)

## Output contract

- `- Reviewed commit: <sha>`
- `- Tier: <NORMAL|EXTERNAL|SENSITIVE|SENSITIVE+EXTERNAL>`
- Scope check result (the exact file list)
- Per-finding judgement: F1 closed? F2 closed?
- Any NEW finding, each with a concrete fix shape
- `VERDICT: ACCEPT` / `IMPROVE` / `REJECT` with the reason

## Constraints

- No source edits. Probe scripts in TEMP only, deleted after.
- Do not run the full backend suite - the Verifier's job.
- Do not push. Never copy, read or print the developer's real `backend/.env`,
  and never let a real `~/.sythion` be copied anywhere.

Reply with a short summary. The FILE is the deliverable.
