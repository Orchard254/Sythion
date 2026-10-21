VERDICT: ACCEPT

# Implementer report 014 round 1 — neutralise process environment in test isolator

Commit: `ba69cc203e606ae0cd0a69058bd054807843b1f7` (branch `orchestra/006-process-env`, off `master`).

## Scope
Single allowed product file changed: `backend/tests/conftest.py`. No other source files edited.
Deliverable report only: `.orchestra/reports/014-implement-1.md`.

## Change
`_clean_settings_snapshot()` now temporarily removes every `SYTHION_*` variable from `os.environ` while constructing the clean `Settings(_env_file=None)` instance, restoring them afterwards. The autouse `_isolate_local_env_settings` fixture also clears every `SYTHION_*` variable from `os.environ` for the duration of each non-`provider_e2e` test (restored at teardown by `monkeypatch`), in addition to assigning the clean field values to the `settings` singleton. The `provider_e2e` carve-out returns early and keeps the ambient environment in both halves.

## Acceptance criteria

1. **Process environment is neutralised.** PASS
   - Empirical subprocess check (`SYTHION_OLLAMA_API_KEY=LEAK_SENTINEL_XYZ` exported in child):
     - `python -m pytest backend/tests/test_setup_readiness.py::test_readiness_states -q` exited `0`.
     - Output contained `1 passed, 724 warnings`.
     - Sentinel appeared in neither stdout nor stderr.
2. **No regression in effective isolation.**PASS
   - `backend/tests/test_setup_readiness.py` full file: `11 passed`.
   - Clean-process (no `SYTHION_*` env) run of same test: `1 passed`.
3. **Provider-e2e carve-out preserved.**PASS
   - Fixture still returns early when `request.node.get_closest_marker("provider_e2e")` is set, so neither the settings nor `os.environ` is neutralised for those modules.
   - Import-time gating remains intact; full collection still includes `test_phase10_provider_e2e.py` and `test_phase975_provider_e2e.py`.
4. **Full suite.**PASS
   - `python -m pytest -q` (with an ambient `SYTHION_OLLAMA_API_KEY` present, proving the isolator's teeth): `1576 passed, 45 skipped` in `314.99s`.

## Limits / honesty

- Only `SYTHION_*` process variables are neutralised, not arbitrary environment variables. Other prefixes (if any are introduced later) must be added to the `SYTHION_`-prefixed loop or to `_LOCAL_ENV_FIELDS`.
- The snapshot half clears the environment only for the duration of snapshot construction and restores it; per-test clearing is done by `monkeypatch` inside the autouse fixture, so direct `os.environ` reads remain clean only inside non-`provider_e2e` tests.
- The (intentional) `provider_e2e` carve-out means those modules still inherit ambient credentials; live provider runs were not executed (`SYTHION_RUN_PROVIDER_E2E` not set).
- Nothing in production code changed; no test files were modified.

## Files changed
- `backend/tests/conftest.py` (+42 / -11)