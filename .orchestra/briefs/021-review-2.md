# Brief: review - 021 - round 2

- Item: 021 The onboarding inference gate must parse the provider's real response shape, and the UI must show the backend's reason
- Role: reviewer (review only - no source edits)
- Reviewed commit: **the tip of `orchestra/021-onboarding-inference-shape`** -
  resolve it with `git rev-parse orchestra/021-onboarding-inference-shape` and
  record the sha. The previous reviewed commit was `a1e2e5a`; this round is
  `a1e2e5a` + one or more round-2 commits.
- Goal: judge ONLY the round-2 delta against
  `.orchestra/briefs/021-implement-2.md`, and confirm that the round-1
  findings it was meant to close are actually closed.

## Read

- `C:\Sythion\sythion\.orchestra\briefs\021-implement-2.md` (binding)
- `C:\Sythion\sythion\.orchestra\reviews\021-r1.md` - your predecessor's
  review, and in particular findings (1) and (2)
- `C:\Sythion\sythion\.orchestra\reports\021-implement-2.md`
- The diff: `git diff a1e2e5a..<reviewed sha>`

## Worktree

- Create `C:\Users\orcha\AppData\Local\Temp\opencode\wt-021-review2`, detached
  at the reviewed sha. (Do not reuse `wt-021-review` or `wt-021`.)

## What to check

1. **Scope**: the round-2 diff touches ONLY the two test files -
   `backend/tests/test_onboarding_inference_response_shape.py` and
   `frontend/src/services/api.test.ts`. **No production file may change.**
   Verify with `git diff --name-only a1e2e5a..<sha>`.
2. **No production change at all**: `git diff a1e2e5a..<sha> -- backend/api/routes.py frontend/src/services/api.ts`
   must be EMPTY. The round was test-only by design.
3. **Finding (1) is closed**: there must now be a test that pins the
   deliberate behaviour change - a provider body whose text candidate is a
   NUMBER (e.g. `{"response": 7}`, `{"message": {"content": 7}}`,
   `{"choices": [{"message": {"content": 7}}]}`) yields **502** with the detail
   `"Inference returned an empty response"`, NOT 200 and NOT 500. Confirm the
   test actually asserts the 502 and the detail string - not merely that the
   request completes. Confirm the comment records that this is a DELIBERATE
   change from the pre-fix parser (which returned 200 and a preview of "7").
   **Also confirm the pinning is not vacuous**: a test that would pass whether
   the endpoint returned 200 or 502 is worthless. Read the assertion.
4. **Finding (2) is closed**: the frontend header assertion must compare
   against a **snapshot copy** taken before the request (e.g.
   `const headersBefore = { ...config.headers }`), not against the same
   object's own keys. A test that compares the keys of an object to a snapshot
   of that same object's keys cannot observe a VALUE mutation. Verify the new
   assertion would FAIL if a header value were changed.
5. **Nothing was weakened**: no existing test may have been relaxed, deleted,
   renamed or skipped to make the suite pass. Diff the test bodies, not just
   the file names.
6. **The 019 tautology guard**: the modified backend test file must not contain
   `or True`, `and False`, or a bare `assert True`.
7. **The frontend test still passes lint with `--max-warnings 0`** and
   type-checks under `strict`. Run `npm run lint` and `npm run build` in your
   worktree.
8. **The counts**: the backend collected count moves by exactly the new test(s)
   (+1 expected, i.e. 1641 -> 1642), and the vitest count moves by 0 (the
   frontend change is an assertion, not a new test) - the frontend file count
   and test count must be UNCHANGED from round 1. Confirm by running
   `npx vitest run` in your worktree.
9. **The round-1 verdict still holds**: the parser order, the endpoint's
   contract, the 502 honesty and the interceptor's behaviour table must be
   UNCHANGED. Re-run the round-1 measurements that the change could plausibly
   have disturbed: the base reproduction (base + the new test file must still
   fail behaviourally with `assert 502 == 200`), the OpenAPI hash (base vs
   branch must still be byte-identical), and the interceptor's 9-row behaviour
   table.
10. **CI facts - check them yourself, do not inherit them.** Your predecessor's
    report claimed "the frontend CI job still runs no vitest step (R-014), so
    `api.test.ts` gates locally only". **That was false** and has been
    corrected: `.github/workflows/ci.yml:103-104` is a `Test (vitest)` ->
    `npm test` step added by item 001 (commit `3ec2d46`). Read `ci.yml`
    yourself and report what it actually says. A stage that repeats a claim
    from its brief instead of measuring it is not independent on that point.

## Write

- `C:\Sythion\sythion\.orchestra\reviews\021-r2.md` (the only file you may write)

## Output contract

- `- Reviewed commit: <sha>`
- `- Tier: <NORMAL|EXTERNAL|SENSITIVE|SENSITIVE+EXTERNAL>`
- Scope check result (the exact file list)
- Per-finding judgement: (1) closed? (2) closed?
- Any NEW finding, each with a concrete fix shape
- `VERDICT: ACCEPT` / `IMPROVE` / `REJECT` with the reason

## Constraints

- No source edits. Probe scripts in TEMP only, deleted after.
- Do not run the full backend suite - the Verifier's job.
- Do not push. Do not call the live provider. Do not read the credential vault.
- If you need the frontend deps in your worktree, run `npm ci` there - do not
  touch the main tree's `frontend/node_modules`.

Reply with a short summary. The FILE is the deliverable.
