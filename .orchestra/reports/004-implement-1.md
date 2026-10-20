# Report: implement - 004 - attempt 1

Item **004 - Four tautological assertions replaced by the assertions they stood
in for** (candidate R-016).
Branch **`orchestra/004-real-assertions`**, one commit, `05589b2`.
Worktree **`C:\Users\orcha\AppData\Local\Temp\opencode\wt-004`** (dedicated;
the orchestrator's tree `C:\Sythion\sythion` was never branched or committed in).

---

## What I did

### Commands (in the order they were run)

```powershell
# 1. dedicated worktree
cd C:\Sythion\sythion
git worktree add C:\Users\orcha\AppData\Local\Temp\opencode\wt-004 -b orchestra/004-real-assertions master
#   -> HEAD is now at da6c5c6 chore(orchestra): plans for 001/002/004, planner critiques, items to planned

# 2. pre-change baseline of the whole suite, BEFORE any edit
cd C:\Users\orcha\AppData\Local\Temp\opencode\wt-004
python -m pytest backend/tests -q -p no:cacheprovider
#   -> 1 failed, 1563 passed, 45 skipped, 406271 warnings in 702.76s (0:11:42)   [EXITCODE=1]

# 3. the four replacements + the new guard (edits below)
# 4. the three affected files
python -m pytest backend/tests/test_phase105_concurrency.py backend/tests/test_phase1075_hardening.py backend/tests/test_phase125_planning_validation.py -q -p no:cacheprovider
#   -> 71 passed, 5 skipped, 7362 warnings in 34.07s                            [EXITCODE=0]

# 5. the guard alone
python -m pytest backend/tests/test_no_tautological_asserts.py -q -p no:cacheprovider
#   -> 2 passed, 81 warnings in 1.99s                                           [EXITCODE=0]

# 6. guard-has-teeth probe (see "Evidence")
# 7. post-change full suite, same environment as the baseline
python -m pytest backend/tests -q -p no:cacheprovider
#   -> 1 failed, 1565 passed, 45 skipped, 407538 warnings in 534.60s (0:08:54)  [EXITCODE=1]

# 8. post-change full suite with the orchestrator's environment (backend/.env loaded)
python -m pytest backend/tests -q -p no:cacheprovider
#   -> 1566 passed, 45 skipped, 407552 warnings in 566.44s (0:09:26)             [EXITCODE=0]
```

### Commits

| sha | message |
|---|---|
| `05589b2` | `test: replace four tautological assertions with the assertions they stood in for` |

One commit, four files:

```
$ git diff --name-only da6c5c6..HEAD
backend/tests/test_no_tautological_asserts.py
backend/tests/test_phase105_concurrency.py
backend/tests/test_phase1075_hardening.py
backend/tests/test_phase125_planning_validation.py

$ git log --oneline da6c5c6..HEAD
05589b2 test: replace four tautological assertions with the assertions they stood in for

$ git status --short
(nothing)
```

No production module was touched. `backend/tests/conftest.py` was not touched.
Nothing was pushed.

### Files written

* `backend/tests/test_phase105_concurrency.py` (2 sites + 1 dead line removed)
* `backend/tests/test_phase1075_hardening.py` (1 site)
* `backend/tests/test_phase125_planning_validation.py` (1 site)
* `backend/tests/test_no_tautological_asserts.py` (NEW, 2 test functions)
* this report (in the MAIN tree's `.orchestra\reports\`, not the worktree's copy)

Scratch artefacts used during the session (outside the repository, never
committed): `C:\Users\orcha\AppData\Local\Temp\opencode\scratch-004\`
(`detect_on_master.py`, `diag_readiness.py`, `measure_event_loss.py`,
`check_bom_safety.py`, `check_py311_syntax.py`, `master-tree/` = a pristine
`git archive master backend` extraction, and the three captured run logs).

---

## Evidence

### 1. The four tautologies, verbatim, at the branch point (`da6c5c6`)

```
$ git show da6c5c6:backend/tests/test_phase105_concurrency.py | Select-String -Pattern "tids_in_hist|side_effect = \[RuntimeError|not fail_res.get"
529:                     tids_in_hist = set(tid for _ in hist)
530:                     assert len(tids_in_hist) == 1 or True  # history per tid already isolated
730:         provider.chat.side_effect = [RuntimeError("fail"), fake_chat([{"role":"user","content":"x"}], model_id="model-b")]
746:         assert not fail_res.get("fallback") is False or True  # fallback handled

$ git show da6c5c6:backend/tests/test_phase1075_hardening.py | Select-String -Pattern "delegation_id"
765:                 assert analysis.get("delegation_id") is None or True  # reasoner returns findings

$ git show da6c5c6:backend/tests/test_phase125_planning_validation.py | Select-String -Pattern "actual_policy.*shadow_policy"
230:         assert "actual_policy" in d and "shadow_policy" in d and "actual_outcome" in d or True
```

### 2. Criterion 1 - no tautology remains in the test tree

```
$ git grep -n "or True" -- backend/tests
git grep exit=1 (1 = no match)

$ Get-ChildItem -Recurse backend\tests -Filter *.py | Select-String -Pattern "or True"
matches: 0

$ Select-String -Path backend\tests\test_no_tautological_asserts.py -Pattern "or True"
guard matches: 0
```

The pattern also does not come back in the diff as an **addition** (the four
occurrences the diff shows are the four *removals*):

```
$ git diff da6c5c6..HEAD | Select-String -Pattern "^\+.*or True"
count: 0
$ git diff da6c5c6..HEAD | Select-String -Pattern "^-.*or True"
count: 4
-                    assert len(tids_in_hist) == 1 or True  # history per tid already isolated
-        assert not fail_res.get("fallback") is False or True  # fallback handled
-                assert analysis.get("delegation_id") is None or True  # reasoner returns findings
-        assert "actual_policy" in d and "shadow_policy" in d and "actual_outcome" in d or True
```

### 3. The guard finds exactly the four known sites on master

The guard's detector, run over the *master* versions of the three affected
files (`scratch-004/detect_on_master.py`):

```
backend/tests/test_phase105_concurrency.py
  backend/tests/test_phase105_concurrency.py:746: not fail_res.get('fallback') is False or True
  backend/tests/test_phase105_concurrency.py:530: len(tids_in_hist) == 1 or True
backend/tests/test_phase1075_hardening.py
  backend/tests/test_phase1075_hardening.py:765: analysis.get('delegation_id') is None or True
backend/tests/test_phase125_planning_validation.py
  backend/tests/test_phase125_planning_validation.py:230: 'actual_policy' in d and 'shadow_policy' in d and ('actual_outcome' in d) or True
TOTAL detector hits on master: 4
EXIT=0
```

### 4. Criterion 5 - the guard has teeth (probe demonstration)

**Step 1 - create a temporary probe** `backend/tests/_tautology_probe_tmp.py`
holding the four original assertions verbatim (scratch artefact, deleted in
step 3, never committed):

**Step 2 - the guard MUST fail:**

```
$ python -m pytest backend/tests/test_no_tautological_asserts.py -q -p no:cacheprovider
E       assert statements must not be unconditionally true; the top-level disjunction includes the boolean literal True:
E         C:\...\wt-004\backend\tests\_tautology_probe_tmp.py:11: len(tids_in_hist) == 1 or True
E         C:\...\wt-004\backend\tests\_tautology_probe_tmp.py:16: not fail_res.get('fallback') is False or True
E         C:\...\wt-004\backend\tests\_tautology_probe_tmp.py:21: analysis.get('delegation_id') is None or True
E         C:\...\wt-004\backend\tests\_tautology_probe_tmp.py:26: 'actual_policy' in d and 'shadow_policy' in d and ('actual_outcome' in d) or True
FAILED backend/tests/test_no_tautological_asserts.py::test_no_assert_statement_disjoins_with_the_literal_true
1 failed, 1 passed, 81 warnings in 2.50s
EXIT=1
```

(The detector's own self-test still passes - `1 passed` - so the failure is
the tree scan, not a broken detector.)

**Step 3 - delete the probe and the guard MUST pass:**

```
$ Remove-Item backend\tests\_tautology_probe_tmp.py
$ python -m pytest backend/tests/test_no_tautological_asserts.py -q -p no:cacheprovider
2 passed, 81 warnings in 1.88s
EXIT=0

$ git status --short
 M backend/tests/test_phase105_concurrency.py
 M backend/tests/test_phase1075_hardening.py
 M backend/tests/test_phase125_planning_validation.py
?? backend/tests/test_no_tautological_asserts.py
```

The probe is gone; `git status` shows only the four intended files.

**Step 4 - the guard is collected by the suite, in both invocation forms:**

```
$ python -m pytest backend/tests --collect-only -q
backend/tests/test_no_tautological_asserts.py::test_no_assert_statement_disjoins_with_the_literal_true
backend/tests/test_no_tautological_asserts.py::test_detector_flags_a_tautological_disjunction_and_ignores_legitimate_ones

$ cd backend && python -m pytest tests --collect-only -q
tests/test_no_tautological_asserts.py::test_no_assert_statement_disjoins_with_the_literal_true
tests/test_no_tautological_asserts.py::test_detector_flags_a_tautological_disjunction_and_ignores_legitimate_ones
```

### 5. Criterion 2 - the three affected files still pass

```
$ python -m pytest backend/tests/test_phase105_concurrency.py backend/tests/test_phase1075_hardening.py backend/tests/test_phase125_planning_validation.py -q -p no:cacheprovider
71 passed, 5 skipped, 7362 warnings in 34.07s
EXIT=0
```

`71 passed, 5 skipped` is exactly the count the plan measured on `master`. I
re-measured it myself on a pristine `master` extraction
(`scratch-004/master-tree`, `git archive master backend`) to be sure:

```
$ cd scratch-004/master-tree
$ python -m pytest backend/tests/test_phase105_concurrency.py backend/tests/test_phase1075_hardening.py backend/tests/test_phase125_planning_validation.py -q -p no:cacheprovider
71 passed, 5 skipped, 7381 warnings in 30.41s
MASTER EXIT=0
```

| tree | result |
|---|---|
| pristine master | `71 passed, 5 skipped` (exit 0) |
| branch `05589b2` | `71 passed, 5 skipped` (exit 0) |

### 6. Criterion 4 - the never-awaited-coroutine warning is gone

```
# pristine master (git archive master backend -> scratch-004/master-tree)
$ python -m pytest backend/tests/test_phase105_concurrency.py -q -p no:cacheprovider 2>&1 | Select-String "never awaited"
  ...\master-tree\backend\tests\test_phase105_concurrency.py:60:
RuntimeWarning: coroutine 'TestModelCallConcurrency.test_provider_calls_isolated.<locals>.fake_chat' was never awaited

# branch
$ python -m pytest backend/tests/test_phase105_concurrency.py -q -p no:cacheprovider 2>&1 | Select-String "never awaited"
(no output)
$ python -m pytest backend/tests/test_phase105_concurrency.py -q -p no:cacheprovider
34 passed, 2 skipped, 2760 warnings in 17.36s
```

The dead assignment
`provider.chat.side_effect = [RuntimeError("fail"), fake_chat([...], model_id="model-b")]`
is gone; `provider` itself is still used by the `run_one` helper, so nothing
else changed.

### 7. Criterion 6 - the suite stays green and no test is lost

**Before / after, same worktree, same environment, same machine:**

| run | command | result | exit |
|---|---|---|---|
| baseline (pre-change) | `python -m pytest backend/tests -q` | `1 failed, 1563 passed, 45 skipped` (1609 collected) in 702.76s | 1 |
| after (post-change) | `python -m pytest backend/tests -q` | `1 failed, 1565 passed, 45 skipped` (1611 collected) in 534.60s | 1 |
| after (post-change, orchestrator env) | `python -m pytest backend/tests -q` | **`1566 passed, 45 skipped`** (1611 collected) in 566.44s | **0** |

Delta baseline -> after: **+2 passed, +0 skipped, +2 collected**, and the
single failure is the *same* pre-existing test with the *same* outcome
(fail -> fail). No test was deleted, renamed, skipped, marked `xfail` or
deselected.

Collected-set size, measured directly:

```
$ python -m pytest backend/tests --collect-only -q
1611 tests collected in 123.71s (0:02:03)
```

`1609 + 2 = 1611`. The orchestrator's own baseline (`1564 passed / 45 skipped
/ 1609 collected`) plus the two new guard tests is `1566 passed / 45 skipped /
1611 collected` - which is exactly what the env-matched run reports.

The single failure in the first two runs is
`backend/tests/test_setup_readiness.py::test_readiness_states`; it is
**pre-existing and environment-specific**, see "Findings".

### 8. The BOM hazard is real, and `tokenize.open` avoids it

`scratch-004/check_bom_safety.py` over all 114 test files:

```
files: 114
BOM-carrying files (8):
    test_model_failure.py
    test_target_validation.py
    test_goal_planning.py
    test_kernel_concurrency.py
    test_main_agent.py
    test_phase45_hardening.py
    test_work_orchestration.py
    test_work_state.py

plain read_text(encoding='utf-8') + ast.parse failures: 8
   test_model_failure.py -> SyntaxError: invalid non-printable character U+FEFF (<unknown>, line 1)
   test_target_validation.py -> SyntaxError: invalid non-printable character U+FEFF (<unknown>, line 1)
   test_goal_planning.py -> SyntaxError: invalid non-printable character U+FEFF (<unknown>, line 1)
   test_kernel_concurrency.py -> SyntaxError: invalid non-printable character U+FEFF (<unknown>, line 1)
   test_main_agent.py -> SyntaxError: invalid non-printable character U+FEFF (<unknown>, line 1)
   test_phase45_hardening.py -> SyntaxError: invalid non-printable character U+FEFF (<unknown>, line 1)
   test_work_orchestration.py -> SyntaxError: invalid non-printable character U+FEFF (<unknown>, line 1)
   test_work_state.py -> SyntaxError: invalid non-printable character U+FEFF (<unknown>, line 1)

tokenize.open + ast.parse failures: 0
```

A guard using `read_text(encoding="utf-8")` would have errored on exactly
these eight files.

### 9. The new isolation assertion is not flaky

20 consecutive isolated runs of the rewritten test:

```
$ for ($i=1; $i -le 20; $i++) { python -m pytest "backend/tests/test_phase105_concurrency.py::TestEventLedgerConcurrency::test_no_duplicate_sequence_and_no_cross_contamination" -q ... }
20 runs of the isolation test: non-passing = 0
```

### 10. The concurrent-append race (the defect the tautology hid) - reproduced

Same test, six runs, showing the worker-thread `IntegrityError` while the
test still reports success:

```
=== attempt 1 ===
1 passed, 77 warnings in 2.39s
=== attempt 2 ===
  sqlite3.IntegrityError: UNIQUE constraint failed: task_events.task_id, task_events.sequence_num
  sqlalchemy.exc.IntegrityError: (sqlite3.IntegrityError) UNIQUE constraint failed: task_events.task_id, task_events.sequence_num
1 passed, 78 warnings in 2.49s
=== attempt 3 ===
  sqlalchemy.exc.IntegrityError: ... UNIQUE constraint failed: task_events.task_id, task_events.sequence_num
  (x2 more)
=== attempt 4 ===
  sqlalchemy.exc.IntegrityError: ... UNIQUE constraint failed: task_events.task_id, task_events.sequence_num
1 passed, 78 warnings in 2.57s
=== attempt 5 ===
  sqlalchemy.exc.IntegrityError: ... UNIQUE constraint failed: task_events.task_id, task_events.sequence_num
1 passed, 78 warnings in 2.18s
=== attempt 6 ===
  sqlalchemy.exc.IntegrityError: ... UNIQUE constraint failed: task_events.task_id, task_events.sequence_num
1 passed, 78 warnings in 2.35s
```

Quantified with `scratch-004/measure_event_loss.py` (attempted 10 events per
task, 2 tasks, 2 threads each):

```
round 1: attempted 10 per task, survived [10, 5], owners [[tid1], [tid2]], worker errors 1
round 2: attempted 10 per task, survived [10, 10], owners [[tid1], [tid2]], worker errors 0
round 3: attempted 10 per task, survived [10, 5], owners [[tid1], [tid2]], worker errors 1
round 4: attempted 10 per task, survived [5, 5], owners [[tid1], [tid2]], worker errors 2
round 5: attempted 10 per task, survived [10, 10], owners [[tid1], [tid2]], worker errors 0
round 6: attempted 10 per task, survived [10, 5], owners [[tid1], [tid2]], worker errors 1
round 7: attempted 10 per task, survived [10, 10], owners [[tid1], [tid2]], worker errors 0
round 8: attempted 10 per task, survived [10, 5], owners [[tid1], [tid2]], worker errors 1
```

The **count** is timing-dependent (5 or 10) - so the plan's decision to pin
the deterministic **ownership** property rather than the count is correct and
necessary. The **owner** of every surviving event was always the task itself
in all 8 rounds and all 20 test runs.

---

## Acceptance criteria

Criterion 6 is evaluated in the **restated** form the plan mandates
("no pre-existing test changes outcome; the only delta is the two new guard
tests"), because a guard that runs inside the suite necessarily adds tests.

| # | Criterion | Result | Evidence |
|---|---|---|---|
| 1 | `grep -rn "or True" backend/tests` matches nothing; each of the four sites is replaced by the concrete assertion it stood in for, or by an explicit negative assertion | **PASS** | `git grep -n "or True" -- backend/tests` -> exit 1 (no match); `Select-String` -> `matches: 0`; guard file itself -> 0 matches; diff adds 0 occurrences and removes 4. Replacements listed in "The four replacements" below; `git diff --name-only` shows only the 4 allowed files. |
| 2 | The three affected files still pass (`... -q` exits 0) | **PASS** | `71 passed, 5 skipped, 7362 warnings in 34.07s`, `EXIT=0` - identical counts to master. |
| 3 | A failing real assertion is never resolved by putting the tautology back; a defect is reported instead | **PASS** | No `or True` anywhere in the tree (criterion 1) and none added by the diff (`^\+.*or True` -> 0). **Defects found** section below lists the one real defect the tautology was hiding, with reproduction. No assertion was weakened, deleted, skipped or `xfail`-ed. |
| 4 | The dead `side_effect` assignment is gone and the "never awaited" warning is gone | **PASS** | The line is absent from the file; on pristine master the warning is emitted, on the branch there is no output from `Select-String "never awaited"`. `34 passed, 2 skipped` for that file. |
| 5 | A committed guard prevents re-introduction and runs as part of the suite | **PASS** | `backend/tests/test_no_tautological_asserts.py` committed in `05589b2`; collected from the repo root and from `backend/` (CI form); `2 passed`, exit 0 on the branch; **fails** with the probe present (`1 failed, 1 passed`, exit 1) and passes again once removed; does not flag legitimate assertions (self-test). |
| 6 | *(restated)* The suite exits 0; no pre-existing test changes outcome; the only delta is the two new guard tests | **PASS** (with the restatement stated) | Baseline `1 failed, 1563 passed, 45 skipped` (1609) -> after `1 failed, 1565 passed, 45 skipped` (1611): `passed_after = passed_before + 2`, `skipped_after = skipped_before`, `collected_after = collected_before + 2`, the one failure identical in both. With the orchestrator's environment the suite is green: `1566 passed, 45 skipped`, **exit 0**. `1611 tests collected` measured directly. |

---

## The four replacements

| Site (master) | Before | After | Why this is the right assertion |
|---|---|---|---|
| `test_phase105_concurrency.py:530` (`TestEventLedgerConcurrency::test_no_duplicate_sequence_and_no_cross_contamination`) | `assert len(tids_in_hist) == 1 or True  # history per tid already isolated` (where `tids_in_hist = set(tid for _ in hist)` iterated the **loop variable**, so it could never detect anything) | `assert hist, f"no events recorded for {tid}"` -> re-read the owning `TaskEvent` rows by their primary key -> `assert len(owner_rows) == len(event_ids)` -> `assert owner_task_ids == {tid}` | The property the test name promises is *every event returned for this task is owned by this task*. The ledger's event projection carries no `task_id` (`backend/runtime/events.py:82-88`), so ownership is checked by re-reading the owning rows by `id`. It is **deterministic** (20/20 runs; owners correct in 8/8 measured rounds) whereas the event **count** is racy (5 or 10) - the concurrent-append race is reported, not asserted. |
| `test_phase105_concurrency.py:746` (`TestModelCallConcurrency::test_provider_calls_isolated`) | `assert not fail_res.get("fallback") is False or True  # fallback handled` | `assert fail_res.get("fallback") is True` / `assert fail_res.get("findings") == []` / `assert fail_res.get("confidence") is None` / `assert ok_res.get("fallback") is False` (the pre-existing `assert ok_res.get("findings")` stays) | This is the **fallback contract** of `SpecialistReasoner.analyze()` -> `_fallback_artifact()` (`backend/agents/reasoner.py:271-273`, `:298-308`): a failing provider yields `findings: []`, `confidence: None`, `fallback: True` - never a fake success - and the sibling call is unaffected. Measured on master by the planner: `FAIL_RES: {'findings': [], 'fallback': True, 'confidence': None, ...}`. |
| `test_phase1075_hardening.py:765` (`TestProviderE2EGated::test_two_reasoning_real_provider_isolated`) | `assert analysis.get("delegation_id") is None or True  # reasoner returns findings` | `assert isinstance(analysis, dict)` / `assert isinstance(analysis.get("findings"), list)` / `assert isinstance(analysis.get("fallback"), bool)` / `assert confidence is None or isinstance(confidence, float)` / `assert analysis.get("delegation_id") is None` | The reasoner returns either `_normalize(...)` (`reasoner.py:334-390`) or `_fallback_artifact(...)` (`:298-308`); **neither ever puts a `delegation_id` in the artifact** - delegation attribution is the caller's job (`backend/agents/coordination.py` attaches it when building the evidence bundle; `SpecialistContextPacket` has no delegation field, `backend/agents/specialist.py:44-56`). So the property is the **guaranteed artifact shape** plus the **explicit negative assertion** the original left-hand side was reaching for. "findings is non-empty" is deliberately *not* asserted: a live model may fail and legitimately return the fallback artifact. |
| `test_phase125_planning_validation.py:230` (`test_shadow_quality_agreement_divergence`) | `assert "actual_policy" in d and "shadow_policy" in d and "actual_outcome" in d or True` (inside `for d in q["details"]` - **vacuous when `details` is empty**) | `assert q["details"], "shadow_quality_report returned no divergent detail rows"` -> `assert "actual_policy" in d and "shadow_policy" in d and "actual_outcome" in d` -> `assert d["actual_outcome"] is None` -> `assert d["would_diverge"] is True` | `PlanningOutcomeStore.shadow_quality_report()` (`backend/learning/planning_store.py:731-790`) builds `details` **only from rows where `would_diverge` is true** (`:744-780`) and each detail carries `actual_policy` (`:767`), `shadow_policy` (`:768`), `actual_outcome` (`:771`). The non-emptiness assertion is the part the original was missing - without it the loop verifies nothing. `actual_outcome is None` is the Phase 12 §15/§16 property ("a shadow comparison never carries a claimed outcome"): `evaluate_shadow` never records an outcome (`backend/learning/planning_shadow.py:32-39`). |

### The new guard

`backend/tests/test_no_tautological_asserts.py` - two test functions, AST-based,
self-contained (no import from `backend/tests/conftest.py`, no repo-root pytest
config needed, no Makefile target needed):

1. `test_no_assert_statement_disjoins_with_the_literal_true` - scans every
   `*.py` under `backend/tests/` (recursively, so `backend/tests/acceptance/`
   is included) and fails if an `assert` statement's **top-level** test is an
   `ast.BoolOp` with `ast.Or` that has an `ast.Constant(value=True)` operand.
   The failure message lists every offending `file:line` plus the expression.
   Non-vacuity is asserted in the same test: the scan must cover >= 50 files
   (there are 114 today) **and** must have included the guard's own file, so a
   broken glob can never make it pass silently. A file that cannot be parsed is
   reported as a violation ("cannot verify"), not skipped. Files are read with
   `tokenize.open` (BOM-safe, PEP 263 encoding detection); the tree is located
   from `Path(__file__).resolve().parent`, so it works from the repo root and
   from `backend/`.
2. `test_detector_flags_a_tautological_disjunction_and_ignores_legitimate_ones`
   - the detector's self-test: a synthetic assert whose top-level expression is
   a disjunction with the literal `True` **is** flagged (1 hit, right line,
   right expression); `assert value == 1 or value == 2`,
   `assert any(v or w for v in vs)` and `assert (v or w) is not None` are **not**
   flagged.

The module never spells the forbidden token sequence on a single line (the
synthetic snippet is assembled at runtime from `("or", "True")`), so
criterion 1's literal grep does not match the guard itself - verified above
(`guard matches: 0`).

---

## Defects found

### D-1 (expected, out of scope): concurrent appends to one task id lose events

**Severity: real production defect.** It is the defect the site-1 tautology was
hiding. It is **reported, not fixed** - this item is tests-only by contract and
`backend/runtime/events.py` is out of scope.

* **Root cause.** `DbEventLedger.append_event` (`backend/runtime/events.py:41-73`)
  allocates the sequence number by *reading* the committed maximum:
  `sequence = max(last.sequence_num if last else 0, pending_max) + 1`
  (`:64`). Two sessions appending to the same `task_id` concurrently compute
  the same sequence number; the unique index
  `uq_task_events_task_sequence` (`backend/core/db_manager.py:128-131`) then
  rejects the second commit.
* **Reproduction** (the test this item touches):
  ```
  python -m pytest "backend/tests/test_phase105_concurrency.py::TestEventLedgerConcurrency::test_no_duplicate_sequence_and_no_cross_contamination" -q -s
  ```
  emits, from a worker thread,
  ```
  sqlalchemy.exc.IntegrityError: (sqlite3.IntegrityError) UNIQUE constraint failed: task_events.task_id, task_events.sequence_num
  ```
  and still reports **`1 passed`** - `threading` swallows the worker exception
  (the test's `write_events` has no error propagation).
* **Measured blast radius** (`scratch-004/measure_event_loss.py`, 8 rounds):
  a task that had 10 events appended concurrently kept 5 or 10, depending on
  timing - i.e. **up to 50 % of a task's event history can be silently lost**
  while every test in the suite stays green.
* **Why the old assertion hid it.** The old site's `tids_in_hist` iterated the
  loop variable, so it checked nothing; and the surviving assertions
  (`len(seqs) == len(set(seqs))`, `sorted(seqs) == list(range(1, len(seqs)+1))`)
  are *count-relative* - they hold just as well for 5 surviving events as for
  10.
* **What this branch does about it.** It pins the deterministic property
  (ownership) instead of the racy one, and it does **not** add
  `assert len(hist) == 10` (which would make the suite intermittently red).
* **Suggested fix for a follow-up item** (needs `backend/runtime/events.py`,
  i.e. a scope change): allocate the sequence atomically - e.g. re-read and
  retry on the unique violation, or serialise appends per `task_id` - plus a
  test that asserts the expected event count. Both are deliberately **not**
  done here.

**No real assertion of mine failed during this work.** The three sites'
replacements and the guard all passed on every run recorded above.

### D-2 (pre-existing, environment artifact): `test_setup_readiness.py::test_readiness_states` fails in a git worktree

Not caused by this change and not a code defect; recorded because it changes
the raw exit code of the suite inside a worktree.

* **Symptom**: `1 failed, 1563 passed, 45 skipped` in the pre-change baseline
  and `1 failed, 1565 passed, 45 skipped` after the change - the same test,
  the same outcome, both times.
  ```
  backend/tests/test_setup_readiness.py:160: AssertionError
  E           assert body["full_ready"] is True
  E           assert False is True
  ```
* **Cause**: the readiness route computes
  `key_ok = has_secret(OLLAMA_API_KEY) or bool(settings.ollama_api_key)`
  (`backend/api/routes.py:2261`). The test installs a fresh, empty
  `MemoryStore` as the secret store, so `has_secret` is False; `full_ready`
  therefore needs `settings.ollama_api_key` to be non-empty. `Settings` is a
  `pydantic_settings.BaseSettings` with
  `model_config = {"env_prefix": "SYTHION_", "env_file": <repo>/backend/.env}`
  (`backend/core/config.py:173-176`), and `backend/.env` is **gitignored** -
  so it exists in the orchestrator's tree `C:\Sythion\sythion\backend\.env`
  (it defines `SYTHION_OLLAMA_API_KEY`) but **not** in a git worktree, which
  checks out tracked files only. Measured on the failing run:
  ```
  ollama_api_key_ok        False      <-- the only failing check
  full_ready: False
  ```
  and `settings.ollama_api_key = ''` in the worktree.
* **Proof it is pre-existing and unrelated**: the test fails identically on a
  **pristine `master` extraction** (`git archive master backend` ->
  `scratch-004/master-tree`, no branch of mine involved):
  ```
  $ python -m pytest backend/tests/test_setup_readiness.py::test_readiness_states -q
  >           assert body["full_ready"] is True
  E           assert False is True
  FAILED backend/tests/test_setup_readiness.py::test_readiness_states - assert ...
  1 failed, 557 warnings in 4.98s
  ```
  and `git diff --stat master -- backend/tests/test_setup_readiness.py` is
  empty (the file is byte-identical to master).
* **How the green number was obtained**: the third full-suite run loaded the
  orchestrator's gitignored `backend/.env` into the process environment
  (variable *names* only are ever printed; the values are never printed, never
  written to a file, and never committed), reproducing the environment the
  orchestrator's own baseline was measured in:
  ```
  loaded env var NAMES: SYTHION_FILE_ROOTS, SYTHION_OLLAMA_API_KEY, SYTHION_SELECTED_MODEL
  1566 passed, 45 skipped, 407552 warnings in 566.44s (0:09:26)
  EXITCODE=0
  ```
* **Recommendation for the orchestrator**: the *same* artifact will affect any
  other implementer working in a worktree. Either copy `backend/.env` into the
  worktree (it is gitignored, so it can never be committed) or make the
  worktrees inherit it; otherwise every worktree-based baseline will show one
  spurious failure.

---

## Findings

1. **Site 1 was doubly broken, as the plan said.** `tids_in_hist = set(tid for
   _ in hist)` iterated the *loop variable* - so even without `or True` it
   could never have detected cross-contamination. Removing the tautology
   without fixing that would have replaced a check of nothing with another
   check of nothing.
2. **The plan's choice of property at site 1 is right, and I confirmed it
   quantitatively.** The event **count** for a task under concurrent appends
   was 5 or 10 across 8 measured rounds; the **owner** of every surviving
   event was always the task itself. `assert len(hist) == 10` would have made
   the suite intermittently red - the plan forbade it and I did not add it.
   The ownership assertion passed 20/20 isolated runs and in both full-suite
   runs.
3. **The defect the tautology hid is real and is not fixed here** - see D-1
   above. Up to 50 % of a task's concurrently-appended events can be lost
   while the whole suite stays green.
4. **A git worktree is not a faithful copy of the developer's tree.**
   `backend/.env` is gitignored, so it is missing from a worktree, and
   `Settings` reads it. One pre-existing test
   (`test_setup_readiness.py::test_readiness_states`) therefore fails in every
   worktree and passes in the main tree - see D-2. The other implementers
   working in worktrees right now will hit the same thing.
5. **The same tautology shape exists in production code**, out of scope for
   this item (criterion 1 covers `backend/tests/` only) and not fixed:
   `backend/services/capabilities.py:219` -
   `if not (has_secret(OLLAMA_API_KEY) or True):` - a branch that can never be
   taken, so the `REQUIRES_CONFIGURATION` health state for the
   `reasoning_model` capability is dead code. It deserves its own item (it
   changes a capability health check's behaviour).
6. **`master` moved under me** while I worked: `da6c5c6` -> `662cbe3` ->
   `163adab` (orchestrator commits, `.orchestra/` only - `git diff --stat
   da6c5c6 master` touches only `.orchestra/backlog.md`, `board.json`,
   `briefs/002-implement-1.md`, `critiques.md`, `goal.md`, `log.md`). **No
   production or test file changed on master**, so my branch needs no rebase
   for code reasons; a rebase onto `163adab` is still advisable to keep the
   `.orchestra/` state linear.
7. **The guard's non-vacuity floor matters.** The guard asserts it scanned
   >= 50 files and that its own file was among them; without that, a broken
   glob (or a wrong `__file__` resolution under `cd backend`) would make it
   pass vacuously - the same failure mode as the tautology it replaces.
8. **The `RuntimeWarning` was a real dead-code smell**, not just noise: the
   never-awaited `fake_chat(...)` coroutine in the dead `side_effect`
   assignment was constructing an object that nothing could ever use. It is
   gone, and the warning with it.
9. **The known hazard did not fire.** Neither of my three full-suite runs died
   with `Windows fatal exception: code 0x80040155`; all three completed
   normally (702.76s / 534.60s / 566.44s). No re-run was needed for that
   reason.

---

## Not checked

1. **CPython 3.11 (CI's pinned version) was not executed.** No 3.11
   interpreter is installed on this machine. I did the strongest static
   substitute available: `scratch-004/check_py311_syntax.py` parsed all 114
   test files and found **no 3.12+ only syntax**
   (`TypeAlias`/`TypeVar`/`ParamSpec`/`TryStar` -> 0 hits), so the tree the
   guard parses on 3.11 is the same tree it parses here. The guard uses only
   `ast`/`tokenize`/`pathlib` features available in 3.11 (`ast.unparse` is
   3.9+). The actual 3.11 run is CI's to make.
2. **Site 3 was never executed.** `TestProviderE2EGated::
   test_two_reasoning_real_provider_isolated` is gated on
   `SYTHION_RUN_PROVIDER_E2E=1` and a healthy provider; it is skipped in every
   normal run and in CI. Its replacement assertions are derived from the
   production source (`_normalize` / `_fallback_artifact`) and from the
   planner's direct reproduction of the reasoner's return value, not from a
   gated run. **This is the weakest of the four replacements in terms of
   executed evidence** and I say so plainly.
3. **The `provider_e2e`-gated path with a real model was not exercised**, so
   I cannot claim the new shape assertions hold against a live provider's
   answer. They are written to hold for *both* artifact shapes precisely
   because that path could not be run.
4. **The concurrent-append defect (D-1) was not fixed and its blast radius
   beyond `backend/tests/test_phase105_concurrency.py` was not measured.**
   `DbEventLedger.append_event` is used across the runtime; how many
   production paths append events to the same task id from different sessions
   was not analysed. Only the test's own scenario was measured.
5. **`backend/services/capabilities.py:219` was not analysed in depth** - only
   the existence and shape of the dead branch were confirmed by reading the
   line. I did not run the capability health check to see what behaviour
   would change if it were fixed.
6. **The 8 BOM-carrying test files were not otherwise modified.** I verified
   only that the guard reads them without error.
7. **The frontend / vitest suites were not run** - out of scope for this
   item, and item 001 owns them.
8. **`git push` was never run and nothing was merged.** The branch
   `orchestra/004-real-assertions` exists only locally, with one commit
   `05589b2`.
