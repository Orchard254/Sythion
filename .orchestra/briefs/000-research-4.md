# Brief: research - 000 - cycle 4 (fresh pass on post-merge tree)

- Item: 000 Research pass (cycle 4) - scout for new candidates after the
  follow-up merge cycle (`007` relocation, `008` durability, `009`, `011`,
  `012`, `013`, `015`, `016`).
- Role: research (read-only, reports only)
- Goal (the slice that matters): the previous research queue (R-001..R-024)
  is fully adopted or rejected. The follow-up cycle landed several more
  changes (event-ledger atomicity, process-env neutralization, vault guard
  relocation, operator endpoint resolution, doc sweep). A fresh pass is needed
  to find (a) new debt those changes may have introduced, (b) candidates that
  were deferred and are now tractable, (c) any remaining code-level defect
  worth an item. Live/network/external actions are OUT of scope (they stay
  follow-ups).

## Read

- `C:\Sythion\sythion\.orchestra\goal.md`
- `C:\Sythion\sythion\.orchestra\follow-ups.md` - the open items; pick the
  ones that are *code-fixable* (not human/external) and re-evaluate.
- `C:\Sythion\sythion\.orchestra\critiques.md` (tail) - know what was just
  accepted/merged and what risks were flagged.
- `C:\Sythion\sythion\.orchestra\research.md` - existing candidates, to avoid
  duplicating.
- `C:\Sythion\sythion\backend/runtime/events.py` (newly refactored - audit
  for the three risks the r1 reviewer flagged, now supposedly closed).
- `C:\Sythion\sythion\backend/tests/conftest.py` (process-env isolation +
  relocated vault guard).
- `C:\Sythion\sythion\scripts/` and `cli.py`, `backend/cli.py`,
  `scripts/smoke_test.py` (operator endpoint resolution).
- `C:\Sythion\sythion\docs/TESTING.md`, `docs/OPERATIONS.md` (post-sweep).

## Write

- `C:\Sythion\sythion\.orchestra\reports\000-research-4.md` - the ONLY file
  you may write. Do not modify any other file, do not branch, do not commit.
- In the report, propose up to 3 NEW candidates with: id (use R-025+
  numbering), source, evidence (file:line or command+output), files touched,
  severity, and whether it is code-fixable or human-action. Also revisit the
  open follow-ups and state which are now tractable for a code item.

## Constraints

- Read-only. No web fetches (network may be unavailable). No test runs beyond
  trivial greps. No source edits.
- Do not duplicate existing candidates. Cross-reference by R-id.
- Separate "code-fixable" from "human/external" clearly.

## Baseline (do not re-measure)

- Suite: 1621 collected (post-merge). Do not re-run the full suite.