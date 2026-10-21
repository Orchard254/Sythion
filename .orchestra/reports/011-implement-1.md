# Item 011 — rename `gated_soak` marker to `learning_soak`

## Verdict

VERDICT: ACCEPT

## Change summary

Renamed the misleading `gated_soak` pytest marker to the honest name
`learning_soak` (no environment gate; runs in the default suite). Touches:

- `backend/tests/conftest.py` — `CANONICAL_MARKERS` entry.
- `backend/tests/test_phase115_soak.py` — `pytestmark` and the module
  docstring ("Gated soak test" -> "Learning soak test").
- `pytest.ini` — marker name + description (dropped the now-false
  "despite the name" clause; states it is not gated and runs in the
  default suite).
- `docs/TESTING.md` — marker-table row name + selectors + description.
- `.orchestra/*` historical references — mechanical token rename so
  `git grep gated_soak` is empty.

## Verification evidence

- `python -m pytest backend/tests/test_phase115_soak.py -q -p no:cacheprovider`
  -> `1 passed`.
- `python -m pytest backend/tests/test_phase115_soak.py -q -p no:cacheprovider --markers`
  shows `@pytest.mark.learning_soak: ...` and no `gated_soak`.
- `python -m pytest backend/tests --collect-only -q`
  -> `1614 tests collected` (unchanged).
- `python -m pytest backend/tests -m learning_soak --collect-only -q`
  -> `1/1614 tests collected` (learning_soak population remains 1).
- `git grep gated_soak -- .` -> no matches.

No test function names were renamed; the one test remains
`test_learning_soak_mixed_workload`.
PY

## Round-2 correction

Round-2 follow-up to the reviewer IMPROVE on item 011. No source or config
change in this commit (documentation only). Two claim corrections:

1. Product-scoped `gated_soak` search (reviewer finding #1). The report's
   earlier line "`git grep gated_soak -- .` -> no matches" was overstated:
   `git grep gated_soak .` returns 5 matches, all of them inside this report
   file (`.orchestra/reports/011-implement-1.md`) where the term is quoted
   for historical context. Scoped to product/config files the result is
   empty:
   - `git grep gated_soak -- src-tauri backend tests pytest.ini docs` -> 0.
   - Including archival/historical locations: 5 total occurrences,
     contained entirely in this report file.

2. Commit scope (reviewer finding #2). The implementer's commit is not "4
   product files" alone; it changed 30 files total: 4 product files
   (`backend/tests/conftest.py`, `backend/tests/test_phase115_soak.py`,
   `pytest.ini`, `docs/TESTING.md`) plus 26 `.orchestra/*` bookkeeping
   files (mechanical token rename of historical references). This is
   tolerable: `.orchestra/` is committed project state per the
   dev-orchestrator skill, and the product change itself is limited to
   those 4 files. Correct wording: "product change: 4 files; bookkeeping:
   26 `.orchestra` files."

Rename stability re-verified in this round (worktree `wt-011-redo`,
detached at b08b5d1):
- Collection: 1614 tests collected
  (`backend/tests --collect-only -q`).
- Full suite: 1569 passed, 45 skipped, 0 failures
  (`python -m pytest backend/tests -q -p no:cacheprovider`, 305.48s).