# 021 - implement - round 4 (pin the strip half of the candidate rule)

- **Item:** 021 - The onboarding inference gate must parse the provider's real response shape, and the UI must show the backend's reason
- **Role:** implementer (round 4)
- **Branch:** `orchestra/021-r3-exhaustive-pin` (EXISTING branch, continued)
- **Base of this round:** `df58bd6`
- **Commit of this round:** `92363431fb08c5c56bc3efb7b08ef1465daf4c76` (`9236343`)
- **Worktree used:** `C:\Users\orcha\AppData\Local\Temp\opencode\wt-021-r3`
- **Kind:** TEST-ONLY round. **No production file changed** (proof below).

---

## 1. What the round did (the finding N3)

The parser's candidate rule (`backend/api/routes.py::_extract_inference_text`, the
nested `_candidate` helper) has TWO halves:

1. the value must be a `str` - pinned exhaustively by round 3;
2. the value must be **non-blank after `strip()`** - **was not pinned** (the round-3
   Reviewer MEASURED that removing `.strip()` leaves the whole file 15/15 green).

This round pins half (2). The parser was READ FIRST and its real behaviour
measured (see the probe in section 3), so the test records what the code actually
does rather than an assumption:

| candidate value | `_extract_inference_text` actually returns |
|---|---|
| `"   "` (blank str) | `""` |
| `"\n\t "` (blank str) | `""` |
| `"  OK  "` (padded str) | `"OK"` - **TRIMMED**, not the raw `"  OK  "` |

The measured behaviour comes from `backend/api/routes.py` lines 2201-2205:

```python
    def _candidate(value: Any) -> str:
        """Return the stripped string, or '' for anything that is not text."""
        if isinstance(value, str):
            return value.strip()
        return ""
```

### What the new coverage pins

**(a) helper level - inside the EXISTING matrix test
`test_a_candidate_is_taken_only_if_its_value_is_a_str`** (one collected test, as
before). The matrix now carries, for **EACH of the parser's four branches**:

* the two blank str candidates `"   "` and `"\n\t "` - must yield `""` (8 cases);
* the padded str candidate `"  OK  "` - must be returned **trimmed** (`"OK"`, 4 cases).

The matrix keeps its ONE `pytest.mark.parametrize` parameter set, so the file did
NOT become 24 collected cases. Every case still carries its own label, so a
failure names the exact branch and candidate that broke.

**(b) endpoint level - a NEW collected test
`test_the_endpoint_reports_502_for_a_whitespace_only_provider_answer`**, placed
directly alongside `test_the_endpoint_reports_502_for_a_numeric_provider_answer`.
It pins:

* a whitespace-only answer is a **502** with the detail
  `"Inference returned an empty response"` - never 200, never 500 - for **EACH of
  the four branches** (8 bodies: both blank strings x the native, openai-chat,
  legacy-choice and legacy-generate shapes);
* a padded answer is NOT an empty answer: `{"response": "  OK  "}` answers **200**
  with `response_preview == "OK"` (the trimmed value);
* a blank candidate must not **mask** a real answer that a LATER branch carries:
  the parser takes the first candidate that yields a NON-BLANK string, so
  `{"message": {"content": "   "}, "response": "OK"}` and
  `{"choices": [{"message": {"content": "   "}, "text": "OK"}]}` answer **200**
  with `response_preview == "OK"`.

The third bullet is what gives the ENDPOINT-level test its teeth: the endpoint
re-strips the text itself (`str(text).strip()`, `backend/api/routes.py:2264`), so
a whitespace-only answer alone would still be a 502 even without `.strip()` in
`_candidate`. Only the masking case flips 200 -> 502 when `.strip()` is removed
(measured - see the teeth proof in section 5).

### Count contract

The round's delta is exactly **+1 collected test**: the matrix was EXTENDED with
cases (it stays one collected test) and exactly one new collected test was added.

| | before (`df58bd6`) | after (`9236343`) |
|---|---|---|
| `backend/tests/test_onboarding_inference_response_shape.py` | 15 passed | **16 passed** |
| `python -m pytest backend/tests --collect-only -q` | 1643 collected | **1644 collected** |

### Scope proof - NO production code changed

```
git diff df58bd6..HEAD --name-only
backend/tests/test_onboarding_inference_response_shape.py

git rev-parse df58bd6:backend/api/routes.py      -> 5aa931a43ee0ba1f05d1b9828a9c2ec36885c2bd
git rev-parse HEAD:backend/api/routes.py         -> 5aa931a43ee0ba1f05d1b9828a9c2ec36885c2bd
git rev-parse df58bd6:frontend/src/services/api.ts -> 736eb8d038151f877d9ccabaf0fa70edb44e70f4
git rev-parse HEAD:frontend/src/services/api.ts    -> 736eb8d038151f877d9ccabaf0fa70edb44e70f4
```

`backend/api/routes.py` and `frontend/src/services/api.ts` are **byte-identical**
(both git blob hashes equal). The only changed path in the round is the test file.

N4 (the one-parameter-set design) and N5 (a report defect) were deliberately NOT
acted on.

---

## 2. The exact diff

```
git diff df58bd6..9236343
diff --git a/backend/tests/test_onboarding_inference_response_shape.py b/backend/tests/test_onboarding_inference_response_shape.py
index 7a44e05..19f9d09 100644
--- a/backend/tests/test_onboarding_inference_response_shape.py
+++ b/backend/tests/test_onboarding_inference_response_shape.py
@@ -201,9 +201,13 @@ def test_a_malformed_body_does_not_raise():
 # --- the candidate rule ------------------------------------------------------
 
 # The parser's rule, over the whole enumerable domain of candidate values: a
-# candidate is taken only if its value is a ``str``.  These are the parser's
-# FOUR branches, in the parser's own order (the ``_extract_inference_text``
-# helper in backend/api/routes.py):
+# candidate is taken only if its value is a ``str`` that is NOT BLANK.  The
+# rule has TWO halves and BOTH of them are pinned in this file:
+#   1. the value must be a ``str``                 -> the non-str candidates
+#   2. the value must be non-blank after ``strip()`` -> the blank-str
+#      candidates (a ``str`` of only whitespace)
+# These are the parser's FOUR branches, in the parser's own order (the
+# ``_extract_inference_text`` helper in backend/api/routes.py):
 #   1. native Ollama /api/chat      -> response["message"]["content"]
 #   2. OpenAI-compatible chat       -> response["choices"][0]["message"]["content"]
 #   3. OpenAI-compatible legacy     -> response["choices"][0]["text"]
@@ -222,14 +226,31 @@ _CANDIDATE_BRANCHES = (
 # None of them is the model's text, so the rule rejects every one of them.
 _NON_STR_CANDIDATES = (7, 7.5, True, {"a": 1}, [1, 2])
 
+# The BLANK str candidates: a ``str`` whose content is only whitespace.  They
+# ARE strings, so they satisfy the first half of the rule, but they carry no
+# text, so the second half rejects them: the parser strips them, sees
+# nothing, and a blank candidate does not even mask a real answer that a
+# LATER branch carries (the parser takes the first candidate that yields a
+# NON-BLANK string).
+_BLANK_STR_CANDIDATES = ("   ", "\n\t ")
+
+# A str candidate with whitespace AROUND it.  It is the model's text, so it
+# IS taken - but TRIMMED: the parser returns ``value.strip()``, so the
+# expected value is ``"OK"`` and not ``"  OK  "``.
+_PADDED_STR_CANDIDATE = "  OK  "
+
 
 def _candidate_cases():
     """Every case the candidate rule is pinned over.
 
     Five non-str candidate types in each of the four branches - 20 cases that
-    must yield ``''`` - plus the four POSITIVE cases, a ``str`` candidate in
-    each branch, that must still be returned.  The positive cases are what
-    stops the rule from being satisfied by a helper that always answers ``''``.
+    must yield ``''`` - plus TWO blank str candidates per branch - 8 more
+    cases that must yield ``''`` - plus the four POSITIVE cases, a ``str``
+    candidate in each branch, that must still be returned, and the four
+    PADDED cases, a ``str`` candidate with whitespace around it, that must
+    be returned TRIMMED (``"  OK  "`` -> ``"OK"``).  The positive cases are
+    what stops the rule from being satisfied by a helper that always answers
+    ``''``.
     """
     cases = []
     for branch, build in _CANDIDATE_BRANCHES:
@@ -241,7 +262,22 @@ def _candidate_cases():
                     "",
                 )
             )
+        for candidate in _BLANK_STR_CANDIDATES:
+            cases.append(
+                (
+                    "%s / blank str %r" % (branch, candidate),
+                    build(candidate),
+                    "",
+                )
+            )
         cases.append(("%s / str" % branch, build("OK"), "OK"))
+        cases.append(
+            (
+                "%s / padded str %r" % (branch, _PADDED_STR_CANDIDATE),
+                build(_PADDED_STR_CANDIDATE),
+                "OK",
+            )
+        )
     return cases
 
 
@@ -267,6 +303,14 @@ def test_a_candidate_is_taken_only_if_its_value_is_a_str(case):
     accepted an int in the fourth branch - would have passed the whole suite
     while the narrowing silently widened.
 
+    The rule has a SECOND half as well, and this matrix pins it too: the
+    value must be non-blank after ``strip()``.  A ``str`` of only whitespace
+    (``"   "``, ``"\\n\\t "``) carries no text, so it must yield ``''`` in
+    EVERY branch - and a ``str`` with whitespace AROUND it (``"  OK  "``) IS
+    the model's text and must be returned TRIMMED (``"OK"``).  Both halves
+    live in this ONE matrix on purpose: the rule is a single logical property
+    and the count contract is one collected test.
+
     The helper is imported INSIDE the test function, exactly as ``_parse``
     above does, so that a tree on which the helper does not exist yet fails
     this test BEHAVIOURALLY instead of turning the whole module into a
@@ -352,6 +396,68 @@ def test_the_endpoint_reports_502_for_a_numeric_provider_answer(monkeypatch):
         assert res.json()["detail"] == "Inference returned an empty response"
 
 
+def test_the_endpoint_reports_502_for_a_whitespace_only_provider_answer(monkeypatch):
+    """Whitespace is not the model's text, so the gate answers 502.
+
+    The parser's candidate rule has TWO halves: the value must be a ``str``
+    AND it must be non-blank after ``strip()``.  The first half is pinned by
+    ``test_a_candidate_is_taken_only_if_its_value_is_a_str`` (helper level)
+    and by ``test_the_endpoint_reports_502_for_a_numeric_provider_answer``
+    (endpoint level); this test pins the SECOND half at the endpoint level.
+
+    A candidate that is a ``str`` of only whitespace carries no text, so the
+    gate must answer 502 with the same detail as every other empty answer -
+    never 200 and never 500.  The bodies below cover ALL FOUR branches of
+    the parser: the native ``/api/chat`` shape, the OpenAI-compatible chat
+    shape, the OpenAI-compatible legacy completions shape and the legacy
+    ``/api/generate`` shape.
+
+    Whitespace AROUND a real answer is not an empty answer: a padded
+    candidate IS the model's text and the gate reports it TRIMMED - a body of
+    ``{"response": "  OK  "}`` answers 200 with a ``response_preview`` of
+    ``"OK"``, not ``"  OK  "`` and not a 502.
+
+    And a blank candidate must not MASK a real answer that a later branch
+    carries: the parser takes the first candidate that yields a NON-BLANK
+    string, so a blank native content falls through to the next branch
+    instead of swallowing the answer.
+    """
+    for body in (
+        {"message": {"content": "   "}},
+        {"message": {"content": "\n\t "}},
+        {"choices": [{"message": {"content": "   "}}]},
+        {"choices": [{"message": {"content": "\n\t "}}]},
+        {"choices": [{"text": "   "}]},
+        {"choices": [{"text": "\n\t "}]},
+        {"response": "   "},
+        {"response": "\n\t "},
+    ):
+        res = _post_inference(monkeypatch, body)
+        assert res.status_code == 502
+        assert res.status_code != 200
+        assert res.status_code != 500
+        assert res.json()["detail"] == "Inference returned an empty response"
+
+    # A padded answer IS the model's text, and it is reported TRIMMED.
+    res = _post_inference(monkeypatch, {"response": "  OK  "})
+    assert res.status_code == 200
+    assert res.json()["response_preview"] == "OK"
+
+    # A blank candidate must not mask a real answer from a LATER branch.
+    res = _post_inference(
+        monkeypatch, {"message": {"content": "   "}, "response": "OK"}
+    )
+    assert res.status_code == 200
+    assert res.json()["response_preview"] == "OK"
+
+    res = _post_inference(
+        monkeypatch,
+        {"choices": [{"message": {"content": "   "}, "text": "OK"}]},
+    )
+    assert res.status_code == 200
+    assert res.json()["response_preview"] == "OK"
+
+
 def test_the_endpoint_contract_is_unchanged(monkeypatch):
     """The wire contract of the gate is exactly what it was."""
     res = _post_inference(monkeypatch, NATIVE_BODY, model_id=None, selected=None)
```

Note on the diff: every existing case and every existing assertion is
**byte-identical** - the diff is purely additive plus three docstring/comment
updates. No existing test was weakened.

---

## 3. The parser was READ FIRST (measured behaviour, not an assumption)

Probe (run from the worktree root, imports the real
`backend.api.routes._extract_inference_text`; the probe file itself lived
outside the repo and was deleted afterwards):

```
native blank spaces                      -> ''
native blank nl-tab                      -> ''
native padded                            -> 'OK'
openai-chat blank                        -> ''
openai-chat padded                       -> 'OK'
legacy-choice blank                      -> ''
legacy-choice padded                     -> 'OK'
legacy-generate blank                    -> ''
legacy-generate padded                   -> 'OK'
native blank + generate real             -> 'OK'
openai blank + legacy choice real        -> 'OK'
legacy-choice blank + generate real      -> 'OK'
native blank + openai padded             -> 'OK'
```

So: blank -> `""` in every branch; padded -> the TRIMMED `"OK"` in every branch; a
blank candidate does not mask a real answer from a later branch. The test pins
exactly this.

---

## 4. Verification - every command and its output

All backend commands were run in the worktree
`C:\Users\orcha\AppData\Local\Temp\opencode\wt-021-r3` at commit `9236343`; all
frontend commands in `C:\Users\orcha\AppData\Local\Temp\opencode\wt-021-r3\frontend`
(this round touches no frontend file - the branch's frontend diff against master
is still only round 3's `src/services/api.test.ts` change).

### 4.1 The backend test file

```
> python -m pytest backend/tests/test_onboarding_inference_response_shape.py -q
................                                                         [100%]
============================== warnings summary ===============================
..\..\..\Programs\Python\Python314\Lib\site-packages\starlette\formparsers.py:10
  C:\Users\orcha\AppData\Local\Programs\Python\Python314\Lib\site-packages\starlette\formparsers.py:10: PendingDeprecationWarning: Please use `import python_multipart` instead.
    import multipart
... (DeprecationWarnings only)
-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
16 passed, 1046 warnings in 2.88s
```

**16 passed** (round 3: 15 passed) - the file's new count.

### 4.2 The frontend test file (UNCHANGED count)

```
> npx vitest run src/services/api.test.ts

 RUN  v1.6.1 C:/Users/orcha/AppData/Local/Temp/opencode/wt-021-r3/frontend

 ✓ src/services/api.test.ts  (9 tests) 20ms

 Test Files  1 passed (1)
      Tests  9 passed (9)
   Start at  12:24:25
   Duration  669ms (transform 88ms, setup 1ms, collect 218ms, tests 20ms, environment 0ms, prepare 146ms)
```

**9 passed** - the count is unchanged, as required.

### 4.3 Lint

```
> npm run lint

> sythion-frontend@0.1.0 lint
> eslint . --ext ts,tsx --report-unused-disable-directives --max-warnings 0

LINT_EXIT=0
```

Exit **0**, no warnings.

### 4.4 Build

```
> npm run build

> sythion-frontend@0.1.0 build
> tsc && vite build

vite v5.4.21 building for production...
transforming...
✓ 194 modules transformed.
rendering chunks...
computing gzip size...
dist/index.html                   1.15 kB │ gzip:   0.63 kB
dist/assets/index-CCraEvIB.css   48.61 kB │ gzip:   9.22 kB
dist/assets/core-BUhCcD2K.js      2.74 kB │ gzip:   1.14 kB │ map:  25.05 kB
dist/assets/window-BPQH9sz4.js   15.60 kB │ gzip:   3.95 kB │ map: 135.17 kB
dist/assets/index-C8_LppAK.js   472.46 kB │ gzip: 142.19 kB │ map: 1,473.53 kB
✓ built in 3.81s
BUILD_EXIT=0
```

Exit **0**.

### 4.5 Collection count

```
> python -m pytest backend/tests --collect-only -q
...
-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
1644 tests collected in 68.29s (0:01:08)
```

**1644 collected** (round 3: 1643) - exactly +1.

### 4.6 The full backend suite (BACKGROUND, polled to completion)

```
> python -m pytest backend/tests -q
.......................................................ss............... [  4%]
........................................................................ [  8%]
........................................................................ [ 13%]
........................................................................ [ 17%]
........................................................................ [ 21%]
........................................................................ [ 26%]
........................................................................ [ 30%]
........................................................................ [ 35%]
........................................................................ [ 39%]
...................ss................s........ss........................ [ 43%]
s....................................................................... [ 48%]
........................................................................ [ 52%]
........................................................................ [ 56%]
........................................................................ [ 61%]
........................................................................ [ 65%]
........................................s...........ss.................. [ 70%]
.......................................sssssssssssssssssssssssssssssss.. [ 74%]
........................................................................ [ 78%]
........................................................................ [ 83%]
........................................................................ [ 87%]
.......................................sss.............................. [ 91%]
........................................................................ [ 96%]
............................................................             [100%]
... (warnings summary block - 432,517 DeprecationWarnings, ~170 KB, elided; it
    contains no test result, only deprecation text)
-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
1599 passed, 45 skipped, 432517 warnings in 314.15s (0:05:14)
FULL_SUITE_EXIT=0
```

**1599 passed / 45 skipped / 0 failed, 1644 collected, exit 0.** The collection
count is exactly the round-3 count (1643) plus this round's one test (1644), and
the skipped count is unchanged. For reference, the round-2 checkpoint recorded
1597 passed / 45 skipped / 1642 collected, i.e. this round's +1 collected
(+1 passed, 45 skipped) is the same delta the item has carried since round 2.

The known intermittent native crash (`0xC0000005`) did **not** fire in this run
and no re-run was needed; the `test_phase975_admission_multiproc.py` contention
flakes did **not** fire either. Both are pre-existing and unrelated to this
change (this change touches one test file and no production code). A grep of the
run's output for `FAILED`/`ERROR` matched only 12 lines, all of them test NAMES
or fixture data that contain the words "failed"/"error" - there is no failing
test in the run.

---

## 5. THE TEETH - proof that the new pins have teeth

Done in a **TEMP detached worktree copy** of the parser, never in the committed
source:

```
> git -C C:\Sythion\sythion worktree add --detach C:\Users\orcha\AppData\Local\Temp\opencode\wt-021-teeth 9236343
HEAD is now at 9236343 test(onboarding): pin the non-blank half of the candidate rule
```

The committed source in `wt-021-r3` was never edited (its `git status` is clean
and its `backend/api/routes.py` blob hash is unchanged - see section 1).

### TEETH 1 - remove the `.strip()` -> the NEW coverage FAILS

In the temp copy, `backend/api/routes.py` was changed from

```python
        if isinstance(value, str):
            return value.strip()
```

to

```python
        if isinstance(value, str):
            return value
```

```
> python -m pytest backend/tests/test_onboarding_inference_response_shape.py -q
.......F......F.                                                         [100%]
================================== FAILURES ===================================
_ test_a_candidate_is_taken_only_if_its_value_is_a_str[every-branch-and-every-candidate-type] _
...
    for label, body, expected in case:
>       assert _extract_inference_text(body) == expected, label
E       AssertionError: native / blank str '   '
E       assert '   ' == ''
E
E         Strings contain only whitespace, escaping them using repr()
E         - ''
E         + '   '

backend\tests\test_onboarding_inference_response_shape.py:322: AssertionError
_____ test_the_endpoint_reports_502_for_a_whitespace_only_provider_answer _____
...
        # A blank candidate must not mask a real answer from a LATER branch.
        res = _post_inference(
            monkeypatch, {"message": {"content": "   "}, "response": "OK"}
        )
>       assert res.status_code == 200
E       assert 502 == 200
E        +  where 502 = <Response [502 Bad Gateway]>.status_code

backend\tests\test_onboarding_inference_response_shape.py:450: AssertionError
=========================== short test summary info ============================
FAILED backend/tests/test_onboarding_inference_response_shape.py::test_a_candidate_is_taken_only_if_its_value_is_a_str[every-branch-and-every-candidate-type]
FAILED backend/tests/test_onboarding_inference_response_shape.py::test_the_endpoint_reports_502_for_a_whitespace_only_provider_answer
2 failed, 14 passed, 1042 warnings in 2.22s
```

**Both the extended matrix AND the new endpoint test fail.** Before this round
this mutation left the file 15/15 green (the round-3 Reviewer's measurement); it
now leaves it 2 failed / 14 passed.

### TEETH 2 - remove the `isinstance(str)` check -> the round-3 matrix STILL FAILS

The temp copy was restored (`git checkout -- backend/api/routes.py`) and then
changed to

```python
    def _candidate(value: Any) -> str:
        """Return the stripped string, or '' for anything that is not text."""
        return value.strip()
```

(the `isinstance(value, str)` guard deleted):

```
> python -m pytest "backend/tests/test_onboarding_inference_response_shape.py::test_a_candidate_is_taken_only_if_its_value_is_a_str" -q
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

value = 7

    def _candidate(value: Any) -> str:
        """Return the stripped string, or '' for anything that is not text."""
>       return value.strip()
               ^^^^^^^^^^^
E       AttributeError: 'int' object has no attribute 'strip'

backend\api\routes.py:2203: AttributeError
...
=========================== short test summary info ============================
FAILED backend/tests/test_onboarding_inference_response_shape.py::test_a_candidate_is_taken_only_if_its_value_is_a_str[every-branch-and-every-candidate-type]
1 failed, 950 warnings in 1.40s
```

The round-3 matrix still fails - the two halves of the rule are pinned
independently.

### TEETH 2b (extra control) - the SUBTLE widening -> assertion-level failure

A third mutation in the same temp copy replaced the guard with a coercion
(`return str(value).strip()`), i.e. the widening that does NOT crash, so the
failure shows at the ASSERTION level (this is the exact scenario the round-3
review described as N1):

```
> python -m pytest backend/tests/test_onboarding_inference_response_shape.py -q
E           AssertionError: assert 'None' == ''
E           AssertionError: native / int
E           assert '7' == ''
E           assert 200 == 502
...
=========================== short test summary info ============================
FAILED backend/tests/test_onboarding_inference_response_shape.py::test_a_shape_that_is_none_of_the_known_ones_yields_no_text
FAILED backend/tests/test_onboarding_inference_response_shape.py::test_a_malformed_body_does_not_raise
FAILED backend/tests/test_onboarding_inference_response_shape.py::test_a_candidate_is_taken_only_if_its_value_is_a_str[every-branch-and-every-candidate-type]
FAILED backend/tests/test_onboarding_inference_response_shape.py::test_the_endpoint_still_reports_502_for_a_genuinely_empty_answer
FAILED backend/tests/test_onboarding_inference_response_shape.py::test_the_endpoint_still_reports_502_for_an_unrecognised_shape
FAILED backend/tests/test_onboarding_inference_response_shape.py::test_the_endpoint_reports_502_for_a_numeric_provider_answer
FAILED backend/tests/test_onboarding_inference_response_shape.py::test_the_endpoint_reports_502_for_a_whitespace_only_provider_answer
7 failed, 9 passed, 994 warnings in 1.75s
```

### Cleanup of the TEMP copies

```
> git -C C:\Users\orcha\AppData\Local\Temp\opencode\wt-021-teeth checkout -- backend/api/routes.py
> git -C C:\Sythion\sythion worktree remove C:\Users\orcha\AppData\Local\Temp\opencode\wt-021-teeth --force
> git -C C:\Sythion\sythion worktree list
C:/Sythion/sythion                                        df5424a [master]
C:/Users/orcha/AppData/Local/Temp/opencode/wt-020         e558991 [orchestra/020-env-isolation]
C:/Users/orcha/AppData/Local/Temp/opencode/wt-020-r3      e993976 [orchestra/020-r3-probe-scrub]
C:/Users/orcha/AppData/Local/Temp/opencode/wt-020-review  2fdc842 (detached HEAD)
C:/Users/orcha/AppData/Local/Temp/opencode/wt-020-review2 e558991 (detached HEAD)
C:/Users/orcha/AppData/Local/Temp/opencode/wt-021-r3      9236343 [orchestra/021-r3-exhaustive-pin]
> Test-Path C:\Users\orcha\AppData\Local\Temp\opencode\wt-021-teeth
False
```

The temp worktree is gone; the committed source was never modified.

---

## 6. Constraints honoured

- **TEST-ONLY.** `git diff df58bd6..HEAD --name-only` lists exactly one path:
  `backend/tests/test_onboarding_inference_response_shape.py`. The blob hashes of
  `backend/api/routes.py` and `frontend/src/services/api.ts` are unchanged.
- **Count contract:** backend file 15 -> **16**; collection 1643 -> **1644**
  (exactly +1, not 24 collected cases).
- **No existing test was weakened**; the diff is additive plus three
  docstring/comment updates.
- **Committed on the EXISTING branch** `orchestra/021-r3-exhaustive-pin`
  (commit `9236343`). **Not pushed.** `master` untouched (still `df5424a`).
  `orchestra/021-onboarding-inference-shape` untouched (still `15a2bbe`).
- **N4 and N5 not acted on.**
- No live provider calls, no credential-vault reads, no destructive commands.
- Every path cited in this report exists (the only path that no longer exists is
  the TEMP teeth worktree, and its removal is documented above).

## 7. Paths referenced in this report (all exist)

- `C:\Sythion\sythion\.orchestra\reports\021-implement-4.md` (this file)
- `C:\Sythion\sythion\.orchestra\briefs\021-implement-4.md` (the brief)
- `C:\Sythion\sythion\.orchestra\reviews\021-r3.md` (the round-3 review)
- `C:\Users\orcha\AppData\Local\Temp\opencode\wt-021-r3` (the worktree used)
- `C:\Users\orcha\AppData\Local\Temp\opencode\wt-021-r3\backend\api\routes.py`
  (the parser, unmodified in this round)
- `C:\Users\orcha\AppData\Local\Temp\opencode\wt-021-r3\backend\tests\test_onboarding_inference_response_shape.py`
  (the only changed file)
- `C:\Users\orcha\AppData\Local\Temp\opencode\wt-021-r3\frontend\src\services\api.test.ts`
  (unchanged in this round)
