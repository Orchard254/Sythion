# Brief: verify - 019 - round 1

- Item: 019 Tautology detector must also catch nested and bare `or True`
- Role: verifier (judge the OUTCOME; do not edit source)
- Reviewed commit: `1304ec7` (branch `orchestra/019-tautology-detector`)
- Goal: decide whether the reported problem is actually gone - the guard that
  fails the suite on an unconditionally-true assertion must now catch the
  nested and bare shapes too, and the two pre-existing placeholder assertions
  must be real assertions - and whether anything else broke.

## Read

- `C:\Sythion\sythion\.orchestra\plans\019.md` (round 2, binding)
- `C:\Sythion\sythion\.orchestra\backlog.md` item 019 (note the AMENDED criterion 5)
- `C:\Sythion\sythion\.orchestra\reviews\019-r1.md`, `019-r2.md`, `019-r3.md`
- `C:\Sythion\sythion\.orchestra\reports\019-implement-1.md`, `-2.md`, `-3.md`

## Worktree

- Create `C:\Users\orcha\AppData\Local\Temp\opencode\wt-019-verify`, detached at
  `1304ec7`. A throwaway worktree at `master` for the base comparison is
  allowed; remove it when done.

## What to verify

1. **Reproduction, fail on base / pass on branch.** In a throwaway worktree at
   master, put a probe file (outside the guarded tree if you can - or inside a
   SCRATCH copy of the tree) containing the four R-027 shapes and show the
   guard does NOT flag them on base; then in your worktree at `1304ec7` show
   the same probe IS flagged (the guard fails, exit 1). Say exactly how you
   built the probe - the guard scans `backend/tests/` of the tree it runs in, so
   a probe must live in a tree the guard scans.
2. **The rule, measured**: an assert is flagged iff a literal `True` sits at a
   guard position (reachable through `ast.BoolOp` values and
   `ast.UnaryOp(ast.Not)` operands only). Verify by probing the shipped
   detector: the four R-027 shapes are flagged; `assert x is True`,
   `assert flag == True`, `assert (a and b) is True`, `assert f(x or y)`,
   `assert x or False` are NOT.
3. **Uniformity**: there is NO exemption mechanism. Grep the guard file for
   `exempt|KNOWN|allow|skip|ALLOW|EXEMPT|waiver|grandfather` and read it in
   full. The rule must apply to the whole tree with no carve-out.
4. **The whole-tree scan**: run the detector over `backend/tests/` yourself -
   it must report **0 violations**. Also confirm the guard's own file contains
   no forbidden sequence on any line (a keyword followed by the literal, or a
   bare assertion of it).
5. **The two replacement assertions are REAL** (not tautologies in disguise):
   - `backend/tests/test_conversation_memory.py` - does `assert len(turns) == 2`
     assert a property of the production code? Would it fail if the production
     code did not commit the turns? Is the `caplog` assertion capable of
     failing? Demonstrate both with a negative control (a throwaway COPY of the
     test, in TEMP, with the mocked read-back removed) - do NOT edit the real
     test file.
   - `backend/tests/test_phase12_planning_learning.py` - does
     `assert goal_state() == before` compare before/after a selection, is the
     snapshot genuinely non-empty, and would a mutation be detected? Negative
     control in a TEMP copy.
6. **Scope**: `git diff --name-only master..1304ec7` is exactly the three files.
7. **No regression**: the collected count is unchanged (1628) and the test ids
   of the three touched files are identical to master's. The full suite on the
   branch: `python -m pytest backend/tests -q` - and a PER-TEST comparison
   against master (`--junitxml`, compared per test, not by totals). The
   expected delta is zero changed outcomes and the same skipped count.

## Write

- `C:\Sythion\sythion\.orchestra\verifications\019-v1.md` (the only file you may
  write; no source edits)

## Output contract

- `- Reviewed commit: 1304ec7`
- `VERDICT: FIXED` / `NOT FIXED` / `REGRESSED` as the FIRST line
- Reproduction result with command + output
- Each acceptance criterion (the five from the backlog, with the amended 5)
  checked against reality
- The regression check with numbers
- What you did NOT check

## Constraints

- No source edits to the committed branch. No push. No destructive commands.
- Negative controls go in TEMP copies, never in the repository.
- Do not ask questions; if something cannot be determined, say so in the report.

Reply with a short summary. The FILE is the deliverable.