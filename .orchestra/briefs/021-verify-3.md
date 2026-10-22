# Brief: verify - 021 - round 3

- Item: 021 The onboarding inference gate must parse the provider's real response shape, and the UI must show the backend's reason
- Role: verifier (judge the OUTCOME; do not edit source)
- Reviewed commit: **the tip of `orchestra/021-r3-exhaustive-pin`** - resolve it
  with `git rev-parse orchestra/021-r3-exhaustive-pin` and record the sha. Its
  base is `15a2bbe`, which is already merged into `master`.
- Goal: round 3 was TEST-ONLY. It closed the two non-blocking findings the
  round-2 review left open: N1 (the parser's "a candidate is a `str`" rule was
  pinned for only some branches and some types) and N2 (`toEqual` ignores
  undefined-valued properties). Verify they are closed and that nothing
  regressed.

## Read

- `C:\Sythion\sythion\.orchestra\briefs/021-implement-3.md` (binding)
- `C:\Sythion\sythion\.orchestra\reviews/021-r2.md` (findings N1 and N2)
- `C:\Sythion\sythion\.orchestra\verifications/021-v2.md` (the round-2 verdict)
- `C:\Sythion\sythion\.orchestra\reports/021-implement-3.md`

## Worktree

- Create `C:\Users\orcha\AppData\Local\Temp\opencode\wt-021-r3-verify`,
  detached at the reviewed sha. A throwaway worktree at the pre-fix base is
  allowed for the reproduction; remove it when done.

## What to verify

1. **No production change**: `git diff 15a2bbe..<sha> -- backend/api/routes.py
   frontend/src/services/api.ts` must be EMPTY.
2. **N1 is closed - the rule is pinned over the whole enumerable domain.**
   Measure it, do not read it:
   - The helper-level test must cover every non-str candidate type (int,
     float, bool, dict, list) in EVERY one of the parser's four branches, and
     must also assert the positive case (a `str` candidate in each branch is
     still returned). Read the parser yourself first to establish the real
     branch list, then count the parameterised cases and confirm the count
     and the coverage.
   - The endpoint-level numeric test must now cover the FOURTH branch
     (`choices[0].text`) as well as the other three.
   - **Prove the teeth**: in a TEMP copy of the parser (never the committed
     source), widen the candidate rule to accept a non-str, and confirm the
     new test FAILS against it. If the new test still passes with a widened
     parser, N1 is NOT closed - it pins nothing.
3. **N2 is closed**: the header comparison must be `toStrictEqual`. **Prove
   the teeth**: in a TEMP copy of the test file only, set a header to
   `undefined` between the snapshot and the assertion and confirm the
   comparison FAILS. Confirm the old `toEqual` form would have PASSED the
   same mutation. Never edit the committed test file.
4. **The pinned invariant still holds end-to-end**: through the REAL endpoint,
   the verbatim R-028 body
   `{"model":"minimax-m3.1-flash","message":{"role":"assistant","content":"OK"},"done":true,"done_reason":"stop","eval_count":2}`
   must still give 200, `success is True`, `response_preview == "OK"`, keys
   exactly `{success, model, response_preview}`. The other three shapes must
   still work, and the 502 must still be honest for an empty and a malformed
   body.
5. **The endpoint's contract is unchanged**: `app.openapi()` byte-identical to
   `15a2bbe`; `backend/api/schemas.py` not in the diff; the status codes
   400/400/200/502/401/503 unchanged.
6. **The frontend interceptor is unchanged**: the same 9-row behaviour table the
   round-1 verifier measured must still hold.
7. **No regression**: the backend suite with a **PER-TEST JUnit comparison**
   against `15a2bbe` - exactly ONE test added, the skipped count unchanged,
   and NO existing test's verdict changes. The frontend file and test counts
   must be UNCHANGED. Known pre-existing on this machine: the
   `test_phase975_admission_multiproc.py` contention flakes (pass in isolation)
   and an intermittent `0xC0000005` native crash that reproduces on the
   pre-change base. Report what you actually see; attribute neither to this
   change.
8. **Nothing was weakened**: no existing assertion relaxed, deleted, renamed
   or skipped. Diff the test bodies, not just the file names.
9. **No live API, no credential**: no test calls the provider, reads the
   credential vault, or opens a socket.

## Write

- `C:\Sythion\sythion\.orchestra\verifications/021-v3.md` (the only file you may write)

## Output contract

- `- Reviewed commit: <sha>`
- `VERDICT: FIXED` / `NOT FIXED` / `REGRESSED` as the FIRST line
- The two teeth proofs, with command + pasted output
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
