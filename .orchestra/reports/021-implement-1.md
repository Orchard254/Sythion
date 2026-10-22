# 021 - implement (attempt 1) - report

- **Item:** 021 - The onboarding inference gate must parse the provider's real
  response shape, and the UI must show the backend's reason.
- **Role:** implementer.
- **Branch:** `orchestra/021-onboarding-inference-shape` (from `master` @ `f8cce86`).
- **Worktree:** `C:\Users\orcha\AppData\Local\Temp\opencode\wt-021`.
- **Plan followed:** `.orchestra/plans/021.md` (binding), plus the brief's
  correction (the slug is `VAULT_WRITE_BLOCKED`, L not D).

## Commit shas

| sha | contents |
|---|---|
| `7131b8a` | `fix(api): parse the native Ollama chat shape in the onboarding inference gate` - `backend/api/routes.py` + `backend/tests/test_onboarding_inference_response_shape.py` |
| `a1e2e5a` | `fix(frontend): surface the backend's error detail instead of axios's generic message` - `frontend/src/services/api.ts` + `frontend/src/services/api.test.ts` |

Nothing was pushed. `master` was not touched. The branch HEAD is `a1e2e5a`.

## Files changed (criterion 6)

```
$ git diff --name-only master..HEAD
backend/api/routes.py
backend/tests/test_onboarding_inference_response_shape.py
frontend/src/services/api.test.ts
frontend/src/services/api.ts

$ git diff --stat master..HEAD
 backend/api/routes.py                              |  55 ++++-
 .../test_onboarding_inference_response_shape.py    | 256 +++++++++++++++++++++
 frontend/src/services/api.test.ts                  | 189 +++++++++++++++
 frontend/src/services/api.ts                       |  65 ++++++
 4 files changed, 559 insertions(+), 6 deletions(-)
```

Exactly four files. `frontend/package.json` is **unchanged** (no new
dependency; the frontend test uses axios's own per-request `adapter` hook,
NOT `axios-mock-adapter`, which is not installed and was not added).
`backend/api/schemas.py`, `backend/main.py`, `frontend/src/screens/Setup.tsx`
and every `backend/core/` file are untouched.

---

## 1. The backend half - the parser order as implemented

`backend/api/routes.py` gains ONE private module-level helper,
`_extract_inference_text(response: Any) -> str`, placed immediately above
`POST /onboarding/inference` (the file's existing convention of module-level
private helpers, e.g. `_safe_json_loads:1614`, `_restore_secret:2161`).

**The parser order as implemented** - the FIRST candidate that yields a
non-blank string wins, and the result is the **stripped** string:

| order | shape | access | source of the shape |
|---|---|---|---|
| 1 | native Ollama `/api/chat` | `response["message"]["content"]` | the live body in R-028's Evidence |
| 2 | OpenAI-compatible chat | `response["choices"][0]["message"]["content"]` | `backend/agents/reasoner.py:317` |
| 3 | OpenAI-compatible legacy completions | `response["choices"][0]["text"]` | the route's previous `choices[0].get("text")` |
| 4 | legacy Ollama `/api/generate` | `response["response"]` | the route's previous `resp.get("response")` |

The rules that make the order deterministic, all implemented:

* a candidate is taken only if its value is a `str` **and** `value.strip()` is
  non-empty - a `None`, a dict, a list, a number or a whitespace-only string
  is not a candidate and the parser moves to the next shape;
* a candidate whose **container** is not the expected type is **skipped**,
  never an error: `message` not a dict, `choices` not a list, `choices` empty,
  `choices[0]` not a dict;
* a body that is not a dict at all yields `""`;
* nothing is raised or logged: the helper returns `""` when no candidate
  qualifies.

The endpoint body changed by exactly the two lines the plan named: the old
inline parse was replaced by `text = _extract_inference_text(resp)`. The
`if not str(text).strip(): raise HTTPException(status_code=502, detail=
"Inference returned an empty response")` guard, the success body, the
`set_setting` evidence write and the `except` mapping (401 / 503 / 502) are
untouched.

```diff
-        text = ""
-        if isinstance(resp, dict):
-            choices = resp.get("choices") or []
-            if choices and isinstance(choices[0], dict):
-                text = (choices[0].get("message") or {}).get("content", "") or choices[0].get("text", "")
-            text = text or resp.get("response", "") or ""
+        text = _extract_inference_text(resp)
         if not str(text).strip():
             raise HTTPException(status_code=502, detail="Inference returned an empty response")
```

**The endpoint's contract is unchanged, and that is measured, not asserted.**
The OpenAPI document was generated on the base tree and on the branch and the
two JSON serialisations hash identically:

```
$ python -c "import json; from backend.main import app; print(json.dumps(app.openapi(), sort_keys=True, indent=1))"
branch: 0AD039759720EE945BD9D39A09DA4B8A5652F94FEBE936C33984B80D910612D7
base:   0AD039759720EE945BD9D39A09DA4B8A5652F94FEBE936C33984B80D910612D7
IDENTICAL: True
```

## 2. The frontend half - the interceptor's behaviour table as implemented

`frontend/src/services/api.ts` gains a **response** interceptor registered on
the existing `apiClient` immediately after the request interceptor, plus two
module-private helpers (`messageOf`, `detailToText`) and one more
(`errorReason`). Nothing is exported: the module's public surface is
unchanged. The request interceptor (`:73-79`), `getControlToken` (`:47-62`) and
`configureBridge` (`:39-43`) are **byte-identical** - they do not appear in
the diff at all.

| # | situation | `error.response.data` | resulting `error.message` | everything else |
|---|---|---|---|---|
| 1 | **success** (2xx) | n/a | n/a - the response is passed through unchanged | the success handler is the identity `(response) => response`; nothing is mutated, no message is attached |
| 2 | error with a `detail` **string** | `{"detail":"Inference returned an empty response"}` | **that string, verbatim and trimmed** | `error.response`, `error.status`, `error.config`, `error.code` untouched; the promise still **rejects** with the same error object |
| 3 | error with `detail` as a **list** (FastAPI 422) | `{"detail":[{"loc":["body","model_id"],"msg":"field required"}]}` | **the `msg` fields joined with `"; "`** | as above |
| 4 | error with an **`error` slug** and no `detail` | `{"error":"VAULT_WRITE_BLOCKED","code":"409"}` | **`"VAULT_WRITE_BLOCKED"` verbatim** | as above |
| 5 | error with **both** | `{"error":"INTERNAL_ERROR","detail":"boom","code":"500"}` | **`"boom"` - `detail` wins over the slug** | as above |
| 6 | error with **no body** | `""` or `undefined` | **unchanged**: axios's `"Request failed with status code 502"` | as above |
| 7 | error whose body is **not a structured error** (plain text, HTML, a stack trace) | `"Traceback (most recent call last): ..."` | **unchanged**: axios's generic message - a raw body is **never** surfaced | as above |
| 8 | **network error, no response at all** | `error.response === undefined` | **unchanged**: axios's `"Network Error"` | the error is re-rejected completely untouched |

**The precedence rule, stated once:** `detail` (string, or the `msg` fields of
a `detail` list) > `error` slug > axios's own message. Only **structured JSON
fields** are ever used; a raw body is never surfaced (row 7), which is what
keeps a stack trace or an HTML error page out of the UI. A `detail` that is
an object carrying a `msg` is also read.

**The error is never swallowed:** the handler always ends with
`return Promise.reject(error);`. The mechanism is to **replace
`error.message` on the same `AxiosError` object** rather than throw a new
error, so the status code, the response and the config stay reachable. The
control token is never read by the response interceptor, so it cannot leak
into a message.

Because every existing frontend call site reads `err.message` (~15 sites),
all of them improve at once - **without editing a single screen**.

---

## 3. Verification - every command and its output

### 3.1 The new backend test file passes

```
$ python -m pytest backend/tests/test_onboarding_inference_response_shape.py -q --no-header -p no:warnings
.............                                                            [100%]
13 passed in 6.56s
```

### 3.2 The BASE REPRODUCTION (behavioural failure, not an import error)

A throwaway worktree at the branch base
(`C:\Users\orcha\AppData\Local\Temp\opencode\wt-021-repro`, detached at
`f8cce86`) had the new test file copied in and was run there. **The worktree
was deleted afterwards.**

```
$ python -m pytest backend/tests/test_onboarding_inference_response_shape.py -q
...
backend\tests\test_onboarding_inference_response_shape.py:198:
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

response = {'message': 'not-a-dict'}

    def _parse(response):
        """Parse ``response`` with the gate's own helper.

        The helper is imported INSIDE the function so that this module can be
        collected on a tree where it does not exist yet: the endpoint tests
        below then fail BEHAVIOURALLY (502 instead of 200) instead of the whole
        module failing to import.
        """
>       from backend.api.routes import _extract_inference_text
E       ImportError: cannot import name '_extract_inference_text' from 'backend.api.routes' (...\wt-021-repro\backend\api\routes.py)

backend\tests\test_onboarding_inference_response_shape.py:57: ImportError
_____________ test_the_endpoint_answers_200_for_the_native_shape ______________

monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x000001D277C030>

    def test_the_endpoint_answers_200_for_the_native_shape(monkeypatch):
        """The live body must not be reported as an empty response any more."""
        res = _post_inference(monkeypatch, NATIVE_BODY)
>       assert res.status_code == 200
E       assert 502 == 200
E        +  where 502 = <Response [502 Bad Gateway]>.status_code

backend\tests\test_onboarding_inference_response_shape.py:207: AssertionError
___________________ test_the_endpoint_contract_is_unchanged ___________________

monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x000001D277C6750>

    def test_the_endpoint_contract_is_unchanged(monkeypatch):
        """The wire contract of the gate is exactly what it was."""
        res = _post_inference(monkeypatch, NATIVE_BODY, model_id=None, selected=None)
        assert res.status_code == 400
        assert res.json()["detail"] == "No model selected"

        res = _post_inference(monkeypatch, NATIVE_BODY, model_id="nope", selected="m1")
        assert res.status_code == 400
        assert res.json()["detail"] == "Model 'nope' is not available"

        res = _post_inference(monkeypatch, NATIVE_BODY)
>       assert res.status_code == 200
E       assert 502 == 200
E        +  where 502 = <Response [502 Bad Gateway]>.status_code

backend\tests\test_onboarding_inference_response_shape.py:255: AssertionError
=========================== short test summary info ===========================
FAILED backend/tests/test_onboarding_inference_response_shape.py::test_the_native_provider_shape_is_parsed
FAILED backend/tests/test_onboarding_inference_response_shape.py::test_the_openai_compatible_shape_is_parsed
FAILED backend/tests/test_onboarding_inference_response_shape.py::test_the_legacy_completion_shape_is_parsed
FAILED backend/tests/test_onboarding_inference_response_shape.py::test_the_legacy_generate_shape_is_parsed
FAILED backend/tests/test_onboarding_inference_response_shape.py::test_the_native_shape_is_preferred_over_the_others
FAILED backend/tests/test_onboarding_inference_response_shape.py::test_a_shape_that_is_none_of_the_known_ones_yields_no_text
FAILED backend/tests/test_onboarding_inference_response_shape.py::test_a_malformed_body_does_not_raise
FAILED backend/tests/test_onboarding_inference_response_shape.py::test_the_endpoint_answers_200_for_the_native_shape
FAILED backend/tests/test_onboarding_inference_response_shape.py::test_the_endpoint_contract_is_unchanged
9 failed, 4 passed in 7.51s
```

**Reading of the result:** the two endpoint tests that need the new parser
fail **behaviourally** with `assert 502 == 200` - exactly the 502 the user
saw - and not with an import error. The 4 that pass on the base are the ones
that must pass on the base (the OpenAI-compatible shape, the legacy shape and
the two 502 guards). The 7 unit tests fail with `ImportError` because the
helper does not exist yet on the base, which is what the plan predicted.

The test module imports the helper **inside** the test function on purpose:
a module-level import would have turned the whole file into a collection
error and the endpoint failure would have been invisible.

### 3.3 The frontend base reproduction (extra, not required by the brief)

The same throwaway worktree, with the new `frontend/src/services/api.test.ts`
copied in and the BASE (unmodified) `frontend/src/services/api.ts`:

```
$ npx vitest run src/services/api.test.ts
 Test Files  1 failed (1)
      Tests  4 failed | 5 passed (9)

FAIL  src/services/api.test.ts > the axios response interceptor > falls back to the error slug when there is no detail
AssertionError: expected 'Request failed with status code 409' to be 'VAULT_WRITE_BLOCKED'

FAIL  src/services/api.test.ts > the axios response interceptor > does not change the request side
AssertionError: expected 'Request failed with status code 502' to be 'Inference returned an empty response'
```

The failures are exactly the four assertions that need the interceptor (F1,
F2, F3 and the message half of F8). F4, F5, F6, F7 and F9 pass on the base,
as they must - they assert that the generic message is KEPT, that a success
is untouched, that a network error is untouched and that the token never
reaches a message.

### 3.4 The new frontend test file passes

```
$ cd frontend && npx vitest run src/services/api.test.ts

 RUN  v1.6.1 C:/Users/orcha/AppData/Local/Temp/opencode/wt-021/frontend

 ✓ src/services/api.test.ts  (9 tests) 87ms

 Test Files  1 passed (1)
      Tests  9 passed (9)
   Start at  03:38:17
   Duration  2.48s (transform 509ms, setup 0ms, collect 1.01s, tests 87ms, environment 1ms, prepare 644ms)
```

### 3.5 The whole frontend suite - counts before and after

**Before** (branch base `f8cce86`, measured in a worktree at the base commit
with a complete `npm ci`):

```
$ cd frontend && npx vitest run
 ✓ src/lib/setupReadiness.test.ts  (17 tests) 47ms
 ✓ src/lib/observation.test.ts  (21 tests) 44ms
 ✓ src/lib/shellLayout.test.ts  (5 tests) 23ms
 ✓ src/hooks/useVoiceCapture.test.tsx  (3 tests) 103ms
 ✓ src/lib/firstConversation.test.ts  (4 tests) 16ms
 ✓ src/lib/companionActivation.test.ts  (6 tests) 19ms
 ✓ src/screens/Setup.test.tsx  (3 tests) 322ms
 ✓ src/screens/Assistant.test.tsx  (3 tests) 461ms

 Test Files  9 passed (9)
      Tests  71 passed (71)
```

**After** (the branch):

```
$ cd frontend && npm test
 ✓ src/lib/observation.test.ts  (21 tests) 45ms
 ✓ src/lib/runtimeHealth.test.ts  (9 tests) 28ms
 ✓ src/lib/setupReadiness.test.ts  (17 tests) 20ms
 ✓ src/hooks/useVoiceCapture.test.tsx  (3 tests) 95ms
 ✓ src/lib/shellLayout.test.ts  (5 tests) 15ms
 ✓ src/services/api.test.ts  (9 tests) 131ms
 ✓ src/lib/firstConversation.test.ts  (4 tests) 15ms
 ✓ src/screens/Setup.test.tsx  (3 tests) 423ms
 ✓ src/lib/companionActivation.test.ts  (6 tests) 12ms
 ✓ src/screens/Assistant.test.tsx  (3 tests) 489ms

 Test Files  10 passed (10)
      Tests  80 passed (80)
```

**Delta: 9 test files -> 10, 71 tests -> 80, i.e. exactly +9 tests and +1
file. No existing test's verdict changed.**

### 3.6 Lint - exit 0, no warnings

```
$ cd frontend && npm run lint

> sythion-frontend@0.1.0 lint
> eslint . --ext ts,tsx --report-unused-disable-directives --max-warnings 0

LINT_EXIT=0
```

(no output at all - zero errors and zero warnings)

### 3.7 Build - exit 0

```
$ cd frontend && npm run build

> sythion-frontend@0.1.0 build
> tsc && vite build

vite v5.4.21 building for production...
transforming...
✓ 194 modules transformed.
rendering chunks...
computing gzip size...
dist/index.html                 1.15 kB │ gzip:   0.63 kB
dist/assets/index-CCraEvIB.css  48.61 kB │ gzip:   9.22 kB
dist/assets/core-BUhCcD2K.js     2.74 kB │ gzip:   1.14 kB │ map:   2.95 kB
dist/assets/window-BPQH9sz4.js  15.60 kB │ gzip:   4.95 kB │ map:  24.95 kB
dist/assets/index-C8_LppAK.js  472.46 kB │ gzip: 142.19 kB │ map: 1,469.03 kB
✓ built in 10.59s

BUILD_EXIT=0
```

`tsc` type-checks the new test file under `strict` (it is inside `src`, so
`tsconfig.json`'s `include: ["src"]` picks it up). The file uses `unknown`
plus one narrow `as unknown as AxiosResponse` cast and writes no `any`, so
`@typescript-eslint/no-explicit-any` (a warning under `--max-warnings 0`) does
not fire.

### 3.8 The collected count, before and after

```
# BEFORE (branch base f8cce86)
$ python -m pytest backend/tests --collect-only -q
1628 tests collected in 167.16s (0:02:47)

# AFTER (branch)
$ python -m pytest backend/tests --collect-only -q
1641 tests collected in 238.97s (0:03:58)
```

**Delta: exactly +13**, as the plan predicted.

### 3.9 The FULL backend suite - baseline and post-change

Both runs are `python -m pytest backend/tests -q` from the repository root of
a worktree, with a JUnit XML written, and both were run to completion in the
BACKGROUND and polled.

**BASELINE** (branch base `f8cce86`, worktree `wt-021-base`):

```
$ cd C:\Users\orcha\AppData\Local\Temp\opencode\wt-021-base; python -m pytest backend/tests -q --junitxml=baseline-junit.xml
...
1583 passed, 45 skipped, 427243 warnings in 813.69s (0:13:33)
EXIT=0
```

**POST-CHANGE** (branch `a1e2e5a`, worktree `wt-021`):

```
$ cd C:\Users\orcha\AppData\Local\Temp\opencode\wt-021; python -m pytest backend/tests -q --junitxml=after-junit.xml
...
1596 passed, 45 skipped, 431814 warnings in 1013.20s (0:16:53)
EXIT=0
```

Neither run died natively: **no known native fault (Fault A or Fault B) fired
in either run**, so no re-run was needed. (Other agents were running suites
on this machine concurrently during the baseline run - the baseline took
13:33 and the post-change run 16:53, both longer than the 4-9 minutes the
brief estimated, which is consistent with that contention.)

**Measured comparison:**

| | baseline (f8cce86) | branch (a1e2e5a) | delta |
|---|---|---|---|
| collected | 1628 | 1641 | **+13** |
| passed | 1583 | **1596** | **+13** |
| skipped | 45 | **45** | **0** |
| failed | 0 | **0** | **0** |
| wall clock | 813.69 s | 1013.20 s | (contention) |

**No existing test's verdict changed - proved by diffing the two JUnit XML
files** (every `testcase` in `baseline-junit.xml` against every `testcase` in
`after-junit.xml`, keyed by `classname::name`):

```
base testcases : 1628
after testcases: 1641
only in base  : 0
only in after : 13
   + backend.tests.test_onboarding_inference_response_shape::test_a_malformed_body_does_not_raise passed
   + backend.tests.test_onboarding_inference_response_shape::test_a_shape_that_is_none_of_the_known_ones_yields_no_text passed
   + backend.tests.test_onboarding_inference_response_shape::test_the_endpoint_answers_200_for_the_legacy_shape passed
   + backend.tests.test_onboarding_inference_response_shape::test_the_endpoint_answers_200_for_the_native_shape passed
   + backend.tests.test_onboarding_inference_response_shape::test_the_endpoint_answers_200_for_the_openai_compatible_shape passed
   + backend.tests.test_onboarding_inference_response_shape::test_the_endpoint_contract_is_unchanged passed
   + backend.tests.test_onboarding_inference_response_shape::test_the_endpoint_still_reports_502_for_a_genuinely_empty_answer passed
   + backend.tests.test_onboarding_inference_response_shape::test_the_endpoint_still_reports_502_for_an_unrecognised_shape passed
   + backend.tests.test_onboarding_inference_response_shape::test_the_legacy_completion_shape_is_parsed passed
   + backend.tests.test_onboarding_inference_response_shape::test_the_legacy_generate_shape_is_parsed passed
   + backend.tests.test_onboarding_inference_response_shape::test_the_native_provider_shape_is_parsed passed
   + backend.tests.test_onboarding_inference_response_shape::test_the_native_shape_is_preferred_over_the_others passed
   + backend.tests.test_onboarding_inference_response_shape::test_the_openai_compatible_shape_is_parsed passed
verdict changed: 0
```

Zero tests disappeared, the 13 that appeared are exactly the new file's 13,
and **no pre-existing test changed its verdict**.

---

## 4. The new backend test file - what it pins

`backend/tests/test_onboarding_inference_response_shape.py` (13 test
functions, 13 collected tests):

| # | test | layer | pins |
|---|---|---|---|
| B1 | `test_the_native_provider_shape_is_parsed` | unit | the live body, verbatim, parses to `"OK"` |
| B2 | `test_the_openai_compatible_shape_is_parsed` | unit | `choices[0].message.content` -> `"OK"` |
| B3 | `test_the_legacy_completion_shape_is_parsed` | unit | `choices[0].text` -> `"OK"` |
| B4 | `test_the_legacy_generate_shape_is_parsed` | unit | `response` -> `"OK"` |
| B5 | `test_the_native_shape_is_preferred_over_the_others` | unit | a body carrying **all four** shapes returns the **native** text - the ORDER is pinned, not just the membership |
| B6 | `test_a_shape_that_is_none_of_the_known_ones_yields_no_text` | unit | `{}`, `{"foo":"bar"}`, `[]`, `"OK"`, `None`, `42` -> `""` |
| B7 | `test_a_malformed_body_does_not_raise` | unit | six malformed bodies -> `""`, no exception |
| B8 | `test_the_endpoint_answers_200_for_the_native_shape` | endpoint | 200, `success is True`, `response_preview == "OK"`, `model` echoed |
| B9 | `test_the_endpoint_answers_200_for_the_openai_compatible_shape` | endpoint | 200 + `response_preview == "OK"` |
| B10 | `test_the_endpoint_answers_200_for_the_legacy_shape` | endpoint | 200 + `response_preview == "OK"` for **both** legacy shapes |
| B11 | `test_the_endpoint_still_reports_502_for_a_genuinely_empty_answer` | endpoint | 502 + `detail == "Inference returned an empty response"` |
| B12 | `test_the_endpoint_still_reports_502_for_an_unrecognised_shape` | endpoint | 502 (not 200, not 500) |
| B13 | `test_the_endpoint_contract_is_unchanged` | endpoint | 400 `"No model selected"`, 400 `"Model 'nope' is not available"`, and exactly the keys `{"success","model","response_preview"}` |

The native fixture is the verbatim live body, including `"done": true` and
`"eval_count": 2`:

```python
NATIVE_BODY = {
    "model": "minimax-m3.1-flash",
    "message": {"role": "assistant", "content": "OK"},
    "done": True,
    "done_reason": "stop",
    "eval_count": 2,
}
```

**Mandatory safety rules, honoured:**

* **No test calls the provider.** The provider is a stub (`_Provider.chat()`
  returns the body under test). The recorded body is a CONSTANT in the file.
* **No test reads the credential vault.** The file installs
  `set_secret_store_for_testing(MemoryStore())` through an autouse fixture
  and resets it to `None` in the teardown, so the real Windows Credential
  Manager can be neither read nor written.
* **No file, no profile directory, no network.** The database is an in-memory
  SQLite session (`StaticPool`) wired through `app.dependency_overrides[get_db]`.
* The 019 tautology guard is satisfied: the file contains no `or True`, no
  `and False` and no bare `assert True`; every assertion is a comparison on
  an opaque leaf. `backend/tests/test_no_tautological_asserts.py` passes.
* The file carries **its own copy** of the client helper, because criterion 6
  fixes the file set and forbids editing `backend/tests/test_setup_readiness.py`.
  The duplication is forced by the criterion and is noted, not hidden.

## 5. The new frontend test file - what it pins

`frontend/src/services/api.test.ts` (9 test functions, 9 collected tests):

| # | test | pins |
|---|---|---|
| F1 | `surfaces the backend detail for an error response` | 502 + `{"detail":"Inference returned an empty response"}` -> the message is exactly that string, `err.response.status === 502`, and the promise **rejects** |
| F2 | `prefers the detail over the error slug` | `{"error":"INTERNAL_ERROR","detail":"boom","code":"500"}` -> `"boom"` |
| F3 | `falls back to the error slug when there is no detail` | `{"error":"VAULT_WRITE_BLOCKED","code":"409"}` -> `"VAULT_WRITE_BLOCKED"` (**the brief's correction: the L spelling, matching `backend/main.py`'s `VAULT_WRITE_VIOLATION_ERROR` and `backend/api/schemas.py`'s `ErrorResponse`**) |
| F4 | `falls back to the generic message when the body carries no detail` | 502 + body `""` -> `"Request failed with status code 502"` |
| F5 | `never surfaces a raw body` | 500 + a plain-text body containing `Traceback (most recent call last):` -> the generic message; the message contains no `Traceback` |
| F6 | `passes a successful response through unchanged` | 200 + `{"success":true,"model":"m1","response_preview":"OK"}` -> `res.data` deep-equals the body, `res.status === 200` |
| F7 | `leaves a network failure without a response alone` | an `AxiosError` with **no** `.response` -> `"Network Error"`, `err.response === undefined` |
| F8 | `does not change the request side` | the config the adapter received still carries `X-Control-Token`, and the response interceptor added/changed nothing on the request's headers |
| F9 | `never puts the control token in the message` | the message does not contain the token the request interceptor attached |

**How it is hermetic:** the test drives the **real** `apiClient` with
axios's own per-request `adapter` hook. The error adapter rejects with
`new AxiosError('Request failed with status code NNN', AxiosError.ERR_BAD_RESPONSE, config, undefined, response)`
whose `response.data` is the raw JSON **string**; axios's own
`dispatchRequest` rejection path then runs the default `transformResponse`
over it, so `error.response.data` is the parsed object - the same shape a
real server produces, with **no socket, no backend and no new dependency**.
`sessionStorage` is stubbed with `vi.stubGlobal` in `beforeEach` and
`vi.unstubAllGlobals()` in `afterEach` (vitest's default environment is
`node`, where `sessionStorage` does not exist and `getControlToken` reads it
on every request).

---

## 6. What was NOT checked - the limits of this report

1. **CI runs no vitest step.** The `frontend` job in `.github/workflows/ci.yml`
   is `npm ci` + `npm run lint` + `npm run build` (R-014). The new frontend
   test therefore gates **locally only**; it will gate CI once R-014 lands.
   Fixing CI is outside this item's file set. The backend job does run
   pytest, so the 13 new backend tests DO gate CI.
2. **The packaged Tauri shell path was not exercised.** The interceptor runs
   in the same bundle, but nothing here built or launched the packaged app.
3. **The live provider was not called.** The confirmation that the real
   Ollama Cloud answer now passes the gate is left to the human operator;
   R-028's evidence body is the fixture and was not re-recorded.
4. **The packaged/installed Python runtime was not exercised** - only the
   development interpreter (Python 3.14.7) was used.
5. **The full backend suite was run once on the branch** and once on the
   base. A single run cannot detect a rare intermittent; the known native
   faults (Fault A: a COM/UIA burst that does not kill the process; Fault B: a
   sqlite3 access violation `0xC0000005` that does) were observed during this
   session's history but **did not fire in either of these two runs**.
6. **The `backend/.env` file is absent from the worktrees** (it is
   gitignored). That makes no difference to the suite:
   `backend/tests/conftest.py`'s autouse `_isolate_local_env_settings`
   fixture replaces the developer's `.env` values with the clean-checkout
   ones for **every** test, so the worktree is an equivalent environment for
   a baseline measurement. The baseline and the post-change run were taken in
   two different worktrees under the same conditions.
7. **The main tree's `frontend/node_modules` is incomplete** - it has no
   `react-test-renderer`, so `npm test` in `C:\Sythion\sythion\frontend`
   fails to load 3 suites (62 tests still pass). This is a pre-existing
   environment defect in the main tree, NOT something this branch caused,
   and it is why the frontend baseline was measured in a worktree with a
   complete `npm ci` install. It should be repaired in the main tree
   (`npm ci` there) - a follow-up, not part of this item.
8. **No screen was changed, so no screen was manually exercised.** The claim
   that the operator now sees the backend's reason is derived from the
   interceptor's behaviour (pinned by F1-F9) and from `Setup.tsx:46-51`
   already surfacing `e.message` - not from a manual click-through.

## 7. Files deliberately NOT changed

* `frontend/src/screens/Setup.tsx` - criterion 6 forbids it, and it does not
  need to change: `run()` already surfaces `e.message`.
* `backend/api/schemas.py`, `backend/main.py` - criterion 3.
* `backend/agents/reasoner.py`, `backend/cognitive/reasoning.py`,
  `backend/adapters/ollama_adapter.py` - already correct (they read
  `message.content` first). They are NOT refactored to share the new helper:
  that would touch three more files than criterion 6 allows. The duplication
  is acknowledged; extracting a shared parser into
  `backend/runtime/model_provider.py` is a follow-up.
* `backend/core/**` - no credential, config, DB or auth path was touched.
* `frontend/package.json` - **no new dependency**; `axios-mock-adapter` is
  not installed and was not added.
* `frontend/vite.config.ts`, `frontend/.eslintrc.cjs`,
  `backend/tests/conftest.py`, `backend/tests/test_setup_readiness.py`,
  `.github/workflows/ci.yml` - unchanged.
* `.orchestra/board.json`, `backlog.md`, `log.md`, `critiques.md` - not the
  implementer's to touch.
