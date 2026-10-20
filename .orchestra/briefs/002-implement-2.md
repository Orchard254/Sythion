# Brief: implement - 002 - attempt 2 (REDO)

- Item: 002 Makefile test selections and pytest configuration stop claiming what they do not do
- Role: implementer (implementer-3) - redo round 1
- Goal (the slice that matters): Sythion's "better" includes **verification
  before claims** and **honest debt reporting** (`goal.md`,
  `docs/QUALITY_GATES.md:34-44` Gate 3, `:45-59` Gate 4 "Docs must reflect
  reality", `:88-97` Gate 8). The point of this item is that the repository's
  test tooling tells the truth. A comment in that tooling that is false, and a
  guard that fires with a misleading message, are the SAME defect the item
  exists to remove - just moved.

## Read

- `C:\Sythion\sythion\.orchestra\reviews/002-r1.md` - **THE REVIEW. Read it
  fully.** It is the list of what to fix. `VERDICT: IMPROVE`.
- `C:\Sythion\sythion\.orchestra\plans/002.md` - the plan you implemented.
- `C:\Sythion\sythion\.orchestra\backlog.md` - item 002's six acceptance
  criteria.
- `C:\Sythion\sythion\.orchestra\reports/002-implement-1.md` - your own
  report for attempt 1.
- The four files you changed: `pytest.ini`, `Makefile`,
  `backend/tests/conftest.py`, `backend/tests/acceptance/test_gui_fixture.py`.

## Write

- The same four files, plus whatever you need within them.
- `C:\Sythion\sythion\.orchestra\reports/002-implement-2.md` - your evidence
  report for this round (same shape as attempt 1).
- Your commits, on the same branch.

## Context

- Current status: item 002 is `needs-improvement`. The Reviewer's verdict is
  `VERDICT: IMPROVE` and the review found three statements in the artifacts
  that are supposed to be true, which the repository contradicts. This is
  round 1 of a maximum 2.
- Branch: **`orchestra/002-test-selection-truth`** - the SAME branch you
  already pushed to. Commit on top of your existing `0f27d93`; do not start
  a new branch, do not rewrite the existing commit.
- Plan: `plans/002.md` (unchanged - the plan was accepted; the findings are
  about the implementation and the comments).

### What you must fix - the three findings, in the Reviewer's order

**1. `pytest.ini` - the `gated_soak` description is false.**
The description says "long-running soak tests, gated behind an explicit
environment flag". The only test carrying the marker
(`backend/tests/test_phase115_soak.py:15`, `pytestmark = pytest.mark.gated_soak`)
has **no gate at all** - no `skipif`, no environment check - so it runs in
every default suite. The Reviewer measured it: `1 passed`. The orchestrator
confirmed it by reading the file.
This is exactly the defect the item exists to remove: a description that
claims a property the code does not have. Either make the description true
(it is not gated - say what it actually is), or gate the test (which is a
behaviour change to a test and must be justified - the plan's rule "the
suite's behaviour is unchanged apart from the intended selection fixes" may
argue against it). **Decide, justify it in the report, and make the text and
the code agree.**

**2. `backend/tests/conftest.py` - the fallback comment promises something the
guard prevents.**
The comment at `:32-35` says the in-file marker declarations are "only a
fallback, so the suite still runs - and still fails loudly on an unknown
marker - when it is run without that file". The Reviewer measured that
without the root `pytest.ini` the suite **aborts** instead of running, and
that `pytest --markers` shows none of the ten markers.
**Reproduce it yourself** - the Reviewer's method, and the orchestrator's
partial reproduction, are:
- the guard only fires on a FULL-TREE collection, so a partial run
  (`pytest backend/tests/test_work_state.py --collect-only`) exits 0 even
  without the ini - that part of the Reviewer's finding does NOT reproduce on
  a partial run, so check the full tree:
  `python -m pytest backend/tests --collect-only -q` with the root
  `pytest.ini` temporarily renamed away. Restore the file afterwards and
  confirm `git status` in the worktree is clean.
Then decide: either make the fallback real (the suite runs without the ini,
and the marker declarations in the conftest are genuinely used), or make the
comment honest (the ini is REQUIRED, the conftest declarations are a
declaration-only fallback, and the guard enforces that). **Whichever you
choose, the comment and the behaviour must agree.** Also check the second
half of the Reviewer's finding - that `pytest --markers` shows none of the ten
markers without the ini - and explain WHY in the report, because it decides
whether the fallback is doing anything at all.

**3. `backend/tests/conftest.py` - the `gui` expectation is hard-coded to one
filename, and the failure message is false.**
`:217` computes `expected_gui` as the items whose file path equals
`_NORM_GUI_MODULE` - one hard-coded path. The consequence: a legitimate
SECOND live-GUI module that declares `pytestmark = pytest.mark.gui` aborts
the suite with the message "marker 'gui' is applied to N test(s) it does not
describe" - which is **false**: those tests DO describe live-GUI work, they
simply are not the one hard-coded module. A guard that fires with a
misleading message trains people to ignore it.
**Reproduce it** (the Reviewer did: add a second module with
`pytestmark = pytest.mark.gui` in a scratch copy, or reason it through and
show the message), then fix it so that the check expresses the property it
means to express. The property that matters is: **`gui` marks exactly the
tests that need a live interactive desktop, and no test carries `gui` by
accident.** Decide how to express that without hard-coding a filename, and
make the violation message state the truth in both directions (a test that
carries `gui` without opting in, and a module that needs a live desktop but
does not carry it).

### The Reviewer also raised two nits - judge them yourself

- The `concurrency` marker description is module-level, while the marker is
  applied per test (53 times). Decide whether the description is accurate.
- `check-test-selections` is not wired into CI. `ci.yml` is item 001's file
  and item 001 is MERGED - do not edit it. Say in your report what the
  integration should be, and leave it to the orchestrator to queue.

### The Reviewer also confirmed, independently, that the rest of the item holds

- Marker populations: `gui`=4 (exactly `test_gui_fixture.py`), `network`=5
  (identical to `provider_e2e`), `acceptance`=193, `slow`=1, `flaky`/`integration`=0,
  `process_kill`=8, `concurrency`=53, `gated_soak`=1.
- The Makefile claims are true: `test-fast` -> 1599 selected / 10 deselected
  (1 slow + 4 gui + 5 network), `test-unit` -> 1416/193, `test-gui` -> 4,
  `test-network` -> 5, `test-acceptance` -> 193. All exit 0.
- `pytest.ini` changes no behaviour: 1609 collected before and after,
  `asyncio: mode=Mode.STRICT`, no `filterwarnings` or `addopts` anywhere in
  the tree, zero `PytestUnknownMarkWarning` in every run.
- The `network` marker cannot cause a credentialed call: all 5 tests are
  gated by `SYTHION_RUN_PROVIDER_E2E=1`.
- The guard is silent on the clean tree, silent on partial runs (57 collected,
  exit 0), fires with exit 4 on drift, and tolerates a new plain acceptance
  module.
- **The cross-item risk the orchestrator had cleared is confirmed in the exact
  CI shape**: `cd backend; pytest tests` -> 1609 collected, exit 0 under the
  PINNED toolchain (pytest 7.4.4 / pytest-asyncio 0.23.3), so the guard does
  not redden the pipeline item 001 repaired.
- `make` is genuinely unavailable on this machine (no binary, no WSL) - the
  Reviewer could not do better than the recipe commands verbatim either.

**Do not regress any of that.** The six acceptance criteria must still hold
after your fix, and the suite must still be `1564 passed / 45 skipped` with
`1609` collected (or `1611` once item 004's two guard tests are on the base
- report whichever applies and say why).

## Constraints

- Work only on the assigned item and the existing branch. No unrelated edits.
- Commit on top of `0f27d93`; do not rewrite history, do not rebase, do not
  amend the existing commit.
- **Never push.** Never deploy, publish, or touch real data.
- **Never run destructive commands:** `rm -rf`, `git reset --hard`,
  `git clean -fd`, `git checkout -- .`, `git stash drop`, force-push.
- Do not edit `.github/workflows/ci.yml` (item 001's file, merged),
  `backend/requirements.txt`, `backend/requirements.lock.txt`, any runtime
  module under `backend/` outside `backend/tests/`, or `docs/`.
- Do not run `make clean`, `scripts/stage_python_runtime.py`, migrations, or
  `npx tauri build`.
- **Known hazard:** the suite is not deterministic on this machine (2 native
  crashes in 6 observed full-suite runs, of two different kinds; neither
  reproduces in isolation; the smallest reproducer is the FULL SUITE). If a
  run dies that way, re-run it and record BOTH attempts - it is a pre-existing
  condition, not your failure.
- Do not ask the user anything.

## Output contract

Write `C:\Sythion\sythion\.orchestra\reports/002-implement-2.md`:

```
# Report: implement - 002 - attempt 2

## What I did
<the exact commands you ran, the commits (sha + message)>

## Evidence
<every claim with its evidence: command + output, file:line>

## The three findings
| # | Finding | What I changed | Evidence that it is now true |
|---|---|---|---|
| 1 | gated_soak description | ... | ... |
| 2 | fallback comment vs behaviour | ... | ... |
| 3 | hard-coded gui expectation | ... | ... |

## Acceptance criteria
| # | Criterion | Result | Evidence |
|---|---|---|---|
| 1 | ... | ... | ... |
(all six, re-checked after the fix)

## The two nits
<what you decided about each>

## Findings
## Not checked
<REQUIRED>
```

- **Paste the ACTUAL command output.** A claim without output is not a claim.
- For finding 2, paste the BEFORE and AFTER behaviour: the full-tree
  collection with the ini hidden, before your fix and after it.
- For finding 3, paste the reproduction of the false message and then the
  result after your fix.
- If a criterion or a finding is not fixed, say so plainly. Do not restate a
  finding as if it were fixed.
- Reply with a short summary: the commits, the three findings, and the
  criteria results. The FILE is the deliverable.

## Previous attempt (if any)

- Attempt 1 (`reports/002-implement-1.md`) was ACCEPTED by the orchestrator and
  then the REVIEWER returned `VERDICT: IMPROVE` on three findings. The
  orchestrator independently confirmed findings 1 and 3 from the source, and
  is still confirming finding 2's full-tree reproduction when this brief was
  written. Address every finding, or explain in writing why you did not.
