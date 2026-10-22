# Brief: review - 021 - round 4 (the combined r3 + r4 delta)

- Item: 021 The onboarding inference gate must parse the provider's real response shape, and the UI must show the backend's reason
- Role: reviewer (review only - no source edits)
- Reviewed commit: **the tip of `orchestra/021-r3-exhaustive-pin`** - resolve it
  with `git rev-parse orchestra/021-r3-exhaustive-pin` and record the sha.
- Goal: judge the COMBINED delta of rounds 3 and 4 against master. Note that
  round 3 was already reviewed and ACCEPTed on its own
  (`.orchestra/reviews/021-r3.md`, at `df58bd6`); this review covers the round-3
  delta PLUS the round-4 addition together, so the two can merge in one commit.
- The base is `15a2bbe`, which is ALREADY MERGED into master. So the whole
  delta under review is `15a2bbe..<sha>`.

## Read

- `C:\Sythion\sythion\.orchestra\briefs/021-implement-3.md` (round 3, binding)
- `C:\Sythion\sythion\.orchestra\briefs/021-implement-4.md` (round 4, binding)
- `C:\Sythion\sythion\.orchestra\reviews/021-r3.md` (the round-3 review and its
  findings N3, N4, N5)
- `C:\Sythion\sythion\.orchestra\reports/021-implement-3.md` and
  `reports/021-implement-4.md`
- The diff: `git diff 15a2bbe..<reviewed sha>`

## Worktree

- Create `C:\Users\orcha\AppData\Local\Temp\opencode\wt-021-r4-review`,
  detached at the reviewed sha. Do not reuse any other worktree.

## What to check

1. **Scope**: the whole delta `15a2bbe..<sha>` touches ONLY
   `backend/tests/test_onboarding_inference_response_shape.py` and
   `frontend/src/services/api.test.ts`. **No production file may change** -
   `git diff 15a2bbe..<sha> -- backend/api/routes.py frontend/src/services/api.ts`
   must be EMPTY.
2. **N3 is closed - the `strip` half of the candidate rule is now pinned.**
   The parser's rule has two halves: the value must be a `str` (pinned by
   round 3), AND it must be non-blank after `strip()` (round 3 left this
   unpinned - the Reviewer MEASURED that removing `.strip()` left the file
   15/15 green). Confirm the new test pins BOTH:
   - a whitespace-only candidate (e.g. `"   "`, `"\n\t "`) yields `""` from
     the helper and a **502** with the detail
     `"Inference returned an empty response"` at the endpoint level, never
     200, across the four branches;
   - a candidate with surrounding whitespace (e.g. `"  OK  "`) yields the
     TRIMMED value. **Read the parser** and confirm the test pins what the
     parser ACTUALLY returns - trimmed or raw - rather than an assumption.
3. **The teeth of the new test**: in a TEMP copy of the parser (never the
   committed source), remove the `.strip()` and confirm the NEW test FAILS.
   Paste the failure. Then confirm that removing the `isinstance(str)` check
   still fails the round-3 matrix. Both halves of the rule must be
   load-bearing.
4. **Round 3's N1 and N2 are still closed** - re-check quickly, they were
   proven in `reviews/021-r3.md`: the matrix still covers 20 negative cases
   (5 types x 4 branches) plus 4 positive ones and the positive case is still
   load-bearing; the header comparison is still `toStrictEqual` and the real
   committed file still FAILS under a hostile interceptor that adds an
   undefined-valued header.
5. **N4 was a deliberate design decision - do not flag it as new.** The
   one-parameter-set matrix is intentional: the rule is a single logical
   property and the count contract is one collected test. Round 4 must NOT
   have restructured it. Confirm the collected count moved by exactly +1 for
   round 4 (i.e. +2 in total against `15a2bbe`).
6. **Nothing was weakened**: no existing assertion relaxed, deleted, renamed
   or skipped. Diff the test bodies, not just the names. The 9 frontend `it(...)`
   names must be byte-identical and the backend's existing assertions intact.
7. **The 019 tautology guard**: the modified backend test file must not
   contain `or True`, `and False`, or a bare `assert True`.
8. **The merge posture**: `15a2bbe` is already in master, so confirm
   `git merge-tree --write-tree master <sha>` exits 0 - a clean merge - and
   that master has changed no product file since.
9. **The report defect N5 is closed**: round 3's report had an EMPTY
   full-suite section and a mistyped path. Round 4's report must contain the
   full suite's ACTUAL output, and every path it cites must exist. Check at
   least one cited path.
10. **CI facts - read `.github/workflows/ci.yml` yourself** and report what it
    actually contains. Do not inherit a CI claim.

## Write

- `C:\Sythion\sythion\.orchestra\reviews\021-r4.md` (the only file you may write)

## Output contract

- `- Reviewed commit: <sha>`
- `- Tier: <NORMAL|EXTERNAL|SENSITIVE|SENSITIVE+EXTERNAL>`
- Scope check result
- Per-finding judgement: N3 closed? N1 and N2 still closed? N5 closed?
- Any NEW finding, each with a concrete fix shape
- `VERDICT: ACCEPT` / `IMPROVE` / `REJECT` with the reason

## Constraints

- No source edits. Probe scripts and temp copies in TEMP only, deleted after.
- Do not run the full backend suite - the Verifier's job.
- Do not push. Do not call the live provider. Do not read the credential vault.
- If you need the frontend deps in your worktree, run `npm ci` THERE - do not
  touch the main tree's `frontend/node_modules`.

Reply with a short summary. The FILE is the deliverable.
