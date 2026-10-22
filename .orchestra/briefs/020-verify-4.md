# Brief: verify - 020 - round 4 (the combined r3 + r4 delta)

- Item: 020 The `.env` isolation must be measured, not assumed, and cover the process environment
- Role: verifier (judge the OUTCOME; do not edit source)
- Reviewed commit: **the tip of `orchestra/020-r3-probe-scrub`** - resolve it
  with `git rev-parse orchestra/020-r3-probe-scrub` and record the sha. Its
  base is `e558991`, already merged into master, so the whole delta is
  `e558991..<sha>`.
- Goal: rounds 3 and 4 together close the safety finding the round-2 review
  raised - that the check's probe could resolve a profile directory OUTSIDE
  its own scratch tree, and that the three nested runs could inherit a
  legacy override that made them fail for a reason unrelated to what they
  test. Verify the OUTCOME: that the check is now contained, and that nothing
  regressed.

## Read

- `C:\Sythion\sythion\.orchestra\briefs/020-implement-3.md` (round 3, binding)
- `C:\Sythion\sythion\.orchestra\briefs/020-implement-4.md` (round 4, binding)
- `C:\Sythion\sythion\.orchestra\reviews/020-r2.md` (the finding)
- `C:\Sythion\sythion\.orchestra\verifications/020-v2.md` (the round-2 verdict)
- `C:\Sythion\sythion\.orchestra\reports/020-implement-3.md` and
  `reports/020-implement-4.md`

## Worktree

- Create `C:\Users\orcha\AppData\Local\Temp\opencode\wt-020-r4-verify`,
  detached at the reviewed sha. A throwaway worktree at the branch base is
  allowed for the reproduction; remove it when done.

## What to verify

1. **The pinned invariant, by name**: *the check writes only inside its own
   scratch tree, and it does so whether or not the developer has a legacy
   profile-dir override configured.* Measure it - do not read it.
2. **Containment, measured by you.** Set the legacy override
   (`Sythion (Synth)_DATA_DIR`) in the PARENT environment, to a directory
   inside a scratch tree you control, and run the check. Confirm:
   - nothing is written to the override's directory;
   - the migration probe's resolved target is the scratch-internal profile
     directory, and the probe reports `migrated=False`;
   - the three nested runs still complete and still report the exact
     positive signal line
     `SENTINEL-PROBE file_neutralised=True env_neutralised=True env_cleared=True profile_isolated=True network_attempts=0`.
   Then set the legacy override to a directory and confirm the check still
   cannot write into it.
3. **The CONTROL - the proof is not vacuous.** In a TEMP copy of the test
   file (never the committed source), remove the scrub from ONE call site at
   a time and confirm the corresponding test goes RED:
   - removing `_child_environment`'s scrub -> the round-4 test RED with the
     ValueError "conflicting profile directories";
   - removing the probe's scrub -> the round-3 probe test RED.
   And confirm that removing EITHER one leaves the OTHER test GREEN - the two
   sites must be independently load-bearing, not one redundant copy. Paste
   both.
4. **The negative control still works**: in a THROWAWAY worktree with
   `autouse=True` removed from `_isolate_local_env_settings` in
   `backend/tests/conftest.py` and no `backend/.env`, the measurement test
   MUST go RED for the right reason - "the exported process-environment
   sentinel reached the effective settings". Paste the failure.
5. **The legacy-migration fence still holds** (item 020's original property):
   a fake `~/.sythion` inside the scratch tree is NOT copied into the
   scratch profile, both with the app lifespan running and not running, and
   the CONTROL against a root with no placeholder DOES copy.
6. **Scope**: the delta `e558991..<sha>` touches ONLY
   `backend/tests/test_env_isolation.py`. No production file, no
   `backend/core/` file.
7. **Collected count**: +2 against `e558991` (1630 -> 1632), and the probe
   template contributes 0.
8. **No regression**: the backend suite with a **PER-TEST JUnit comparison**
   against `e558991` - exactly TWO tests added, the skipped count unchanged,
   and NO existing test's verdict changes. Known pre-existing on this
   machine: the `test_phase975_admission_multiproc.py` contention flakes
   (pass in isolation) and an intermittent `0xC0000005` native crash in
   `test_phase95_hardening.py` that reproduces on the pre-change base. The
   implementer reported that the FIRST full-suite run died with that crash
   and the re-run was clean - report what you actually see, and attribute
   neither to this change.
9. **No credential touched**: the developer's real `backend/.env` is never
   copied, read, printed or diffed; no real `~/.sythion` is copied anywhere;
   and nothing is written outside a scratch tree.

## Write

- `C:\Sythion\sythion\.orchestra\verifications/020-v4.md` (the only file you may write)

## Output contract

- `- Reviewed commit: <sha>`
- `VERDICT: FIXED` / `NOT FIXED` / `REGRESSED` as the FIRST line
- The pinned invariant checked BY NAME
- The containment measurement, the two-site teeth proof, and the control
- The regression check with numbers
- What you did NOT check

## Constraints

- No source edits to the committed branch. No push. No destructive commands.
- Never copy, read or print the developer's real `backend/.env`, and never
  let a real `~/.sythion` be copied anywhere. Never write outside a scratch
  tree in any probe.
- Run the full backend suite in the BACKGROUND and poll it to completion.
- If something cannot be determined, say so in the report rather than
  assuming it.

Reply with a short summary. The FILE is the deliverable.
