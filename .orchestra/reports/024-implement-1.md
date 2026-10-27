# Implement item 024 — honest safe onboarding

- Branch: `orchestra/024-safe-onboarding`
- Worktree: `C:\Users\orcha\AppData\Local\Temp\opencode\sythion-024-implement`
- Base reviewed tip: `29d87986a259dd27637a581a105cc7ce166b29ae`
- Implemented tip: `fd3ef4c65d8c8a14f9b367ca68c81994bb9f6e81`
- Commit: `fd3ef4c65d8c8a14f9b367ca68c81994bb9f6e81` (`feat(024): make safe onboarding truthful and atomic`)

## Allowed files changed

Product/test files:
- `backend/api/routes.py`
- `backend/tests/test_onboarding_safe_settings.py` (new)
- `frontend/src/screens/Setup.tsx`
- `frontend/src/screens/Setup.test.tsx`

Orchestration bookkeeping files also updated:
- `C:\Sythion\sythion\.orchestra\critiques.md`
- `C:\Sythion\sythion\.orchestra\onboarding-run-20261023.md`
- `C:\Sythion\sythion\.orchestra\reports\024-implement-1.md`

No other product files were edited. `src-tauri/resources/backend/api/routes.py` was left untouched.

## Implementation summary

Backend `POST /setup/safe-settings`:
- Validates the runtime-visible effective autonomy expression exactly:
  `os.environ.get("SYTHION_AUTONOMY_LEVEL", settings.autonomy_level) == "guarded"`.
- Non-guarded effective authority returns HTTP 409 before any write, with no env/settings mutation.
- In guarded mode stages `retention_days="90"` and `setup_safe_initial_config_applied="true"` using `set_setting`, then one `db.commit`.
- Stage/flush/commit failures call `db.rollback()` and return HTTP 500 with a bounded message, never raw database detail.

Backend `GET /setup/readiness` (and aliases):
- `safe_settings_ok` is true only when marker is `"true"`, persisted retention is exactly `"90"`, and effective authority is guarded.
- Rechecked on every request; malformed or missing marker/retention remains false.

Frontend `Setup.tsx`:
- Removed the unused `settingsService` import.
- Continue now calls `setupService.applySafeSettings()`, checks `ok === true`, refreshes readiness, requires `canStartChat(report)` before advancing, and surfaces errors.

## Verification

Focused backend:
```text
cd C:\Users\orcha\AppData\Local\Temp\opencode\sythion-024-implement
$env:SYTHION_OLLAMA_API_KEY="test-key"
python -m pytest backend/tests/test_onboarding_safe_settings.py backend/tests/test_setup_readiness.py backend/tests/test_settings_api.py backend/tests/test_settings_store.py backend/tests/acceptance/test_importance_classifier.py backend/tests/test_tools.py -q
```
Result: `91 passed, 3 skipped`.

New safe-settings tests:
```text
python -m pytest backend/tests/test_onboarding_safe_settings.py -q
```
Result: `8 passed`.

Frontend:
```text
cd C:\Users\orcha\AppData\Local\Temp\opencode\sythion-024-implement\frontend
npm test
```
Result: `11 test files, 93 tests passed`.

Frontend type check:
```text
npm exec -- tsc --noEmit
```
Result: exit 0.

Frontend lint:
```text
npm run lint
```
Result: exit 0.

Full backend suite:
```text
cd C:\Users\orcha\AppData\Local\Temp\opencode\sythion-024-implement
$env:SYTHION_OLLAMA_API_KEY="test-key"
python -m pytest backend/tests -q -p no:cacheprovider
```
Result: `1619 passed, 45 skipped`.

## Base versus fixed

Base-negative controls (for example autonomous runtime 409, marker-only false readiness, failed-write rollback, and frontend rejection/`ok=false`/stale-readiness stay-on-step-1) were not re-executed as separate base-tree replay by this implementer. Focused regressions and full suites were run on the fixed tree; hidden independent review/verification remains responsible for base/fixed deltas. No tests were weakened.

## Not done / limits

- No production build, packaged mirror update, browser real-UI replay, commit-to-main merge, or deployment was performed.
- Frontend tests use the existing React test renderer mock patterns and do not exercise a live browser or actual conversation.