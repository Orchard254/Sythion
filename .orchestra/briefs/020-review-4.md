# Brief: review - 020 - round 4 (the combined r3 + r4 delta)

- Item: 020 The `.env` isolation must be measured, not assumed, and cover the process environment
- Role: reviewer (review only - no source edits)
- Reviewed commit: **the tip of `orchestra/020-r3-probe-scrub`** - resolve it
  with `git rev-parse orchestra/020-r3-probe-scrub` and record the sha. Its
  base is `e558991`, which is ALREADY MERGED into master, so the whole delta
  under review is `e558991..<sha>` - rounds 3 and 4 together, so they can
  merge in one commit.
- Note: round 3 was already reviewed and ACCEPTed on its own
  (`.orchestra/reviews/020-r2.md` covered the round-2 delta; the round-3 and
  round-4 deltas are new). The round-2 review raised the LOW finding that
  both rounds 3 and 4 address.

## Read

- `C:\Sythion\sythion\.orchestra\briefs/020-implement-3.md` (round 3, binding)
- `C:\Sythion\sythion\.orchestra\briefs/020-implement-4.md` (round 4, binding)
- `C:\Sythion\sythion\.orchestra\reviews/020-r2.md` (the finding that started rounds 3 and 4)
- `C:\Sythion\sythion\.orchestra\reports/020-implement-3.md` and
  `reports/020-implement-4.md`
- The diff: `git diff e558991..<reviewed sha>`

## Worktree

- Create `C:\Users\orcha\AppData\Local\Temp\opencode\wt-020-r4-review`,
  detached at the reviewed sha. Do not reuse any other worktree.

## What to check

1. **Scope**: the whole delta `e558991..<sha>` touches ONLY
   `backend/tests/test_env_isolation.py`. No production file, no
   `backend/core/` file, no other test file.
2. **The root cause is understood, not just patched.** `backend/core/storage.py`
   honours exactly two profile-dir overrides: `DATA_DIR_ENV` (canonical) and
   `LEGACY_DATA_DIR_ENV` (legacy, honoured when the canonical is absent). The
   legacy name upper-cases to `SYTHION (SYNTH)_DATA_DIR` - a SPACE, not an
   underscore - which is why the isolation's `SYTHION_*` sweep never saw it.
   Read `storage.py` yourself and confirm the constants the code uses are
   THE PRODUCTION CONSTANTS, imported rather than re-spelled, and that the
   scrub is case-insensitive.
3. **The scrub is factored into ONE helper used by BOTH call sites**, so the
   migration probe and `_child_environment` cannot drift apart again. Read
   the code and confirm there is genuinely one implementation and two
   call sites - not two copies.
4. **Each site is independently load-bearing.** In TEMP copies (never the
   committed source), confirm:
   - removing `_child_environment`'s scrub makes the round-4 test RED with
     the ValueError "conflicting profile directories", while round 3's probe
     test stays GREEN;
   - removing the probe's own scrub makes round 3's probe test RED, while the
     round-4 test stays GREEN.
   Paste both. This two-site independence is the point of the round.
5. **The round-3 test still has all three of its assertions**, and each still
   has teeth: the resolved target, `migrated=False`, and the override's
   directory being empty. Removing the scrub must fail it.
6. **The round-4 test is honest**: it sets the legacy override in the PARENT
   environment, to a directory inside its OWN scratch tree, re-runs the
   measurement verbatim, and asserts the override's directory stays empty.
   Confirm it cannot write outside the scratch tree - that is the whole
   safety property.
7. **Nothing was weakened.** The signal line, the three-run structure, the
   timeout, the negative control's shape, the fence, the redaction helper and
   the leak-check ordering are all verified correct by the round-2 review and
   must be UNCHANGED. `_child_environment` must still remove only the
   profile-dir overrides and must still provide everything the child needs.
   Diff the test bodies, not just the file names.
8. **The 019 tautology guard** must hold, and the collected count must move by
   exactly +2 against `e558991` (1630 -> 1632) with the probe template still
   contributing 0.
9. **The negative control still works** - in a THROWAWAY worktree, remove
   `autouse=True` from `_isolate_local_env_settings` and confirm the
   measurement test still goes RED for the right reason. Delete the
   throwaway worktree afterwards.
10. **The report is honest about the native crash.** The implementer reports
    the full suite's FIRST run died with the known native `0xC0000005` in
    `test_phase95_hardening.py::validate_in_thread` and that the re-run was
    clean. Confirm the report contains BOTH runs' output rather than only
    the clean one, and that every path it cites exists.
11. **Merge posture**: confirm `git merge-tree --write-tree master <sha>`
    exits 0 - a clean merge.
12. **CI facts - read `.github/workflows/ci.yml` yourself** and report what it
    actually contains. Do not inherit a CI claim.

## Write

- `C:\Sythion\sythion\.orchestra\reviews\020-r4.md` (the only file you may write)

## Output contract

- `- Reviewed commit: <sha>`
- `- Tier: <NORMAL|EXTERNAL|SENSITIVE|SENSITIVE+EXTERNAL>`
- Scope check result
- Per-finding judgement: the round-2 finding closed? the round-3 residual
  closed? each site load-bearing?
- Any NEW finding, each with a concrete fix shape
- `VERDICT: ACCEPT` / `IMPROVE` / `REJECT` with the reason

## Constraints

- No source edits. Probe scripts and temp copies in TEMP only, deleted after.
- Do not run the full backend suite - the Verifier's job.
- Do not push. Never copy, read or print the developer's real `backend/.env`,
  and never let a real `~/.sythion` be copied anywhere. Never write outside
  a scratch tree in any probe.

Reply with a short summary. The FILE is the deliverable.
