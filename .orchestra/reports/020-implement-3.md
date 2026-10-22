# Report: implement - 020 - round 3 (scrub the legacy data-dir override in the probe)

- **Item**: 020 - "The `.env` isolation must be measured, not assumed, and cover the process environment"
- **Role**: implementer (round 3)
- **Branch**: `orchestra/020-r3-probe-scrub` (NEW, created from `e558991`)
- **Worktree**: `C:\Users\orcha\AppData\Local\Temp\opencode\wt-020-r3`
- **Commit**: `e993976bf58237af27e2f6f327c91940c5d2f6e4` (`e993976`)
- **Base of this round**: `e558991` (the tip of `orchestra/020-env-isolation`)
- **Scope**: TEST-ONLY. `git diff e558991..HEAD` touches **exactly one file**:
  `backend/tests/test_env_isolation.py`. **No production file was changed** - not
  `backend/core/storage.py`, not `backend/tests/conftest.py`, not the probe
  template. Verified below.

---

## 1. What the finding actually is, measured first

`backend/core/storage.py` resolves the profile directory from **two** environment
variables (read before writing anything, as the brief demanded):

```python
# backend/core/storage.py:46-48
DATA_DIR_ENV = "SYTHION_DATA_DIR"          # canonical
LEGACY_DATA_DIR_ENV = "Sythion (Synth)_DATA_DIR"   # legacy
DEFAULT_DATA_DIR_NAME = "Sythion (Synth)"
```

and `data_dir()` (storage.py:109-125) honours the legacy one **whenever the
canonical one is absent**:

```python
base = resolve_data_dir(
    os.environ.get(DATA_DIR_ENV, ""),
    os.environ.get(LEGACY_DATA_DIR_ENV, ""),
)
```

The canonical name starts with `SYTHION_`, so the probe's `SYTHION_*` scrub
removed it. **The legacy name does not** - `"Sythion (Synth)_DATA_DIR".upper()`
is `"SYTHION (SYNTH)_DATA_DIR"`, which starts with `"SYTHION "` (a space), not
`"SYTHION_"`. So the legacy override survived the scrub, and the isolation
*deletes* the canonical one, which is exactly the condition under which
`storage.py` follows the legacy one.

The same two names are the only ones in the tree: `src-tauri/src/sidecar.rs:220-221`
declares the identical pair (`DATA_DIR_ENV`, `LEGACY_DATA_DIR_ENV`), and
`backend/tests/test_cli_endpoint_resolution.py:33` already `delenv`s the legacy
name. **There is exactly one legacy spelling and one canonical spelling; both
are used verbatim below, imported from the production module - not re-spelled.**

### Reproduction on the pre-change base (`e558991`), before any edit

The legacy override was set in the shell, pointing at a directory under the
approved temp root (never outside the workspace):

```
$ Set-Item -Path Env:"Sythion (Synth)_DATA_DIR" -Value C:\...\opencode\legacy-override-measure
$ python -m pytest backend/tests/test_env_isolation.py::test_the_legacy_migration_cannot_copy_user_data_into_the_scratch_tree -q
...
>           assert migration.target == str(_fallback_profile_dir(scratch)), (
E           AssertionError: in the isolation-present shape the profile directory resolved to
E           C:\Users\orcha\AppData\Local\Temp\opencode\legacy-override-measure and not to the
E           placeholder-bearing C:\Users\orcha\AppData\Local\Temp\sythion-020-migration-b9jm_45p\localappdata\Sythion (Synth)
backend\tests\test_env_isolation.py:824: AssertionError
1 failed, 89 warnings in 1.23s

=== what landed in the override dir ===
C:\Users\orcha\AppData\Local\Temp\opencode\legacy-override-measure\legacy-marker.txt
```

**Confirmed exactly as the review described**: the probe's `data_dir()` resolved
to the override, the fake legacy marker was **copied there** (a write outside
the scratch tree), and only then did the test fail loudly at line 824.

---

## 2. The change

Only `backend/tests/test_env_isolation.py` changed.

### 2a. The import of the production constants (was `DEFAULT_DATA_DIR_NAME` only)

```python
try:
    from backend.core.storage import (
        DATA_DIR_ENV,
        DEFAULT_DATA_DIR_NAME,
        LEGACY_DATA_DIR_ENV,
    )
except ImportError:  # pragma: no cover - the standalone ``backend/`` layout
    from core.storage import (
        DATA_DIR_ENV,
        DEFAULT_DATA_DIR_NAME,
        LEGACY_DATA_DIR_ENV,
    )
```

### 2b. The scrub, in `_run_the_migration_probe` only

```python
    env = _child_environment(scratch, scratch / "migration-probe-signal.txt")
    for key in list(env):
        if key.upper().startswith("SYTHION_"):
            del env[key]
+   # The profile directory has a SECOND override, and the prefix scrub above
+   # cannot see it: ``backend/core/storage.py`` honours the legacy
+   # ``Sythion (Synth)_DATA_DIR`` whenever the canonical
+   # ``SYTHION_DATA_DIR`` is absent - and the isolation DELETES the
+   # canonical one, so the legacy one is exactly what ``data_dir()`` would
+   # follow.  The legacy name starts with ``Sythion ``, not ``SYTHION_``,
+   # so it survives the scrub above and would resolve the probe's profile
+   # directory OUTSIDE the scratch tree (and the migration would copy the
+   # fake legacy marker there).  Both overrides are therefore removed HERE,
+   # by their production constants, so a rename in
+   # ``backend/core/storage.py`` moves the scrub with it.  The comparison is
+   # case-insensitive because that is how the Windows environment block
+   # behaves - ``os.environ`` itself upper-cases the key.
+   for override in (DATA_DIR_ENV, LEGACY_DATA_DIR_ENV):
+       for key in list(env):
+           if key.upper() == override.upper():
+               del env[key]
    env["LOCALAPPDATA"] = str(localappdata)
```

The comparison is case-insensitive on purpose: on Windows `os.environ`
upper-cases every key, and the Windows environment block is case-insensitive,
so `os.environ.get("Sythion (Synth)_DATA_DIR")` matches whatever spelling the
shell used. On POSIX the exact-case name is what `storage.py` reads, and the
case-insensitive comparison removes it too.

### 2c. The new test

`test_the_migration_probe_stays_in_the_scratch_tree_when_the_legacy_override_is_set`
(appended to the same file):

* sets the legacy override **in the parent environment**, pointing at a
  directory **inside the scratch tree** (`<scratch>/legacy-override-target`) -
  so the test itself never writes outside the scratch tree, and
  `monkeypatch` restores whatever the shell had afterwards;
* puts a fake legacy profile (with the marker file) inside the scratch tree
  and points the child's `HOME`/`USERPROFILE` at it, so the developer's real
  `~/.sythion` is never a source;
* runs the migration probe in the `migration` mode;
* asserts three things:
  1. **the containment** - the probe's resolved target is the expected
     scratch-internal directory `<scratch>/localappdata/Sythion (Synth)`
     (built by the existing `_fallback_profile_dir`), not the override's
     directory;
  2. **the probe's own report** - `migrated == "False"`;
  3. **the write property** - the override's directory is empty, and the fake
     legacy marker was not copied anywhere in the scratch tree.

The scratch tree is still removed in a `finally`, and a removal that fails
still fails the test (unchanged house style).

### 2d. The exact diff

```
$ git diff e558991..HEAD
diff --git a/backend/tests/test_env_isolation.py b/backend/tests/test_env_isolation.py
index 7d1c9c6..7962bd5 100644
--- a/backend/tests/test_env_isolation.py
+++ b/backend/tests/test_env_isolation.py
@@ -44,6 +44,11 @@ incidental):
   * the child environment is built EXPLICITLY (every ``SYTHION_*`` variable
     removed, ``PYTHONPATH`` scrubbed) so the developer's shell contributes
     nothing and the scratch tree cannot be shadowed;
+  * the migration probe's child environment removes the LEGACY profile-dir
+    override ``Sythion (Synth)_DATA_DIR`` as well: it does not start with
+    ``SYTHION_``, and ``backend/core/storage.py`` honours it whenever the
+    canonical ``SYTHION_DATA_DIR`` is absent - so it would follow the probe
+    out of the scratch tree;
   * the scratch tree is removed in a ``finally``, and a removal that still
     fails FAILS the test - debris must not accumulate silently.
 
@@ -66,9 +71,17 @@ from pathlib import Path
 import pytest
 
 try:
-    from backend.core.storage import DEFAULT_DATA_DIR_NAME
+    from backend.core.storage import (
+        DATA_DIR_ENV,
+        DEFAULT_DATA_DIR_NAME,
+        LEGACY_DATA_DIR_ENV,
+    )
 except ImportError:  # pragma: no cover - the standalone ``backend/`` layout
-    from core.storage import DEFAULT_DATA_DIR_NAME
+    from core.storage import (
+        DATA_DIR_ENV,
+        DEFAULT_DATA_DIR_NAME,
+        LEGACY_DATA_DIR_ENV,
+    )
 
 
 _TESTS_DIR = Path(__file__).resolve().parent
@@ -711,8 +724,11 @@ def _run_the_migration_probe(scratch, mode, localappdata, fake_home):
     The environment is the CHECK's own child environment (see
     ``_child_environment``) with the isolation's effect applied: the
     isolation deletes EVERY ``SYTHION_*`` variable for the duration of every
-    test, so the probe observes none of them - not even ``SYTHION_DATA_DIR``,
-    which is what makes ``data_dir()`` fall back to ``%LOCALAPPDATA%``.
+    test, so the probe observes none of them - not even ``SYTHION_DATA_DIR``.
+    The LEGACY profile-dir override is removed as well, because it is the
+    one the ``SYTHION_*`` scrub cannot see, and it is exactly what
+    ``data_dir()`` would follow once the canonical variable is gone.  With
+    both overrides absent, ``data_dir()`` falls back to ``%LOCALAPPDATA%``.
 
     Two variables are redirected into the scratch tree, and only into the
     scratch tree: ``%LOCALAPPDATA%`` (where the profile directory resolves)
@@ -726,6 +742,23 @@ def _run_the_migration_probe(scratch, mode, localappdata, fake_home):
     for key in list(env):
         if key.upper().startswith("SYTHION_"):
             del env[key]
+    # The profile directory has a SECOND override, and the prefix scrub above
+    # cannot see it: ``backend/core/storage.py`` honours the legacy
+    # ``Sythion (Synth)_DATA_DIR`` whenever the canonical
+    # ``SYTHION_DATA_DIR`` is absent - and the isolation DELETES the
+    # canonical one, so the legacy one is exactly what ``data_dir()`` would
+    # follow.  The legacy name starts with ``Sythion ``, not ``SYTHION_``,
+    # so it survives the scrub above and would resolve the probe's profile
+    # directory OUTSIDE the scratch tree (and the migration would copy the
+    # fake legacy marker there).  Both overrides are therefore removed HERE,
+    # by their production constants, so a rename in
+    # ``backend/core/storage.py`` moves the scrub with it.  The comparison is
+    # case-insensitive because that is how the Windows environment block
+    # behaves - ``os.environ`` itself upper-cases the key.
+    for override in (DATA_DIR_ENV, LEGACY_DATA_DIR_ENV):
+        for key in list(env):
+            if key.upper() == override.upper():
+                del env[key]
     env["LOCALAPPDATA"] = str(localappdata)
     env["HOME"] = str(fake_home)
     env["USERPROFILE"] = str(fake_home)
@@ -905,3 +938,108 @@ def test_the_legacy_migration_cannot_copy_user_data_into_the_scratch_tree():
                 "the scratch tree %s could not be removed (%s); debris must not "
                 "accumulate silently" % (scratch, removal_error)
             )
+
+
+# ---------------------------------------------------------------------------
+# The legacy profile-dir override must not reach the migration probe
+# ---------------------------------------------------------------------------
+#
+# ``backend/core/storage.py`` resolves the profile directory from TWO
+# environment variables: the canonical ``SYTHION_DATA_DIR`` and the legacy
+# ``Sythion (Synth)_DATA_DIR`` (the packaging and launch scripts set the
+# legacy one).  The legacy one is honoured whenever the canonical one is
+# absent - and the isolation DELETES the canonical one, so the legacy one is
+# precisely what ``data_dir()`` follows.
+#
+# The legacy name starts with ``Sythion ``, not ``SYTHION_``, so a scrub that
+# only removes ``SYTHION_*`` variables does not see it.  On a machine where
+# the legacy override happens to be set, the migration probe's profile
+# directory would therefore resolve OUTSIDE the scratch tree and the fake
+# legacy marker would be copied there.
+#
+# The test below measures exactly that.  The override is set in the PARENT
+# environment and points at a directory INSIDE the scratch tree, so the test
+# itself never writes outside it, and the test asserts both halves of the
+# containment: the probe's profile directory is the expected scratch-internal
+# one, and the override's directory is left untouched.
+
+
+def test_the_migration_probe_stays_in_the_scratch_tree_when_the_legacy_override_is_set(
+    monkeypatch,
+):
+    """A legacy profile-dir override in the shell must not move the probe out.
+
+    ``backend/core/storage.py`` honours ``Sythion (Synth)_DATA_DIR`` when the
+    canonical ``SYTHION_DATA_DIR`` is absent, and the isolation deletes the
+    canonical one.  The legacy name does not start with ``SYTHION_``, so it
+    is not removed by a ``SYTHION_*`` scrub: the probe would follow it out
+    of the scratch tree and copy the fake legacy marker there.
+
+    The override points at a directory INSIDE the scratch tree, so the test
+    never writes outside it, and the assertions cover the whole property:
+    the probe's resolved profile directory is the expected scratch-internal
+    one, the migration reports that it copied nothing, and the override's
+    directory is left empty.
+    """
+    scratch = Path(tempfile.mkdtemp(prefix="sythion-020-legacy-override-"))
+    try:
+        _build_scratch_tree(scratch)
+
+        # The fake legacy profile, INSIDE the scratch tree: the source the
+        # migration would copy.  The child's ``HOME``/``USERPROFILE`` point at
+        # it, so the developer's real ``~/.sythion`` is never a source.
+        fake_home = scratch / "fake-home"
+        legacy_dir = fake_home / ".sythion"
+        legacy_dir.mkdir(parents=True)
+        (legacy_dir / LEGACY_MARKER_NAME).write_text(
+            LEGACY_MARKER_CONTENT, encoding="utf-8"
+        )
+
+        # The override the developer's shell may carry, pointing INSIDE the
+        # scratch tree.  ``monkeypatch`` restores whatever was there before,
+        # so the real override (if any) is untouched by this test.
+        override_dir = scratch / "legacy-override-target"
+        override_dir.mkdir()
+        monkeypatch.setenv(LEGACY_DATA_DIR_ENV, str(override_dir))
+
+        run = _run_the_migration_probe(
+            scratch, "migration", scratch / "localappdata", fake_home
+        )
+        assert run.completed.returncode == 0, (
+            "the migration probe (migration) exited %d; the output was:\n%s"
+            % (run.completed.returncode, _redact_sentinels(run.output))
+        )
+
+        # (1) the containment: the probe's profile directory is the
+        # scratch-internal one, NOT the directory the override named.
+        assert run.target == str(_fallback_profile_dir(scratch)), (
+            "the probe's profile directory resolved to %s and not to the "
+            "scratch-internal %s: the legacy override %s reached the probe's "
+            "child environment"
+            % (run.target, _fallback_profile_dir(scratch), LEGACY_DATA_DIR_ENV)
+        )
+
+        # (2) the probe's own report: the migration ran and copied nothing.
+        assert run.migrated == "False", (
+            "the migration reported that it copied the legacy profile into %s"
+            % run.target
+        )
+
+        # (3) the write property: the override's directory is untouched, and
+        # the fake legacy profile was not copied ANYWHERE in the scratch tree.
+        written = sorted(path.name for path in override_dir.iterdir())
+        assert written == [], (
+            "the probe wrote %s into the directory the legacy override named"
+            % written
+        )
+        assert _legacy_marker_copies(scratch, legacy_dir) == [], (
+            "the fake legacy profile was copied into the scratch tree: %s"
+            % _legacy_marker_copies(scratch, legacy_dir)
+        )
+    finally:
+        removal_error = _remove_scratch_tree(scratch)
+        if removal_error is not None:
+            pytest.fail(
+                "the scratch tree %s could not be removed (%s); debris must not "
+                "accumulate silently" % (scratch, removal_error)
+            )
```

```
$ git diff --stat e558991..HEAD
 backend/tests/test_env_isolation.py | 146 +++++++++++++++++++++++++++++++++++-
 1 file changed, 142 insertions(+), 4 deletions(-)
```

**Nothing else was touched.** In particular, all of the following are
**byte-identical** to `e558991`: the signal line, the three-run structure, the
timeout, `_child_environment` (the child environment for the OTHER runs), the
negative control's shape, the fence (`_fallback_profile_dir`,
`_write_the_placeholder`, `_build_scratch_tree`), the redaction helper
(`_redact_sentinels`) and the leak-check ordering (`_assert_no_sentinel_leaked`
before any raw output is rendered).

---

## 3. Verification

All commands were run in `C:\Users\orcha\AppData\Local\Temp\opencode\wt-020-r3`
at commit `e993976`. Python 3.14.7, pytest 8.4.2.

### 3a. The new test file

```
$ python -m pytest backend/tests/test_env_isolation.py -q
3 passed, 133 warnings in 23.36s
wall: 24.71s
```

**3 passed, wall 24.71 s** (23.36 s of pytest time). 3 = the round-2 file's two
tests + the new one.

The commit was then amended once, to correct a docstring that attributed the
`%LOCALAPPDATA%` fallback to the removal of the canonical variable alone (the
legacy variable also has to be gone). The final committed state was re-run:

```
$ python -m pytest backend/tests/test_env_isolation.py -q
3 passed, 133 warnings in 16.41s
```

The docstring change is inert; the full suite in 3f was started at commit
`37e28b8` and the commit was amended to `e993976` while it was already
running - the only difference is that one docstring, and the module had
already been imported and collected before the amend, so the run exercised
the final code. The file's own three tests were re-run at the final commit
and pass (above).

### 3b. `test_setup_readiness.py`

```
$ python -m pytest backend/tests/test_setup_readiness.py -q
11 passed, 760 warnings in 2.55s
wall: 4.45s
```

### 3c. Collect-only count

```
$ python -m pytest backend/tests --collect-only -q
1631 tests collected in 71.70s (0:01:11)
wall: 77.33s
```

**1631** - the round-2 count was 1630, so **+1**, exactly the one new test. The
probe template still contributes 0 (its name does not match `test_*.py`).

### 3d. The teeth of the new test

The TEMP copy was `C:\Users\orcha\AppData\Local\Temp\opencode\wt-020-r3-teeth`
(a `robocopy` of the worktree, `.git` excluded). **It has been deleted**
(verified: `Test-Path` -> `False`). It was taken at `37e28b8`; the final
commit `e993976` differs from it by one docstring only (see 3a), which is
inert for this check.

**TEETH 1/2 - TEMP copy WITHOUT the scrub -> the new test FAILS:**

```
$ python -m pytest "backend/tests/test_env_isolation.py::test_the_migration_probe_stays_in_the_scratch_tree_when_the_legacy_override_is_set" -q
...
E           AssertionError: the probe's profile directory resolved to C:\Users\orcha\AppData\Local\Temp\sythion-020-legacy-override-cp345367\legacy-override-target and not to the scratch-internal C:\Users\orcha\AppData\Local\Temp\sythion-020-legacy-override-cp345367\localappdata\Sythion (Synth): the legacy override Sythion (Synth)_DATA_DIR reached the probe's child environment
E           assert 'C:\\Users\o...erride-target' == 'C:\\Users\o...thion (Synth)'
E             - cp345367\localappdata\Sythion (Synth)
E             + cp345367\legacy-override-target

backend\tests\test_env_isolation.py:996: AssertionError
=========================== short test summary info ===========================
FAILED backend/tests/test_env_isolation.py::test_the_migration_probe_stays_in_the_scratch_tree_when_the_legacy_override_is_set
1 failed, 133 warnings in 1.65s
```

**All three assertions were shown to have independent teeth** (assertion 1 was
disabled in the TEMP copy, then assertion 2 as well):

```
### assertion 2 (the probe's own report), with assertion 1 disabled:
>           assert run.migrated == "False", (
E           AssertionError: the migration reported that it copied the legacy profile into
E           C:\Users\orcha\AppData\Local\Temp\sythion-020-legacy-override-xykfhmzg\legacy-override-target
E           assert 'True' == 'False'
backend\tests\test_env_isolation.py:1001: AssertionError
1 failed, 133 warnings in 1.19s

### assertion 3 (the filesystem write check), with assertions 1+2 disabled:
>           assert written == [], (
E           AssertionError: the probe wrote ['legacy-marker.txt'] into the directory the legacy override named
E           assert ['legacy-marker.txt'] == []
backend\tests\test_env_isolation.py:1008: AssertionError
1 failed, 133 warnings in 0.78s
```

So without the scrub the probe really does resolve the profile directory to the
override, really does report that it copied, and really does write the marker
file into the override's directory.

**TEETH 2/2 - TEMP copy WITH the scrub -> the new test PASSES:**

```
$ python -m pytest "backend/tests/test_env_isolation.py::test_the_migration_probe_stays_in_the_scratch_tree_when_the_legacy_override_is_set" -q
1 passed, 133 warnings in 0.59s

$ python -m pytest backend/tests/test_env_isolation.py -q          # the whole file, in the TEMP copy
3 passed, 133 warnings in 14.96s
```

(The same file in the real worktree: `3 passed, 133 warnings in 23.36s`, wall
`24.71s` - see 3a.)

### 3e. The negative control

Throwaway worktree `C:\Users\orcha\AppData\Local\Temp\opencode\wt-020-r3-negctl`
(detached at `37e28b8`, **no `backend/.env`** - the CI shape; existence checked,
never read). `autouse=True` was removed from `_isolate_local_env_settings` in
`backend/tests/conftest.py` (only that fixture - `_guarded_autonomy_default`
and `_forbid_product_credential_writes` were left alone). **The throwaway
worktree has been deleted** (verified: `Test-Path` -> `False`).

The throwaway was created at `37e28b8`; the final commit `e993976` differs from
it by one docstring only (see 3a), which is inert for this check.

```
$ python -m pytest backend/tests/test_env_isolation.py -q
...
E                     assert env_neutralised is True, (
E                         "the exported process-environment sentinel reached the effective settings"
E                     )
E                     AssertionError: the exported process-environment sentinel reached the effective settings
E                     assert False is True
E                      tests\test_env_isolation_probe.py:154: AssertionError
E                     - generated xml file: C:\Users\orcha\AppData\Local\Temp\sythion-020-k30gbo_x\report-1.xml -
E                     =========================== short test summary info ===========================
E                     FAILED tests\test_env_isolation_probe.py::test_the_probe_observes_that_the_local_env_isolation_holds
E                     1 failed, 11 passed, 826 warnings in 3.18s
...
>           assert 1 == 0
E            +  where 1 = <backend.tests.test_env_isolation._NestedRun object at 0x...>.returncode
backend\tests\test_env_isolation.py:509: AssertionError
=========================== short test summary info ===========================
FAILED backend/tests/test_env_isolation.py::test_the_local_env_isolation_is_measured_not_assumed
1 failed, 2 passed, 133 warnings in 13.65s
wall: 14.91s
```

**The negative control still works, for the right reason**: with the autouse
isolation removed, the nested probe fails with exactly
`"the exported process-environment sentinel reached the effective settings"`
and run 1 exits 1, so the measurement test goes RED.

**Honest note about the new round-3 test in that same throwaway:**

```
$ python -m pytest "backend/tests/test_env_isolation.py::test_the_migration_probe_stays_in_the_scratch_tree_when_the_legacy_override_is_set" -q
1 passed, 133 warnings in 0.65s
```

The round-3 test is **not** sensitive to the autouse fixture, and it is not
supposed to be: the migration probe runs as a **plain script** (not a pytest
child), so the autouse fixture never applies to it. Its own anti-vacuity
mechanism is the **scrub removal** (teeth 1/2 above), which is the property
this round is about. The autouse-removal negative control belongs to the
round-2 measurement test, and it still fires as shown.

### 3f. The full backend suite

```
$ python -m pytest backend/tests -q
```

Run in the BACKGROUND and polled to completion.

```
EXITCODE=0 WALL=369.1s
...
-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
1586 passed, 45 skipped, 428810 warnings in 360.23s (0:06:00)
```

**1586 passed, 45 skipped, 0 failed, 0 errors** (exit code 0, wall 369.1 s,
pytest 360.23 s). `1586 + 45 = 1631` = the collected count in 3c, so nothing
was skipped silently beyond the 45 known skips.

**Neither known pre-existing nuisance appeared in this run:** no native crash
(`0xC0000005`) and no `test_phase975_admission_multiproc.py` contention flake.
No re-run was needed, so there is nothing to attribute to the concurrent
activity of other agents. For reference, the round-2 full-suite figures were
`1585 passed, 45 skipped` at 1630 collected (`e558991`); the delta here is
exactly **+1 passed**, the one new test.

---

## 4. The full-suite result

| Check | Result |
| --- | --- |
| `backend/tests/test_env_isolation.py -q` | **3 passed**, 23.36 s (wall 24.71 s); re-run at the final commit: 3 passed, 16.41 s |
| `backend/tests/test_setup_readiness.py -q` | **11 passed**, 2.55 s (wall 4.45 s) |
| `backend/tests --collect-only -q` | **1631 collected** (round 2: 1630, +1) |
| teeth: TEMP copy without the scrub | **FAIL** (3 separate assertions, each proven to have teeth) |
| teeth: TEMP copy with the scrub | **PASS** (1 passed; whole file 3 passed) |
| negative control (`autouse=True` removed) | the measurement test goes **RED** for the right reason: *"the exported process-environment sentinel reached the effective settings"*; run 1 exits 1 |
| full suite `backend/tests -q` | **1586 passed, 45 skipped**, 0 failed, 360.23 s (wall 369.1 s), exit 0 |

The known intermittent native crash (`0xC0000005`) and the known
`test_phase975_admission_multiproc.py` contention flakes did **not** occur in
this run, so no re-run was needed and nothing had to be attributed to
concurrent activity.

---

## 5. A second manifestation of the same root cause - FOUND, NOT FIXED

The brief said explicitly: *"Do NOT touch ... the child environment for the
OTHER runs"*. I obeyed that, but I measured what the legacy override does to
the **three nested runs**, and it is a **second, distinct** failure mode that
the round-3 brief did not mention. Reporting it, because it is real and it is
not fixed by this commit.

On the **pre-change base** `e558991`, with the legacy override set in the shell:

```
$ python -m pytest backend/tests/test_env_isolation.py::test_the_local_env_isolation_is_measured_not_assumed -q
E                 raise ValueError(
E             E     ValueError: conflicting profile directories: SYTHION_DATA_DIR points at
E             'C:\Users\orcha\AppData\Local\Temp\sythion-020-ll48hurc\profile' but
E             Sythion (Synth)_DATA_DIR points at 'C:\...\opencode\legacy-override-measure'.
E             Set only SYTHION_DATA_DIR, or make both point at the same directory.
E             - generated xml file: C:\Users\orcha\AppData\Local\Temp\sythion-020-ll48hurc\report-1.xml -
E             =========================== short test summary info ===========================
E             ERROR tests\test_setup_readiness.py - ValueError: conflicting profile directo...
E             !!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
E             132 warnings, 1 error in 2.21s
...
>           assert 2 == 0
E            +  where 2 = <backend.tests.test_env_isolation._NestedRun object at 0x...>.returncode
backend\tests\test_env_isolation.py:496: AssertionError
1 failed, 89 warnings in 5.19s

=== what landed in the override dir ===
(nothing)
```

Why: `_child_environment` sets `SYTHION_DATA_DIR=<scratch>/profile` **and**
inherits the legacy variable (it is not `SYTHION_`-prefixed), so inside the
nested run `storage.resolve_data_dir()` sees **both**, they name different
directories, and `storage.py` raises `ValueError` by design (storage.py:94-100)
at import time. The nested run dies during collection, run 1 exits 2, and the
measurement test goes RED.

Consequences and non-consequences:

* the nested run fails **loudly** and **before** any write - the `ValueError`
  is raised before `_ensure_private_dir()`, so **nothing** was written into the
  override's directory (measured: the directory stayed empty);
* but `test_the_local_env_isolation_is_measured_not_assumed` is **RED on any
  machine where the legacy override is set** - i.e. the item's headline
  measurement is not green on such a machine.

The fix is the same one-line idea (remove both overrides in
`_child_environment` as well), but the brief forbade touching that function,
so it is **not** in this commit. It is left open for the next round, together
with a test for it.

---

## 6. Statement about production code

**No production file was changed.** `git diff e558991..HEAD` touches exactly
one path, `backend/tests/test_env_isolation.py` (142 insertions, 4 deletions -
the 2 deletions are the two `from ... import DEFAULT_DATA_DIR_NAME` lines that
became the three-name import). `backend/core/storage.py`, `backend/main.py`,
`backend/tests/conftest.py` and `backend/tests/isolation_probe_template.py` are
untouched.

## 7. Safety statements

* The developer's real `backend/.env` was never copied, read or printed. The
  scratch tree is built by `_build_scratch_tree`, which excludes `.env`/
  `.env.*` from the copy; the worktree used here has no `backend/.env` at all
  (existence checked, never read).
* The developer's real `~/.sythion` was never a source and never a copy
  target: the probe child's `HOME`/`USERPROFILE` point at a fake home inside
  the scratch tree, and the legacy override used in the new test points at a
  directory inside the scratch tree.
* No test writes outside the scratch tree. The reproduction runs used
  `C:\Users\orcha\AppData\Local\Temp\opencode\legacy-override-measure` (an
  empty directory under the approved temp root) and it was deleted afterwards.
* The TEMP teeth copy and the throwaway negative-control worktree were both
  deleted (verified with `Test-Path`).
* No destructive commands were used; `git worktree remove --force` was used
  only on the throwaway worktrees this round created.

## 8. What was NOT touched (per the brief)

| Item | Status |
| --- | --- |
| the signal line (`SIGNAL_ISOLATED` / `SIGNAL_UNISOLATED`) | byte-identical |
| the three-run structure | byte-identical |
| `RUN_TIMEOUT_SECONDS` | byte-identical |
| `_child_environment` (the child environment for the OTHER runs) | byte-identical |
| the negative control's shape (`_disable_the_isolation_in_the_scratch_copy`, `ISOLATION_ANCHOR`) | byte-identical |
| the fence (`_fallback_profile_dir`, `_write_the_placeholder`, `_build_scratch_tree`) | byte-identical |
| the redaction helper (`_redact_sentinels`) | byte-identical |
| the leak-check ordering (`_assert_no_sentinel_leaked` before any rendering) | byte-identical |

Only the migration probe's own child environment, the module's import list, the
module docstring (one added bullet) and the new test were changed.
