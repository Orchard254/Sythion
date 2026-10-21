# Brief: verify - 017 - round 1

- Item: 017 Event ledger counter-table flag must not stick after a rolled-back append
- Role: verifier (judge the OUTCOME; do not edit code)
- Reviewed commit: `9b5b4531508eb36d1902252a182e275b8f955705`
- Goal: determine whether the reported problem is actually gone. Reproduce the
  original problem against base (it must fail on base, pass on the branch),
  check each acceptance criterion against reality, and check nothing else broke.

## Read

- `C:\Sythion\sythion\.orchestra\plans\017.md` (acceptance criteria)
- `C:\Sythion\sythion\.orchestra\reviews\017-r1.md`
- `C:\Sythion\sythion\.orchestra\reports\017-implement-1.md`

## Worktree

- Create `C:\Users\orcha\AppData\Local\Temp\opencode\wt-017-verify` detached at
  `9b5b453`. Independently run the tests there. To test "fails on base", use
  the plan's stated base-tree check (stashing/restoring the counter-flag gating)
  OR rely on the reviewer's recorded evidence; do not destroy the branch.

## Write

- `C:\Sythion\sythion\.orchestra\verifications\017-v1.md` (the only file; no code edits)

## Output contract

Verification report with:
- `- Reviewed commit: 9b5b4531508eb36d1902252a182e275b8f955705`
- Reproduction result (fail on base / pass on branch) with command + output
- Each acceptance criterion checked against reality
- Regression check (existing tests still green; signatures/_to_dict unchanged)
- `VERDICT: FIXED` / `NOT FIXED` / `REGRESSED` at top

Reply with a short summary. The FILE is the deliverable.