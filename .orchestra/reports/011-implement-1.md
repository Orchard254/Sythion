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