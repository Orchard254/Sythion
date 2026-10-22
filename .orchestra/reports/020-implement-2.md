# 020 - implement, round 2: F1 (the placeholder fenced the wrong directory) and F2 (raw output rendered before the leak check)

- Item: 020 - The `.env` isolation must be measured, not assumed, and cover the process environment
- Role: implementer, round 2
- Branch: `orchestra/020-env-isolation` (continued)
- Base of this round: `2fdc842`
- **Commit: `e558991`** - `test(020): fence the legacy migration in the profile directory it really uses`
- Worktree: `C:\Users\orcha\AppData\Local\Temp\opencode\wt-020`
- **No production code changed.** The diff is `backend/tests/` only - the same three files as round 1:

```
$ git diff --name-only f1ad188..HEAD
backend/tests/isolation_probe_template.py
backend/tests/test_env_isolation.py
backend/tests/test_setup_readiness.py
```

---

## 1. What the round-1 report claimed, and why it was false

Round 1 reported: *"the scratch profile dir carries a placeholder so the legacy `~/.sythion`
migration cannot copy real user data in"*. The reviewer refuted that, and the reviewer was
right.

The migration lives in `backend/core/storage.py`:

```python
LEGACY_DATA_DIR = os.path.join(os.path.expanduser("~"), ".sythion")   # storage.py:20

def migrate_legacy_data_dir() -> bool:                                # storage.py:205
    legacy = Path(LEGACY_DATA_DIR)
    target = Path(data_dir())
    if not legacy.is_dir() or target == legacy:
        return False
    if any(target.iterdir()):        # <-- the fence: the target must be EMPTY
        return False
    ...copy every item of legacy into target...
```

The fence is *"the profile directory is not empty"*. The profile directory is **not**
`<scratch>/profile` in the shape the nested runs observe:

* `data_dir()` -> `resolve_data_dir(os.environ.get("SYTHION_DATA_DIR"), ...)` -> when no
  override is set it falls back to `default_data_dir()` -> `%LOCALAPPDATA%\Sythion (Synth)`
  (`storage.py:40-41`, `:51-64`, `:109-125`);
* the isolation fixture deletes **every** `SYTHION_*` variable for the duration of every test
  (`backend/tests/conftest.py:610-612`), so `SYTHION_DATA_DIR` is gone;
* `LOCALAPPDATA` points into the scratch tree -> the real target is
  **`<scratch>/localappdata/Sythion (Synth)`**.

Round 1 put the placeholder in `<scratch>/profile` only, so it fenced a directory the
migration does not look at in that shape. Nothing was copied only because the readiness file
builds `TestClient(app, ...)` **without** a `with` block, so `backend/main.py:87
migrate_legacy_data_dir()` never runs. The property was accidental, not constructed.

---

## 2. F1 - what changed

### 2.1 The fix

`backend/tests/test_env_isolation.py` now writes the placeholder into **both** directories the
profile can resolve to, **before anything runs**:

```python
def _fallback_profile_dir(scratch):
    """The profile directory the isolation-present shape resolves to.
    ...
    The name is taken from the production constant, not re-spelled here, so a
    rename in ``backend/core/storage.py`` moves this fence with it.
    """
    return scratch / "localappdata" / DEFAULT_DATA_DIR_NAME


def _write_the_placeholder(directory):
    """Put the isolation placeholder into a directory, creating it if needed."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / ".isolation-placeholder").write_text("", encoding="utf-8")
```

```python
    _write_the_placeholder(scratch / "profile")
    _write_the_placeholder(_fallback_profile_dir(scratch))
```

`DEFAULT_DATA_DIR_NAME` is imported from `backend/core/storage.py` (the production constant,
not a re-spelling), so a rename in production moves the fence with it.

### 2.2 The proof - a new test

`test_the_legacy_migration_cannot_copy_user_data_into_the_scratch_tree` (same file, no new
file) drives four probe subprocesses inside a scratch tree:

| run | shape | what it shows |
|---|---|---|
| 1 | `migration` - the migration invoked in the isolation-present shape (the reviewer's own probe) | the target is `<scratch>/localappdata/Sythion (Synth)`, it holds **only** the placeholder, `migrated=False`, **nothing copied** |
| 2 | `migration` - the same run against a second `%LOCALAPPDATA%` root with **no** placeholder (the control) | `migrated=True`, the fake legacy profile **IS** copied - so the fence, not luck, is what stops the copy |
| 3 | `current-shape` - the app imported, its `TestClient` built but never entered (today's shape) | the lifespan never runs, nothing copied |
| 4 | `lifespan` - the app's **own** startup path entered (`with TestClient(app)`) | the lifespan ran, nothing copied |

Design points that matter:

* **The fake legacy profile lives inside the scratch tree** (`<scratch>/fake-home/.sythion`)
  and the probe child's `HOME`/`USERPROFILE` point at it, so the developer's real
  `~/.sythion` is never even a candidate source (`LEGACY_DATA_DIR` is computed from
  `expanduser("~")` at import time). Nothing real is ever copied anywhere.
* **The probe child's environment is the check's own child environment** (`_child_environment`)
  with the isolation's effect applied - every `SYTHION_*` variable removed, exactly as the
  fixture does for the duration of a test. The check's own three nested runs keep their child
  environment **unchanged** (see section 4).
* **The migration probe runs FIRST**, because the profile directory has to be *pristine* when
  the migration looks at it. The probe reports what the directory held
  (`PROOF target_entries=...`) and the test asserts it was exactly
  `[".isolation-placeholder"]` - so a tree that was not pristine fails **loudly** instead of
  proving nothing.
* The probe never prints a file's content; the marker is a file whose content the test looks
  for by name.

---

## 3. F2 - what changed

**Shape chosen: assert the leak FIRST, and redact on top.** The leak check now runs
immediately after each nested run, *before* the return-code assertion that embeds the raw
child output; and every rendering of the raw output goes through `_redact_sentinels`.

```python
        run1 = _run_nested(scratch, 1)
        # The leak check comes FIRST: the raw child output is rendered in the
        # failure message below, and the whole point of the sentinel is that
        # it never appears in any captured stdout/stderr.  Once this check has
        # passed, the output is provably sentinel-free, so rendering it is
        # safe (and it is redacted a second time, see ``_redact_sentinels``).
        _assert_no_sentinel_leaked(run1, "run 1")
        assert run1.returncode == 0, (
            "run 1 (no .env, isolation present) exited %d; the output was:\n%s"
            % (run1.returncode, _redact_sentinels(run1.output))
        )
```

**Why this keeps failures diagnosable:** after the leak check has passed, the captured output
is *provably* sentinel-free, so the full raw output is still embedded in the failure message -
the developer sees the child's whole stdout/stderr, exactly as before. The redaction is a
second, independent layer: even if the leak assertion were reordered or removed, the value
could not escape.

**One extra finding, made while proving F2:** the leak assertion itself was a leak.
`assert sentinel not in text` makes pytest render **both operands** - the sentinel value and
the text containing it - so the check that exists to keep the value out of the output was the
very thing printing it. The comparison is now a boolean:

```python
    for text, where in ((run.output, "stdout/stderr"), (run.report_text, "the JUnit XML")):
        for sentinel in (FILE_SENTINEL, ENV_SENTINEL):
            leaked = sentinel in text
            assert leaked is False, (
                "%s: a sentinel value reached the %s" % (label, where)
            )
```

The assertion is exactly as strong; only the rendering changed (the property that broke is
named, never the value).

---

## 4. What did NOT change

* the check's signal line (`SIGNAL_ISOLATED` / `SIGNAL_UNISOLATED`);
* the three-run structure (runs 1, 2, 3) and their meaning;
* the expected signal;
* the timeout (`RUN_TIMEOUT_SECONDS = 120`);
* **the check's child environment** - `_child_environment()` is untouched: every `SYTHION_*`
  removed, `PYTHONPATH`/`PYTEST_ADDOPTS` scrubbed, `LOCALAPPDATA` and `SYTHION_DATA_DIR`
  redirected into the scratch tree, the process-environment sentinel exported. The
  `HOME`/`USERPROFILE` redirection and the "no `SYTHION_*`" shape live in the **separate**
  migration-probe environment (`_run_the_migration_probe`), which is a new mechanism for a
  new proof and does not touch the measurement;
* the negative control's shape (the anchor, the `subn(count=1)`, the loud `pytest.fail` when
  the anchor is missing);
* no test was weakened, skipped or deleted; the collected count moves by exactly **+1**
  (1629 -> 1630) - the one new F1 proof test.

---

## 5. Verification

### 5.1 The new test file

```
$ python -m pytest backend/tests/test_env_isolation.py -q
2 passed, 89 warnings in 15.98s
WALL: 17.2s
```

```
$ python -m pytest backend/tests/test_env_isolation.py -q -s
020 isolation measurement: run 1 5.1s [SENTINEL-PROBE file_neutralised=True env_neutralised=True env_cleared=True profile_isolated=True network_attempts=0] | run 2 3.4s [SENTINEL-PROBE file_neutralised=True env_neutralised=True env_cleared=True profile_isolated=True network_attempts=0] | run 3 4.4s [SENTINEL-PROBE file_neutralised=False env_neutralised=False env_cleared=False profile_isolated=True network_attempts=0]
2 passed, 89 warnings in 20.32s
WALL: 22.0s
```

(The three signal lines are byte-identical to the ones the reviewer measured in round 1.)

### 5.2 The readiness file

```
$ python -m pytest backend/tests/test_setup_readiness.py -q
11 passed, 760 warnings in 2.64s
```

### 5.3 The collected count

```
$ python -m pytest backend/tests --collect-only -q
1630 tests collected in 124.37s (0:02:04)
```

(1629 in round 1, +1 for the F1 proof test. The probe template still contributes 0.)

### 5.4 The negative control (throwaway worktree, deleted afterwards)

Throwaway worktree `wt-020-negctl-r2` at `e558991` (detached), with `autouse=True` removed
from `_isolate_local_env_settings` in `backend/tests/conftest.py` and nothing else changed:

```
$ python -m pytest backend/tests/test_env_isolation.py -q
FAILED backend/tests/test_env_isolation.py::test_the_local_env_isolation_is_measured_not_assumed
1 failed, 1 passed, 89 warnings in 16.97s
```

The failure is RED **for the right reason**:

```
E           AssertionError: run 1 (no .env, isolation present) exited 1; the output was:
E             F...........                                                             [100%]
...
E                     assert file_neutralised is True, (
E                         "the generated .env sentinel reached the effective settings"
E                     )
E             >       assert env_neutralised is True, (
E                         "the exported process-environment sentinel reached the effective settings"
E             E       AssertionError: the exported process-environment sentinel reached the effective settings
E             E       assert False is True
E             tests\test_env_isolation_probe.py:154: AssertionError
```

and **zero** sentinel values appear anywhere in the output:

```
$ grep -c "SENTINEL-FILE-020-DO-NOT-PRINT\|SENTINEL-ENV-020-DO-NOT-PRINT" <output>
0
```

The new F1 proof test still passes there (`1 passed`) - it does not depend on the conftest
isolation, because it builds its own explicitly-shaped environment.

The throwaway worktree was deleted afterwards (`git worktree remove --force`); the directory
no longer exists.

### 5.5 The F1 proof

Produced by the committed code itself (the test module's own helpers, run from a scratch
script outside the repository):

```
scratch tree: C:\Users\orcha\AppData\Local\Temp\sythion-020-evidence-sio8xt28
fake legacy profile: C:\Users\orcha\AppData\Local\Temp\sythion-020-evidence-sio8xt28\fake-home\.sythion
marker file: C:\Users\orcha\AppData\Local\Temp\sythion-020-evidence-sio8xt28\fake-home\.sythion\legacy-marker.txt

--- mode=migration  %LOCALAPPDATA%=localappdata  exit=0
      PROOF legacy=...\sythion-020-evidence-sio8xt28\fake-home\.sythion
      PROOF migrated=False
      PROOF target=...\sythion-020-evidence-sio8xt28\localappdata\Sythion (Synth)
      PROOF target_entries=.isolation-placeholder
      marker copies under the scratch tree: []

--- mode=migration  %LOCALAPPDATA%=localappdata-unfenced  exit=0
      PROOF legacy=...\sythion-020-evidence-sio8xt28\fake-home\.sythion
      PROOF migrated=True
      PROOF target=...\sythion-020-evidence-sio8xt28\localappdata-unfenced\Sythion (Synth)
      PROOF target_entries=
      marker copies under the scratch tree: ['localappdata-unfenced\\Sythion (Synth)\\legacy-marker.txt']

--- mode=current-shape  %LOCALAPPDATA%=localappdata  exit=0
      PROOF legacy=...\sythion-020-evidence-sio8xt28\fake-home\.sythion
      PROOF lifespan=not-run
      PROOF target=...\sythion-020-evidence-sio8xt28\localappdata\Sythion (Synth)
      PROOF target_entries=.isolation-placeholder
      marker copies under the scratch tree: ['localappdata-unfenced\\Sythion (Synth)\\legacy-marker.txt']

--- mode=lifespan  %LOCALAPPDATA%=localappdata  exit=0
      PROOF legacy=...\sythion-020-evidence-sio8xt28\fake-home\.sythion
      PROOF lifespan=ran
      PROOF target=...\sythion-020-evidence-sio8xt28\localappdata\Sythion (Synth)
      PROOF target_entries=.isolation-placeholder,logs
      marker copies under the scratch tree: ['localappdata-unfenced\\Sythion (Synth)\\legacy-marker.txt']

scratch tree removed: True
```

Read it:

* **run 1 (the reviewer's own probe, isolation-present shape)** - the migration's target is
  `<scratch>/localappdata/Sythion (Synth)` (NOT `<scratch>/profile`), it holds exactly one
  entry, the placeholder; `migrated=False`; **the fake legacy profile was NOT copied**
  (`marker copies: []`).
* **run 2 (the control - the ONLY difference is the placeholder)** - the target is empty,
  `migrated=True`, and the fake legacy profile **IS** copied into the unfenced profile
  directory. So the fence is load-bearing: it is the placeholder, not luck, that stops the
  copy.
* **run 3 (today's shape: the app imported, its `TestClient` never entered)** - the lifespan
  never runs, nothing is copied.
* **run 4 (the app's OWN lifespan running)** - `PROOF lifespan=ran`, and nothing is copied.

Honest note on run 4: in the app-lifespan shape the profile directory is non-empty for
**three** reasons at once - the placeholder, the `logs` directory
`backend/core/logging_config.py` creates in the profile directory at import time (visible in
`target_entries=.isolation-placeholder,logs`), and the profile lock + handshake file the
lifespan creates before `backend/main.py:87` calls the migration. That shape therefore cannot
discriminate the placeholder on its own; runs 1 and 2 are the discriminating pair (the
migration-direct shape, where the profile directory is pristine except for the placeholder).
The property the item cares about - *a copy of a user's real data into the scratch tree is
impossible regardless of whether the lifespan runs* - holds in both shapes, and the fence is
in place **before anything runs**.

**The proof is not vacuous - the fix was removed and the test went RED:**

```
E           AssertionError: the profile directory held [] and not only the placeholder, so this run would prove
E           nothing: the fence has to be the ONLY thing in the directory when the migration looks at it
E           assert [] == ['.isolation-placeholder']
```

(that was the state with `_write_the_placeholder(_fallback_profile_dir(scratch))` deleted; the
line was restored afterwards and the tree is committed clean).

### 5.6 The F2 before/after, measured

The same injection (a probe test that fails with the sentinel value in its message) was run
against the round-1 code and against the round-2 code:

| code | sentinel value occurrences in the outer output | the failure message |
|---|---|---|
| round 1 (`2fdc842`) | **1** | `run 1 (no .env, isolation present) exited 1; the output was: ... AssertionError: the exported process-environment sentinel reached the effective settings: SENTINEL-FILE-020-DO-NOT-PRINT` - the value was printed by the raw-output embedding, **before** the leak assertion ran |
| round 2 (`e558991`) | **0** | `run 1: a sentinel value reached the stdout/stderr` / `assert True is False` - the property that broke, never the value |

Round-1 output (excerpt):

```
E           AssertionError: run 1 (no .env, isolation present) exited 1; the output was:
E             .F...........                                                            [100%]
E             ================================== FAILURES ===================================
E             ________________ test_the_sentinel_leaks_for_the_demonstration ________________
E                 def test_the_sentinel_leaks_for_the_demonstration():
E             >       assert False, 'the exported process-environment sentinel reached the effective settings: %s' % FILE_SENTINEL
E             E       AssertionError: the exported process-environment sentinel reached the effective settings: SENTINEL-FILE-020-DO-NOT-PRINT
```

Round-2 output (excerpt):

```
E               AssertionError: run 1: a sentinel value reached the stdout/stderr
E               assert True is False
```

Both demonstrations were temporary edits; the committed tree contains neither of them.

### 5.7 The tautology guard (019)

```
$ python -m pytest backend/tests/test_no_tautological_asserts.py -q
2 passed, 89 warnings in 2.57s
```

### 5.8 The full backend suite

```
$ python -m pytest backend/tests -q
```

<!--SUITE-->

---

## 6. Statement about scope

* **No production code changed.** `git diff --name-only f1ad188..HEAD` lists exactly the same
  three files as round 1, all under `backend/tests/`.
* `backend/core/storage.py`, `backend/core/config.py`, `backend/main.py`, `backend/api/`,
  `scripts/` and `pytest.ini` are untouched. The new test only READS the production constant
  `DEFAULT_DATA_DIR_NAME` from `backend/core/storage.py`.
* The developer's real `backend/.env` was never copied, read or printed (the copy excludes
  it; the scratch tree has no `.env`; the probe imports the SCRATCH copy of `backend/`, whose
  `env_file` path is `<scratch>/backend/.env`).
* The developer's real `~/.sythion` was never copied anywhere: the probe child's
  `HOME`/`USERPROFILE` point at a fake home inside the scratch tree, and the migration's
  target carries the placeholder in every shape. (This machine has no `~/.sythion` at all:
  `Test-Path "$env:USERPROFILE\.sythion"` -> `False`.)
* The commit is on `orchestra/020-env-isolation`. Nothing was pushed; `master` was not
  touched.

---

## 7. The exact diff of this round (`2fdc842..e558991`)

Only `backend/tests/test_env_isolation.py` changed in this round (the other two files are
unchanged since round 1).

```diffdiff --git a/backend/tests/test_env_isolation.py b/backend/tests/test_env_isolation.py
index ac079b5..7d1c9c6 100644
--- a/backend/tests/test_env_isolation.py
+++ b/backend/tests/test_env_isolation.py
@@ -30,6 +30,11 @@ incidental):
   * the developer's real ``backend/.env`` is never copied, never read and
     never printed - the copy EXCLUDES it and the scratch tree gets a
     GENERATED ``.env`` with an invented sentinel;
+  * the legacy ``~/.sythion`` migration cannot copy a real user's data into
+    the scratch tree: the profile directory the migration REALLY looks at
+    carries a placeholder before anything runs, and a fake legacy profile
+    inside the scratch tree is proven not to be copied - with the app's
+    lifespan running and with it not running;
   * no sentinel value is ever RENDERED: every comparison is boolean and every
     failure message names the property that broke, and this module asserts
     that neither sentinel occurs in any captured output or JUnit XML;
@@ -60,6 +65,11 @@ from pathlib import Path
 
 import pytest
 
+try:
+    from backend.core.storage import DEFAULT_DATA_DIR_NAME
+except ImportError:  # pragma: no cover - the standalone ``backend/`` layout
+    from core.storage import DEFAULT_DATA_DIR_NAME
+
 
 _TESTS_DIR = Path(__file__).resolve().parent
 _REPO_ROOT = _TESTS_DIR.parent.parent
@@ -125,6 +135,29 @@ class _NestedRun(object):
             return ""
 
 
+def _fallback_profile_dir(scratch):
+    """The profile directory the isolation-present shape resolves to.
+
+    ``backend/core/storage.py`` resolves the profile directory from
+    ``SYTHION_DATA_DIR`` first and falls back to
+    ``%LOCALAPPDATA%\\<DEFAULT_DATA_DIR_NAME>`` when no override is set.  The
+    isolation deletes EVERY ``SYTHION_*`` variable for the duration of every
+    test, so the profile directory the nested runs - and the app lifespan -
+    really use is the ``%LOCALAPPDATA%`` one, which inside the scratch tree
+    is ``<scratch>/localappdata/Sythion (Synth)``.
+
+    The name is taken from the production constant, not re-spelled here, so a
+    rename in ``backend/core/storage.py`` moves this fence with it.
+    """
+    return scratch / "localappdata" / DEFAULT_DATA_DIR_NAME
+
+
+def _write_the_placeholder(directory):
+    """Put the isolation placeholder into a directory, creating it if needed."""
+    directory.mkdir(parents=True, exist_ok=True)
+    (directory / ".isolation-placeholder").write_text("", encoding="utf-8")
+
+
 def _build_scratch_tree(scratch):
     """Copy the repository's ``backend`` package into a scratch tree.
 
@@ -155,10 +188,26 @@ def _build_scratch_tree(scratch):
     )
     (scratch / "profile").mkdir(exist_ok=True)
     (scratch / "localappdata").mkdir(exist_ok=True)
-    # The legacy profile migration copies ``~/.sythion`` into the profile
-    # directory when that directory is EMPTY.  A placeholder keeps the
-    # scratch tree free of anything but fakes.
-    (scratch / "profile" / ".isolation-placeholder").write_text("", encoding="utf-8")
+    # The legacy profile migration (``backend/core/storage.py``::
+    # ``migrate_legacy_data_dir``) copies ``~/.sythion`` into the PROFILE
+    # DIRECTORY, but only when that directory is EMPTY.  There are TWO
+    # directories the profile can resolve to, and BOTH have to carry the
+    # placeholder:
+    #
+    #   * ``<scratch>/profile`` - the target while ``SYTHION_DATA_DIR`` is
+    #     set (the child environment sets it, and the isolation removes it
+    #     again for the duration of every test);
+    #   * ``<scratch>/localappdata/Sythion (Synth)`` - the target in the
+    #     isolation-PRESENT shape, where ``SYTHION_DATA_DIR`` is gone and
+    #     ``data_dir()`` falls back to ``%LOCALAPPDATA%``.  This is the target
+    #     the nested runs and the app lifespan really use, so a placeholder in
+    #     the first directory alone fences nothing there.
+    #
+    # The placeholder is written BEFORE anything runs, so the fence is a
+    # property of the tree and not of the order in which the app happens to
+    # start up.
+    _write_the_placeholder(scratch / "profile")
+    _write_the_placeholder(_fallback_profile_dir(scratch))
 
 
 def _assert_no_env_file_was_copied(scratch):
@@ -334,11 +383,36 @@ def _failure_of(run, module, name):
     return None
 
 
+def _redact_sentinels(text):
+    """Replace a sentinel value with a placeholder, should one occur at all.
+
+    A second layer under the leak assertion: the raw child output may be
+    embedded in a failure message, and this makes it impossible for a
+    sentinel VALUE to reach a terminal or a JUnit XML even if the leak
+    assertion is ever reordered or removed.
+    """
+    for sentinel in (FILE_SENTINEL, ENV_SENTINEL):
+        text = text.replace(sentinel, "[REDACTED-SENTINEL]")
+    return text
+
+
 def _assert_no_sentinel_leaked(run, label):
-    """Neither sentinel may occur in any captured output of a run."""
+    """Neither sentinel may occur in any captured output of a run.
+
+    This assertion is deliberately made BEFORE any assertion that renders the
+    run's raw output: the raw output is the one place a sentinel value could
+    escape, so it may only be rendered once this check has passed.
+
+    The comparison is a BOOLEAN on purpose.  ``assert sentinel not in text``
+    would make pytest render both operands - the sentinel value and the text
+    that contains it - so the check that exists to keep the value out of the
+    output would be the very thing that prints it.  Only the property that
+    broke is ever named.
+    """
     for text, where in ((run.output, "stdout/stderr"), (run.report_text, "the JUnit XML")):
         for sentinel in (FILE_SENTINEL, ENV_SENTINEL):
-            assert sentinel not in text, (
+            leaked = sentinel in text
+            assert leaked is False, (
                 "%s: a sentinel value reached the %s" % (label, where)
             )
 
@@ -413,9 +487,15 @@ def test_the_local_env_isolation_is_measured_not_assumed():
 
         # -- run 1: no .env at all, the isolation is present ---------------
         run1 = _run_nested(scratch, 1)
+        # The leak check comes FIRST: the raw child output is rendered in the
+        # failure message below, and the whole point of the sentinel is that
+        # it never appears in any captured stdout/stderr.  Once this check has
+        # passed, the output is provably sentinel-free, so rendering it is
+        # safe (and it is redacted a second time, see ``_redact_sentinels``).
+        _assert_no_sentinel_leaked(run1, "run 1")
         assert run1.returncode == 0, (
             "run 1 (no .env, isolation present) exited %d; the output was:\n%s"
-            % (run1.returncode, run1.output)
+            % (run1.returncode, _redact_sentinels(run1.output))
         )
         _assert_the_signal(run1, SIGNAL_ISOLATED, "run 1")
         outcomes1 = _outcomes(run1.report_path)
@@ -428,15 +508,15 @@ def test_the_local_env_isolation_is_measured_not_assumed():
             assert verdict == "passed", (
                 "run 1: the readiness test %s was %s" % (name, verdict)
             )
-        _assert_no_sentinel_leaked(run1, "run 1")
         _assert_no_network_attempt(run1, "run 1")
 
         # -- run 2: the GENERATED sentinel .env, the isolation is present --
         _write_the_generated_env_file(scratch)
         run2 = _run_nested(scratch, 2)
+        _assert_no_sentinel_leaked(run2, "run 2")
         assert run2.returncode == 0, (
             "run 2 (sentinel .env, isolation present) exited %d; the output was:\n%s"
-            % (run2.returncode, run2.output)
+            % (run2.returncode, _redact_sentinels(run2.output))
         )
         _assert_the_signal(run2, SIGNAL_ISOLATED, "run 2")
         outcomes2 = _outcomes(run2.report_path)
@@ -455,12 +535,12 @@ def test_the_local_env_isolation_is_measured_not_assumed():
             "the readiness file's outcome set changed when the sentinel .env "
             "was present: %s became %s" % (readiness1, readiness2)
         )
-        _assert_no_sentinel_leaked(run2, "run 2")
         _assert_no_network_attempt(run2, "run 2")
 
         # -- run 3: the NEGATIVE CONTROL, the isolation is removed ---------
         _disable_the_isolation_in_the_scratch_copy(scratch)
         run3 = _run_nested(scratch, 3)
+        _assert_no_sentinel_leaked(run3, "run 3")
         assert run3.returncode != 0, (
             "run 3 (sentinel .env, isolation removed) exited 0: the negative "
             "control did not fail - the check is vacuous"
@@ -472,7 +552,6 @@ def test_the_local_env_isolation_is_measured_not_assumed():
         assert _failure_of(run3, READINESS_MODULE, READINESS_ISOLATION_TEST) is not None, (
             "run 3: the repository's own %s did not fail" % READINESS_ISOLATION_TEST
         )
-        _assert_no_sentinel_leaked(run3, "run 3")
         _assert_no_network_attempt(run3, "run 3")
 
         print(
@@ -494,3 +573,335 @@ def test_the_local_env_isolation_is_measured_not_assumed():
                 "the scratch tree %s could not be removed (%s); debris must not "
                 "accumulate silently" % (scratch, removal_error)
             )
+
+
+# ---------------------------------------------------------------------------
+# F1 - the legacy ``~/.sythion`` migration fence
+# ---------------------------------------------------------------------------
+#
+# ``backend/core/storage.py::migrate_legacy_data_dir()`` copies the legacy
+# ``~/.sythion`` profile into the PROFILE DIRECTORY, and only when that
+# directory is EMPTY.  Two things follow, and both are measured below:
+#
+#   1. the directory the migration looks at is NOT always
+#      ``<scratch>/profile``.  In the isolation-PRESENT shape the isolation
+#      has deleted ``SYTHION_DATA_DIR``, so ``data_dir()`` resolves to
+#      ``<scratch>/localappdata/Sythion (Synth)``.  A placeholder in
+#      ``<scratch>/profile`` alone fences nothing there - the fence has to be
+#      in the directory the migration really looks at, and it has to be there
+#      BEFORE anything runs;
+#   2. "nothing was copied" must not be an accident of the app never
+#      starting up.  The proof therefore runs the app's OWN lifespan with a
+#      fake legacy profile present, and it runs the same migration once with
+#      the placeholder REMOVED, to show that the fence - and not luck - is
+#      what stops the copy.
+#
+# The fake legacy profile lives INSIDE the scratch tree, and the child's
+# ``HOME``/``USERPROFILE`` point at it, so the developer's real ``~/.sythion``
+# is never even a candidate source: ``LEGACY_DATA_DIR`` is computed from
+# ``expanduser("~")`` at import time.
+
+# The fake legacy profile carries a file whose CONTENT is the marker.  If the
+# migration ever copies the legacy profile into the scratch tree, the marker
+# shows up there - a name and a content, never a real user's data.
+LEGACY_MARKER_NAME = "legacy-marker.txt"
+LEGACY_MARKER_CONTENT = "LEGACY-MIGRATION-MARKER-020\n"
+
+# The probe that observes the migration in the scratch tree.  It is written
+# into the scratch tree at run time (the file set of this item stays at three
+# files under ``backend/tests/``).  It prints one ``PROOF <name>=<value>`` line
+# per fact and never prints a file's content.
+_MIGRATION_PROBE_SOURCE = '''\
+"""Observe the legacy profile migration in one chosen shape.
+
+The probe runs inside the scratch tree, prints one ``PROOF <name>=<value>``
+line per observed fact, and never prints a file's content.
+"""
+
+import os
+import sys
+
+MODE = os.environ["PROOF_MODE"]
+SCRATCH_ROOT = os.environ["PROOF_SCRATCH_ROOT"]
+sys.path.insert(0, SCRATCH_ROOT)
+
+from backend.core import storage
+
+print("PROOF target=%s" % storage.data_dir())
+print("PROOF legacy=%s" % storage.LEGACY_DATA_DIR)
+# The entries the profile directory holds RIGHT NOW - before the app is
+# imported and before the migration is invoked.  The parent test uses them to
+# prove that the profile directory was PRISTINE (nothing but the
+# placeholder) when the migration looked at it, so that "nothing was copied"
+# is the fence's doing and not the tree's state.
+print("PROOF target_entries=%s" % ",".join(sorted(os.listdir(storage.data_dir()))))
+
+if MODE == "migration":
+    # The migration invoked in the isolation-present shape, with nothing else
+    # running - the shape in which the legacy profile would be copied.
+    print("PROOF migrated=%s" % storage.migrate_legacy_data_dir())
+elif MODE == "current-shape":
+    # The CURRENT shape: the app is imported and its TestClient is built, but
+    # it is never entered, so the app's lifespan never runs.
+    import backend.tests.conftest  # noqa: F401 - installs the httpx shim
+    from fastapi.testclient import TestClient
+    from backend.main import app
+    TestClient(app)
+    print("PROOF lifespan=not-run")
+elif MODE == "lifespan":
+    # The app's OWN startup path, entered.
+    import backend.tests.conftest  # noqa: F401 - installs the httpx shim
+    from fastapi.testclient import TestClient
+    from backend.main import app
+    with TestClient(app):
+        pass
+    print("PROOF lifespan=ran")
+else:
+    raise SystemExit("unknown proof mode: %s" % MODE)
+'''
+
+
+class _MigrationProbeRun(object):
+    """One migration probe subprocess and the facts it reported."""
+
+    def __init__(self, mode, completed):
+        self.mode = mode
+        self.completed = completed
+
+    @property
+    def output(self):
+        return (self.completed.stdout or "") + (self.completed.stderr or "")
+
+    @property
+    def facts(self):
+        """The ``PROOF <name>=<value>`` lines the probe printed."""
+        facts = {}
+        for line in (self.completed.stdout or "").splitlines():
+            if not line.startswith("PROOF "):
+                continue
+            name, _, value = line[len("PROOF "):].partition("=")
+            facts[name] = value
+        return facts
+
+    @property
+    def target(self):
+        """The profile directory the migration looked at."""
+        return self.facts.get("target", "")
+
+    @property
+    def legacy(self):
+        """The legacy directory the migration would have copied."""
+        return self.facts.get("legacy", "")
+
+    @property
+    def target_entries(self):
+        """The names the profile directory held when the probe looked at it."""
+        entries = self.facts.get("target_entries", "")
+        return [name for name in entries.split(",") if name]
+
+    @property
+    def migrated(self):
+        """Whether the migration reported that it copied the legacy profile."""
+        return self.facts.get("migrated", "")
+
+
+def _run_the_migration_probe(scratch, mode, localappdata, fake_home):
+    """Run the migration probe in the isolation-present shape.
+
+    The environment is the CHECK's own child environment (see
+    ``_child_environment``) with the isolation's effect applied: the
+    isolation deletes EVERY ``SYTHION_*`` variable for the duration of every
+    test, so the probe observes none of them - not even ``SYTHION_DATA_DIR``,
+    which is what makes ``data_dir()`` fall back to ``%LOCALAPPDATA%``.
+
+    Two variables are redirected into the scratch tree, and only into the
+    scratch tree: ``%LOCALAPPDATA%`` (where the profile directory resolves)
+    and ``HOME``/``USERPROFILE`` (where the legacy profile the migration
+    reads resolves - it is computed from ``expanduser("~")`` at import time).
+    """
+    script = scratch / "migration-probe.py"
+    script.write_text(_MIGRATION_PROBE_SOURCE, encoding="utf-8")
+
+    env = _child_environment(scratch, scratch / "migration-probe-signal.txt")
+    for key in list(env):
+        if key.upper().startswith("SYTHION_"):
+            del env[key]
+    env["LOCALAPPDATA"] = str(localappdata)
+    env["HOME"] = str(fake_home)
+    env["USERPROFILE"] = str(fake_home)
+    env["PROOF_MODE"] = mode
+    env["PROOF_SCRATCH_ROOT"] = str(scratch)
+
+    try:
+        completed = subprocess.run(
+            [sys.executable, str(script)],
+            cwd=str(scratch / "backend"),
+            env=env,
+            stdout=subprocess.PIPE,
+            stderr=subprocess.PIPE,
+            encoding="utf-8",
+            errors="replace",
+            timeout=RUN_TIMEOUT_SECONDS,
+        )
+    except subprocess.TimeoutExpired:
+        pytest.fail(
+            "the migration probe (%s) exceeded the %d s timeout"
+            % (mode, RUN_TIMEOUT_SECONDS)
+        )
+    return _MigrationProbeRun(mode, completed)
+
+
+def _legacy_marker_copies(scratch, legacy_dir):
+    """Every file under the scratch tree that carries the legacy marker.
+
+    The fake legacy profile itself is excluded: it is the SOURCE, not a copy.
+    """
+    copies = []
+    for path in sorted(scratch.rglob(LEGACY_MARKER_NAME)):
+        if not path.is_file():
+            continue
+        if legacy_dir in path.parents:
+            continue
+        try:
+            content = path.read_text(encoding="utf-8", errors="replace")
+        except OSError:
+            continue
+        if LEGACY_MARKER_CONTENT in content:
+            copies.append(str(path.relative_to(scratch)))
+    return copies
+
+
+def test_the_legacy_migration_cannot_copy_user_data_into_the_scratch_tree():
+    """The legacy ``~/.sythion`` migration must not be able to copy into the scratch tree.
+
+    The fence is a property of the tree, not of the order in which the app
+    happens to start up:
+
+      * in the isolation-present shape the profile directory resolves to
+        ``<scratch>/localappdata/Sythion (Synth)`` - the directory that
+        carries the placeholder - and NOT to ``<scratch>/profile``;
+      * with the app's OWN lifespan running, and with a fake legacy profile
+        inside the scratch tree, nothing is copied;
+      * with the app's lifespan NOT running (the current shape), nothing is
+        copied either;
+      * and the same migration, invoked with the placeholder REMOVED, DOES
+        copy - so the fence is what stops the copy, and the proof above is
+        not vacuous.
+    """
+    scratch = Path(tempfile.mkdtemp(prefix="sythion-020-migration-"))
+    try:
+        _build_scratch_tree(scratch)
+
+        # The fake legacy profile, INSIDE the scratch tree.
+        fake_home = scratch / "fake-home"
+        legacy_dir = fake_home / ".sythion"
+        legacy_dir.mkdir(parents=True)
+        (legacy_dir / LEGACY_MARKER_NAME).write_text(
+            LEGACY_MARKER_CONTENT, encoding="utf-8"
+        )
+
+        # The scratch tree's own ``%LOCALAPPDATA%`` root - the one the app's
+        # profile directory really resolves to - and a second root that
+        # carries NO placeholder, which is the control.
+        fenced_root = scratch / "localappdata"
+        unfenced_root = scratch / "localappdata-unfenced"
+
+        def _run(mode, root):
+            run = _run_the_migration_probe(scratch, mode, root, fake_home)
+            assert run.completed.returncode == 0, (
+                "the migration probe (%s) exited %d; the output was:\n%s"
+                % (mode, run.completed.returncode, _redact_sentinels(run.output))
+            )
+            return run
+
+        # -- the migration invoked in the isolation-present shape -----------
+        # This run comes FIRST, and it has to: the profile directory must be
+        # PRISTINE when the migration looks at it, so that the only thing that
+        # can stop the copy is the placeholder.  The probe reports what the
+        # directory held, and the assertion below checks it - so a tree that
+        # was not pristine fails LOUDLY instead of proving nothing.
+        migration = _run("migration", fenced_root)
+        assert migration.target == str(_fallback_profile_dir(scratch)), (
+            "in the isolation-present shape the profile directory resolved to "
+            "%s and not to the placeholder-bearing %s"
+            % (migration.target, _fallback_profile_dir(scratch))
+        )
+        assert migration.legacy == str(legacy_dir), (
+            "the migration would have read %s and not the fake legacy profile "
+            "inside the scratch tree" % migration.legacy
+        )
+        assert migration.target_entries == [".isolation-placeholder"], (
+            "the profile directory held %s and not only the placeholder, so "
+            "this run would prove nothing: the fence has to be the ONLY thing "
+            "in the directory when the migration looks at it"
+            % migration.target_entries
+        )
+        assert migration.migrated == "False", (
+            "the migration reported that it copied the legacy profile into "
+            "the scratch tree (target %s)" % migration.target
+        )
+        assert _legacy_marker_copies(scratch, legacy_dir) == [], (
+            "the fake legacy profile was copied into the scratch tree: %s"
+            % _legacy_marker_copies(scratch, legacy_dir)
+        )
+
+        # -- the control: the same run WITHOUT the placeholder --------------
+        # The only difference to the run above is the placeholder: a second
+        # ``%LOCALAPPDATA%`` root inside the scratch tree that carries none.
+        # Without the fence the migration has to copy, otherwise the
+        # assertions above would prove nothing.
+        control = _run("migration", unfenced_root)
+        assert control.target_entries == [], (
+            "the control's profile directory was not empty (%s), so the "
+            "control would prove nothing" % control.target_entries
+        )
+        assert control.migrated == "True", (
+            "the control did not copy the fake legacy profile: without the "
+            "placeholder the migration has to copy it, otherwise this proof "
+            "proves nothing (target %s)" % control.target
+        )
+        expected_copy = str(
+            Path("localappdata-unfenced")
+            / DEFAULT_DATA_DIR_NAME
+            / LEGACY_MARKER_NAME
+        )
+        assert _legacy_marker_copies(scratch, legacy_dir) == [expected_copy], (
+            "the control did not copy the fake legacy profile into the "
+            "unfenced profile directory: %s"
+            % _legacy_marker_copies(scratch, legacy_dir)
+        )
+
+        # -- the CURRENT shape: the app's lifespan never runs --------------
+        current = _run("current-shape", fenced_root)
+        assert current.target == str(_fallback_profile_dir(scratch)), (
+            "in the isolation-present shape the profile directory resolved to "
+            "%s and not to the placeholder-bearing %s"
+            % (current.target, _fallback_profile_dir(scratch))
+        )
+        assert current.target_entries == [".isolation-placeholder"], (
+            "the profile directory held %s and not only the placeholder"
+            % current.target_entries
+        )
+        assert _legacy_marker_copies(scratch, legacy_dir) == [expected_copy], (
+            "the fake legacy profile was copied into the scratch tree while "
+            "the app's lifespan was not running (only the control's copy is "
+            "expected): %s" % _legacy_marker_copies(scratch, legacy_dir)
+        )
+
+        # -- the app's OWN lifespan running --------------------------------
+        lifespan_run = _run("lifespan", fenced_root)
+        assert lifespan_run.facts.get("lifespan") == "ran", (
+            "the app's lifespan did not run: %s" % lifespan_run.facts
+        )
+        assert _legacy_marker_copies(scratch, legacy_dir) == [expected_copy], (
+            "the fake legacy profile was copied into the scratch tree while "
+            "the app's lifespan was running (only the control's copy is "
+            "expected): %s" % _legacy_marker_copies(scratch, legacy_dir)
+        )
+    finally:
+        removal_error = _remove_scratch_tree(scratch)
+        if removal_error is not None:
+            pytest.fail(
+                "the scratch tree %s could not be removed (%s); debris must not "
+                "accumulate silently" % (scratch, removal_error)
+            )
```
