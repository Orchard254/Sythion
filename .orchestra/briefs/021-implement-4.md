# Brief: implement - 021 - round 4 (pin the strip half of the candidate rule)

- Item: 021 The onboarding inference gate must parse the provider's real response shape, and the UI must show the backend's reason
- Role: implementer (round 4)
- Branch: `orchestra/021-r3-exhaustive-pin` (EXISTING - continue on it)
- Base of this round: `df58bd6` (the current tip of that branch)
- Goal: close the ONE remaining finding from the round-3 review (N3). The round
  was accepted with three non-blocking findings; N3 is the one that is a
  real coverage hole of the same class N1 was. **Test-only** - no production
  code changes.

## The finding (N3)

The parser's candidate rule has TWO halves:

1. the value must be a `str` - pinned exhaustively by round 3;
2. the value must be non-blank after `strip()` - **NOT pinned**.

The round-3 Reviewer MEASURED this: removing `.strip()` from the candidate
rule leaves the whole test file **15/15 green**. So either half of the rule
could be dropped and the suite would not notice. Half (1) is now pinned;
half (2) is not.

## The change

Add the missing pin to
`backend/tests/test_onboarding_inference_response_shape.py`:

- **A whitespace-only candidate is NOT the model's text.** For each of the
  parser's four branches, a candidate that is a `str` of only whitespace
  (e.g. `"   "`, `"\n\t "`) must yield `""` from the helper - and, at the
  endpoint level, a **502** with the detail
  `"Inference returned an empty response"`, never 200.
- **A candidate with surrounding whitespace IS the model's text, trimmed.**
  A candidate of `"  OK  "` must yield `"OK"` - the trimmed value, not the
  raw one with the spaces. Read the parser and confirm what it actually
  returns (the trimmed value or the raw one) and pin THAT, so the test
  records the real behaviour rather than an assumption.

Put the helper-level part in the existing matrix test
`test_a_candidate_is_taken_only_if_its_value_is_a_str` (or add a sibling if
that keeps the matrix readable) and the endpoint-level part alongside
`test_the_endpoint_reports_502_for_a_numeric_provider_answer`.

**Keep the collected count at +1 for this round** - the round-3 matrix is
deliberately ONE collected test, and this round must not turn the file into
24 collected cases. The brief's count contract from round 3 still holds.

## The other two round-3 findings - do NOT act on them

- **N4** (the one-parameter-set design masks sibling failures) is a
  deliberate, documented design decision: the rule is a single logical
  property and the count contract is one collected test. Do not restructure
  it.
- **N5** (the implement report's empty full-suite section and a mistyped
  path) is a REPORT defect, not a code defect. The orchestrator is fixing
  the report separately. Do not touch the report - your own report is
  `.orchestra/reports/021-implement-4.md`.

## Do NOT do

- Do not change `backend/api/routes.py` or `frontend/src/services/api.ts`.
- Do not change the parser's rule, the interceptor's behaviour, or any
  existing test's assertion.
- Do not weaken any existing test.
- `git diff df58bd6..HEAD` must touch ONLY
  `backend/tests/test_onboarding_inference_response_shape.py`.

## Verify (paste all of it)

- `python -m pytest backend/tests/test_onboarding_inference_response_shape.py -q`
  - all pass; report the count (it should be the round-3 count + 1, i.e. 16).
- `cd frontend && npx vitest run src/services/api.test.ts` - all pass,
  UNCHANGED count (9) - this round touches no frontend file.
- `cd frontend && npm run lint` - exit 0, no warnings.
- `cd frontend && npm run build` - exit 0.
- `python -m pytest backend/tests --collect-only -q` - report the count
  (expect 1643 + 1 = 1644).
- **THE TEETH - this is the point of the round.** In a TEMP copy of the
  parser (never the committed source), remove the `.strip()` and confirm the
  NEW test FAILS. Then, in a second TEMP copy, remove the `isinstance(str)`
  check and confirm the round-3 matrix still fails. Paste both. The TEMP
  copies must be deleted afterwards.
- The full backend suite: `python -m pytest backend/tests -q` - run it in the
  BACKGROUND and poll to completion. NOTE: other agents may be running
  suites concurrently, which makes the known intermittent native crash
  (`0xC0000005`, reproducible on the pre-change base) MORE likely - if a run
  dies natively, re-run once and report BOTH. The
  `test_phase975_admission_multiproc.py` contention flakes are known and
  pre-existing (they pass in isolation). Report what you actually see;
  attribute neither to this change.

## Write

- `C:\Sythion\sythion\.orchestra\reports/021-implement-4.md` - commit sha, the
  exact diff, every command's output, the teeth proof, and a statement that
  no production code changed. **Include the full suite's actual output** -
  the round-3 report's full-suite section was empty, which the Reviewer
  flagged (N5), and every path you cite must be one that exists.

## Constraints

- Commit on the EXISTING branch `orchestra/021-r3-exhaustive-pin`. Do not
  push. Do not touch master. Do not modify
  `orchestra/021-onboarding-inference-shape` (already merged).
- No destructive commands. No live provider calls. No credential vault reads.

## Output contract

Report at `.orchestra/reports/021-implement-4.md`. Reply with a short summary
and the commit sha. The FILE is the deliverable.
