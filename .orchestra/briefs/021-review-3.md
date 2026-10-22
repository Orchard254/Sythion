# Brief: review - 021 - round 3

- Item: 021 The onboarding inference gate must parse the provider's real response shape, and the UI must show the backend's reason
- Role: reviewer (review only - no source edits)
- Reviewed commit: **the tip of `orchestra/021-r3-exhaustive-pin`** - resolve it
  with `git rev-parse orchestra/021-r3-exhaustive-pin` and record the sha. Its
  base is `15a2bbe` (the merge of which is already in `master`).
- Goal: judge ONLY the round-3 delta against
  `.orchestra/briefs/021-implement-3.md`, and confirm the two findings the
  round-2 review left open are actually closed.

## Read

- `C:\Sythion\sythion\.orchestra\briefs\021-implement-3.md` (binding)
- `C:\Sythion\sythion\.orchestra\reviews\021-r2.md` - findings **N1** and **N2**
- `C:\Sythion\sythion\.orchestra\reports\021-implement-3.md`
- The diff: `git diff 15a2bbe..<reviewed sha>`

## Worktree

- Create `C:\Users\orcha\AppData\Local\Temp\opencode\wt-021-r3-review`,
  detached at the reviewed sha. Do not reuse any other worktree.

## What to check

1. **Scope**: the round-3 diff touches ONLY
   `backend/tests/test_onboarding_inference_response_shape.py` and
   `frontend/src/services/api.test.ts`. **No production file may change** -
   `git diff 15a2bbe..<sha> -- backend/api/routes.py frontend/src/services/api.ts`
   must be EMPTY.
2. **N1 (a) - the fourth branch is now covered**: the body tuple in
   `test_the_endpoint_reports_502_for_a_numeric_provider_answer` must include
   `{"choices": [{"text": 7}]}` so that all FOUR of the parser's branches have
   a numeric case. **Read the parser first** and confirm the four branches you
   find are the four the test covers - do not take the brief's word for it.
3. **N1 (b) - the rule is pinned over the enumerable domain, and the new test
   is NOT VACUOUS.** The new helper-level test must assert that
   `_extract_inference_text` returns `""` for every non-str candidate type
   (int, float, bool, dict, list) across all four branches, AND that a `str`
   candidate in each branch is still returned. Confirm:
   - the parameterisation really covers 5 types x 4 branches (read the case
     list; count it);
   - the POSITIVE case is present - without it, the test would pass against a
     helper that always returns `""`;
   - the helper is imported INSIDE the test function, so a missing helper
     cannot turn the file into a collection error;
   - the assertion is on the helper's RETURN VALUE, not merely that it does
     not raise.
4. **N2 - the header comparison is strengthened**: the comparison must be
   `toStrictEqual`, not `toEqual`. Confirm the test still passes with it
   (both sides must be plain objects after the spread - if `toStrictEqual`
   fails for a reason other than the undefined case, that is a finding).
5. **Nothing was weakened**: no existing assertion relaxed, deleted, renamed
   or skipped. Diff the test bodies, not just the file names. The round-2
   numeric test and the interceptor's 9 tests must still be exactly as they
   were.
6. **The 019 tautology guard**: the modified backend test file must not
   contain `or True`, `and False`, or a bare `assert True`.
7. **Counts**: the backend test file gains exactly 1 test and the collected
   count moves by exactly +1 over `15a2bbe`; the **frontend** file and test
   counts must be UNCHANGED (this round changed an assertion, not a frontend
   test count).
8. **The round-1 and round-2 properties still hold** - re-measure the ones
   this delta could disturb: the OpenAPI hash (byte-identical to `15a2bbe`),
   the pinned invariant through the real endpoint with the verbatim R-028
   body, and the 502 still honest for an empty and a malformed body.
9. **The teeth of the new test**: confirm the new helper-level test FAILS
   if the parser is widened to accept a non-str. Do the widening in a TEMP
   copy - never in the committed source - and paste the failure.
10. **CI facts - read `.github/workflows/ci.yml` yourself** and report what it
    actually contains. Do not inherit a CI claim.

## Write

- `C:\Sythion\sythion\.orchestra\reviews\021-r3.md` (the only file you may write)

## Output contract

- `- Reviewed commit: <sha>`
- `- Tier: <NORMAL|EXTERNAL|SENSITIVE|SENSITIVE+EXTERNAL>`
- Scope check result
- Per-finding judgement: N1 closed? N2 closed?
- Any NEW finding, each with a concrete fix shape
- `VERDICT: ACCEPT` / `IMPROVE` / `REJECT` with the reason

## Constraints

- No source edits. Probe scripts and temp copies in TEMP only, deleted after.
- Do not run the full backend suite - the Verifier's job.
- Do not push. Do not call the live provider. Do not read the credential vault.
- If you need the frontend deps in your worktree, run `npm ci` THERE - do not
  touch the main tree's `frontend/node_modules`.

Reply with a short summary. The FILE is the deliverable.
