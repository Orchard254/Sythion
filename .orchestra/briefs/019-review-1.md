# Brief: review - 019 - round 1

- Item: 019 Tautology detector must also catch nested and bare `or True`
- Role: reviewer (review only - no source edits)
- Reviewed commit: `76c5c1d` (branch `orchestra/019-tautology-detector`)
- Goal: judge the diff against `plans/019.md` (the ROUND 2 plan), the amended
  backlog item 019, and the acceptance criteria. Work in your OWN worktree
  checked out at the reviewed commit.

## Read

- `C:\Sythion\sythion\.orchestra\plans\019.md` (round 2 - binding)
- `C:\Sythion\sythion\.orchestra\backlog.md` item 019 (note the AMENDED criterion 5)
- `C:\Sythion\sythion\.orchestra\reports\019-implement-1.md`
- The diff: `git diff master..76c5c1d`

## Worktree

- Create `C:\Users\orcha\AppData\Local\Temp\opencode\wt-019-review`, detached at
  `76c5c1d`. Do not reuse wt-019.

## What to check (be adversarial)

1. **Scope**: `git diff --name-only master..76c5c1d` must be EXACTLY
   `backend/tests/test_no_tautological_asserts.py`,
   `backend/tests/test_conversation_memory.py`,
   `backend/tests/test_phase12_planning_learning.py`. Nothing else.
2. **No exemption mechanism**: grep the guard file for `exempt`, `KNOWN`,
   `allow`, `skip`, `except` and read it in full. There must be no exemption
   list, no stale-list check, no per-file allowlist, no path-based carve-out.
   The rule must apply uniformly to the whole tree.
3. **The rule is what the plan says**: a guard position is reached by descending
   only through `ast.BoolOp` values and `ast.UnaryOp(ast.Not)` operands;
   everything else (Compare, Call, Subscript, containers) is an opaque leaf.
   Verify by reading the code AND by probing it: `assert x is True`,
   `assert flag == True`, `assert (a and b) is True`, `assert f(x or y)`,
   `assert x or False` must NOT be flagged; `assert True`,
   `assert (x or True) and y`, `assert not (x or True)`, `assert x or (y or True)`
   MUST be flagged.
4. **The whole-tree scan**: run the detector over `backend/tests/` yourself
   (a throwaway script outside the repo, or by running the guard) and confirm
   **0 violations**. Then check that the number of collected tests is unchanged
   and that the guard file itself contains no forbidden sequence on any line.
5. **The two replacement assertions are REAL, not tautologies in disguise**:
   - `backend/tests/test_conversation_memory.py` - read the whole test function.
     Does `assert len(turns) == 2` assert a property of the production code, or
     of the mock? Is the number 2 actually produced by the production path, and
     would the assertion fail if the production code did not commit the turns?
     Is the `caplog` assertion capable of failing (does the warning actually get
     logged, and would it be absent if the early-return path did not run)?
     **Also check the COMMENTS around it**: the comment above the assertion
     still says the log message is "implicitly tested by no exception", which
     the new explicit assertion contradicts. Say whether that comment is now
     false and must be corrected.
   - `backend/tests/test_phase12_planning_learning.py` - read
     `test_learned_planning_cannot_change_runtime_permissions` in full. Does
     `assert goal_state() == before` compare the state BEFORE and AFTER the
     selection? Is the snapshot genuinely non-empty (does the helper's own
     assert make an empty snapshot fail rather than pass)? Would the assertion
     detect a mutation of the goal row?
6. **The guard does not flag ITSELF**: the file must not contain, on any single
   line, a keyword followed by the boolean literal, nor a bare assertion of the
   literal. Check with a textual scan, not just by reading.
7. **The self-test's probes**: one probe per new shape, inside the EXISTING
   self-test (no new test function - the collected count must be unchanged).
   Check that removing a probe's tautology makes the probe pass and
   re-introducing it fails again - i.e. that each probe has teeth.
8. **The docstring and the failure message are ACCURATE**: they must describe a
   SYNTACTIC rule, not claim "unconditionally true" (a literal under a negation
   makes the outcome always FALSE).
9. **Regression**: the report claims 1583 passed / 45 skipped on the branch and
   the same on master, with two native crashes (the known Fault-B) in the first
   two runs. Judge whether that is a sufficient basis for criterion 4/5, and
   whether any part of the claim is unsupported.

You may run read-only commands and targeted test runs in your worktree. If you
need a probe script, put it OUTSIDE the repository (TEMP) and delete it after.

## Write

- `C:\Sythion\sythion\.orchestra\reviews\019-r1.md` (the only file you may write)

## Output contract

- `- Reviewed commit: 76c5c1d`
- `- Tier: <NORMAL|EXTERNAL|SENSITIVE|SENSITIVE+EXTERNAL>` (run the skill's
  classifier if available; otherwise state your own and why)
- Scope check result
- Per-acceptance-criterion judgement (019's five criteria)
- Findings, each with a concrete fix shape
- `VERDICT: ACCEPT` / `IMPROVE` / `REJECT` with the reason

Reply with a short summary. The FILE is the deliverable.