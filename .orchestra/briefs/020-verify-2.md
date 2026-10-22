# Brief: verify - 020 - round 2

- Item: 020 The `.env` isolation must be measured, not assumed, and cover the process environment
- Role: verifier (judge the OUTCOME; do not edit source)
- Reviewed commit: **the tip of `orchestra/020-env-isolation`** - resolve it
  with `git rev-parse orchestra/020-env-isolation` and record the sha. It
  must be the same commit the round-2 reviewer judged.
- Goal: decide whether the reported problem is actually gone. The problem: the
  repository's `.env` isolation was ASSUMED to work - the 006 verifier
  measured that deleting the isolation left the readiness test green on a
  clean checkout with no `backend/.env`, and that an exported
  `SYTHION_OLLAMA_API_KEY` was NOT neutralised because the clean snapshot
  still read the process environment.

## Read

- `C:\Sythion\sythion\.orchestra\plans\020.md`
- `C:\Sythion\sythion\.orchestra\backlog.md` item 020 (the six criteria)
- `C:\Sythion\sythion\.orchestra\reviews\020-r1.md` (findings F1, F2)
- `C:\Sythion\sythion\.orchestra\reports/020-implement-2.md`

## Worktree

- Create `C:\Users\orcha\AppData\Local\Temp\opencode\wt-020-verify2`,
  detached at the reviewed sha. Throwaway worktrees at the branch base are
  allowed for the reproduction; remove them when done.

## What to verify

1. **The pinned invariant, by name**: *the isolation is MEASURED, not
   assumed - deleting it is detectable, and it neutralises the process
   environment as well as the file.* Measure it, do not read it.
2. **The negative control, executed by you, in the repository.** In a
   THROWAWAY worktree with **no `backend/.env`** (the CI shape) and with
   `autouse=True` removed from `_isolate_local_env_settings` in
   `backend/tests/conftest.py`: the new test MUST go **RED**, and for the
   right reason - the exported process-environment sentinel reaching the
   effective settings. Paste the failure. Then confirm the repository's own
   `test_the_suite_does_not_inherit_the_local_env_file` also fails in that
   shape (that gap is what the item exists to close).
3. **The positive signal, in both directions.** The exact signal line must be
   `SENTINEL-PROBE file_neutralised=True env_neutralised=True env_cleared=True profile_isolated=True network_attempts=0`
   for runs 1 and 2, and the run-3 negative control must report
   `file_neutralised=False env_neutralised=False env_cleared=False profile_isolated=True network_attempts=0`.
   Paste both.
4. **F1 - the legacy-migration fence holds BY CONSTRUCTION.** Plant a fake
   `~/.sythion` in the scratch tree and show it is NOT copied into the scratch
   profile, **both** with the current shape (lifespan not run) **and** with the
   app lifespan running. This is the finding the round-1 reviewer REFUTED in
   the implementer's report, so your measurement is the one that matters.
5. **F2 - the sentinel cannot leak through a failure.** Force a failure in the
   outer test and confirm the sentinel VALUE does not appear in the failure
   output. The raw child output must not be rendered before the leak check.
6. **Scope**: the diff against the branch base lists exactly the three
   `backend/tests/` files. No production file, no `backend/core/` file.
7. **The collected count** is +1 over the branch base (1628 -> 1629) and the
   probe template is not collected.
8. **No regression**: the backend suite on the branch, with a **PER-TEST JUnit
   comparison** against the branch base - the collected count moves by exactly
   the new test, the skipped count is unchanged, and NO existing test's verdict
   changes. The two `test_phase975_admission_multiproc.py` contention flakes
   and the intermittent `0xC0000005` in `test_phase95_hardening.py` are
   KNOWN and PRE-EXISTING on the base tree; report what you actually see and
   do not attribute either to this change.
9. **No credential touched**: the developer's real `backend/.env` is never
   copied, read, printed or diffed; no real `~/.sythion` is copied anywhere;
   the probe renders boolean comparisons only, never a sentinel value.

## Write

- `C:\Sythion\sythion\.orchestra\verifications/020-v2.md` (the only file you may write)

## Output contract

- `- Reviewed commit: <sha>`
- `VERDICT: FIXED` / `NOT FIXED` / `REGRESSED` as the FIRST line
- The pinned invariant checked BY NAME
- The reproduction with command + pasted output
- Per-criterion judgement against reality
- The regression check with numbers
- What you did NOT check

## Constraints

- No source edits to the committed branch. No push. No destructive commands.
- Never copy, read or print the developer's real `backend/.env`, and never let
  a real `~/.sythion` be copied anywhere.
- Run the full backend suite in the BACKGROUND and poll it to completion.
- If something cannot be determined, say so in the report rather than
  assuming it.

Reply with a short summary. The FILE is the deliverable.
