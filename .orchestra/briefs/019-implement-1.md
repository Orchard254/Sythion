# Brief: implement - 019 - attempt 1

- Item: 019 Tautology detector must also catch nested and bare `or True` (R-027)
- Role: implementer
- Goal: implement `plans/019.md` (round 2, the revised plan) exactly. The guard
  must detect a literal `True` at any GUARD POSITION (reached through `ast.BoolOp`
  values and `ast.UnaryOp(ast.Not)` operands), must flag a bare `assert True`,
  and must apply UNIFORMLY to the whole tree with **no exemption list of any
  kind**. The two pre-existing placeholder `assert True` sites are FIXED, not
  exempted.

## Read

- `C:\Sythion\sythion\.orchestra\plans\019.md` - the binding plan (round 2).
  Read it in full before writing anything.
- `C:\Sythion\sythion\backend\tests\test_no_tautological_asserts.py` - the guard.
- `C:\Sythion\sythion\backend\tests\test_conversation_memory.py` (the test
  ending at the placeholder around line 637) and
  `C:\Sythion\sythion\backend\tests\test_phase12_planning_learning.py`
  (`test_learned_planning_cannot_change_runtime_permissions`, around line 366).

## Branch / worktree

- Branch: `orchestra/019-tautology-detector`, from master (25a9c02 + state commit).
- Worktree: `C:\Users\orcha\AppData\Local\Temp\opencode\wt-019`.
- Small commits on the branch. Do NOT push. Do not touch master directly.

## Write (code) - exactly three files

1. `backend/tests/test_no_tautological_asserts.py`
   - the recursive `_is_tautology` / updated `_find_tautologies` per the plan's
     guard-position rule; `_find_tautologies` keeps its signature and its
     `(lineno, expression)` return shape;
   - the self-test gains one probe per new shape (the plan specifies ~17 probes
     inside the EXISTING self-test - do NOT add a new test function, so the
     collected count does not change);
   - the docstring and the failure message state what the rule actually finds
     (a literal `True` in a guard position), NOT "unconditionally true";
   - **NO exemption list, no `_KNOWN_VACUOUS_SITES`, no stale/over-use checks.**
2. `backend/tests/test_conversation_memory.py` - replace the placeholder
   `assert True` with the plan's real assertion (the `turns` count AND the
   `caplog` warning check, with the signature/docstring updates the plan names).
3. `backend/tests/test_phase12_planning_learning.py` - replace the placeholder
   `assert True` with the plan's real assertion (the goal-state snapshot
   comparison, with the local helper the plan names).

## Verify (paste all of it)

- `python -m pytest backend/tests/test_no_tautological_asserts.py -q` - 2 passed.
- `python -m pytest backend/tests/test_conversation_memory.py backend/tests/test_phase12_planning_learning.py -q`
  - 63 passed (same as before the change; the two files must stay green).
- A whole-tree scan of `backend/tests/` with the new rule: **0 violations**,
  and the collected count is unchanged.
- The collected count of the whole tree is UNCHANGED (the plan says 1628 today).
  Confirm with `python -m pytest backend/tests --collect-only -q`.
- The full suite: `python -m pytest backend/tests -q` (from the repository root
  in the worktree). Paste the result. If the suite dies natively, re-run once
  and report both.

## Write (report)

- `C:\Sythion\sythion\.orchestra\reports\019-implement-1.md` - commit sha, files
  changed, the rule as implemented, every command's output, the whole-tree scan
  numbers, and what was NOT checked.

## Constraints

- `git diff --name-only` must list EXACTLY the three files above. No production
  file, no other test file.
- No exemption mechanism of any kind.
- Do not weaken the guard to make it pass. If the whole-tree scan reports a
  violation you did not expect, fix the CAUSE or report it - do not add an
  exemption.
- No destructive commands, no push, no deploy.

## Output contract

Report at `.orchestra/reports/019-implement-1.md` including the commit sha.
Reply with a short summary: the rule, the scan numbers, the suite result, and
anything you could not determine. The FILE is the deliverable.