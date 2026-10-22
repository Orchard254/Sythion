# Brief: implement - 021 - round 2 (pin the one unpinned behaviour change)

- Item: 021 The onboarding inference gate must parse the provider's real response shape, and the UI must show the backend's reason
- Role: implementer (round 2)
- Branch: `orchestra/021-onboarding-inference-shape` (EXISTING - continue on it)
- Base of this round: `a1e2e5a`
- Goal: the review ACCEPTed the item, but found ONE behaviour change that is
  **not pinned by any test**. The sensitive-change gate requires the invariant
  to be pinned, so it must be pinned before the merge. **Production code does
  NOT change in this round** - both changes are in the test file.

## The finding

The reviewer measured that the new parser's "a candidate is a `str`" rule moves
one previously-200 provider answer to a 502:

| provider body | old parser | new parser |
|---|---|---|
| `{"response": 7}` | **200**, `response_preview == "7"` | **502** |

The plan mandated the rule ("A candidate is taken only if its value is a `str`
and `value.strip()` is non-empty"), and the new behaviour is arguably more
correct - a provider that answers with a number did not answer with text. But
**no test pins it**, so the contract change is invisible to the suite. The
reviewer also noted (F2) that the frontend test's header assertion compares the
header KEYS to a snapshot of the same object's keys, so it cannot detect a
VALUE mutation.

## The two changes (test file only)

1. **Pin the numeric-response case** in
   `backend/tests/test_onboarding_inference_response_shape.py`: an endpoint-level
   test asserting that a provider body of `{"response": 7}` (and, for good
   measure, `{"message": {"content": 7}}` and `{"choices": [{"message":
   {"content": 7}}]}`) yields **502** with the detail
   `"Inference returned an empty response"` - NOT 200 and NOT 500. Add a
   one-line comment saying this is a DELIBERATE behaviour change from the
   pre-fix parser (which returned 200 and a preview of "7"), so the next reader
   knows it was chosen rather than drifted into.
2. **Fix F2** in `frontend/src/services/api.test.ts`: the header assertion must
   compare against a **snapshot copy** taken before the request (e.g.
   `const headersBefore = { ...config.headers }`) rather than against the same
   object's own keys, so a value mutation would be visible.

## Do NOT do

- Do not change `backend/api/routes.py` or `frontend/src/services/api.ts`.
- Do not change the parser's rule, the interceptor's behaviour, the test ids,
  or the file set.
- Do not weaken any existing test.
- `git diff --name-only master..HEAD` must still be exactly the four files.

## Verify (paste all of it)

- `python -m pytest backend/tests/test_onboarding_inference_response_shape.py -q`
  - all pass, and the count is the previous count + the new test(s).
- `cd frontend && npx vitest run src/services/api.test.ts` - all pass.
- `cd frontend && npm run lint` - exit 0, no warnings.
- The collected count: `python -m pytest backend/tests --collect-only -q`
  (report it; the plan's +13 becomes +14 with the new test).
- The full backend suite: `python -m pytest backend/tests -q` - BACKGROUND,
  poll to completion, re-run once if it dies natively. NOTE: the full suite has
  TWO known pre-existing failures on this machine
  (`backend/tests/test_phase975_admission_multiproc.py` contention flakes, which
  pass in isolation) and a known intermittent native crash
  (`0xC0000005` in `test_phase95_hardening.py`, reproducible on pre-change
  master). Report what you actually see; do not attribute either to this change.

## Write

- `C:\Sythion\sythion\.orchestra\reports/021-implement-2.md` - commit sha, the
  exact diff, every command's output, and a statement that no production code
  changed.

## Constraints

- Commit on the SAME branch. Do not push. Do not touch master.
- No destructive commands.

## Output contract

Report at `.orchestra/reports/021-implement-2.md`. Reply with a short summary
and the commit sha. The FILE is the deliverable.