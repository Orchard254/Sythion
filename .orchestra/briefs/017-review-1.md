# Brief: review - 017 - round 1

- Item: 017 Event ledger counter-table flag must not stick after a rolled-back append
- Role: reviewer (do not modify code; review only)
- Reviewed commit: `9b5b4531508eb36d1902252a182e275b8f955705` (`orchestra/017-counter-flag`)
- Goal: judge the diff against `plans/017.md`: correctness, simplicity, scope,
  and whether it meets each acceptance criterion. Work in your OWN worktree
  checked out at the reviewed commit - never the implementer's tree or master's.

## Read

- `C:\Sythion\sythion\.orchestra\plans\017.md`
- `C:\Sythion\sythion\.orchestra\backlog.md` item 017
- The diff: `git diff master..9b5b453` (or in your worktree `git show 9b5b453`)
- `backend/runtime/events.py`, `backend/tests/test_event_ledger_counter_flag.py`,
  `backend/core/db_manager.py:148-158` (verify untouched)

## Worktree

- Create `C:\Users\orcha\AppData\Local\Temp\opencode\wt-017-review` checked out at
  `9b5b4531508eb36d1902252a182e275b8f955705` (detached). Do not reuse wt-017.

## Write

- `C:\Sythion\sythion\.orchestra\reviews\017-r1.md` (the only file; no code edits)
- Also may run read-only commands (`git`, `python -m pytest --collect-only`,
  targeted test runs) inside your worktree; do not edit source.

## Output contract

Review report with at least:
- `- Reviewed commit: 9b5b4531508eb36d1902252a182e275b8f955705`
- `- Tier: <NORMAL|EXTERNAL|SENSITIVE|SENSITIVE+EXTERNAL>` (run classifier if possible)
- Scope check: exact `git diff --name-only` vs plan
- Per-acceptance-criterion judgement (each of 017's criteria)
- Risks found, verdict `ACCEPT`/`IMPROVE`/`REJECT` with reason

Reply with a short summary. The FILE is the deliverable.