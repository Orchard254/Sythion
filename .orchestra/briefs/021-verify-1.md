# Brief: verify - 021 - round 1

- Item: 021 The onboarding inference gate must parse the provider's real response shape, and the UI must show the backend's reason
- Role: verifier (judge the OUTCOME; do not edit source)
- Reviewed commit: `a1e2e5a` (branch `orchestra/021-onboarding-inference-shape`)
- Goal: decide whether the reported problem is actually gone. The user's failure
  was: the provider answers, the app reports "Request failed with status code
  502" and refuses to complete first-time setup. Verify that the provider's
  real answer is now understood, and that the operator can read WHY when
  something still fails.

## Read

- `C:\Sythion\sythion\.orchestra\plans/021.md` (binding)
- `C:\Sythion\sythion\.orchestra\backlog.md` item 021 (the seven criteria)
- `C:\Sythion\sythion\.orchestra\research.md` - R-028 (the verbatim live-API body)
- `C:\Sythion\sythion\.orchestra\reports/021-implement-1.md`

## Worktree

- Create `C:\Users\orcha\AppData\Local\Temp\opencode\wt-021-verify`, detached at
  `a1e2e5a`. A throwaway worktree at the branch base is allowed for the
  reproduction; remove it when done.

## What to verify

1. **Reproduction, fail on base / pass on branch.** In a throwaway worktree at
   the base, copy the new backend test file in and run it: the ENDPOINT tests
   must FAIL **behaviourally** (502 vs 200), not with a collection/import error.
   In your worktree they must pass. Paste both.
2. **The pinned invariant, by name**: *a provider answer in the native Ollama
   `/api/chat` shape is understood and reported as a success, not as an empty
   response.* Measure it against the **verbatim** body from R-028's evidence -
   `{"model":"minimax-m3.1-flash","message":{"role":"assistant","content":"OK"},"done":true,"done_reason":"stop","eval_count":2}` -
   through the REAL endpoint: status 200, `success is True`,
   `response_preview == "OK"`, `model` echoed. Then confirm the other two
   shapes still work (OpenAI-compatible and legacy) - the fix must not have
   traded one shape for another.
3. **The 502 is still honest.** A body with no text, and a malformed body, must
   still give 502 - not 200, not 500. A parser that reports success for an
   empty answer would let the setup gate advance on a lie.
4. **The endpoint's contract is unchanged**: 400 "No model selected"; 400
   "Model 'X' is not available"; 200 with exactly the keys
   `{"success","model","response_preview"}`; 502 for an empty answer. Confirm
   `app.openapi()` is identical between base and branch, and that
   `backend/api/schemas.py` is not in the diff.
5. **The frontend surfaces the reason.** Drive the real axios instance and
   measure the resulting `error.message` for: a 502 with `detail`; a 502 with
   no body; a 502 with an `error` slug and no detail; a body with both; a
   non-JSON/raw body; a network error with no response; and a successful
   response. The measured table must match the plan's table, the promise must
   still REJECT on every error case, and the success case must be unchanged.
6. **The token cannot leak and the error cannot be swallowed**: the message
   never contains the control token; the rejection is still an `AxiosError`
   with `response` and `status` reachable.
7. **Scope**: `git diff --name-only master..a1e2e5a` is exactly the four files;
   `frontend/package.json` unchanged.
8. **No regression**: the backend suite on the branch, and a PER-TEST JUnit
   comparison against the base (the collected count moves by exactly the new
   tests, the skipped count is unchanged, and NO existing test's verdict
   changes). The frontend suite is green and its test count moved by exactly
   the new tests.
9. **No live API, no credential**: confirm by inspection and by grep that no
   test calls `https://ollama.com`, reads the credential vault, or opens a
   socket. The recorded body must be a constant in the test file.

## Write

- `C:\Sythion\sythion\.orchestra\verifications/021-v1.md` (the only file you may write)

## Output contract

- `- Reviewed commit: a1e2e5a`
- `VERDICT: FIXED` / `NOT FIXED` / `REGRESSED` as the FIRST line
- Reproduction result with command + output
- The pinned invariant checked BY NAME
- Each of the seven acceptance criteria checked against reality
- The regression check with numbers
- What you did NOT check

## Constraints

- No source edits to the committed branch. No push. No destructive commands.
- **Do not call the live provider and do not read the credential vault.** The
  evidence for the live behaviour is R-028's recorded body; the tests must
  replay it, not re-fetch it.
- If the frontend suite needs `npm ci` in your worktree, that is fine - but the
  MAIN tree's `frontend/node_modules` is missing `react-test-renderer`
  (pre-existing, reported by the implementer), so do not "fix" the main tree.
- Do not ask questions; if something cannot be determined, say so in the report.

Reply with a short summary. The FILE is the deliverable.