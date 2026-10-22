# Item 020 - implement, round 4: scrub the legacy override in the child environment too

- Branch: `orchestra/020-r3-probe-scrub` (continued; base `e993976`)
- Commit: **`bc87121`** (`bc87121a45b34f884a6363ddfa000a12aaeef7b5`) - `test(020): scrub the legacy data-dir override from the child environment`
- Worktree: `C:\Users\orcha\AppData\Local\Temp\opencode\wt-020-r3`
- **No production file changed.** `git diff e558991..HEAD --name-only` ->
  `backend/tests/test_env_isolation.py` (one file, 298 insertions / 14 deletions).
- Not pushed. `master` untouched. `orchestra/020-env-isolation` untouched.

## The residual this round closes

Round 3 scrubbed the legacy profile-dir override from the MIGRATION PROBE's
child environment only. The three NESTED RUNS still inherited it, because
`_child_environment` sets the canonical `SYTHION_DATA_DIR` and the legacy name
is not `SYTHION_`-prefixed (it upper-cases to `SYTHION (SYNTH)_DATA_DIR` - a
SPACE, not an underscore - so the `SYTHION_*` sweep never saw it). Inside the
nested run `backend/core/storage.py::resolve_data_dir()` then raised
`ValueError: conflicting profile directories` at IMPORT time and
`test_the_local_env_isolation_is_measured_not_assumed` went RED on a machine
that merely has the legacy override configured.

## What changed (`backend/tests/test_env_isolation.py` only)

1. **ONE helper for the scrub, used by BOTH child environments.**
   `_scrub_the_profile_dir_overrides(env)` removes `DATA_DIR_ENV`
   (`SYTHION_DATA_DIR`) and `LEGACY_DATA_DIR_ENV` (`Sythion (Synth)_DATA_DIR`)
   from a child environment, case-insensitively, by the PRODUCTION constants
   imported from `backend/core/storage.py` (`backend/core/storage.py:46-47`).
2. **The shared base.** The parent-environment sweep (no `SYTHION_*`, no
   `PYTHONPATH`, no `PYTEST_ADDOPTS`) moved into `_inherited_environment()`,
   which BOTH the nested runs' environment and the migration probe's
   environment are built from - so the two cannot drift apart.
3. **`_child_environment()` now scrubs the legacy override** (call site 1) and
   then sets the canonical override ON PURPOSE to the scratch tree's own
   profile directory. Nothing else the child legitimately needs is removed:
   `LOCALAPPDATA`, `SYTHION_DATA_DIR`, `SYTHION_SELECTED_MODEL` and the
   `PROBE_*` plumbing are set exactly as before.
4. **`_run_the_migration_probe()` uses the SAME helper** (call site 2) in
   place of its own inline loop. It no longer derives its environment from
   `_child_environment`, so the two call sites are independent and BOTH are
   load-bearing (proven by the teeth below).
5. **New test** `test_the_nested_runs_stay_green_with_the_legacy_override_is_set`:
   it sets the legacy override in the PARENT environment (pointing at a
   directory INSIDE the test's own scratch tree) and re-runs the measurement
   VERBATIM - the same three nested runs, the same signal line, the same
   negative control - then asserts the override's directory is untouched.
   A setup guard asserts the parent environment really carries the override
   (case-insensitively), so the test cannot pass vacuously.

**Not touched:** the signal line, the three-run structure, the timeout
(`RUN_TIMEOUT_SECONDS = 120`), the negative control's shape, the fence
(`test_the_legacy_migration_cannot_copy_user_data_into_the_scratch_tree`),
the redaction helper (`_redact_sentinels`), the leak-check ordering, and
round 3's test
(`test_the_migration_probe_stays_in_the_scratch_tree_when_the_legacy_override_is_set`,
all three of its independently-teethed assertions intact).

### The diff (full)

```diff
--- a/backend/tests/test_env_isolation.py
+++ b/backend/tests/test_env_isolation.py
@@ -44,11 +44,16 @@ incidental):
   * the child environment is built EXPLICITLY (every ``SYTHION_*`` variable
     removed, ``PYTHONPATH`` scrubbed) so the developer's shell contributes
     nothing and the scratch tree cannot be shadowed;
-  * the migration probe's child environment removes the LEGACY profile-dir
-    override ``Sythion (Synth)_DATA_DIR`` as well: it does not start with
-    ``SYTHION_``, and ``backend/core/storage.py`` honours it whenever the
-    canonical ``SYTHION_DATA_DIR`` is absent - so it would follow the probe
-    out of the scratch tree;
+  * EVERY child environment - the three nested runs' and the migration
+    probe's - removes BOTH profile-dir overrides, by their production
+    constants: the canonical ``SYTHION_DATA_DIR`` and the LEGACY
+    ``Sythion (Synth)_DATA_DIR``.  The legacy name does not start with
+    ``SYTHION_`` - it upper-cases to ``SYTHION (SYNTH)_DATA_DIR``, a SPACE
+    and not an underscore - so the ``SYTHION_*`` sweep cannot see it, and
+    ``backend/core/storage.py`` honours it whenever the canonical variable
+    is absent: a child would follow it out of the scratch tree, or - with
+    the canonical override set as well - raise ``ValueError: conflicting
+    profile directories`` at import time;
   * the scratch tree is removed in a ``finally``, and a removal that still
     fails FAILS the test - debris must not accumulate silently.
@@ -267,13 +272,15 @@ def _disable_the_isolation_in_the_scratch_copy(scratch):
         handle.write(patched)
 
 
-def _child_environment(scratch, signal_path):
-    """Build the child environment EXPLICITLY.
+def _inherited_environment():
+    """The parent environment, EXPLICITLY.
 
     Nothing is inherited wholesale: every ``SYTHION_*`` variable is removed
-    (case-insensitively), ``PYTHONPATH`` is scrubbed so the scratch tree
-    cannot be shadowed, and ``PYTEST_ADDOPTS`` is removed so the caller's
-    pytest options cannot change the child's command line.
+    (case-insensitively) and ``PYTHONPATH``/``PYTEST_ADDOPTS`` are scrubbed,
+    so the developer's shell contributes nothing and the scratch tree cannot
+    be shadowed.  This is the base EVERY child environment is built from -
+    the three nested runs' and the migration probe's - so the two cannot
+    drift apart.
     """
     env = {}
     for key, value in os.environ.items():
@@ -283,6 +290,57 @@ def _child_environment(scratch, signal_path):
         if upper in ("PYTHONPATH", "PYTEST_ADDOPTS"):
             continue
         env[key] = value
+    return env
+
+
+def _scrub_the_profile_dir_overrides(env):
+    """Remove BOTH profile-dir overrides from ``env``, in place.
+
+    ``backend/core/storage.py`` resolves the profile directory from TWO
+    environment variables: the canonical ``SYTHION_DATA_DIR``
+    (``storage.DATA_DIR_ENV``) and the legacy ``Sythion (Synth)_DATA_DIR``
+    (``storage.LEGACY_DATA_DIR_ENV``).  The legacy one is honoured whenever
+    the canonical one is absent - and the isolation DELETES the canonical
+    one for the duration of every test - so the legacy one is exactly what
+    ``data_dir()`` would follow.
+
+    The legacy name starts with ``Sythion ``, not ``SYTHION_``, so the
+    ``SYTHION_*`` sweep cannot see it.  On a machine whose shell carries it,
+    a child would resolve a profile directory OUTSIDE the scratch tree - or,
+    when the canonical override is set as well, raise ``ValueError:
+    conflicting profile directories`` at import time.
+
+    ONE helper serves EVERY child environment - the three nested runs' and
+    the migration probe's - so the two cannot drift apart again.  Both names
+    are taken from the PRODUCTION constants, so a rename in
+    ``backend/core/storage.py`` moves the scrub with it.  The comparison is
+    case-insensitive because that is how the Windows environment block
+    behaves: ``os.environ`` itself upper-cases the key.
+    """
+    for override in (DATA_DIR_ENV, LEGACY_DATA_DIR_ENV):
+        for key in list(env):
+            if key.upper() == override.upper():
+                del env[key]
+    return env
+
+
+def _child_environment(scratch, signal_path):
+    """Build the child environment EXPLICITLY.
+
+    Nothing is inherited wholesale (see ``_inherited_environment``), and
+    NEITHER profile-dir override survives the scrub: the canonical one is
+    ``SYTHION_*``-prefixed, the legacy one is removed by its production name
+    (see ``_scrub_the_profile_dir_overrides``).  Without that, a machine
+    whose shell carries the legacy override would hand the child TWO
+    conflicting profile directories, and ``backend/core/storage.py`` would
+    raise ``ValueError: conflicting profile directories`` at import time.
+
+    The canonical override is then set ON PURPOSE, to the scratch tree's own
+    profile directory, so the child's data and its database never touch a
+    real profile.
+    """
+    env = _inherited_environment()
+    _scrub_the_profile_dir_overrides(env)
     # The profile directory and %LOCALAPPDATA% resolve inside the scratch
     # tree, so the child's data and its database never touch a real profile.
     env["LOCALAPPDATA"] = str(scratch / "localappdata")
@@ -722,11 +780,14 @@ def _run_the_migration_probe(scratch, mode, localappdata, fake_home):
     """Run the migration probe in the isolation-present shape.
 
     The environment is the CHECK's own child environment (see
-    ``_child_environment``) with the isolation's effect applied: the
+    ``_inherited_environment``) with the isolation's effect applied: the
     isolation deletes EVERY ``SYTHION_*`` variable for the duration of every
     test, so the probe observes none of them - not even ``SYTHION_DATA_DIR``.
-    The LEGACY profile-dir override is removed as well, because it is the
-    one the ``SYTHION_*`` scrub cannot see, and it is exactly what
+    The base environment removes them all, so the probe's own view is the
+    isolation-present one.
+    The LEGACY profile-dir override is removed as well, by the SAME helper
+    the nested runs use (see ``_scrub_the_profile_dir_overrides``): it is
+    the one the ``SYTHION_*`` scrub cannot see, and it is exactly what
     ``data_dir()`` would follow once the canonical variable is gone.  With
     both overrides absent, ``data_dir()`` falls back to ``%LOCALAPPDATA%``.
@@ -738,27 +799,23 @@ def _run_the_migration_probe(scratch, mode, localappdata, fake_home):
     script = scratch / "migration-probe.py"
     script.write_text(_MIGRATION_PROBE_SOURCE, encoding="utf-8")
 
-    env = _child_environment(scratch, scratch / "migration-probe-signal.txt")
-    for key in list(env):
-        if key.upper().startswith("SYTHION_"):
-            del env[key]
-    # The profile directory has a SECOND override, and the prefix scrub above
-    # cannot see it: ``backend/core/storage.py`` honours the legacy
+    env = _inherited_environment()
+    # The profile directory has a SECOND override, and the prefix scrub
+    # above cannot see it: ``backend/core/storage.py`` honours the legacy
     ``Sythion (Synth)_DATA_DIR`` whenever the canonical
     ``SYTHION_DATA_DIR`` is absent - and the isolation DELETES the
     canonical one, so the legacy one is exactly what ``data_dir()`` would
     follow.  The legacy name starts with ``Sythion ``, not ``SYTHION_``,
-    so it survives the scrub above and would resolve the probe's profile
+    so it survives the sweep above and would resolve the probe's profile
     directory OUTSIDE the scratch tree (and the migration would copy the
     fake legacy marker there).  Both overrides are therefore removed HERE,
-    by their production constants, so a rename in
-    ``backend/core/storage.py`` moves the scrub with it.  The comparison is
-    case-insensitive because that is how the Windows environment block
-    behaves - ``os.environ`` itself upper-cases the key.
-    for override in (DATA_DIR_ENV, LEGACY_DATA_DIR_ENV):
-        for key in list(env):
-            if key.upper() == override.upper():
-                del env[key]
+    # by the SHARED helper - the very same one the nested runs' child
+    # environment uses - so the two places cannot drift apart again.  The
+    # names come from the production constants, so a rename in
+    # ``backend/core/storage.py`` moves the scrub with it, and the
+    # comparison is case-insensitive because that is how the Windows
+    # environment block behaves - ``os.environ`` itself upper-cases the key.
+    _scrub_the_profile_dir_overrides(env)
     env["LOCALAPPDATA"] = str(localappdata)
     env["HOME"] = str(fake_home)
@@ -1043,3 +1100,92 @@ def test_the_migration_probe_stays_in_the_scratch_tree_when_the_legacy_override_
                 "the scratch tree %s could not be removed (%s); debris must not "
                 "accumulate silently" % (scratch, removal_error)
             )
+
+
+# ---------------------------------------------------------------------------
+# The legacy profile-dir override must not reach the NESTED RUNS either
+# ---------------------------------------------------------------------------
+#
+# The same root cause, a second manifestation.  ``_child_environment`` sets
+# the canonical ``SYTHION_DATA_DIR`` (to the scratch tree's own profile
+# directory) and INHERITED the legacy override - it is not ``SYTHION_``-
+# prefixed, so the ``SYTHION_*`` sweep could not see it.  Inside the nested
+# run ``backend/core/storage.py::resolve_data_dir()`` then raised
+# ``ValueError: conflicting profile directories`` at IMPORT time (both
+# overrides present, naming different directories), so the measurement went
+# RED on a machine that merely has the legacy override configured.
+#
+# Nothing was written outside the scratch tree in that shape - the failure
+# happened before anything was created - but a RED measurement on a
+# differently configured machine is a false alarm, and the ambient process
+# environment is exactly what this item is about.
+#
+# The test below re-runs the measurement VERBATIM - the same three nested
+# runs, the same assertions, the same negative control - with the legacy
+# override present in the PARENT environment.  The override names a
+# directory INSIDE this test's own scratch tree, so the test never writes
+# outside it, and the test asserts that directory is left untouched.
+
+
+def test_the_nested_runs_stay_green_with_the_legacy_override_is_set(monkeypatch):
+    """The three nested runs must stay GREEN with the legacy override configured.
+
+    ``backend/core/storage.py`` honours ``Sythion (Synth)_DATA_DIR`` whenever
+    the canonical ``SYTHION_DATA_DIR`` is absent, and the child environment
+    sets the canonical one.  The legacy name does not start with
+    ``SYTHION_``, so it is not removed by a ``SYTHION_*`` scrub: the nested
+    run would carry TWO conflicting profile directories and die at import
+    time with ``ValueError: conflicting profile directories``.
+
+    The measurement is re-run here exactly as it is - the same three nested
+    runs, the same signal line, the same negative control - with the legacy
+    override set in the PARENT environment.  It has to stay GREEN, and the
+    directory the override names has to stay empty.
+    """
+    scratch = Path(tempfile.mkdtemp(prefix="sythion-020-nested-legacy-"))
+    try:
+        # The directory the legacy override names.  It is INSIDE this test's
+        # own scratch tree, so nothing can be written outside it.
+        override_dir = scratch / "legacy-override-target"
+        override_dir.mkdir()
+        # ``monkeypatch`` restores whatever was there before, so the real
+        # override (if any) is untouched by this test.
+        monkeypatch.setenv(LEGACY_DATA_DIR_ENV, str(override_dir))
+
+        # The setup has to be real, or the test would prove nothing: the
+        # PARENT environment really carries the legacy override.  The
+        # comparison is case-insensitive because ``os.environ`` upper-cases
+        # the key on Windows - the legacy name is seen there as
+        # ``SYTHION (SYNTH)_DATA_DIR``.
+        present = any(
+            key.upper() == LEGACY_DATA_DIR_ENV.upper() for key in os.environ
+        )
+        assert present is True, (
+            "the legacy override %s is not in the parent environment, so this "
+            "test would prove nothing" % LEGACY_DATA_DIR_ENV
+        )
+
+        # The measurement itself, verbatim.
+        try:
+            test_the_local_env_isolation_is_measured_not_assumed()
+        except AssertionError as exc:
+            pytest.fail(
+                "the isolation measurement went RED with the legacy override "
+                "%s set in the parent environment: %s"
+                % (LEGACY_DATA_DIR_ENV, exc)
+            )
+
+        # The write property: the directory the legacy override named is
+        # untouched, so no child resolved a profile directory there.
+        written = sorted(path.name for path in override_dir.iterdir())
+        assert written == [], (
+            "the nested runs wrote %s into the directory the legacy override "
+            "named" % written
+        )
+    finally:
+        removal_error = _remove_scratch_tree(scratch)
+        if removal_error is not None:
+            pytest.fail(
+                "the scratch tree %s could not be removed (%s); debris must not "
+                "accumulate silently" % (scratch, removal_error)
+            )
```

## Verification (every command, actual output)

All commands were run in the worktree
`C:\Users\orcha\AppData\Local\Temp\opencode\wt-020-r3` at `bc87121`.
Python 3.14.7, pytest 8.4.2.

### 1. The changed test file

```
> python -m pytest backend/tests/test_env_isolation.py -q
....                                                                     [100%]
4 passed, 177 warnings in 33.13s
```

(The 4 tests are the three from round 3 plus the new one. Wall time 33.13 s.)

### 2. The readiness file

```
> python -m pytest backend/tests/test_setup_readiness.py -q
11 passed, 760 warnings in 3.17s
```

### 3. The collected count

```
> python -m pytest backend/tests --collect-only -q
1632 tests collected in 101.95s (0:01:41)
```

1632 = 1631 (round 3) + 1 (the new test). Expected count confirmed.

### 4. THE TEETH (TEMP copy, now deleted)

The TEMP copy was `C:\Users\orcha\AppData\Local\Temp\opencode\020-r4-teeth`
(a copy of `backend/` + `pytest.ini` from the worktree; no `.env` in it - the
copy excludes `.env`, `__pycache__`, `*.db`). **That path no longer exists**:
it was deleted after the teeth run (`teeth_copy_deleted=True`). The captured
logs of that run are at
`C:\Users\orcha\AppData\Local\Temp\opencode\020-r4-teeth-logs\` (which does
exist).

The copy with the fix in place is green first, so the teeth are the ONLY
difference:

```
> cd C:\Users\orcha\AppData\Local\Temp\opencode\020-r4-teeth
> python -m pytest backend/tests/test_env_isolation.py -q
4 passed, 177 warnings in 35.29s
```

**TEETH A - the `_child_environment` scrub removed** (the single line
`    _scrub_the_profile_dir_overrides(env)` inside `_child_environment` was
deleted) -> the NEW test goes RED:

```
> python -m pytest "backend/tests/test_env_isolation.py::test_the_nested_runs_stay_green_with_the_legacy_override_is_set" -q
...
E                 ValueError: conflicting profile directories: SYTHION_DATA_DIR points at
E                 'C:\Users\orcha\AppData\Local\Temp\sythion-020-287za1i9\profile' but Sythion (Synth)_DATA_DIR points at
E                 'C:\Users\orcha\AppData\Local\Temp\sythion-020-nested-legacy-5xw3t_uy\legacy-override-target'. Set only
E                 SYTHION_DATA_DIR, or make both point at the same directory.
E
E                =========================== short test summary info ============================
E               ERROR tests\test_setup_readiness.py - ValueError: conflicting profile directo...
E
E               assert 2 == 0
E                +  where 2 = <backend.tests.test_env_isolation._NestedRun object at 0x000001AF7687AE40>.returncode

backend\tests\test_env_isolation.py:1172: Failed
...
E                Failed: the isolation measurement went RED with the legacy override Sythion (Synth)_DATA_DIR set in the parent
environment: run 1 (no .env, isolation present) exited 2; the output was:
...
=========================== short test summary info ============================
FAILED backend/tests/test_env_isolation.py::test_the_nested_runs_stay_green_with_the_legacy_override_is_set
1 failed, 177 warnings in 6.32s
```

That is EXACTLY the residual the round-3 implementer measured: the nested run
dies at import with `ValueError: conflicting profile directories` because the
child carries the canonical override AND inherits the legacy one.

In the same variant round 3's own test still passes - the probe's call site is
independent and load-bearing:

```
> python -m pytest "backend/tests/test_env_isolation.py::test_the_migration_probe_stays_in_the_scratch_tree_when_the_legacy_override_is_set" -q
1 passed, 177 warnings in 1.30s
```

**TEETH B - the migration probe's own scrub removed** (the single line
`    _scrub_the_profile_dir_overrides(env)` inside `_run_the_migration_probe`
was deleted) -> round 3's probe test goes RED:

```
> python -m pytest "backend/tests/test_env_isolation.py::test_the_migration_probe_stays_in_the_scratch_tree_when_the_legacy_override_is_set" -q
...
E               AssertionError: the probe's profile directory resolved to C:\Users\orcha\AppData\Local\Temp\sythion-020-legacy-override-cbu9buc4\legacy-override-target and not to the scratch-internal C:\Users\orcha\AppData\Local\Temp\sythion-020-legacy-override-cbu9buc4\localappdata\Sythion (Synth): the legacy override Sythion (Synth)_DATA_DIR reached the probe's child environment
E               assert 'C:\\Users\o...erride-target' == 'C:\\Users\\o...thion (Synth)'
E
E                Skipping 62 identical leading characters in diff, use -v to show
E                - cbu9buc4\localappdata\Sythion (Synth)
E                + cbu9buc4\legacy-override-target

backend\tests\test_env_isolation.py:1072: AssertionError
...
=========================== short test summary info ============================
FAILED backend/tests/test_env_isolation.py::test_the_migration_probe_stays_in_the_scratch_tree_when_the_legacy_override_is_set
1 failed, 177 warnings in 1.61s
```

And in the same variant the NEW test still passes - the `_child_environment`
call site is independent and load-bearing:

```
> python -m pytest "backend/tests/test_env_isolation.py::test_the_nested_runs_stay_green_with_the_legacy_override_is_set" -q
1 passed, 177 warnings in 19.04s
```

So both call sites of the ONE shared helper carry their own teeth:
`_child_environment` -> the new test; `_run_the_migration_probe` -> round 3's
test. Neither can be dropped silently, and neither can drift from the other.

### 5. THE NEGATIVE CONTROL (throwaway worktree, now deleted)

A throwaway worktree `C:\Users\orcha\AppData\Local\Temp\opencode\wt-020-r4-negctl`
was created at `bc87121` (`git worktree add --detach ... bc87121`), and
`autouse=True` was removed from `_isolate_local_env_settings` in
`backend/tests/conftest.py` (line 576: `@pytest.fixture(autouse=True)` ->
`@pytest.fixture`). The measurement test then goes RED for the RIGHT reason
(the scratch copy inherits the modified conftest, so the child no longer
neutralises the exported process-environment sentinel):

```
> python -m pytest "backend/tests/test_env_isolation.py::test_the_local_env_isolation_is_measured_not_assumed" -q
...
E                     assert file_neutralised is True, (
E                         "the generated .env sentinel reached the effective settings"
E                     )
E             >       assert env_neutralised is True, (
E                         "the exported process-environment sentinel reached the effective settings"
E                     )
E             E       AssertionError: the exported process-environment sentinel reached the effective settings
E             E       assert False is True
E
E             tests\test_env_isolation_probe.py:154: AssertionError
E             - generated xml file: C:\Users\orcha\AppData\Local\Temp\sythion-020-0yxdu71d\report-1.xml -
E             =========================== short test summary info ============================
E             FAILED tests\test_env_isolation_probe.py::test_the_probe_observes_that_the_local_env_isolation_holds
E             1 failed, 11 passed, 826 warnings in 4.29s
E
E           assert 1 == 0
E            +  where 1 = <backend.tests.test_env_isolation._NestedRun object at 0x0000013F7D2A30E0>.returncode

backend\tests\test_env_isolation.py:567: AssertionError
...
=========================== short test summary info ===========================
FAILED backend/tests/test_env_isolation.py::test_the_local_env_isolation_is_measured_not_assumed
1 failed, 177 warnings in 8.14s
```

The throwaway worktree was removed afterwards (`git worktree remove --force`),
so `C:\Users\orcha\AppData\Local\Temp\opencode\wt-020-r4-negctl` no longer
exists.

### 6. THE FULL SUITE (background, polled to completion)

Run 1 died with the KNOWN intermittent native crash (0xC0000005), at ~61 %,
inside `backend/tests/test_phase95_hardening.py::validate_in_thread` ->
SQLAlchemy -> `backend/agents/delegation.py` - a module this change does not
touch:

```
> python -m pytest backend/tests -q            (background, pid 34268)
........................................................................ [ 57%]
........................................................................ [ 61%]
...............................................................
Windows fatal exception: access violation

Current thread 0x0000f2ec [Thread-259 (validate_in_thread)] (most recent call first):
  File "...\site-packages\sqlalchemy\orm\loading.py", line 231 in chunks
  File "...\site-packages\sqlalchemy\engine\result.py", line 1883 in _fetchone_impl
  File "...\site-packages\sqlalchemy\engine\result.py", line 1280 in _fetchone_impl
  File "...\site-packages\sqlalchemy\engine\result.py", line 1397 in first
  File "...\site-packages\sqlalchemy\orm\query.py", line 2823 in first
  File "...\wt-020-r3\backend\agents\delegation.py", line 1358 in get_delegation
  File "...\wt-020-r3\backend\agents\delegation.py", line 1374 in _require
  File "...\wt-020-r3\backend\agents\delegation.py", line 394 in validate
  File "...\wt-020-r3\backend\tests\test_phase95_hardening.py", line 68 in validate_in_thread
  File "...\threading.py", line 1024 in run
  ...
```

The suite was re-run once, as instructed:

```
> python -m pytest backend/tests -q            (background, pid 17364)
...
-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
1587 passed, 45 skipped, 428938 warnings in 346.66s (0:05:46)
```

Run 2: **1587 passed, 45 skipped, 0 failed**, 346.66 s (5:46), exit 0, empty
stderr. 1587 = round 3's 1586 + the new test. The
`test_phase975_admission_multiproc.py` contention flakes did NOT occur in
either run.

The native crash in run 1 and any such contention flake are pre-existing and
are NOT attributed to this change: the change touches
`backend/tests/test_env_isolation.py` only, and the crash happened in
`backend/tests/test_phase95_hardening.py` (SQLAlchemy / delegation), a code
path this change does not reach. Both runs are reported above as they
happened.

## Safety constraints honoured

- **No production file changed.** `git diff e558991..HEAD --name-only` lists
  only `backend/tests/test_env_isolation.py`.
- The developer's real `backend/.env` (which exists in the main checkout at
  `C:\Sythion\sythion\backend\.env`) was never read, copied or printed. The
  worktree has no `.env` at all; the scratch trees exclude `.env` (the
  pre-existing `_build_scratch_tree` rule) and get a GENERATED `.env` with an
  invented sentinel; the TEMP copy used for the teeth also excluded `.env`
  (`env_present=False`).
- No test writes outside a scratch tree: the new test's legacy override
  points at a directory INSIDE its own scratch tree, and the test asserts that
  directory stays empty.
- Nothing was pushed; `master` was not touched; `orchestra/020-env-isolation`
  was not modified.
- The TEMP copy (`...\020-r4-teeth`) and the throwaway worktree
  (`...\wt-020-r4-negctl`) were both deleted.

## Notes for the reviewer

- The scrub helper removes ONLY the two profile-dir overrides. The canonical
  one is removed from the INHERITED environment and then set on purpose to
  the scratch tree's own profile directory; the legacy one is removed and
  never set. Nothing else the child legitimately needs is touched
  (`LOCALAPPDATA`, `SYTHION_SELECTED_MODEL`, the `PROBE_*` plumbing and the
  probe's `PROOF_*` variables are unchanged).
- The migration probe's environment is now built from the shared base
  `_inherited_environment()` rather than from `_child_environment()`. It is
  the SAME environment content as before (no `SYTHION_*`, no `PYTHONPATH`, no
  `PYTEST_ADDOPTS`, plus the probe's own `LOCALAPPDATA`/`HOME`/`USERPROFILE`/
  `PROOF_*`), and this is what makes the probe's own scrub call load-bearing
  instead of a redundant no-op.
- The new test calls `test_the_local_env_isolation_is_measured_not_assumed()`
  directly: the measurement is re-run VERBATIM (same three nested runs, same
  signal line, same negative control) under a parent environment that carries
  the legacy override. That is what the round-4 brief asks for - "the main
  isolation test still passes on a machine that merely has the legacy
  override configured" - and it is what makes the new test fail on the
  pre-fix code (teeth A).
