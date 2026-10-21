# Report: implement - 019 - round 2 (wording corrections only)

- **Item:** 019 - "Tautology detector must also catch nested and bare `or True`" (R-027)
- **Role:** implementer, round 2
- **Branch:** `orchestra/019-tautology-detector` (EXISTING branch, continued - not re-created)
- **Worktree:** `C:\Users\orcha\AppData\Local\Temp\opencode\wt-019` (already on the branch at `76c5c1d`)
- **Base of this round:** `76c5c1d` (the commit the reviewer reviewed)
- **Commit SHA:** `f0fc3c603e7499970e340a104d4d5de4b11b5952` (1 commit on top of `76c5c1d`, **not pushed**)
- **Files changed in this round (exactly the same three as round 1):**
  - `backend/tests/test_no_tautological_asserts.py` (the guard)
  - `backend/tests/test_conversation_memory.py` (site 1)
  - `backend/tests/test_phase12_planning_learning.py` (site 2)

```
$ git diff --name-only master..HEAD
backend/tests/test_conversation_memory.py
backend/tests/test_no_tautological_asserts.py
backend/tests/test_phase12_planning_learning.py

$ git log --oneline master..HEAD
f0fc3c6 docs(019): correct the guard's wording and harden the parts/placeholder map
76c5c1d fix(019): widen the tautology guard to every guard position, fix the two placeholder asserts

$ git diff --stat master..HEAD
 backend/tests/test_conversation_memory.py       |  18 ++-
 backend/tests/test_no_tautological_asserts.py   | 199 ++++++++++++++++++++----
 backend/tests/test_phase12_planning_learning.py |  36 ++++-
 3 files changed, 215 insertions(+), 38 deletions(-)

$ git status --short
(empty - the worktree is clean, nothing is staged or untracked)
```

**No behaviour changed.** This round is text only:

* the detector's **rule** (`_is_tautology`, `_find_tautologies`, the guard-position
  definition, the opaque leaves) is **byte-identical** to `76c5c1d`;
* the **probes** (P1-P4 and their twins), the negative list, the self-test's structure,
  the **test ids** and the collected count (1628) are unchanged;
* **no assertion's semantics was changed** - the only assertion *added* is the F7
  length guard inside the `_source()` helper (`assert len(_TAUTOLOGY_PARTS) == 5`),
  which is the hardening the review asked for and which is itself a `Compare` form
  (an opaque leaf), so it is legal under the guard's own rule;
* no test was added, removed, renamed, re-ordered, skipped or marked;
* no exemption mechanism was introduced (there was none and there is none now);
* the file set is unchanged: exactly three files, the same three.

---

## 1. The findings, and what was done about each

| finding | required? | what changed | file |
|---|---|---|---|
| **F1** - the comment above the site-1 assertion still said the log message was "implicitly tested by no exception" while the explicit assertion sits four lines below | **required** | the false clause and the now-redundant "so we just verify the code path ran" sentence were deleted; the comment now reads "... so we check that the verification log message is emitted (asserted below)." | `test_conversation_memory.py` |
| **F2** - the guard's failure message enumerated three guard positions and omitted the bare literal (the assert's own test) | **required** | "or the assertion's own test" was added to the parenthetical. Message text only. | `test_no_tautological_asserts.py` |
| **F3** - the docstring's opening rationale claimed such an assertion "verifies nothing", false for two of the four shapes the rule now flags and contradicted by the same docstring four paragraphs later | **required** | reworded to "so the assertion does not check the property its shape suggests". | `test_no_tautological_asserts.py` |
| **F7** - `_source()` built its placeholder map with `dict(zip(...))`, which truncates silently, so a part added to the middle of `_TAUTOLOGY_PARTS` would *shift* the mapping instead of failing | hardening | a one-line length guard before the zip: `assert len(_TAUTOLOGY_PARTS) == 5, "parts tuple and placeholder map must stay in step"` (plus a one-line comment naming the reason). | `test_no_tautological_asserts.py` |
| **F5** - `assert len(turns) == 2` pins the row count visible to the test's *own* session (autoflush), not persistence | clarification | the comment next to the assertion now says: "The count below is what this test's own session sees (autoflush); it is not a persistence check." **The assertion itself is unchanged.** | `test_conversation_memory.py` |
| **F6** - the site-2 comparison pins *persisted* state only; an uncommitted mutation is discarded by `db.expire_all()` and is not detected | clarification | the comment now reads: "No goal mutation via planning - the comparison pins persisted state: an uncommitted mutation is discarded by `db.expire_all()` and is not detected." **The assertion itself is unchanged.** | `test_phase12_planning_learning.py` |
| **F8** - `caplog.text` is the root handler's text, not the module logger's | clarification | a comment line was added above the assertion: "caplog.text is the root handler's text, not the module logger's." **The assertion itself is unchanged** (the review explicitly forbids changing it). | `test_conversation_memory.py` |

F4 (the guard's test *function name* still mentions only disjunction) and F9 (the plan's
quoted "24" for `not` over a non-vacuous operand, measured 145) were recorded by the
reviewer as non-blocking observations with **no change required** and are deliberately
left alone: F4's fix would move a collected test id, which the brief forbids.

---

## 2. The exact diff of this round (`76c5c1d..f0fc3c6`)

```diff
diff --git a/backend/tests/test_conversation_memory.py b/backend/tests/test_conversation_memory.py
index 62e5b4e..de60281 100644
--- a/backend/tests/test_conversation_memory.py
+++ b/backend/tests/test_conversation_memory.py
@@ -631,13 +631,15 @@ async def test_post_task_intelligence_save_conversation_turn_verification(memory
         await save_conversation_turn(session, "task_conv_verify", task)
 
     # Fix behavior: returns early (verification caught the failure)
-    # The turns may have been committed before verification, so we just verify the code path ran
-    # by checking that the verification log message was emitted (implicitly tested by no exception)
+    # The turns may have been committed before verification, so we check that the
+    # verification log message is emitted (asserted below).
     turns = session.query(ConversationTurn).filter_by(task_id="task_conv_verify").all()
-    # The fix returns early when verification fails, but the commit already happened
-    # The key test is that the verification code path exists and runs:
-    # the production code logs this warning when the read-back finds no turns.
+    # The fix returns early when verification fails, but the commit already happened.
+    # The count below is what this test's own session sees (autoflush); it is not a
+    # persistence check.  The key test is that the verification code path exists and
+    # runs: the production code logs this warning when the read-back finds no turns.
     assert len(turns) == 2
+    # caplog.text is the root handler's text, not the module logger's.
     assert "Verification failed" in caplog.text
 
 
diff --git a/backend/tests/test_no_tautological_asserts.py b/backend/tests/test_no_tautological_asserts.py
index b911293..726aa7f 100644
--- a/backend/tests/test_no_tautological_asserts.py
+++ b/backend/tests/test_no_tautological_asserts.py
@@ -2,10 +2,11 @@
 
 An ``assert`` whose test carries the boolean literal ``True`` in a *boolean
 position* is degenerate: the literal decides that position instead of the
-code under test, so the assertion verifies nothing while the suite that
-contains it advertises exactly the property it does not check.  This module
-scans every ``*.py`` file under ``backend/tests`` (including
-``backend/tests/acceptance/``) and fails if such an assertion exists.
+code under test, so the assertion does not check the property its shape
+suggests while the suite that contains it advertises exactly the property
+it does not check.  This module scans every ``*.py`` file under
+``backend/tests`` (including ``backend/tests/acceptance/``) and fails if
+such an assertion exists.
 
 Detection rule, exactly:
 
@@ -114,6 +115,9 @@ def _source(template):
     ``{not}`` and ``{assert}``; they are filled from the parts tuple so that
     no single line of this file spells a forbidden sequence.
     """
+    # zip() truncates silently, so a length guard keeps the parts tuple and the
+    # placeholder map in step.
+    assert len(_TAUTOLOGY_PARTS) == 5, "parts tuple and placeholder map must stay in step"
     parts = dict(zip(("or", "true", "and", "not", "assert"), _TAUTOLOGY_PARTS))
     return template.format(**parts)
 
@@ -143,7 +147,8 @@ def test_no_assert_statement_disjoins_with_the_literal_true():
     assert not violations, (
         "assert statements under backend/tests must not carry the boolean "
         "literal True in a boolean position (an operand of a conjunction or "
-        "a disjunction, or the operand of a negation):\n"
+        "a disjunction, or the operand of a negation, or the assertion's own "
+        "test):\n"
         + "\n".join(violations)
     )
 
diff --git a/backend/tests/test_phase12_planning_learning.py b/backend/tests/test_phase12_planning_learning.py
index a2211be..565f48b 100644
--- a/backend/tests/test_phase12_planning_learning.py
+++ b/backend/tests/test_phase12_planning_learning.py
@@ -362,7 +362,8 @@ def test_learned_planning_cannot_grant_capabilities(db):
 def test_learned_planning_cannot_change_runtime_permissions(db):
     # Planning selector has no pathway to alter runtime policy — assert no side effects on Goal state
     from backend.models.database import Objective
-    # No goal mutation via planning
+    # No goal mutation via planning - the comparison pins persisted state: an
+    # uncommitted mutation is discarded by db.expire_all() and is not detected.
     goal = Objective(id="sec_perm_goal", title="runtime-permission invariant",
                      status="active", priority=3)
     db.add(goal)
```

Everything above is a comment, a docstring, a failure-message string, or the one
F7 length guard. No executable path of the detector, of the self-test, of site 1 or
of site 2 was altered.

---

## 3. Every command and its output

All commands were run in `C:\Users\orcha\AppData\Local\Temp\opencode\wt-019`
(local interpreter: **CPython 3.14.7**).

### 3.1 The guard file alone - 2 passed

```
$ python -m pytest backend/tests/test_no_tautological_asserts.py -q
..
2 passed, 89 warnings in 0.67s
```

### 3.2 The two edited files - 63 passed

```
$ python -m pytest backend/tests/test_conversation_memory.py backend/tests/test_phase12_planning_learning.py -q
...
63 passed, 4919 warnings in 12.40s
```

### 3.3 The collected count of the whole tree - 1628

```
$ python -m pytest backend/tests --collect-only -q
1628 tests collected in 53.23s
```

Unchanged from `76c5c1d` (1628) and from master `c855d72` (1628).

### 3.4 The whole-tree scan with the new rule - 0 violations

The guard's own tree-scan test (3.1) *is* the whole-tree scan, and it is green. In
addition, a standalone scanner was run **outside the repository**
(`C:\Users\orcha\AppData\Local\Temp\opencode\scan019\scan019.py`) that imports the
**shipped** guard module from the worktree and reuses its own `_read_source` and
`_find_tautologies`, so these are the shipped rule's numbers, not a
re-implementation's:

```
$ python C:\Users\orcha\AppData\Local\Temp\opencode\scan019\scan019.py
py files under backend/tests:            120
parse errors:                            0
assert statements:                       4000
violations (literal True at a guard position): 0
guard's own file hits:                   0
---
EXITCODE=0
```

**0 violations.** The assert-statement count is 4000, one more than the 3999 the
reviewer measured at `76c5c1d`: the difference is exactly the one assertion this
round adds (the F7 length guard). The *collected test* count is unchanged at 1628 -
the two numbers count different things.

### 3.5 The guard does not flag itself (the self-reference trap)

Textual scan of the guard file after the edits:

```
$ (per-line scan of backend/tests/test_no_tautological_asserts.py)
forbidden sequence counts (whole file, substring):
  'or True' -> 0
  'and True' -> 0
  'not True' -> 0
  'assert True' -> 0
per-line regex (keyword immediately followed by the literal, or a bare assert of it):
  lines: 0
token 'True' total: 17
```

The required **0 / 0 / 0 / 0** holds. (The F2 and F3 edits add occurrences of the
token `True` inside prose that is never adjacent to a keyword on the same line.)

### 3.6 Test ids of the three touched files - unchanged

An AST-based id collector (run outside the repository, no pytest import, so it could
not interfere with the suite) compared the normalised pytest node ids of the three
touched files on `master` and on the branch:

```
$ python C:\Users\orcha\AppData\Local\Temp\opencode\scan019\ids.py
backend/tests/test_conversation_memory.py                  master= 21 branch= 21 identical=True
backend/tests/test_phase12_planning_learning.py            master= 42 branch= 42 identical=True
backend/tests/test_no_tautological_asserts.py              master=  2 branch=  2 identical=True
```

65 ids on master, 65 on the branch, identical lists - matching the reviewer's
measurement at `76c5c1d`. No test was added, removed, renamed, re-ordered, skipped
or marked.

### 3.7 The full suite

```
$ python -m pytest backend/tests -q
```

| run | result |
|---|---|
| 1 | **died natively** at ~61% - `Windows fatal exception: access violation` (exit code `-1073741819` = 0xC0000005), faulthandler dump, no pytest summary. |
| 2 (the re-run the brief asks for) | **1583 passed, 45 skipped, 427243 warnings in 349.83s (0:05:49)**, exit 0 |

`1583 passed + 45 skipped = 1628` - the same total as `76c5c1d` and as master.

**The native death is the known Fault-B class, in code this diff does not touch.**
The faulting frame chain of run 1:

```
Windows fatal exception: access violation

Current thread 0x00005294 [Thread-237 (validate_in_thread)] (most recent call first):
  File "...\site-packages\sqlalchemy\orm\loading.py", line 231 in chunks
  File "...\site-packages\sqlalchemy\engine\result.py", line 1883 in _fetchone_impl
  File "...\site-packages\sqlalchemy\engine\result.py", line 1280 in _fetchone_impl
  File "...\site-packages\sqlalchemy\engine\result.py", line 1397 in first
  File "...\site-packages\sqlalchemy\orm\query.py", line 2823 in first
  File "...\wt-019\backend\agents\delegation.py", line 1358 in get_delegation
  File "...\wt-019\backend\agents\delegation.py", line 1374 in _require
  File "...\wt-019\backend\agents\delegation.py", line 394 in validate
  File "...\wt-019\backend\tests\test_phase95_hardening.py", line 68 in validate_in_thread
  File "...\threading.py", line 1024 in run
```

That is the identical frame chain the round-1 report attributed to Fault-B and
recorded in `.orchestra/goal.md` / `.orchestra/log.md` (an access violation in
native sqlite3 reached through `backend/agents/delegation.py` -> SQLAlchemy). The
faulting path - `backend/agents/delegation.py` (production) and
`backend/tests/test_phase95_hardening.py` - is **not touched by this diff**; the
diff is three test files, two of them comment-only and the third a pure-AST guard
with no I/O. The round-1 reviewer also saw a green first attempt on the same branch,
so the fault is intermittent (goal.md records ~33% of full-suite runs dying
natively, in two distinct fault classes).

### 3.8 The reproduction of the guard's failure path (unchanged)

The guard's failure message is the one the reviewer required to name the bare
literal (F2). Its text now reads:

```
assert statements under backend/tests must not carry the boolean literal True in a
boolean position (an operand of a conjunction or a disjunction, or the operand of a
negation, or the assertion's own test):
```

The detection behaviour was **not** re-litigated this round (the rule is byte-identical
to the reviewed `76c5c1d`); the round-1 report already documents the four R-027
shapes being listed by the tree scan with exit code 1, and the review re-verified them
independently.

---

## 4. What was NOT checked / not done

- **No push, no merge to master, no board update.** The branch is local only
  (`git log master..HEAD` = two commits, both unpushed; `git status -sb` shows
  `## orchestra/019-tautology-detector` with no upstream).
- **No production file, no fixture, no config, no docs, no other test file** was
  touched. The diff is exactly the three test files the amended criterion 5 allows.
- **The suite was not run under CI's pinned interpreter (3.11)**; the local one is
  CPython 3.14.7. The change is comment/docstring/string plus one `len(...) == 5`
  comparison - interpreter-agnostic - but the 3.11 run was not executed.
- **The CI form of the whole suite** (`cd backend && python -m pytest tests -q`) was
  not run; the repository-root form the brief specifies was used for every command.
- **The 45 skipped tests** are the provider-gated population; no credential is
  present here, so they are skipped in every number above.
- **F4 was not applied** (the guard's test function name still says "disjoins"),
  because the brief forbids renaming a test and moving its collected id.
- **The F7 guard catches a changed part COUNT, not a reordering.** A part inserted in
  the middle of `_TAUTOLOGY_PARTS` still shifts the mapping; the length guard is the
  fix shape the review explicitly offered (`assert len(parts) == 5`) and is what the
  brief asked for.
- **The `post-commit` git hook printed `Python was not found`** (it is the project's
  memory-capture hook with a `#!/usr/bin/env python3` shebang, and `python3` is not
  resolvable in this environment). It is unrelated to the change, it does not touch
  the working tree, and this commit message carries no `[decision]`/`[constraint]` tag,
  so the hook had nothing to capture. The commit itself is complete and the worktree
  is clean.
- **The CogniGraph memory server was not available in this sub-agent session** (no
  `memory_*` tool is exposed in the tool catalogue; `list_mcp_resources` returns
  empty), so no memory briefing was used and no memory fact was written. The brief
  and the round-1 review were read directly.

---

## 5. Statement

**No behaviour changed.** The detector's rule, the probes, the self-test's structure,
the test ids, the collected count (1628), the two replacement assertions' semantics
and the file set (exactly three test files) are all exactly as the reviewer left
them at `76c5c1d`. This commit contains three wording corrections (F1, F2, F3), one
one-line hardening (F7) and three comment-level clarifications (F5, F6, F8) - nothing
else. The guard file still scans the whole tree to **0 violations** and still does
not flag itself (**0 / 0 / 0 / 0** forbidden sequences). The full suite is green on
the re-run: **1583 passed, 45 skipped**, exit 0.

**Commit:** `f0fc3c603e7499970e340a104d4d5de4b11b5952` on
`orchestra/019-tautology-detector` (not pushed).
