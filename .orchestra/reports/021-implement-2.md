# Report: implement - 021 - round 2 (pin the one unpinned behaviour change)

- **Item:** 021 - the onboarding inference gate must parse the provider's real response shape, and the UI must show the backend's reason.
- **Role / round:** implementer, round 2.
- **Branch:** `orchestra/021-onboarding-inference-shape` (EXISTING, continued - not re-cut).
- **Base of this round:** `a1e2e5a` (round 1's tip).
- **Worktree:** `C:\Users\orcha\AppData\Local\Temp\opencode\wt-021` (checked out on the branch).
- **Commit:** `15a2bbe0a3afb79e27f1fa7243f88d7c282bb553` (see §1).
- **Production code changed:** **NONE.** Both changes are in test files only.

> **This round changes NO production code.** `backend/api/routes.py` and
> `frontend/src/services/api.ts` are byte-identical to `a1e2e5a`. The round
> only (1) pins the one behaviour change the round-1 review found unpinned and
> (2) repairs a frontend assertion that could not detect what it claimed to
> detect.

---

## 1. The commit

```
$ git log --oneline -3
15a2bbe test(onboarding): pin the numeric-answer 502 and fix the header snapshot assertion
a1e2e5a fix(frontend): surface the backend's error detail instead of axios's generic message
7131b8a fix(api): parse the native Ollama chat shape in the onboarding inference gate

$ git rev-parse HEAD
15a2bbe0a3afb79e27f1fa7243f88d7c282bb553

$ git show --stat --oneline HEAD
15a2bbe test(onboarding): pin the numeric-answer 502 and fix the header snapshot assertion
 .../test_onboarding_inference_response_shape.py    | 22 ++++++++++++++++++++++
 frontend/src/services/api.test.ts                  | 12 ++++++++----
 2 files changed, 30 insertions(+), 4 deletions(-)

(not pushed; master untouched)
```

`git diff --name-status a1e2e5a..HEAD` (this round's own delta):

```
M	backend/tests/test_onboarding_inference_response_shape.py
M	frontend/src/services/api.test.ts
```

---

## 2. The two changes

### 2.1 Backend - the numeric-answer case is now pinned

**File:** `backend/tests/test_onboarding_inference_response_shape.py` (one test
added, no existing test touched).

The round-1 review's finding #1: the parser's rule "a candidate is taken only
if its value is a `str`" moves one previously-200 provider answer to a 502,
and **no test pinned it**. The new test pins it at the endpoint level.

```
+def test_the_endpoint_reports_502_for_a_numeric_provider_answer(monkeypatch):
+    """A number is not the model's text, so the gate answers 502.
+
+    DELIBERATE change from the pre-fix parser: the old inline parse let any
+    truthy value through, so a provider body of ``{"response": 7}`` answered
+    **200** with a ``response_preview`` of ``"7"``.  The parser's rule - a
+    candidate is taken only if its value is a ``str`` - rejects a number, so
+    the same body is an empty response now.  Pinned here so the narrowing is
+    a recorded decision and not a drift nobody noticed.
+    """
+    for body in (
+        {"response": 7},
+        {"message": {"content": 7}},
+        {"choices": [{"message": {"content": 7}}]},
+    ):
+        res = _post_inference(monkeypatch, body)
+        assert res.status_code == 502
+        assert res.status_code != 200
+        assert res.status_code != 500
+        assert res.json()["detail"] == "Inference returned an empty response"
```

It drives the **real** route (`POST /api/v1/onboarding/inference`) through
FastAPI's `TestClient` with the file's existing stub provider - the same
endpoint layer as B8-B13 - and asserts **502** (not 200, not 500) with the
detail `"Inference returned an empty response"`. The docstring carries the
required one-line statement that this is a **deliberate** change from the
pre-fix parser.

### 2.2 Frontend - the header assertion now compares against a snapshot COPY (F2)

**File:** `frontend/src/services/api.test.ts` (one test's assertion repaired;
the test's name/id is unchanged, no other test touched).

The round-1 review's finding #2: the assertion compared the header **keys** of
the very object it later compared, so a header **value** mutated by the
response interceptor was invisible.

```
-    const headerKeys: string[][] = [];
+    const headersBefore: Array<Record<string, unknown>> = [];
     const adapter: AxiosAdapter = (config) => {
       seen.push(config);
-      headerKeys.push(Object.keys(config.headers));
+      // A COPY of the headers as they are on the wire, taken BEFORE the
+      // response interceptor runs.  Comparing against the same object's own
+      // keys could never see a header VALUE mutated by the response side.
+      headersBefore.push({ ...config.headers });
...
-    // The response interceptor changed nothing on the request.
-    expect(Object.keys(seen[0].headers)).toEqual(headerKeys[0]);
+    // The response interceptor changed nothing on the request: same headers,
+    // same values, as they were when the request went out.
+    expect({ ...seen[0].headers }).toEqual(headersBefore[0]);
```

The snapshot is a **copy** (`{ ...config.headers }`) taken at adapter time -
i.e. before the response interceptor runs - and the assertion compares the
post-rejection headers **by value** against that copy.

---

## 3. The full diff of this round

```
diff --git a/backend/tests/test_onboarding_inference_response_shape.py b/backend/tests/test_onboarding_inference_response_shape.py
index aabc756..db99318 100644
--- a/backend/tests/test_onboarding_inference_response_shape.py
+++ b/backend/tests/test_onboarding_inference_response_shape.py
@@ -241,6 +241,28 @@ def test_the_endpoint_still_reports_502_for_an_unrecognised_shape(monkeypatch):
     assert res.json()["detail"] == "Inference returned an empty response"


+def test_the_endpoint_reports_502_for_a_numeric_provider_answer(monkeypatch):
+    """A number is not the model's text, so the gate answers 502.
+
+    DELIBERATE change from the pre-fix parser: the old inline parse let any
+    truthy value through, so a provider body of ``{"response": 7}`` answered
+    **200** with a ``response_preview`` of ``"7"``.  The parser's rule - a
+    candidate is taken only if its value is a ``str`` - rejects a number, so
+    the same body is an empty response now.  Pinned here so the narrowing is
+    a recorded decision and not a drift nobody noticed.
+    """
+    for body in (
+        {"response": 7},
+        {"message": {"content": 7}},
+        {"choices": [{"message": {"content": 7}}]},
+    ):
+        res = _post_inference(monkeypatch, body)
+        assert res.status_code == 502
+        assert res.status_code != 200
+        assert res.status_code != 500
+        assert res.json()["detail"] == "Inference returned an empty response"
+
+
 def test_the_endpoint_contract_is_unchanged(monkeypatch):
     """The wire contract of the gate is exactly what it was."""
     res = _post_inference(monkeypatch, NATIVE_BODY, model_id=None, selected=None)
diff --git a/frontend/src/services/api.test.ts b/frontend/src/services/api.test.ts
index b0c4b29..b1a30a3 100644
--- a/frontend/src/services/api.test.ts
+++ b/frontend/src/services/api.test.ts
@@ -145,10 +145,13 @@ describe('the axios response interceptor', () => {

   it('does not change the request side', async () => {
     const seen: InternalAxiosRequestConfig[] = [];
-    const headerKeys: string[][] = [];
+    const headersBefore: Array<Record<string, unknown>> = [];
     const adapter: AxiosAdapter = (config) => {
       seen.push(config);
-      headerKeys.push(Object.keys(config.headers));
+      // A COPY of the headers as they are on the wire, taken BEFORE the
+      // response interceptor runs.  Comparing against the same object's own
+      // keys could never see a header VALUE mutated by the response side.
+      headersBefore.push({ ...config.headers });
       return Promise.reject(
         new AxiosError(
           'Request failed with status code 502',
@@ -172,8 +175,9 @@ describe('the axios response interceptor', () => {
     expect(seen).toHaveLength(1);
     // The request interceptor still ran: the control token is on the way out.
     expect(seen[0].headers['X-Control-Token']).toBe(CONTROL_TOKEN);
-    // The response interceptor changed nothing on the request.
-    expect(Object.keys(seen[0].headers)).toEqual(headerKeys[0]);
+    // The response interceptor changed nothing on the request: same headers,
+    // same values, as they were when the request went out.
+    expect({ ...seen[0].headers }).toEqual(headersBefore[0]);
   });

   it('never puts the control token in the message', async () => {
```

---

## 4. Verification

All commands were run in `C:\Users\orcha\AppData\Local\Temp\opencode\wt-021`
(branch `orchestra/021-onboarding-inference-shape`) with the round-2 changes
applied.

### 4.1 The backend test file

```
$ python -m pytest backend/tests/test_onboarding_inference_response_shape.py -q
...
14 passed, 908 warnings in 5.44s
```

**14 passed** - the previous count was **13**, so the file grew by exactly the
**one** new test. The plan's `+13` backend delta becomes **`+14`**.

### 4.2 The frontend test file

```
$ cd frontend && npx vitest run src/services/api.test.ts
 RUN  v1.6.1 C:/Users/orcha/AppData/Local/Temp/opencode/wt-021/frontend

 ✓ src/services/api.test.ts  (9 tests) 65ms

 Test Files  1 passed (1)
      Tests  9 passed (9)
   Duration  1.92s
```

**9 passed** - the frontend test count is unchanged (the test's *id* did not
change; only its assertion got teeth).

### 4.3 Frontend lint

```
$ cd frontend && npm run lint
> sythion-frontend@0.1.0 lint
> eslint . --ext ts,tsx --report-unused-disable-directives --max-warnings 0

lint exit: 0
```

**exit 0, no output, no warnings.**

### 4.4 The collected backend count

```
$ python -m pytest backend/tests --collect-only -q
...
1642 tests collected in 146.39s (0:02:26)
```

**1642 collected** - the branch's round-1 count was **1641**, so this round
adds exactly **one** collected test (`+14` over the plan's `+13`).

### 4.5 The full backend suite

```
$ python -m pytest backend/tests -q
...
-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
1597 passed, 45 skipped, 432033 warnings in 465.50s (0:07:45)

(exit code 0)
```

**1597 passed, 45 skipped, 0 failed** (exit 0). The run completed normally on
the first attempt - it did **not** die natively, so no re-run was needed.

Against the round-1 numbers measured on the same branch tip `a1e2e5a` by the
round-1 review (**1641 collected / 1596 passed / 45 skipped**):

| measure | `a1e2e5a` (round 1) | `15a2bbe` (round 2) | delta |
|---|---|---|---|
| collected | 1641 | **1642** | **+1** |
| passed | 1596 | **1597** | **+1** |
| skipped | 45 | **45** | 0 |
| failed | 0 | **0** | 0 |

The plan's planned `+13` backend delta is therefore now **`+14`**, and the
skipped population is unchanged.

**On the two known pre-existing problems on this machine:** neither occurred
in this run. The `test_phase975_admission_multiproc.py` contention flakes did
**not** fire (that file's tests passed), and there was **no** `0xC0000005`
native crash in `test_phase95_hardening.py`. Nothing in this round can be
attributed to either of them, and nothing needs to be: the round adds one
Python test and repairs one TypeScript assertion, and the suite was already
green here.

### 4.6 Extra (not required, but run because the change touches both a Python
and a TypeScript test file)

```
$ cd frontend && npm run build
> sythion-frontend@0.1.0 build
> tsc && vite build
...
✓ 194 modules transformed.
✓ built in 9.20s
build exit: 0
```

`tsc` is clean under `strict` (so the new `Array<Record<string, unknown>>`
annotation and the header spread type-check).

---

## 5. Evidence that the two changes have teeth

### 5.1 The new backend test fails against the PRE-FIX parser

A read-only probe (TEMP, outside the repository, deleted afterwards) re-ran the
**old** inline parse from base `f8cce86` against the **real** new helper:

```
$ python C:\Users\orcha\AppData\Local\Temp\opencode\probe_021_r2.py
{'response': 7}
   old parser -> (200, '7')
   new parser -> (502, 'Inference returned an empty response')
{'message': {'content': 7}}
   old parser -> (502, 'Inference returned an empty response')
   new parser -> (502, 'Inference returned an empty response')
{'choices': [{'message': {'content': 7}}]}
   old parser -> (200, '7')
   new parser -> (502, 'Inference returned an empty response')
```

Two of the three bodies answered **200 with a `response_preview` of `"7"`**
under the pre-fix parser; the new test asserts 502, so it **fails behaviourally
on the pre-fix parser** (not by import error). The third body
(`{"message": {"content": 7}}`) was already a 502 under the old parser and is
pinned for completeness.

### 5.2 The repaired frontend assertion now sees a header VALUE mutation

A temporary negative-control vitest file (created inside the worktree, run,
then **deleted** - `git status` below confirms nothing was left behind)
registered a hostile response interceptor that mutates the request's
`X-Control-Token` **value**, and then applied BOTH assertions:

```
$ cd frontend && npx vitest run src/services/f2probe.test.ts
 ✓ src/services/f2probe.test.ts  (1 test) 36ms

 Test Files  1 passed (1)
      Tests  1 passed (1)
```

The probe asserts, in one test:

* `expect(Object.keys(seen[0].headers)).toEqual(Object.keys(headersBefore[0]))`
  - the **OLD** assertion - **passes** even though the value was mutated
  (it cannot see it);
* `expect({ ...seen[0].headers }).not.toEqual(headersBefore[0])`
  - the **NEW** assertion - **fails** on the same mutation, i.e. it **sees** it.

So the repaired assertion is strictly stronger, and the old one demonstrably
was blind to a value mutation.

---

## 6. Scope

```
$ git diff --name-status a1e2e5a..HEAD
M	backend/tests/test_onboarding_inference_response_shape.py
M	frontend/src/services/api.test.ts
```

* The round's delta touches **only the two test files**.
* `backend/api/routes.py` and `frontend/src/services/api.ts` are **not** in
  this round's delta - they are byte-identical to `a1e2e5a`.
* The item's total delta against the merge base `f8cce86` is still exactly the
  **four** files:

```
$ git diff --name-status f8cce86..HEAD
M	backend/api/routes.py
A	backend/tests/test_onboarding_inference_response_shape.py
A	frontend/src/services/api.test.ts
M	frontend/src/services/api.ts
```

* The literal `git diff --name-only master..HEAD` view also lists
  `.orchestra/` bookkeeping entries. That is **not** a scope violation and it
  is **pre-existing**: `master` advanced past the branch's merge base with
  `chore(orchestrator)` commits, so the two-dot view shows the reverse of
  master's own records (exactly the situation the round-1 review measured and
  recorded). Measured at the time of writing:

```
$ git diff --name-only master..HEAD
.orchestra/briefs/020-implement-2.md
.orchestra/briefs/020-review-1.md
.orchestra/briefs/021-implement-2.md
.orchestra/briefs/021-review-1.md
.orchestra/briefs/021-verify-1.md
.orchestra/critiques.md
.orchestra/follow-ups.md
.orchestra/reports/020-implement-1.md
.orchestra/reports/021-implement-1.md
.orchestra/reviews/020-r1.md
.orchestra/reviews/021-r1.md
.orchestra/verifications/021-v1.md
backend/api/routes.py
backend/tests/test_onboarding_inference_response_shape.py
frontend/src/services/api.test.ts
frontend/src/services/api.ts
```

  The **source** part of that list is still exactly the item's four files, and
  the branch itself touches **no** `.orchestra/` file:
  `git diff f8cce86..HEAD -- .orchestra` is **empty** (measured, no output).
  `git diff --name-status f8cce86..HEAD` is the authoritative view of the
  item's delta and it lists the four files and nothing else.
* No test was weakened, no test id was changed, no dependency was added, no
  lock file moved.

---

## 7. Notes for the reviewer

* The provider was **not** called, the credential vault was **not** read, no
  socket was opened. The backend provider is the file's existing stub; the
  frontend uses axios's own per-request `adapter` hook. The in-memory
  `MemoryStore` guard is still installed for every test in the file.
* The two known pre-existing problems on this machine (the
  `test_phase975_admission_multiproc.py` contention flakes and the
  intermittent native `0xC0000005` crash in `test_phase95_hardening.py`,
  reproducible on pre-change master) are **not** attributable to this change -
  this round adds one Python test and repairs one TypeScript assertion. In
  this run **neither** of them fired: the full suite completed with exit 0 and
  zero failures on the first attempt (see §4.5).
* Two temporary probe artefacts were used and **deleted**:
  `frontend/src/services/f2probe.test.ts` (the negative control of §5.2, run
  inside the worktree and then removed) and
  `C:\Users\orcha\AppData\Local\Temp\opencode\probe_021_r2.py` (the
  old-vs-new parse probe of §5.1, outside the repository). The worktree is
  clean: `git status --short` is empty after the commit.
* Not pushed. `master` untouched. The report lives at
  `C:\Sythion\sythion\.orchestra\reports\021-implement-2.md` (main checkout,
  master), not on the branch - the branch's file set stays at the four files.
