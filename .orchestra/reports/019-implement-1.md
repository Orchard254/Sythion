# Report: implement - 019 - attempt 1

- **Item:** 019 - "Tautology detector must also catch nested and bare `or True`" (R-027)
- **Branch:** `orchestra/019-tautology-detector` (from `master` @ `c855d72`)
- **Worktree:** `C:\Users\orcha\AppData\Local\Temp\opencode\wt-019`
- **Commit SHA:** `76c5c1de3c4f50cf6cd4f61f23b9836e797b6fc2` (1 commit, not pushed)
- **Files changed (exactly three):**
  - `backend/tests/test_no_tautological_asserts.py` (the guard)
  - `backend/tests/test_conversation_memory.py` (site 1)
  - `backend/tests/test_phase12_planning_learning.py` (site 2)

```
$ git diff --name-only master..HEAD
backend/tests/test_conversation_memory.py
backend/tests/test_no_tautological_asserts.py
backend/tests/test_phase12_planning_learning.py
```

No production file, no other test file, no fixture, no config, no docs.

---

## 1. The rule as implemented

`_is_tautology(node)` (new, in `backend/tests/test_no_tautological_asserts.py`):

```python
def _is_tautology(node):
    if _is_boolean_literal_true(node):
        return True
    if isinstance(node, ast.BoolOp):
        return any(_is_tautology(value) for value in node.values)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return _is_tautology(node.operand)
    return False
```

`_find_tautologies(source, filename="<synthetic>")` keeps its signature and its
`(lineno, expression)` return shape (`expression` is `ast.unparse(test)`); only the
detection rule behind it widened: it now calls `_is_tautology(node.test)` for every
`ast.Assert` node instead of requiring a top-level `ast.BoolOp(ast.Or)` with a literal
`True` operand.

**In words:** a *guard position* of an assert's test is a node reachable from the test
by descending **only** through `ast.BoolOp` (any of its `values`) and
`ast.UnaryOp` whose `op` is `ast.Not` (its `operand`). Every other node is an opaque
leaf - the traversal does not descend into it. A comparison is opaque (so its `left`
and `comparators` are not guard positions), and so are calls, subscripts, attributes,
lambdas and every container literal. An assert is flagged iff one of its guard
positions is `ast.Constant(value=True)`.

Python has no node for parentheses, so grouping is transparent: `(x or True) and y`
parses as `BoolOp(And, [BoolOp(Or, [Name x, Constant True]), Name y])` and the
recursion handles it with no extra case.

**No exemption mechanism of any kind.** There is no `_KNOWN_VACUOUS_SITES`, no
comment marker, no line-number list, no stale-entry check, no over-use check and no
partition of the scan's hits - one bucket (violations), and the assert fails if it is
non-empty. The module gains no module-level constant other than the extended
`_TAUTOLOGY_PARTS` tuple.

**The claim was corrected to match the code.** The module docstring now states the
guard-position rule, states that the rule is *syntactic* and therefore also flags
shapes that are not literally always-true (a conjunction whose left operand carries
the literal reduces to its right operand; a literal under a negation fixes the
outcome at false), lists what is deliberately not inspected (comparison operands, call
arguments, subscripts, containers), and states that the rule applies uniformly to the
whole tree with no exceptions. The phrase "unconditionally true" is no longer used as
the rule's description anywhere in the file. The tree scan's failure message was
rewritten the same way and now lists every offending `file:line: expression`.

**The self-reference trap was extended to four sequences.** The old rule forbade one
token sequence; the new rule forbids four (a keyword immediately followed by the
boolean literal, and a bare assertion of that literal). The file therefore assembles
every synthetic probe source at runtime from `_TAUTOLOGY_PARTS = ("or", "True", "and",
"not", "assert")` through a new `_source(template)` helper (placeholders `{or}`,
`{true}`, `{and}`, `{not}`, `{assert}`). The one negative-list source that would have
spelled a forbidden sequence is templated for the same reason.

---

## 2. The self-test (probes live inside the EXISTING test function)

`test_detector_flags_a_tautological_disjunction_and_ignores_legitimate_ones` gained
**four probe pairs**, each a tautological source and a de-tautologised twin that
differs only by the literal. Each probe asserts exactly four things: 1 hit, the
right line, the right expression, 0 hits for the twin.

| probe | tautological source | de-tautologised twin |
|---|---|---|
| P1 - literal under a conjunction | `assert (x or True) and y` | `assert (x or t) and y` |
| P2 - literal under a negation | `assert not (x or True)` | `assert not (x or t)` |
| P3 - literal nested in a disjunction | `assert x or (y or True)` | `assert x or (y or t)` |
| P4 - bare literal | `assert True` | `assert t` |

The existing top-level probe (`assert value == 1 or True` -> exactly 1 hit) and the
existing negative source stay unchanged. The negative list was extended to the
plan's twelve sources, each required to yield zero hits: `value == 1 or value == 2`,
`any(v or w for v in vs)`, `(v or w) is not None`, `f(x or y)`, `x is True`,
`flag == True`, `(a and b) is True`, `r.verified_success is not True`, `not x`,
`not (x and y)`, `x or False`, `d["k"] is True`.

**No new test function was added** - the probes are inside the existing self-test, so
the collected count cannot move.

---

## 3. The two placeholder sites (fixed, not exempted)

### Site 1 - `backend/tests/test_conversation_memory.py`

`test_post_task_intelligence_save_conversation_turn_verification` gained the built-in
`caplog` fixture, `import logging` was added to the import block, and
`caplog.set_level(logging.WARNING, logger="backend.services.post_task_intelligence")`
is called before the patched call. The placeholder is replaced by the plan's
assertions:

```python
    assert len(turns) == 2
    assert "Verification failed" in caplog.text
```

The test's name, id, docstring and every other line are unchanged.

### Site 2 - `backend/tests/test_phase12_planning_learning.py`

`test_learned_planning_cannot_change_runtime_permissions` keeps its name, id and its
two comment lines; the previously unused `Objective` import is now used. The
placeholder is replaced by the plan's block: a seeded `Objective` row, a `goal_state()`
helper that asserts the snapshot is non-empty (so the comparison cannot be vacuous),
`db.expire_all()` before **both** snapshots, ten recorded `DELEGATED_PLAN` outcomes
plus a `select()` call with a violated hard constraint between them, and finally
`assert goal_state() == before` over every column of `Objective.__table__.columns`.

---

## 4. Every command and its output

### 4.1 The guard alone

```
$ python -m pytest backend/tests/test_no_tautological_asserts.py -q
2 passed, 89 warnings in 1.30s
```

CI's form also works (the guard resolves `backend/tests` from `__file__`):

```
$ cd backend && python -m pytest tests/test_no_tautological_asserts.py -q
2 passed, 89 warnings in 1.90s
```

### 4.2 The two edited files

```
$ python -m pytest backend/tests/test_conversation_memory.py backend/tests/test_phase12_planning_learning.py -q
63 passed, 4919 warnings in 13.32s
```

### 4.3 The collected count of the whole tree (unchanged)

```
$ python -m pytest backend/tests --collect-only -q
1628 tests collected in 77.27s (0:01:17)
```

Base tree, same command, before the change: `1628 tests collected in 77.03s`.
Unchanged.

Per-file test ids of the three touched files were collected on master and on the
branch and compared: **70 ids on master, 70 on the branch, the normalised id lists
are identical** (`Compare-Object` produced no output) - no test was added, removed,
renamed, re-ordered, skipped or marked.

### 4.4 The full suite

```
$ python -m pytest backend/tests -q
```

| run | tree | result |
|---|---|---|
| 1 | branch | **died natively** at ~61% - `Windows fatal exception: access violation` (0xC0000005), faulthandler dump in `Thread-237 (validate_in_thread)`. No pytest summary. |
| 2 (the re-run the brief asks for) | branch | **died natively** at ~61% - the same fault, the same thread. No pytest summary. |
| 3 | branch | **1583 passed, 45 skipped, 427247 warnings in 317.40s (0:05:17)**, exit 0 |
| base (master, detached worktree) | master | **1583 passed, 45 skipped, 427202 warnings in 322.54s (0:05:22)**, exit 0 |

Branch and base agree exactly: `1583 passed, 45 skipped` (= 1628 collected).

**Attribution of the two native deaths (plan risk R8).** The fault is the
pre-existing Fault-B class recorded in `.orchestra/goal.md` and in
`.orchestra/log.md`: an access violation in native sqlite3 reached through
`backend/tests/test_phase95_hardening.py::validate_in_thread` ->
`backend/agents/delegation.py` `validate` -> SQLAlchemy -> native sqlite3.
The same fault is recorded in `.orchestra/log.md` as having killed a master run
during item 001's merge verification, at the same frame. Three facts support
"pre-existing, not caused by this change":

1. the faulting code path (`test_phase95_hardening.py`, `backend/agents/delegation.py`,
   native sqlite3) is **not touched by this item** - the diff is three test files, two
   of them pure-assertion edits and the guard a pure-AST test with no I/O;
2. `python -m pytest backend/tests/test_phase95_hardening.py -q` on the branch is
   **33 passed** - the faulting module is clean in isolation;
3. the base tree, run in its own detached worktree, is green.

The branch's third run is green with the same counts as the base, and the base run
itself was the *first* attempt in that worktree - the crash is intermittent
(goal.md records ~33% of full-suite runs dying natively, in two distinct fault
classes).

### 4.5 The whole-tree scan with the new rule

A scanner (written outside the repository, in the session temp dir; it implements the
old and the new rule inline so both trees can be measured with the same code) over
`backend/tests/`, BOM-safe via `tokenize.open`:

```
=== tree: C:\Sythion\sythion\backend\tests ===            (BASE, master)
py files under backend/tests:            120
parse errors:                            0
assert statements:                       3980
hits under the OLD (top-level or) rule:  0
hits under the NEW (guard-position) rule:2
  NEW ...\backend\tests\test_conversation_memory.py:637: True
  NEW ...\backend\tests\test_phase12_planning_learning.py:366: True
```

```
=== tree: ...\wt-019\backend\tests ===                   (BRANCH, after the change)
py files under backend/tests:            120
parse errors:                            0
assert statements:                       3999
hits under the OLD (top-level or) rule:  0
hits under the NEW (guard-position) rule:0
exemption partition / known sites:       0   (the mechanism does not exist)
guard's own file hits under the new rule: 0
```

The branch's numbers match the plan's predicted figures exactly: **120 files, 0 parse
errors, 3999 assert statements, 0 violations, no exemption partition**. The 3999 is
the plan's arithmetic: 3980 today + 17 new assert statements in the guard's self-test
(4 probes x 4 statements + 1 statement in the loop over the 12 negative sources) + 1
net at site 1 (1 placeholder -> 2 assertions) + 1 net at site 2 (1 placeholder -> 2
assertions: the helper's `assert rows` and the state comparison).

The legal shapes stay legal, and their counts are identical before and after the
change:

| shape | base | branch |
|---|---|---|
| `True` as a comparison operand (`x is True`, `flag == True`) | 307 | 307 |
| comparison with a `BoolOp` on one side (`(a and b) is True`) | 9 | 9 |
| `BoolOp` whose `True` sits inside a comparison operand | 10 | 10 |
| assert whose test is a `Call` | 194 | 194 |
| bare non-`True` constant assert | 0 | 0 |
| `not` over a non-vacuous operand | 145 | 145 |

(The plan quotes 24 for the last row; the measured figure is 145 - the plan's number
appears to count a narrower shape. The invariant that matters holds either way: **0**
violations, and every one of those `not` asserts is still legal. The breakdown is
`not` over a name 17, over a call 57, over a comparison 1, over other 70, over a
`BoolOp` 0 - identical on base and branch.)

### 4.6 The textual scan of the guard file (the self-reference trap)

```
textual scan of the guard file for the four forbidden sequences:
  'or True'    0
  'and True'   0
  'not True'   0
  'assert True' 0
  total token True: 17
```

The required report is **0 / 0 / 0 / 0**. (The base file measured 0 / 0 / 0 / 0 as
well, with 9 occurrences of the token `True`; the branch has 17 because the new
docstring, the failure message and the probe messages spell the literal more often -
never adjacent to a keyword on the same line.)

### 4.7 The reproduction: the four R-027 shapes

A scratch probe file `backend/tests/_tautology_probe_019_tmp.py` containing the four
R-027 shapes (one assert per shape, inside a function body) was created, the guard
was run, and the file was deleted again:

```
$ python -m pytest backend/tests/test_no_tautological_asserts.py -q
E  AssertionError: assert statements under backend/tests must not carry the boolean literal True in a boolean
position (an operand of a conjunction or a disjunction, or the operand of a negation):
E    ...\backend\tests\_tautology_probe_019_tmp.py:5: (x or True) and y
E    ...\backend\tests\_tautology_probe_019_tmp.py:6: not (x or True)
E    ...\backend\tests\_tautology_probe_019_tmp.py:7: x or (y or True)
E    ...\backend\tests\_tautology_probe_019_tmp.py:8: True
1 failed, 1 passed
EXIT=1
```

**Four violations, one per shape, exit code 1.** On the same tree without the probe
file the guard prints `2 passed`, exit 0. The probe file was deleted before the final
runs and is **not** in the commit (`git status --short` is empty).

### 4.8 Red-on-base / green-on-branch (the probes, then the rule)

The plan asks for the demonstration in the order *probe first, rule second*. It was
done that way on the branch:

- **Step A** - the new probes and the extended negative list were added while
  `_find_tautologies` still carried the **base** rule (top-level `or` + literal):

  ```
  $ python -m pytest backend/tests/test_no_tautological_asserts.py -q
  E  AssertionError: detector did not flag a conjunction whose left operand carries the boolean literal: []
  E  assert 0 == 1
  1 failed, 1 passed
  ```

  The self-test is **red on the base rule**.

- **Step B** - the new rule was applied; the same command printed `2 passed`.

The "remove the tautology / re-introduce it" property was also demonstrated
end-to-end (on a scratch copy of the guard file outside the repository, so the
running suite was never disturbed):

```
--- P1 tautology REMOVED (must fail) (exit 1) ---
E  AssertionError: detector did not flag a conjunction whose left operand carries the boolean literal: []
1 failed, 1 deselected
--- P1 tautology RESTORED (must pass) (exit 0) ---
1 passed, 1 deselected
```

### 4.9 The negative controls (do the replacement assertions have teeth?)

Two scratch control tests, written outside the repository and deleted afterwards:

- **Control A (site 1):** the same production call **without** the mocked read-back
  session, so the real verification session finds the two committed turns. Measured:
  **no warning record at all** - `assert "Verification failed" in caplog.text` is
  **False** there, while `len(turns) == 2` still holds. The site-1 assertion is
  therefore specific to the verification-failure branch and does not merely observe
  "a log happened".
- **Control B (site 2):** the same block with the goal row mutated
  (`status = "completed"`) before the comparison. Measured: the comparison
  **detected** the mutation (`goal_state() != before`). The selector ran and returned
  `selected_policy=DECOMPOSE_PLAN` (the vetoed `DELEGATED_PLAN` was correctly not
  selected) and the `objectives` row count stayed at 1.

Both controls passed (`2 passed`), which is what "the assertion has teeth" means.

### 4.10 Test ids of the touched files (no test added, removed or renamed)

```
$ python -m pytest backend/tests/test_conversation_memory.py \
                     backend/tests/test_phase12_planning_learning.py \
                     backend/tests/test_no_tautological_asserts.py --collect-only -q
master: 70 ids    branch: 70 ids    normalised id lists identical
```

---

## 5. What was NOT checked

- **The suite was not run under CI's pinned interpreter.** The local interpreter is
  CPython 3.14.7; CI pins 3.11 (`.github/workflows/ci.yml:19`). The change is
  interpreter-agnostic (AST only), but the 3.11 run was not executed.
- **The CI form of the whole suite was not run** (`cd backend && python -m pytest
  tests -q`). Only the guard module was run in that form (2 passed); the whole-tree
  run used the repository-root form the brief specifies.
- **The `network` / `provider_e2e` population never executes here** (no provider
  credential), so those 45 skipped tests are skipped in every number reported above,
  base and branch alike.
- **The guard was not run on a non-Windows checkout**; the BOM/`tokenize.open`
  handling is unchanged from the base file and was not re-verified on the eight
  BOM-carrying modules beyond the whole-tree scan's 0 parse errors.
- **The two replacement assertions were not verified against a mutated production
  tree.** The plan forbids touching production, so the "the pre-fix code would fail
  this test" direction is argued from the control measurements (control A: no
  warning without the failure branch; control B: a mutation is detected) rather than
  demonstrated by deleting the verification block from
  `backend/services/post_task_intelligence.py`.
- **`caplog` vs a patched module logger** was not tried; the plan specifies
  `caplog` and the fallback was not needed.
- **The full suite's warning count** is machine-specific and is not treated as a
  baseline (427247 on the branch vs 427202 on the base - the difference is the
  DeprecationWarnings the changed tests emit).
- **The native crash was not root-caused.** It is attributed by frame and by the
  pre-existing record in `.orchestra/goal.md` / `.orchestra/log.md`, not proved.
- **No push, no deploy, no merge to master, no board update.** The branch is local
  only (`git log master..HEAD` = one commit, unpushed).
- **The CogniGraph memory server was not available in this sub-agent session** (no
  `memory_*` tool is exposed in the tool catalogue), so no memory fact was written
  and no memory briefing was used. The plan and the brief were read directly.

---

## 6. Observations (not blockers)

- The comment block immediately above the site-1 replacement still ends with
  "(implicitly tested by no exception)", which the new assertions supersede. The
  plan states that the site-1 test's "name, id, docstring and every other line stay
  as they are", so that line was left untouched. It is a one-line comment fix inside
  an already-amended file if the board wants it.
- The plan quotes 24 for "`not` over an operand that is not vacuous"; the measured
  count is 145 (identical on base and branch). The number the plan requires to be
  exactly 0 - the violation count - is 0.
- The guard's own file is scanned by the tree scan on every run and currently has
  **0** hits under the new rule, so the guard cannot flag itself.
