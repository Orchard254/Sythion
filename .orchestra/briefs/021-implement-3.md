# Brief: implement - 021 - round 3 (exhaustive pin + one assertion strengthening)

- Item: 021 The onboarding inference gate must parse the provider's real response shape, and the UI must show the backend's reason
- Role: implementer (round 3)
- Branch: `orchestra/021-r3-exhaustive-pin` (NEW - create it from `15a2bbe`)
- Base of this round: `15a2bbe` (the tip of `orchestra/021-onboarding-inference-shape`)
- Goal: close the two non-blocking findings the round-2 review left open. Both
  are TEST-ONLY. **No production code changes.**

## Background

The round-2 review (`reviews/021-r2.md`) ACCEPTed the item and recorded two
non-blocking findings:

- **N1** - the parser's rule "a candidate is taken only if its value is a
  `str`" is a real behaviour change from the pre-fix parser, which let ANY
  truthy value through. The round-2 test pins THREE numeric bodies, so three
  of the parser's FOUR branches have a numeric case and the **fourth branch
  (`choices[0].text`, the legacy OpenAI completions shape) has none**. The
  non-int types (float, bool, dict, list) are also unpinned.
- **N2** - the frontend header comparison uses `toEqual`, which IGNORES
  undefined-valued properties, so a header set to `undefined` would slip
  through.

## The two changes

### 1. N1 - pin the rule exhaustively

Two parts, both required:

**(a) The endpoint-level test must cover the fourth branch.** Add
`{"choices": [{"text": 7}]}` to the body tuple in
`test_the_endpoint_reports_502_for_a_numeric_provider_answer`, so all four
parser branches have a numeric case. Keep the existing three. Keep the
`DELIBERATE` docstring, and extend it to say that the rule is "a candidate is
taken only if its value is a `str`", so a non-str of ANY type is an empty
response.

**(b) Add a helper-level test that pins the rule over the whole enumerable
domain.** The helper `_extract_inference_text` is importable from
`backend.api.routes` (import it INSIDE the test function, exactly as the
existing tests do, so that a missing helper cannot turn the file into a
collection error). Write ONE new test that, for every non-str candidate type
and every one of the four branches, asserts the helper returns `""`:

- the candidate types: `7` (int), `7.5` (float), `True` (bool), `{"a": 1}`
  (dict), `[1, 2]` (list)
- the four branches, as the body a provider would send:
  1. native: `{"message": {"content": <candidate>}}`
  2. OpenAI chat: `{"choices": [{"message": {"content": <candidate>}}]}`
  3. OpenAI legacy: `{"choices": [{"text": <candidate>}}]}`
  4. legacy Ollama: `{"response": <candidate>}`

That is 5 types x 4 branches = 20 cases in one test. Use `pytest.mark.parametrize`
so a failure names the exact case. The test must also assert the POSITIVE
case still holds - a `str` candidate in each of the four branches is still
returned - so the test cannot pass by the helper always returning `""`.

**Read the parser first** and make the case list match the parser's ACTUAL
order and shape - do not assume. If the parser's branches differ from the four
above, use the real ones and say so in the report.

### 2. N2 - strengthen the header comparison

In `frontend/src/services/api.test.ts`, change the header comparison from
`toEqual` to **`toStrictEqual`**:

```ts
expect({ ...seen[0].headers }).toStrictEqual(headersBefore[0]);
```

`toStrictEqual` also checks that undefined-valued properties are present on
both sides, so a header mutated to `undefined` is now caught. Confirm this is
still true for the shapes the test actually produces (both sides are plain
objects after the spread, and axios's `AxiosHeaders` instance is spread into
one) - if `toStrictEqual` fails for a reason other than the undefined case,
report it rather than weakening the assertion.

## Do NOT do

- Do not change `backend/api/routes.py` or `frontend/src/services/api.ts`.
- Do not change the parser's rule, the interceptor's behaviour, or any
  existing test's assertion.
- Do not weaken any existing test.
- `git diff 15a2bbe..HEAD` must touch only the two test files.

## Verify (paste all of it)

- `python -m pytest backend/tests/test_onboarding_inference_response_shape.py -q`
  - all pass; report the count (it should be the round-2 count + 1).
- `cd frontend && npx vitest run src/services/api.test.ts` - all pass; report
  the count (it should be UNCHANGED - this round adds no frontend test).
- `cd frontend && npm run lint` - exit 0, no warnings.
- `cd frontend && npm run build` - exit 0.
- `python -m pytest backend/tests --collect-only -q` - report the count (the
  round-2 count + 1).
- **The teeth of the new helper test**: in a TEMP script, confirm the new test
  FAILS if you change the helper to accept a non-str (e.g. temporarily
  widen the candidate check in a COPY of the parser in TEMP - never in the
  committed source) and that it PASSES against the real parser. Paste both.
- The full backend suite: `python -m pytest backend/tests -q` - run it in the
  BACKGROUND and poll to completion. NOTE: this machine has TWO known
  pre-existing problems - the `test_phase975_admission_multiproc.py` contention
  flakes (which pass in isolation) and an intermittent native crash
  (`0xC0000005`) that reproduces on the pre-change base. Report what you
  actually see; attribute neither to this change. Another agent may be running
  a suite concurrently, which makes the native crash more likely.

## Write

- `C:\Sythion\sythion\.orchestra\reports/021-implement-3.md` - commit sha, the
  exact diff, every command's output, the teeth proof, and a statement that no
  production code changed.

## Constraints

- Commit on the NEW branch `orchestra/021-r3-exhaustive-pin`. Do not push. Do
  not touch master. Do not modify `orchestra/021-onboarding-inference-shape`
  (that branch is being merged concurrently).
- No destructive commands. No live provider calls. No credential vault reads.

## Output contract

Report at `.orchestra/reports/021-implement-3.md`. Reply with a short summary
and the commit sha. The FILE is the deliverable.
