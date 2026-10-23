# Scout brief 000-scout-6

You are the Scout for the Sythion repository.

## Inputs
- `.orchestra/goal.md`
- `.orchestra/research.md`, especially new candidate R-030.
- `.orchestra/backlog.md`
- `.orchestra/log.md`

## Task
Adopt R-030 into `.orchestra/backlog.md` as a ranked item (likely the current top item), and mark R-030 `adopted` with the item id via only its `- Status:` line in `research.md`.

Do not modify candidate bodies. Mirror status into `board.json` appropriately. If you judge it should not be adopted, mark it rejected with reason.

## Priority and evidence context
- R-030 candidate: intermittent concurrency admission exceeding `max_specialists_per_task`.
- Research classified production vs harness (StaticPool) as unknown and explicitly recommends reproducing with file-backed SQLite.

## Requirements for the item
- id, title, why matters tied to goal, numbered verifiable acceptance criteria, size S/M/L, risk low/med/high, likely files, predicted sensitivity tier and domain areas (separate fields), rank position.
- No manufacturing. If nothing should be adopted, say so.

## Output contract
- write report `reports/000-scout-6.md`
- Update `backlog.md`
- Update `research.md` Status line only and board.json.
- State what was not checked.
