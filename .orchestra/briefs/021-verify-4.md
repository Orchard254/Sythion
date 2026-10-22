# Brief: verify - 021 - round 4 (the combined r3 + r4 delta)

- Item: 021 The onboarding inference gate must parse the provider's real response shape, and the UI must show the backend's reason
- Role: verifier (judge the OUTCOME; do not edit source)
- Reviewed commit: **the tip of `orchestra/021-r3-exhaustive-pin`** - resolve it
  with `git rev-parse orchestra/021-r3-exhaustive-pin` and record the sha. It
  must be the same commit the round-4 Reviewer judged.
- Goal: rounds 3 and 4 were TEST-ONLY and together they close every unpinned
  corner of the parser's candidate rule. Verify the OUTCOME: that the rule is
  now pinned on every axis, and that nothing regressed.

## Read

- `C:\Sythion\sythion\.orchestra\briefs/021-implement-3.md` (round 3, binding)
- `C:\Sythion\sythion\.orchestra\briefs/021-implement-4.md` (round 4, binding)
- `C:\Sythion\sythion\.orchestra\reviews/021-r3.md` (findings N1-N5)
- `C:\Sythion\sythion\.orchestra\verifications/021-v2.md` (the round-2 verdict)
- `C:\Sythion\sythion\.orchestra\reports/021-implement-3.md` and
  `reports/021-implement-4.md`

## Worktree

- Create `C:\Users\orcha\AppData\Local\Temp\opencode\wt-021-r4-verify`,
  detached at the reviewed sha. A throwaway worktree at the pre-fix base
  (`f8cce86`) is allowed for the reproduction; remove it when done.

## What to verify

1. **No production change**: `git diff 15a2bbe..<sha> -- backend/api/routes.py
   frontend/src/services/api.ts` must be EMPTY. The rule must be pinned by
   TESTS, never changed.
2. **The rule is pinned on EVERY axis.** The parser's candidate rule has two
   halves and four branches. Verify all of it by measurement:
   - **the str half**: a non-str candidate (int, float, bool, dict, list) in
     each of the four branches yields `""`;
   - **the strip half**: a whitespace-only candidate (`"   "`, `"\n\t "`) in
     each of the four branches yields `""`, and a padded candidate
     (`"  OK  "`) yields the TRIMMED value - read the parser and pin what it
     ACTUALLY returns;
   - **the positive case**: a `str` candidate in each of the four branches is
     still returned, so no test can pass by the helper always returning `""`.
3. **THE TEETH - prove the rule is fully pinned.** In TEMP copies of the
   parser (never the committed source), confirm that EACH of these mutations
   makes the suite RED:
   - remove `.strip()` - and specifically confirm the MASKING case is what
     catches it. The endpoint re-strips the text itself, so a blank answer
     alone would still yield 502 without `.strip()`; the masking case (a blank
     candidate must not mask a LATER branch's real answer) is the one with
     teeth. Verify that reasoning against the code.
   - remove the `isinstance(str)` check.
   Paste the failures. Delete the TEMP copies.
4. **The endpoint-level pins hold**: a whitespace-only provider answer gives
   **502** with the detail `"Inference returned an empty response"` - never
   200, never 500 - in each of the four branches; a padded answer gives **200**
   with `response_preview == "OK"`; a numeric answer gives 502.
5. **The pinned invariant still holds end-to-end**: through the REAL endpoint,
   the verbatim R-028 body
   `{"model":"minimax-m3.1-flash","message":{"role":"assistant","content":"OK"},"done":true,"done_reason":"stop","eval_count":2}`
   must still give 200, `success is True`, `response_preview == "OK"`, keys
   exactly `{success, model, response_preview}`. The other three shapes must
   still work, and the 502 must still be honest for an empty and a malformed
   body.
6. **The endpoint's contract is unchanged**: `app.openapi()` byte-identical to
   `15a2bbe`; `backend/api/schemas.py` not in the diff; the status codes
   400/400/200/502/401/503 unchanged.
7. **The frontend interceptor is unchanged**: the same 9-row behaviour table
   the round-1 verifier measured must still hold, and the header comparison
   must still be `toStrictEqual` - the real committed file must FAIL under a
   hostile interceptor that adds an undefined-valued header.
8. **No regression**: the backend suite with a **PER-TEST JUnit comparison**
   against `15a2bbe` - exactly TWO tests added (round 3's matrix and round
   4's whitespace test), the skipped count unchanged, and NO existing test's
   verdict changes. The frontend file and test counts must be UNCHANGED at
   10 files / 80 tests. Known pre-existing on this machine: the
   `test_phase975_admission_multiproc.py` contention flakes (pass in isolation)
   and an intermittent `0xC0000005` native crash that reproduces on the
   pre-change base. Report what you actually see; attribute neither to this
   change.
9. **Nothing was weakened**: no existing assertion relaxed, deleted, renamed
   or skipped. Diff the test bodies, not just the file names.
10. **No live API, no credential**: no test calls the provider, reads the
    credential vault, or opens a socket.

## Write

- `C:\Sythion\sythion\.orchestra\verifications/021-v4.md` (the only file you may write)

## Output contract

- `- Reviewed commit: <sha>`
- `VERDICT: FIXED` / `NOT FIXED` / `REGRESSED` as the FIRST line
- The teeth proofs, with command + pasted output
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
