# Report: implement - 006 - attempt 1

Item 006 - "The readiness test must not borrow the developer's gitignored `backend/.env`".
Role: implementer-5. Branch: `orchestra/006-readiness-test-isolation`.

**Status: the change is implemented and verified. Every acceptance criterion is met
EXCEPT criterion 6's second half, which the plan explicitly assigns to the
orchestrator (an orchestration state commit to `.orchestra/follow-ups.md`, not part of
the branch). Section "Acceptance criteria" says exactly which is which, and the
"Follow-up the orchestrator must land" section gives the text.**

---

## What I did

### Working tree

The brief forbids touching the orchestrator's tree. I created a dedicated worktree
and worked only inside it:

```
git -C C:\Sythion\sythion worktree add C:\Users\orcha\AppData\Local\Temp\opencode\wt-006 -b orchestra/006-readiness-test-isolation master
```

```
Updating files: 100% (4895/4895), done.
HEAD is now at a26228c chore(orchestra): planner critiques for 006/007, items planned, dispatch implementer-007
```

Three scratch worktrees were used for the proof and removed again afterwards:

```
git -C C:\Sythion\sythion worktree add --detach C:\...\opencode\wt-006-before a26228c   # the BASE code, no .env
git -C C:\Sythion\sythion worktree add --detach C:\...\opencode\wt-006-after  orchestra/006-readiness-test-isolation
git -C C:\Sythion\sythion worktree add --detach C:\...\opencode\wt-006-teeth  orchestra/006-readiness-test-isolation  # negative controls
git -C C:\Sythion\sythion worktree remove --force <each of the three>
```

Final state:

```
$ git worktree list
C:/Sythion/sythion                                      5851739 [master]
C:/Users/orcha/AppData/Local/Temp/opencode/wt-002       cc03a7b [orchestra/002-test-selection-truth]
C:/Users/orcha/AppData/Local/Temp/opencode/wt-006       d89fb95 [orchestra/006-readiness-test-isolation]
C:/Users/orcha/AppData/Local/Temp/opencode/wt-007       e1ff9b1 [orchestra/007-no-real-vault-writes]
C:/Users/orcha/AppData/Local/Temp/opencode/wt007-master 7cf679e (detached HEAD)
```

Nothing was checked out, committed or written in `C:\Sythion\sythion` except this
report. The main tree's `git status` shows only the orchestrator's own edits
(`.orchestra/critiques.md`, `.orchestra/follow-ups.md`, two untracked briefs) - none of
them mine.

### Files changed (exactly the two the brief allows)

```
$ git --no-pager diff --name-only master...orchestra/006-readiness-test-isolation
backend/tests/conftest.py
backend/tests/test_setup_readiness.py
```

### Commits

| SHA | Message | Files |
|---|---|---|
| `8746b8a` | `test(readiness): pin the credential the readiness test asserts on` | `backend/tests/test_setup_readiness.py` |
| `d89fb95` | `test(suite): neutralise the ambient backend/.env for the whole suite` | `backend/tests/conftest.py` |

```
$ git --no-pager log --oneline master..orchestra/006-readiness-test-isolation
d89fb95 test(suite): neutralise the ambient backend/.env for the whole suite
8746b8a test(readiness): pin the credential the readiness test asserts on

$ git --no-pager show --stat --oneline 8746b8a
 backend/tests/test_setup_readiness.py | 77 ++++++++++++++++++++++++++++++++++++++++++++++++++-
 1 file changed, 76 insertions(+), 1 deletion(-)

$ git --no-pager show --stat --oneline d89fb95
 backend/tests/conftest.py | 66 ++++++++++++++++++++++++++++++++++++++++++++++++-
 1 file changed, 65 insertions(+), 1 deletion(-)
```

### A rebase the branch needed (please read, Committer)

The branch was cut from `a26228c`. **While I was working, the orchestrator REWROTE
`master`'s history** (commit `d9f0291` "committer-004 second dispatch - report:
accidental-amend repair record"), so `a26228c` is no longer an ancestor of `master`:

```
$ git merge-base --is-ancestor a26228c master
exit=1 (1 = NO, master was rewritten)

$ git merge-base master orchestra/006-readiness-test-isolation
7412649233dc8345845edeff2861d05793fc5b86      <- before the rebase
```

Left as it was, the plan's own criterion-4 command
(`git diff --name-only master...orchestra/006-readiness-test-isolation`) listed six
`.orchestra/` files, because my branch still carried the orphaned orchestration commit
`a26228c` (an `.orchestra`-only commit; its content is byte-identical in the current
`master`). I therefore rebased my two commits onto the then-current `master`
(`7cf679e`), dropping the orphaned `.orchestra` commit:

```
$ git rebase --onto master a26228c        # in wt-006
Successfully rebased and updated refs/heads/orchestra/006-readiness-test-isolation.
```

Nothing was lost: `git diff a26228c master -- .orchestra/briefs/007-implement-1.md
.orchestra/plans/007.md` is empty (identical), and `git diff a26228c master -- backend/`
is empty (the `backend/` trees are byte-identical), so the rebase changed **no**
repository content - only the ancestry. The pre-rebase SHAs were `d729308` / `b6f4ce0`;
they no longer exist as branch tips. `master` has since moved again (`5851739`,
`.orchestra`-only), and the criterion-4 check still holds against it:

```
$ git merge-base master orchestra/006-readiness-test-isolation
7cf679e4a7017f01a42ba5d1e5d983568a0b315e
$ git --no-pager diff --name-only master...orchestra/006-readiness-test-isolation
backend/tests/conftest.py
backend/tests/test_setup_readiness.py
```

### What the change does

**`backend/tests/test_setup_readiness.py`**

* `_pin_test_credential(monkeypatch, value="test-key")` - the exact mirror of the
  file's own `_clear_local_env_key` helper, in the other direction. It pins the
  repository's existing placeholder on the already-imported settings singleton:
  `monkeypatch.setattr(core_config.settings, "ollama_api_key", value)`.
* `test_readiness_states` now calls it at the top of the function, so the credential is
  a **constant** of the test, and it states the tenth check explicitly in **both**
  blocks:
  * healthy: `assert body["checks"]["ollama_api_key_ok"] is True` next to
    `assert body["full_ready"] is True`;
  * incomplete: `assert body["checks"]["ollama_api_key_ok"] is True` **and**
    `assert body["checks"]["selected_model_ok"] is False` **and**
    `assert body["full_ready"] is False` - a configured credential alone is not
    readiness.
* `test_the_readiness_credential_check_follows_the_effective_configuration` - exercises
  the check in BOTH directions (cleared -> `False`, pinned -> `True`) and asserts the
  endpoint never echoes the credential (`assert "test-key" not in pinned.text`).
* `test_the_suite_does_not_inherit_the_local_env_file` - asserts the three effective
  settings equal a `Settings(_env_file=None)` instance's.

**`backend/tests/conftest.py`** (appended at the END of the file)

* `_LOCAL_ENV_FIELDS = ("ollama_api_key", "selected_model", "file_roots")` - the single
  place that names the `.env`-sourced fields, with the contract for extending it.
* `_CLEAN_SETTINGS = _clean_settings_snapshot()` - computed ONCE at import from
  `Settings(_env_file=None)`, i.e. **with the env FILE disabled**, so the developer's
  `backend/.env` is never read to obtain the values. Lists are copied so a test that
  mutates one in place cannot corrupt the snapshot.
* `_isolate_local_env_settings` - one autouse fixture that assigns only those clean
  values onto the already-imported settings singleton, undone by `monkeypatch` at
  teardown. It carries the documented `provider_e2e` carve-out.

I did **not** touch `pytest_configure`, the marker declarations, the marker
classification rules, the selection-contract guard, or the existing
`_guarded_autonomy_default` fixture. The region I touched in `conftest.py` is the 65
lines after the last existing fixture (`acceptance_control_token`).

### Verification helpers (outside the repository)

Three helpers live in `C:\Users\orcha\AppData\Local\Temp\opencode` and are NOT part of
the branch:

* `run_env.py` - runs pytest in a child process whose environment has no `SYTHION_*`
  variable, and prints the scrub result so the scrub is demonstrable.
* `probe_clean_settings.py` - prints **booleans only** about the effective settings.
* `compare_junit.py`, `ast_ambient_check.py` - compare JUnit outcome sets; AST-check the
  two changed files for ambient credential sources.

---

## Evidence

### E0 - the defect, reproduced (base tree, `.env` absent)

```
$ cd C:\...\opencode\wt-006-before        # base code, no backend/.env
$ python C:\...\opencode\run_env.py backend/tests/test_setup_readiness.py -q
[run_env] SYTHION_* variables in the child environment: []
FAILED backend/tests/test_setup_readiness.py::test_readiness_states - assert ...
1 failed, 7 passed, 597 warnings in 3.03s
Exited with code 1
```

The failure, in isolation:

```
>           assert body["full_ready"] is True
E           assert False is True

backend\tests\test_setup_readiness.py:160: AssertionError
```

### E1 - the proof matrix (all four cells, same command, `SYTHION_*` scrubbed)

| # | Tree | Code | `backend/.env` | Result |
|---|---|---|---|---|
| V1 | `wt-006-before` | base | absent | **1 failed, 7 passed** (exit 1) - the defect |
| V2 | `wt-006-before` | base | dummy | **8 passed** (exit 0) - the base borrows the ambient value |
| V3 | `wt-006-after` | branch | absent | **10 passed** (exit 0) - the fix |
| V4 | `wt-006-after` | branch | dummy | **10 passed** (exit 0) - identical outcome to V3 |

```
### V3: branch code, NO backend/.env ###
[run_env] SYTHION_* variables in the child environment: []
..........                                                               [100%]
10 passed in 4.68s
exit=0

### V2: BASE code, dummy .env present ###
[run_env] SYTHION_* variables in the child environment: []
........                                                                [100%]
8 passed in 2.69s
exit=0

### V4: BRANCH code, dummy .env present ###
[run_env] SYTHION_* variables in the child environment: []
..........                                                               [100%]
10 passed in 2.85s
exit=0
```

(The file now holds 10 tests, not 8: the plan's "8 passed" counts predate the two new
tests this item adds. The pass/fail SET is what matters and it is compared as such
below.)

Re-run at the **rebased** tip `d89fb95`:

```
### V3 (re-run at rebased tip): BRANCH, NO .env ###
[run_env] SYTHION_* variables in the child environment: []
..........                                                               [100%]
10 passed in 1.50s
exit=0
```

### E2 - the mechanism probe (booleans only, never a value)

In a worktree (no `backend/.env`):

```
$ cd C:\...\opencode\wt-006; python C:\...\opencode\probe_clean_settings.py
env_file configured on Settings: True
backend/.env present in this tree: False
bool(Settings(_env_file=None).ollama_api_key): False
bool(Settings(_env_file=None).selected_model): False
bool(Settings(_env_file=None).file_roots): False
bool(settings.ollama_api_key) [singleton, reads the .env file]: False
```

In the **developer's** tree (the real `.env` present) - the contrast that proves the
snapshot never reads the file:

```
$ cd C:\Sythion\sythion; python C:\...\opencode\probe_clean_settings.py
env_file configured on Settings: True
backend/.env present in this tree: True
bool(Settings(_env_file=None).ollama_api_key): False
bool(Settings(_env_file=None).selected_model): False
bool(Settings(_env_file=None).file_roots): False
bool(settings.ollama_api_key) [singleton, reads the .env file]: True
```

`bool(Settings(_env_file=None).ollama_api_key)` is **False** in both states - the
conftest's clean values are computed with the env file disabled, so the developer's
`.env` is never the source of them.

### E3 - criterion 3: suite-wide isolation (the four files, with and without a `.env`)

```
$ cd C:\...\opencode\wt-006-after
$ python run_env.py backend/tests/test_setup_readiness.py backend/tests/test_file_roots_grants.py \
      backend/tests/test_settings_store.py backend/tests/test_settings_api.py -q --junitxml=out-dummyenv.xml
36 passed in 7.66s
$ python run_env.py <same four files> -q --junitxml=out-noenv.xml
36 passed in 6.13s

$ python compare_junit.py out-noenv.xml out-dummyenv.xml
A: ...\out-noenv.xml -> 36 triples
B: ...\out-dummyenv.xml -> 36 triples
--- in A but not in B (A outcome | B outcome) ---
--- in B but not in A ---
failures in A: []
failures in B: []
OUTCOME SETS EQUAL: True
compare exit=0
```

Re-run at the rebased tip `d89fb95`: `36 passed` / `36 passed`, `OUTCOME SETS EQUAL: True`.

### E4 - criterion 5: the full suite

| Run | Tree | Code | `backend/.env` | Result |
|---|---|---|---|---|
| full-before | `wt-006-before` | base | absent | `1 failed, 1565 passed, 45 skipped` (exit 1) - only `test_readiness_states` |
| full-after attempt 1 | `wt-006-after` | branch | absent | `1 failed, 1567 passed, 45 skipped` - a GUI flake, see below |
| full-after attempt 2 | `wt-006-after` | branch | absent | **native crash**, exit `-1073741819` (`0xC0000005`) |
| full-after attempt 3 | `wt-006-after` | branch | absent | `1568 passed, 45 skipped` (exit 0) |
| full-after-dummy | `wt-006-after` | branch | dummy | `1568 passed, 45 skipped` (exit 0) |
| full-devtree | `C:\Sythion\sythion` | base | **real** | `1566 passed, 45 skipped` (exit 0) |

```
### FULL SUITE: BASE tree, NO .env ###
1 failed, 1565 passed, 45 skipped in 249.63s (0:04:09)
exit=1

### FULL SUITE ATTEMPT 3: BRANCH tree, NO .env ###
1568 passed, 45 skipped in 332.75s (0:05:32)
exit=0

### FULL SUITE: BRANCH tree, DUMMY .env ###
1568 passed, 45 skipped in 625.45s (0:10:25)
exit=0

### FULL SUITE: DEVELOPER'S TREE, base code, REAL .env ###
1566 passed, 45 skipped in 327.96s (0:05:27)
exit=0
```

Outcome-set comparison (the plan's required DELTA):

```
############ (2) BASE no-.env  vs  BRANCH no-.env ############
A: ...\full-before.xml -> 1611 triples
B: ...\full-after3.xml -> 1613 triples
--- in A but not in B (A outcome | B outcome) ---
  backend.tests.test_setup_readiness::test_readiness_states  [failed -> passed]
--- in B but not in A ---
  backend.tests.test_setup_readiness::test_readiness_states  [passed -> failed]
  backend.tests.test_setup_readiness::test_the_readiness_credential_check_follows_the_effective_configuration  [passed -> ABSENT]
  backend.tests.test_setup_readiness::test_the_suite_does_not_inherit_the_local_env_file  [passed -> ABSENT]
failures in A: [('backend.tests.test_setup_readiness', 'test_readiness_states', 'failed')]
failures in B: []
```

The difference is EXACTLY the item's own change: the readiness test moves failed ->
passed, and the two new tests appear (absent -> passed). Nothing else moved.

```
############ (1) BRANCH no-.env  vs  BRANCH dummy-.env ############
A: ...\full-after3.xml -> 1613 triples
B: ...\full-after-dummyenv.xml -> 1613 triples
--- in A but not in B ---
--- in B but not in A ---
failures in A: []
failures in B: []
OUTCOME SETS EQUAL: True
```

The whole suite's outcome set is identical with and without a `.env` - the
environment-level invariant.

```
############ (3) BRANCH no-.env  vs  DEVELOPER'S TREE (base code, REAL .env) ############
A: ...\full-after3.xml -> 1613 triples
B: ...\full-devtree.xml -> 1611 triples
--- in A but not in B ---
  ...::test_the_readiness_credential_check_follows_the_effective_configuration  [passed -> ABSENT]
  ...::test_the_suite_does_not_inherit_the_local_env_file  [passed -> ABSENT]
--- in B but not in A ---
failures in A: []
failures in B: []

############ (4) BASE no-.env  vs  DEVELOPER'S TREE (base code, REAL .env) ############
--- in A but not in B ---
  backend.tests.test_setup_readiness::test_readiness_states  [failed -> passed]
failures in A: [('backend.tests.test_setup_readiness', 'test_readiness_states', 'failed')]
failures in B: []
```

(3) says: the branch in a `.env`-free tree behaves exactly like the developer's tree
with the real `.env` - the only difference is that the branch has two extra tests.
(4) says: the base code's ONLY difference between the two states is the readiness
test - the asymmetry that made this a release-confidence defect.

**On the absolute counts.** The plan quotes a baseline of `1564 passed, 45 skipped`
(1609 collected). That baseline was measured before item 004 was merged; item 004 added
`backend/tests/test_no_tautological_asserts.py` with exactly two tests
(`test_no_assert_statement_disjoins_with_the_literal_true`,
`test_detector_flags_a_tautological_disjunction_and_ignores_legitimate_ones`), so the
current base is `1566 passed, 45 skipped` (1611 collected) and my branch is
`1568 passed, 45 skipped` (1613 collected). The +2 is exactly this item's two new
tests; no test was deleted, skipped or marked.

**The two flaky/crashing runs (known baseline condition, recorded as required).**

* Attempt 1 of the branch's full suite: `1 failed` -
  `backend/tests/acceptance/test_gui_fixture.py::test_set_value_writes_fixture_edit`,
  a Windows UIA assertion (`assert '1hello-fixture' == 'hello-fixture'`) - the fixture
  window's edit control had a stale character in it. It is a GUI/UIA timing artefact,
  not a settings artefact: the file passes in isolation in the same tree
  (`4 passed in 2.47s`), and it passed in the base tree's runs and in the branch's
  attempt 3.
* Attempt 2 of the branch's full suite died with a native access violation
  (`exit=-1073741819` = `0xC0000005`) after 188 s - the known R-007 crash class
  ("2 native crashes in 6 observed full-suite runs"). Attempt 3 was clean.

### E5 - the new tests have teeth (negative controls, scratch worktree only)

NC1 - the conftest neutralisation disabled, dummy `.env` present:

```
backend\tests\test_setup_readiness.py:328: AssertionError
E       AssertionError: assert 'dummy-not-a-real-credential' == ''
FAILED backend/tests/test_setup_readiness.py::test_the_suite_does_not_inherit_the_local_env_file
1 failed, 9 passed in 5.78s
exit=1
```

(The value printed is my own dummy placeholder, never the developer's credential.)

NC2 - the credential pin removed from `test_readiness_states`, `.env` absent:

```
>       assert pinned.json()["checks"]["ollama_api_key_ok"] is True
E       assert False is True
FAILED backend/tests/test_setup_readiness.py::test_readiness_states - assert ...
FAILED backend/tests/test_setup_readiness.py::test_the_readiness_credential_check_follows_the_effective_configuration
2 failed, 8 passed in 2.49s
exit=1
```

So the explicit `ollama_api_key_ok is True` assertion is what detects the missing pin:
the test cannot go green for the wrong reason, and a global pin in the conftest is
detected by the isolation test.

### E6 - the `provider_e2e` carve-out works (probe, scratch worktree only)

A temporary probe module (never committed, deleted afterwards) with one
`provider_e2e`-marked test and one plain test, run in a tree carrying the dummy `.env`:

```
backend/tests/test_zz_probe_carveout.py::test_probe_provider_e2e_keeps_the_ambient_value PASSED [ 50%]
backend/tests/test_zz_probe_carveout.py::test_probe_a_plain_test_gets_the_clean_value PASSED [100%]
============================== 2 passed in 0.09s ==============================
```

The marked item keeps the ambient value; every other item gets the clean value.

### E7 - criterion 2: the pin's shape, and no other credential source

```
$ git --no-pager diff master...orchestra/006-readiness-test-isolation -- backend/tests/test_setup_readiness.py \
    | Select-String -Pattern "monkeypatch.setattr|_pin_test_credential|_clear_local_env_key|ollama_api_key"
+    _pin_test_credential(monkeypatch)
+        assert body["checks"]["ollama_api_key_ok"] is True
+        assert body["checks"]["ollama_api_key_ok"] is True
+def _pin_test_credential(monkeypatch, value="test-key"):
+    monkeypatch.setattr(core_config.settings, "ollama_api_key", value)
```

AST check of the branch's two files (executable code only, not docstrings):

```
$ python ast_ambient_check.py backend/tests/test_setup_readiness.py backend/tests/conftest.py
backend/tests/test_setup_readiness.py:
   no os.environ / os.getenv / file-read call in EXECUTABLE code
backend/tests/conftest.py:
   no os.environ / os.getenv / file-read call in EXECUTABLE code
```

Every string literal on the added lines (there is exactly one credential-ish literal in
the whole change - `"test-key"`):

```
+_LOCAL_ENV_FIELDS = ("ollama_api_key", "selected_model", "file_roots")
+def _pin_test_credential(monkeypatch, value="test-key"):
+    monkeypatch.setattr(core_config.settings, "ollama_api_key", value)
```

And the repository's own payload checker classifies every literal involved as a
placeholder, not a credential:

```
$ python -c "import sys; sys.path.insert(0,'scripts'); import verify_release_payload as v; ..."
test-key                      -> False
dummy-not-a-real-credential   -> False
dummy-model-id                -> False
```

(`scripts/verify_release_payload.py:148` - `_TEST_LITERAL_PREFIXES = ("test-", ...)`;
`:138,140` - `"dummy"`, `"not-a-real"` in `_PLACEHOLDER_MARKERS`.)

### E8 - criterion 4: the product is untouched

```
$ git --no-pager diff --name-only master...orchestra/006-readiness-test-isolation
backend/tests/conftest.py
backend/tests/test_setup_readiness.py

$ git --no-pager diff --name-only master...orchestra/006-readiness-test-isolation -- . ':(exclude)backend/tests/'
(empty = none)

$ git --no-pager diff --name-only master...orchestra/006-readiness-test-isolation -- backend/core/config.py backend/api/routes.py backend/core/secrets.py
(empty = unchanged)
```

`backend/core/config.py` is not modified: the `env_file` setting (`:173-176`) and the
import-time singleton (`:179`) are as they were, so the product still reads
`backend/.env` when it exists. `backend/api/routes.py` and `backend/core/secrets.py`
are untouched - item 009's sanctioned key-resolution point stays deferred and
out of scope.

### E9 - how the suite was invoked

```
$ python run_env.py backend/tests/test_setup_readiness.py -p no:warnings
============================= test session starts =============================
platform win32 -- Python 3.14.7, pytest-8.4.2, pluggy-1.6.0
rootdir: C:\Users\orcha\AppData\Local\Temp\opencode\wt-006-after
plugins: anyio-4.15.1, asyncio-0.23.8
asyncio: mode=Mode.STRICT
collected 10 items
```

There is **no `configfile:` line** - `pytest.ini` does not exist yet, because item 002
has NOT merged (the reviewer sent it back to the implementer; `master` has no
`pytest.ini`). This is the plan's Risk R5: the selection-contract guard is absent and
the suite runs with `inifile: None`. My change is independent of item 002's content.

---

## Acceptance criteria

| # | Criterion | Result | Evidence |
|---|---|---|---|
| 1 | `test_readiness_states` passes with NO `backend/.env` and no `SYTHION_*` in the environment | **MET** | E0 (base: `1 failed, 7 passed`), E1 (branch: `10 passed`, exit 0), E2 (probe: `bool(Settings(_env_file=None).ollama_api_key)` is `False` in both trees), E4 (`1568 passed, 45 skipped`, exit 0 in the `.env`-free worktree). The "with a `.env` present" half is V2/V4 (dummy `.env`) and the developer's-tree run: `1566 passed, 45 skipped`, exit 0 - see the deviation note below. |
| 2 | The test pins the credential it asserts on instead of borrowing ambient state | **MET** | E7: `monkeypatch.setattr(core_config.settings, "ollama_api_key", value)` in the shape of the file's own `_clear_local_env_key`; the AST check finds no `os.environ`/`getenv`/file read in either changed file; the only literal is `"test-key"`. |
| 3 | Suite-wide isolation: the autouse isolation neutralises `ollama_api_key`, `selected_model`, `file_roots` for every test (except the `provider_e2e` carve-out) | **MET, with the plan's documented carve-out** | E3: the four files produce the SAME 36-triple outcome set with and without a `.env`, 0 failures in both (re-run at the rebased tip: equal). E4: the FULL suite's outcome set is identical with and without a `.env` (1613 triples each, 0 failures). E6: the carve-out keeps the ambient value for `provider_e2e` items and only for them. |
| 4 | The product's behaviour is unchanged: only files under `backend/tests/`; `config.py`, `routes.py`, `secrets.py` untouched | **MET** | E8: the three `git diff --name-only` checks. |
| 5 | The full suite still passes; no test deleted, skipped or marked | **MET** | E4: `1568 passed, 45 skipped`, exit 0 in the `.env`-free worktree; the delta vs the base is exactly the readiness test flipping to passed plus the two new tests; the base's current count is `1566 passed, 45 skipped` (the documented `1564` predates item 004's two tests); nothing was skipped, marked or deleted. Two known-baseline anomalies recorded in E4 (one GUI flake, one native crash). |
| 6 | The defect is recorded where the run can see it | **PARTLY - the report half is MET, the `follow-ups.md` half is the orchestrator's** | This report states the before/after (base `.env`-absent `1 failed, 7 passed`; branch `.env`-absent `10 passed`; developer's tree `1566 passed`; the dummy-`.env` cells V2/V4) with the worktree paths and the base SHA `a26228c` / rebased base `7cf679e`. **The `.orchestra/follow-ups.md:16` update is an ORCHESTRATION STATE COMMIT per the plan (Open question 3) and my brief forbids me from writing that file** ("`.orchestra/follow-ups.md` (the orchestrator writes that)"). The entry still reads "EXPECT THE FIRST RUN TO BE RED ... item 006's subject". Exact text to land is below. |

### Deviation from the plan's V5/V6 (documented, not hidden)

The plan's V5 wanted the BRANCH code run inside the developer's tree (which has the real
`.env`). **I did not do that, and could not do it without breaking two rules**: the
brief forbids copying/reading the developer's `backend/.env`, and the brief forbids
checking out a branch or committing in `C:\Sythion\sythion` (the orchestrator's tree -
and item 002 was merging into it during my run). Copying the branch's two test files
into the developer's tree would have raced with that merge.

Substitutes, all of them stronger or equivalent:

* "branch code with a `.env` present" -> the DUMMY `.env` in `wt-006-after` (V4 and
  `full-after-dummyenv`): the same outcome set as without a `.env`.
* "developer's tree" -> the developer's tree was run as-is with the base code and its
  REAL `.env` (`full-devtree`): `1566 passed, 45 skipped`, exit 0 - green, and the only
  difference from the `.env`-free base run is the readiness test, which is exactly the
  asymmetry the plan wanted recorded.
* The real `.env` was never copied, moved, renamed, deleted or printed. The only
  `.env` any step wrote is the dummy one, inside a scratch worktree.

---

## The .env-free proof

**How it was run.** The proof was run in three git worktrees under
`C:\Users\orcha\AppData\Local\Temp\opencode` - a clean checkout of the tracked files
and nothing else. `backend/.env` is gitignored (`.gitignore:16`) and untracked
(`git ls-files -- backend/.env` is empty), so a worktree physically cannot contain the
developer's file:

```
$ foreach ($wt in @("wt-006-before","wt-006-after")) { "$wt  backend\.env present: $(Test-Path "$wt\backend\.env")" }
wt-006-before  backend\.env present: False
wt-006-after   backend\.env present: False
```

Every pytest invocation went through `run_env.py`, which strips every `SYTHION_*`
variable from the CHILD environment and prints the scrub, so the scrub is demonstrable
rather than assumed:

```
[run_env] SYTHION_* variables in the child environment: []
```

`SYTHION_RUN_PROVIDER_E2E` was **never** set to `1`, so the two provider-e2e modules
stayed skipped in every run.

**Base tree next to my branch, in the same `.env`-absent environment:**

| | base (`wt-006-before`, `a26228c`) | branch (`wt-006-after`, `d89fb95`) |
|---|---|---|
| `backend/tests/test_setup_readiness.py` | `1 failed, 7 passed` (exit 1) | `10 passed` (exit 0) |
| the four criterion-3 files | (see the full-suite delta) | 36 passed, same set with/without `.env` |
| `backend/tests` (full suite) | `1 failed, 1565 passed, 45 skipped` | `1568 passed, 45 skipped` (exit 0) |

**The credential was never read, copied or printed.** The conftest's clean values are
computed with `Settings(_env_file=None)` - the env FILE disabled - so the developer's
`.env` is not the source of any value the suite uses (E2). The only credential-ish
literal introduced is `"test-key"`, the repository's own placeholder, which the
repository's own payload checker classifies as a non-credential (E7). The dummy `.env`
used for the "with a `.env` present" half contains only placeholders
(`dummy-not-a-real-credential`, `dummy-model-id`, `dummy-root`) and was written only
inside scratch worktrees.

---

## Findings

1. **The plan's expected counts are off by the two tests this item adds** ("8 passed"
   for the readiness file, "1564 passed" for the suite). The readiness file holds 10
   tests after this change and the suite holds 1613 collected. The plan's `1564`
   baseline also predates item 004's merge, which added exactly 2 tests
   (`backend/tests/test_no_tautological_asserts.py`), so the current base is
   `1566 passed`. Nothing regressed; the numbers simply moved.
2. **`master`'s history was rewritten during my run** (the accidental-amend repair).
   Any branch cut from `a26228c` is now off `master`, and criterion 4's literal command
   picks up the orphaned `.orchestra` commits. I rebased onto the then-current `master`
   (`7cf679e`); the rebase changed no repository content (the `backend/` trees are
   byte-identical). **The Committer should still rebase onto whatever `master` is when
   they merge** - `master` has already moved twice since (`5851739`).
3. **The `provider_e2e` carve-out is a documented deviation from criterion 3's "for
   every test".** The two provider-e2e modules gate themselves at IMPORT time, i.e.
   before any fixture runs; without the carve-out the gate would still pass (it sees
   the ambient value at import) but the live call would then run with no credential -
   a confusing 401 for a developer whose key lives in `backend/.env`. The carve-out is
   confined to the two explicitly opt-in modules and the criterion-3 proof set contains
   none of them.
4. **`test_the_suite_does_not_inherit_the_local_env_file` is a discriminator in a tree
   that HAS a `.env` and a tautology in a clean checkout.** I am stating this rather
   than overselling it. Its teeth are demonstrated in E5 (NC1): with the neutralisation
   removed and a `.env` present, it fails.
5. **The GUI/UIA acceptance test is flaky on this machine**
   (`backend/tests/acceptance/test_gui_fixture.py::test_set_value_writes_fixture_edit`,
   a stale character in the fixture window's edit control). It failed in one of four
   full-suite runs and passes in isolation. Unrelated to this change, but the Reviewer
   and the Verifier should know it can turn a full-suite run red on its own.
6. **The known native crash struck this run too**: attempt 2 died with `0xC0000005`
   after 188 s. Attempt 3 was clean. Same class as R-007.
7. **`backend/api/routes.py:2261` and `:2007` still read `settings.ollama_api_key`
   directly** instead of going through `backend.core.secrets.resolve_ollama_api_key`.
   That is item 009's subject and was deliberately NOT absorbed here (both files are
   off-limits for this item). Worth noting for the follow-up list.
8. **Side effect of running the full suite in the developer's tree**: item 007's known
   defect (the acceptance suite writes and deletes the product's credential target in
   the real Windows Credential Manager) executes in every full-suite run. It is item
   007's subject, not this item's failure; I did not touch it.
9. **`pytest.ini` does not exist yet** (item 002 is back with the implementer), so the
   runs above have `inifile: None` and no selection-contract guard. My change does not
   depend on item 002.

### Follow-up the orchestrator must land (criterion 6, second half)

`.orchestra/follow-ups.md`, the item-001 entry (currently the first bullet) - the plan
says this is an orchestration state commit, NOT part of the branch, so I did not write
it. Suggested replacement for the "EXPECT THE FIRST RUN TO BE RED" sentence:

> The first-run red this entry used to predict is FIXED: `backend/.env` is
> gitignored, so a clean CI checkout has no credential, and the readiness test used
> to borrow the developer's one. Branch `orchestra/006-readiness-test-isolation`
> (merge SHA `<to be filled by the Committer>`) makes the suite independent of the
> developer's working tree: the readiness test pins its own placeholder credential and
> `backend/tests/conftest.py` neutralises the ambient `.env`-sourced values. The
> `backend-tests` job should now be green on its first real run - unless something else
> fails.

---

## Not checked

* **The GitHub Actions workflow was not run or dispatched.** The `backend-tests` job on
  a real runner is unverified; the `.env`-free behaviour was proven locally in a clean
  worktree, which is the same file set a runner gets.
* **The live provider path was not exercised.** `SYTHION_RUN_PROVIDER_E2E=1` was never
  set (the plan forbids it: it would make the two provider-e2e modules attempt live
  provider calls). The carve-out was proven with a local probe instead (E6); that the
  opt-in live run still works with a real credential is therefore **unverified**.
* **The packaged/installed product was not tested.** Only the repository suite was run.
* **The developer's tree was NOT run with the BRANCH code** (see the deviation above).
  The developer's tree was run with the base code and its real `.env` only.
* **`backend/api/routes.py`, `backend/core/config.py` and `backend/core/secrets.py` were
  not modified and not tested beyond the suite above.**
* **No acceptance-suite-specific credential enumeration was performed** - item 007's
  subject; the full-suite run in the developer's tree did execute its known defect.
* **The native crash was not root-caused** (R-007), and the GUI/UIA flake was not
  root-caused.
* **Item 002's `pytest.ini` and selection-contract guard were not exercised**, because
  item 002 has not merged. The interaction between its marker classification rules and
  my new tests (which carry **no** marker and are **not** under
  `backend/tests/acceptance/`) is therefore unverified.
* **Nothing was pushed, no deployment or migration was run**, and
  `make clean`, `scripts/stage_python_runtime.py` and `npx tauri build` were not run.

### Files touched outside the two named ones

* `C:\Sythion\sythion\.orchestra\reports\006-implement-1.md` - this report (the
  deliverable, in the MAIN tree's `.orchestra`, as instructed).
* `C:\Users\orcha\AppData\Local\Temp\opencode\` - the verification helpers
  `run_env.py`, `probe_clean_settings.py`, `compare_junit.py`, `ast_ambient_check.py`,
  the JUnit XMLs and the run logs. All OUTSIDE the repository; none committed.
* `C:\Users\orcha\AppData\Local\Temp\opencode\wt-006-teeth\backend\tests\test_zz_probe_carveout.py`
  - a TEMPORARY probe file inside a scratch worktree, used only for E6, deleted
  afterwards and never committed. It never reached the branch.
* The dummy `backend/.env` files inside the scratch worktrees (`wt-006-before`,
  `wt-006-after`, `wt-006-teeth`), all deleted with the worktrees. **The developer's
  real `backend/.env` was never written to, copied, moved, renamed or deleted.**
