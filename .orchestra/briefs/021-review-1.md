# Brief: review - 021 - round 1

- Item: 021 The onboarding inference gate must parse the provider's real response shape, and the UI must show the backend's reason
- Role: reviewer (review only - no source edits)
- Reviewed commit: `a1e2e5a` (branch `orchestra/021-onboarding-inference-shape`; `7131b8a` is the backend half, `a1e2e5a` the frontend half)
- Goal: judge the diff against `plans/021.md` (binding) and the seven acceptance
  criteria in backlog item 021. Work in your OWN worktree at the reviewed commit.

## Read

- `C:\Sythion\sythion\.orchestra\plans\021.md` (binding - the parser order table and the interceptor behaviour table are the contract)
- `C:\Sythion\sythion\.orchestra\backlog.md` item 021 (the seven criteria)
- `C:\Sythion\sythion\.orchestra\reports\021-implement-1.md`
- The diff: `git diff master..a1e2e5a`

## Worktree

- Create `C:\Users\orcha\AppData\Local\Temp\opencode\wt-021-review`, detached at `a1e2e5a`.

## What to check (be adversarial)

1. **Scope**: `git diff --name-only master..a1e2e5a` must be EXACTLY
   `backend/api/routes.py`, `frontend/src/services/api.ts`,
   `backend/tests/test_onboarding_inference_response_shape.py`,
   `frontend/src/services/api.test.ts`. `frontend/package.json` MUST be unchanged.
2. **The parser's ORDER is the plan's order**: native `message.content` ->
   OpenAI `choices[0].message.content` -> `choices[0].text` -> legacy
   `response`; first non-blank stripped string wins. Verify by reading the code
   AND by probing it: a body carrying ALL FOUR shapes must return the NATIVE
   text. Each shape alone must return its own text.
3. **No shape was lost**: the old parser handled `choices[0].message.content`,
   `choices[0].text` and `response`. Confirm all three still work, and that
   the old behaviour for a body with BOTH `choices[0].message.content` and
   `choices[0].text` is unchanged (the old code preferred `.message.content`).
4. **The empty-answer guard is intact**: a body with no text (and a malformed
   body) must still produce 502, NOT 200 and NOT 500. A parser that reports
   success for an empty answer would let the setup gate advance on a lie.
5. **The endpoint's contract is unchanged**: same path, method, status codes
   (400 / 400 / 200 / 502 / 401 / 503), same response keys
   (`success`, `model`, `response_preview`). Read the diff and confirm the
   `except` mapping at the bottom of the endpoint was not touched, and that the
   `set_setting` evidence write still happens on success.
6. **The interceptor cannot swallow an error**: the rejection handler must
   always end in `Promise.reject(error)`. A handler that resolved would turn a
   failed setup into a false success.
7. **The interceptor cannot corrupt a success**: the success handler must be the
   identity, and no `response.data` mutation may occur.
8. **The control token cannot leak**: the message may only be built from the
   response body's `detail` / `error` fields - never from the config, the
   headers, the URL or a raw body. Confirm the request interceptor and
   `getControlToken` are byte-identical to master.
9. **Raw bodies are never surfaced**: an HTML page or a Python traceback in
   `response.data` must NOT become the message.
10. **The new tests are honest**: do they actually exercise the real code
    (the real helper, the real route, the real axios instance), or do they
    re-implement the logic and assert on the re-implementation? The frontend
    test must drive the real `apiClient` - check that it is not asserting on a
    mock of the interceptor.
11. **The 019 tautology guard**: the new backend test file must not contain
    `or True`, `and False`, or a bare `assert True`. The new frontend test must
    pass `npm run lint` with `--max-warnings 0` and type-check under `strict`.
12. **No live API, no credential**: no test may call the provider, read the
    credential vault, or open a socket. The recorded body must be a CONSTANT.

## Write

- `C:\Sythion\sythion\.orchestra\reviews\021-r1.md` (the only file you may write)

## Output contract

- `- Reviewed commit: a1e2e5a`
- `- Tier: <NORMAL|EXTERNAL|SENSITIVE|SENSITIVE+EXTERNAL>` (run the skill's
  classifier if available; otherwise state your own and why)
- Scope check result
- Per-acceptance-criterion judgement (021's seven criteria)
- Findings, each with a concrete fix shape
- `VERDICT: ACCEPT` / `IMPROVE` / `REJECT` with the reason

## Constraints

- No source edits. Probe scripts in TEMP only, deleted after.
- If you run the frontend tests, do it in your worktree - and note that the
  MAIN tree's `frontend/node_modules` is missing `react-test-renderer`
  (pre-existing, reported by the implementer), so `npm ci` may be needed in
  your worktree.
- Do not push. Do not write to the real credential vault.

Reply with a short summary. The FILE is the deliverable.