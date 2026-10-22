# Brief: verify - 021 - round 2

- Item: 021 The onboarding inference gate must parse the provider's real response shape, and the UI must show the backend's reason
- Role: verifier (judge the OUTCOME; do not edit source)
- Reviewed commit: **the tip of `orchestra/021-onboarding-inference-shape`** -
  resolve it with `git rev-parse orchestra/021-onboarding-inference-shape` and
  record the sha. It must be the same commit the round-2 reviewer judged. The
  round-1 reviewed commit was `a1e2e5a`.
- Goal: this round was TEST-ONLY - it closed round-1 finding (1), an unpinned
  behaviour change on a public API file, and finding (2), a header assertion
  that could not observe a value mutation. Verify that the two findings are
  genuinely closed and that nothing regressed.

## Read

- `C:\Sythion\sythion\.orchestra\briefs/021-implement-2.md` (binding)
- `C:\Sythion\sythion\.orchestra\reviews/021-r1.md` (findings 1 and 2)
- `C:\Sythion\sythion\.orchestra\reports/021-implement-2.md`
- `C:\Sythion\sythion\.orchestra\verifications/021-v1.md` (the round-1 verdict)

## Worktree

- Create `C:\Users\orcha\AppData\Local\Temp\opencode\wt-021-verify2`,
  detached at the reviewed sha. A throwaway worktree at the pre-fix base is
  allowed for the reproduction; remove it when done.

## What to verify

1. **No production change.** `git diff a1e2e5a..<sha> -- backend/api/routes.py
   frontend/src/services/api.ts` must be EMPTY. The round was test-only by
   design; a production change here would be a scope violation AND would
   invalidate the round-1 verification.
2. **Finding (1) is closed - AND THE TEST HAS TEETH.** This is the point of the
   round. The new test
   `test_the_endpoint_reports_502_for_a_numeric_provider_answer` must fail
   against the PRE-FIX parser. **Prove it yourself:** in a throwaway worktree
   at the pre-fix base (`f8cce86`), copy the new test file in and run it - the
   numeric test must FAIL, and it must fail because the endpoint answered
   **200** (with a `response_preview` of `"7"`) where it now answers 502. If
   the test passes against the pre-fix parser, it pins nothing and the finding
   is NOT closed. Paste the failure.
3. **Finding (2) is closed - AND THE ASSERTION HAS TEETH.** The frontend header
   assertion must now compare a **copy** against a **copy**, so that a header
   VALUE mutation is observable. **Prove it yourself, in a TEMP copy of the
   test file only** (never in the committed branch): (a) mutate a header value
   between the adapter's snapshot and the assertion and confirm the new
   assertion FAILS; (b) confirm the OLD keys-only form would have PASSED the
   same mutation. If the new assertion cannot fail under a value mutation,
   finding (2) is not closed. Delete the temp copy afterwards.
4. **The pinned invariant still holds end-to-end.** Through the REAL endpoint,
   the verbatim R-028 body
   `{"model":"minimax-m3.1-flash","message":{"role":"assistant","content":"OK"},"done":true,"done_reason":"stop","eval_count":2}`
   must still give **200**, `success is True`, `response_preview == "OK"`, keys
   exactly `{success, model, response_preview}`. The other three response
   shapes must still work. The 502 must still be honest for an empty and a
   malformed body.
5. **The endpoint's contract is unchanged**: `app.openapi()` byte-identical to
   `a1e2e5a`; `backend/api/schemas.py` not in the diff; the status codes
   400/400/200/502/401/503 unchanged.
6. **The frontend interceptor is unchanged**: the same 9-row behaviour table
   the round-1 verifier measured must still hold (detail > error slug > axios
   message; a raw body never surfaced; a 422 list -> the `msg` field; every
   error case still rejects as the same `AxiosError`; the success case
   untouched; the control token never in the message yet still attached).
7. **No regression**: the backend suite on the branch with a **PER-TEST JUnit
   comparison** against `a1e2e5a` - exactly ONE test added, the skipped count
   unchanged, and NO existing test's verdict changes. The frontend file and
   test counts must be UNCHANGED from round 1 (10 files / 80 tests) - this
   round changed an assertion, not a test count.
8. **Nothing was weakened**: no existing assertion relaxed, deleted, renamed
   or skipped. Diff the test bodies, not just the file names.
9. **No live API, no credential**: no test calls the provider, reads the
   credential vault, or opens a socket. The recorded body is a file constant.
   Confirm by inspection and by grep.

## Write

- `C:\Sythion\sythion\.orchestra\verifications/021-v2.md` (the only file you may write)

## Output contract

- `- Reviewed commit: <sha>`
- `VERDICT: FIXED` / `NOT FIXED` / `REGRESSED` as the FIRST line
- The two teeth proofs, with command + pasted output
- The pinned invariant checked BY NAME
- The regression check with numbers
- What you did NOT check

## Constraints

- No source edits to the committed branch. No push. No destructive commands.
- **Do not call the live provider and do not read the credential vault.**
- Run the full backend suite in the BACKGROUND and poll it to completion.
- If you need the frontend deps in your worktree, run `npm ci` THERE - do not
  touch the main tree's `frontend/node_modules`.
- If something cannot be determined, say so in the report rather than
  assuming it.

Reply with a short summary. The FILE is the deliverable.
