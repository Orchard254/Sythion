# Brief: implement - 021 - attempt 1

- Item: 021 The onboarding inference gate must parse the provider's real response shape, and the UI must show the backend's reason
- Role: implementer
- Goal: implement `plans/021.md` exactly. Two halves:
  (a) the backend parses the NATIVE Ollama `/api/chat` shape, so a successful
  model answer stops being reported as "empty response" (502);
  (b) the frontend surfaces the backend's `detail` instead of axios's generic
  "Request failed with status code NNN".

## Read

- `C:\Sythion\sythion\.orchestra\plans/021.md` - the binding plan. Read it in
  full; the parser order, the behaviour table and the risk list are binding.
- `C:\Sythion\sythion\.orchestra\research.md` - R-028 and R-029 (R-028 has the
  verbatim live-API body).
- `C:\Sythion\sythion\backend\api\routes.py:2186-2238` and
  `backend\agents\reasoner.py:310-318` (the pattern to match).

## Branch / worktree

- Branch: `orchestra/021-onboarding-inference-shape`, from master (current HEAD).
- Worktree: `C:\Users\orcha\AppData\Local\Temp\opencode\wt-021`.
- Small commits on the branch. Do NOT push. Do not touch master directly.

## Write (code) - EXACTLY four files

1. `backend/api/routes.py` - the private helper `_extract_inference_text` and the
   two-line change in the endpoint body. `Any` is already imported (`:11`), and
   `set_secret_store_for_testing` exists in `backend/core/secrets.py:132`.
2. `frontend/src/services/api.ts` - the response interceptor + two module-private
   helpers, registered AFTER the existing request interceptor. The request
   interceptor, `getControlToken` and `configureBridge` must stay byte-identical.
3. `backend/tests/test_onboarding_inference_response_shape.py` (NEW) - the 13 tests.
4. `frontend/src/services/api.test.ts` (NEW) - the 9 tests.

No other file. `frontend/package.json` MUST be unchanged (no new dependency).

## CORRECTION to the plan - apply this

The plan's behaviour table row 4 and its test F3 spell the fixture slug
**`VAULT_WRITE_BACKLED`** (a D for an L) while expecting **`VAULT_WRITE_BLOCKED`**.
Use the CORRECT spelling in both the fixture and the expectation - the real
shape is `backend/api/schemas.py`'s `ErrorResponse.error` and
`backend/main.py`'s `VAULT_WRITE_VIOLATION_ERROR`, which is
`"VAULT_WRITE_BLOCKED"`. A verbatim copy of the plan would assert a string the
backend never sends.

## MANDATORY safety rules

- **No test may call the provider.** The recorded body is a CONSTANT in the test
  file. No socket, no credential read, no live API. The test file must install
  `set_secret_store_for_testing(MemoryStore())` for its duration and reset it to
  `None` in teardown, so it can neither read nor write the real Windows
  Credential Manager.
- The frontend test must use axios's own per-request `adapter` hook - NOT
  `axios-mock-adapter` (which is not installed and would change
  `frontend/package.json`).
- The new backend test file must satisfy the 019 tautology guard: no `or True`,
  no `and False`, no bare `assert True`. The new frontend test must pass
  `npm run lint` with `--max-warnings 0` and must type-check under `strict`
  (use `unknown` + one narrow cast; `any` is a lint warning).
- The endpoint's contract must not change: same path, method, status codes
  (400/400/200/502/401/503), same response keys, no schema change, no new route.

## Verify (paste all of it)

- `python -m pytest backend/tests/test_onboarding_inference_response_shape.py -q`
  - all pass.
- **The reproduction**: in a THROWAWAY worktree at the branch BASE, copy the new
  test file in and run it - the endpoint tests must FAIL with 502-vs-200
  (a BEHAVIOURAL failure, not an import error). Paste that failure. Delete the
  throwaway worktree afterwards.
- `cd frontend && npx vitest run src/services/api.test.ts` - all pass.
- `cd frontend && npm test` - the whole vitest suite green; report the file and
  test counts before and after.
- `cd frontend && npm run lint` - exit 0, no warnings.
- `cd frontend && npm run build` - exit 0.
- The collected count: `python -m pytest backend/tests --collect-only -q` before
  and after (the plan predicts +13).
- The FULL backend suite: `python -m pytest backend/tests -q`. Run it in the
  BACKGROUND and poll to completion - it takes 4-9 minutes; do not report until
  it has actually finished. If it dies natively, re-run once and report BOTH.
  **NOTE:** another agent may be running a full suite concurrently on this
  machine, which can make the known intermittent native faults more likely -
  if a run dies, re-run and say so in the report.

## Write (report)

- `C:\Sythion\sythion\.orchestra\reports\021-implement-1.md` - commit sha, files
  changed, every command's output, the base-reproduction failure, the measured
  baseline vs post-change counts, the parser order as implemented, the
  interceptor's behaviour table as implemented, and what was NOT checked
  (including: the packaged Tauri shell path was not exercised).
  **CORRECTION 2026-10-22:** an earlier version of this brief told the
  implementer to report "CI runs no vitest step, so the frontend test gates
  locally only". **That was false** - the `frontend` CI job runs
  `npm test` -> vitest at `.github/workflows/ci.yml:103-104` (added by item
  001, commit `3ec2d46`). The frontend test DOES gate CI. Do not report the
  opposite; check `ci.yml` before asserting anything about CI.

## Constraints

- No push, no deploy, no destructive commands.
- Do not weaken a test to make it pass. If a test fails, fix the cause or
  report it.
- Do not touch `frontend/src/screens/Setup.tsx` - it does not need to change and
  criterion 6 forbids it.

## Output contract

Report at `.orchestra/reports/021-implement-1.md` including the commit sha.
Reply with a short summary: the parser order as implemented, the base
reproduction result, the counts (backend collected/passed, vitest), and anything
you could not determine. The FILE is the deliverable.