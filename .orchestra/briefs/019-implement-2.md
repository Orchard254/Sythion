# Brief: implement - 019 - round 2 (wording corrections only)

- Item: 019 Tautology detector must also catch nested and bare `or True`
- Role: implementer (round 2)
- Branch: `orchestra/019-tautology-detector` (EXISTING - continue on it)
- Base of this round: `76c5c1d` (the commit the reviewer reviewed)
- Goal: fix the reviewer's three REQUIRED wording findings and one one-line
  hardening. **No behaviour, no scope, no collected-count change.**

## Read

- `C:\Sythion\sythion\.orchestra\reviews\019-r1.md` - F1, F2, F3 (required) and
  F5, F6, F7, F8 (non-blocking). This is the whole job.

## Worktree

- `C:\Users\orcha\AppData\Local\Temp\opencode\wt-019` already exists on the
  branch (re-create from the branch if it is gone). Work there.

## Required changes

1. **F1 - `backend/tests/test_conversation_memory.py`.** The comment above the
   new assertion still ends with "by checking that the verification log message
   was emitted (implicitly tested by no exception)". The explicit assertion
   `assert "Verification failed" in caplog.text` is four lines below, so the
   parenthetical is now FALSE. Delete the "(implicitly tested by no exception)"
   clause and the now-redundant "so we just verify the code path ran" sentence.
   The review's suggested replacement: "... the verification log message is
   emitted (asserted below)". One line, no behaviour.

2. **F2 - `backend/tests/test_no_tautological_asserts.py`, the failure message.**
   It says "... in a boolean position (an operand of a conjunction or a
   disjunction, or the operand of a negation)". The rule also flags the BARE
   literal - the assertion's own test - which the message does not mention.
   Add "or the assertion's own test" to the parenthetical. Message text only.

3. **F3 - `backend/tests/test_no_tautological_asserts.py`, the opening paragraph.**
   "the literal decides that position instead of the code under test, so the
   assertion verifies nothing" is false for two of the four shapes the rule now
   flags (`assert (x or True) and y` still verifies `y`; `assert not (x or True)`
   fixes the outcome at false) and is contradicted by the file's own third
   paragraph. Reword the clause (the review suggests "... so the assertion does
   not check the property its shape suggests", or drop the clause - the third
   paragraph already carries the precise statement).

4. **F7 - `backend/tests/test_no_tautological_asserts.py`, `_source()`.**
   `dict(zip(("or","true","and","not","assert"), _TAUTOLOGY_PARTS))` truncates
   silently, so adding a part to the middle of the tuple would SHIFT the
   mapping instead of failing. Fix shape: an explicit index map, or
   `assert len(parts) == 5` before the zip. One line.

## Also do (comment-level clarifications, no behaviour change)

- **F5**: state in the comment next to `assert len(turns) == 2` that the count
  is what the test's own session sees (autoflush), NOT a persistence check. Do
  NOT change the assertion itself.
- **F6**: add one clause to the comment at site 2 in
  `backend/tests/test_phase12_planning_learning.py` making explicit that the
  comparison pins PERSISTED state (an uncommitted mutation is discarded by
  `db.expire_all()` and is not detected). Do NOT change the assertion itself.
- **F8**: do NOT change the `caplog.text` assertion. You may note in a comment
  that `caplog.text` is the root handler's text rather than the module logger's.

## Do NOT do

- Do not change the detector's RULE, the probes, the self-test's structure, the
  test ids, or any assertion's semantics.
- Do not add, remove, rename, skip or mark any test.
- `git diff --name-only master..HEAD` must still be EXACTLY the same three
  files, and the collected count must still be 1628.
- Do not add an exemption mechanism.

## Verify (paste all of it)

- `python -m pytest backend/tests/test_no_tautological_asserts.py -q` - 2 passed.
- `python -m pytest backend/tests/test_conversation_memory.py backend/tests/test_phase12_planning_learning.py -q` - 63 passed.
- `python -m pytest backend/tests --collect-only -q` - 1628 collected.
- A whole-tree scan with the new rule - 0 violations.
- The full suite: `python -m pytest backend/tests -q` (BACKGROUND, poll to
  completion; it takes 4-6 minutes). Re-run once if it dies natively.

## Write

- `C:\Sythion\sythion\.orchestra\reports\019-implement-2.md` - commit sha, the
  exact diff (text only), every command's output, and a statement that no
  behaviour changed.

## Constraints

- Commit on the SAME branch. Do not push. Do not touch master.
- No destructive commands.

## Output contract

Report at `.orchestra/reports/019-implement-2.md`. Reply with a short summary
and the commit sha. The FILE is the deliverable.