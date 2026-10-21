# Brief: implement - 017 - attempt 1

- Item: 017 Event ledger counter-table flag must not stick after a rolled-back append (R-025)
- Role: implementer
- Goal: Apply the plan in `plans/017.md`: make `DbEventLedger` tolerate a rolled-back
  counter-table creation so a subsequent append allocates successfully, pin both
  variants with named regression tests, change only `backend/runtime/events.py` + a new
  test file. No schema/migration changes.

## Read

- `C:\Sythion\sythion\.orchestra\plans\017.md` (the plan - full)
- `C:\Sythion\sythion\backend\runtime\events.py` (current implementation)
- `C:\Sythion\sythion\backend\core\db_manager.py:148-158` (schema owner - do not touch)
- `C:\Sythion\sythion\backend\tests\test_event_ledger_sequence_invariant.py`
- `C:\Sythion\sythion\backend\tests\test_phase105_concurrency.py`

## Branch / worktree

- Branch: `orchestra/017-counter-flag`, from `master`.
- Create a worktree: `C:\Users\orcha\AppData\Local\Temp\opencode\wt-017`.
- Make small commits on the branch. Do not push. Do not touch master directly.

## Write (code)

- `backend/runtime/events.py` (modified): transaction-resident existence check
  replacing the sticky flag, per plan. `_counter_table_checked` (if retained) must
  never gate correctness.
- A new test file under `backend/tests/` with the two named regression tests:
  `test_counter_table_not_stuck_variant_a_caller_rollback` and
  `test_counter_table_not_stuck_variant_b_failed_allocation`, each persisting
  through a fresh `DbEventLedger`/commit+re-read. Both must fail on base, pass
  after.

## Write (report)

- `C:\Sythion\sythion\.orchestra\reports\017-implement-1.md` (report of what changed,
  test output pasted, commit sha).

## Constraints

- Only `backend/runtime/events.py` and a new test file may change (git diff --name-only).
- No changes under backend/models/, backend/migrations/, backend/core/db_manager.py.
- `append_event` and `event_history` signatures unchanged; `_to_dict` unchanged.
- Run the relevant tests and paste output. Also run the concurrency + invariant tests.
- Do not push, deploy, or run destructive commands. Do not touch credentials.
- No more than 2 commits beyond branch creation.

## Output contract

Report file at `.orchestra/reports/017-implement-1.md` including reviewed commit sha,
files changed, and test output. Reply with a short summary. The FILE is the
deliverable.