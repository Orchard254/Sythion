# Implement report — item 023 (continuation)

## Scope

Implement item 023 from `C:\Sythion\sythion\.orchestra\plans\onboarding-repair-20261023.md` (lines 25-44): sync provider discovery is run through `run_in_threadpool`, frontend model IDs are normalized, and the two discovery regressions plus the Setup selector regression are present. No 024 work was performed.

This is the continuation of the stalled background task. The implementation files already existed in the worktree; this continuation verified them, ran the required gates, and committed the allowed files only. No product file was edited during this continuation.

## Working copy

- Worktree: `C:\Users\orcha\AppData\Local\Temp\opencode\sythion-023-implement`
- Branch: `orchestra/023-onboarding-discovery`
- Base / merge base: `7a9c2f5ea5cefa67edde14465890145ce9b74b47`
- Committed tip: `5f266827034d9c423294b2819934fca73dde75fe`
- Main checkout `C:\Sythion\sythion` was not modified.

## Files changed (allowed list only)

- `backend/api/routes.py` — `GET /models` now calls `await run_in_threadpool(provider.list_models)`; added `from starlette.concurrency import run_in_threadpool`.
- `backend/tests/test_onboarding_model_discovery.py` — new, 8 tests (5 route/auth tests + 1 offload heartbeat test).
- `frontend/src/services/api.ts` — `modelService.available` normalizes provider records to nonempty string IDs (`name`, then `model`), with runtime type checks.
- `frontend/src/services/modelDiscovery.test.ts` — new, 9 tests through real service/client.
- `frontend/src/screens/Setup.test.tsx` — discovery selector regression using actual object-to-ID boundary.

Commit: `5f26682 feat(023): implement onboarding model discovery`

## Verification run in this continuation

### 1. Backend isolated regression

Command:
```
python -m pytest backend/tests/test_onboarding_model_discovery.py backend/tests/test_control_boundary.py -q
```
Working directory: `C:\Users\orcha\AppData\Local\Temp\opencode\sythion-023-implement`

Result: `11 passed`, `729 warnings` in `1.64s`. No failures or errors.

Included tests:
- `test_authenticated_sync_discovery_preserves_provider_payload`
- `test_empty_discovery_is_a_success`
- `test_discovery_error_preserves_503_contract`
- `test_rejected_auth_never_invokes_provider[None]`
- `test_rejected_auth_never_invokes_provider[wrong-inert-token]`
- `test_sync_discovery_is_offloaded_and_loop_heartbeat_progresses` (thread/offload heartbeat proof, bounded 5s)
- four `test_control_boundary.py` tests.

### 2. Frontend vitest (focused)

Command:
```
npm exec -- vitest run src/services/modelDiscovery.test.ts src/screens/Setup.test.tsx src/services/api.test.ts
```
Working directory: `C:\Users\orcha\AppData\Local\Temp\opencode\sythion-023-implement\frontend`

Result: `3` test files, `22 passed`.

### 3. Frontend vitest (full)

Command:
```
npm test
```

Result: `11` test files, `90 passed`.

### 4. TypeScript

Command:
```
npm exec -- tsc --noEmit
```

Result: no output, exit code 0.

### 5. Lint

Command:
```
npm run lint
```

Result: no warnings/errors (exit code 0).

## Commit status

- `git add` was restricted to the five allowed files only.
- Commit created: `5f266827034d9c423294b2819934fca73dde75fe`.
- Working tree is clean after commit.
- No push or merge was performed.

## Negative controls / base evidence

- This continuation did not re-run the implementation on the pre-item base to demonstrate the expected raw base failures (`503` vs `200`, invalid selector contents). The implementation and tests were present from the stalled task; here only verification on the fixed tree was executed. This is reported as not independently re-proven in this continuation rather than inferred.
- The offload heartbeat test is present and passed on the fixed tree. Its deletion behavior (`deleting offload on fixed tree also fails`) was not separately executed in this continuation.

## Tests not run

- Full backend suite (`python -m pytest backend/tests -q`) was not run in this continuation; only the item-required backend files plus `test_control_boundary.py` were run.
- Base-tree negative-control replay was not run.
- No isolated real-browser/UI E2E was run (out of this item's scope and not authorized here).

## Issues / limits

- No failures to report from the required gates.
- Tests were not weakened or modified during this continuation.
- No event-loop/asynchronous offload test is missing; `test_sync_discovery_is_offloaded_and_loop_heartbeat_progresses` exists and passed.
- No source edits beyond allowed files; packaging mirror was not touched.
- Because pre-item base negative-control replay was not executed here, the historical "fails on base" proof is claimed by the plan/brief only in this report, not independently re-measured in this continuation.

## Status

Item 023 remains in `building` status pending independent Reviewer/Verifier. This report is canonical implement report 023-implement-1.