# Brief: implement - 002 - attempt 1

- Item: 002 Makefile test selections and pytest configuration stop claiming what they do not do
- Role: implementer (implementer-3)
- Goal (the slice that matters): Sythion's "better" includes **verification
  before claims** and **honest debt reporting** (`goal.md`,
  `docs/QUALITY_GATES.md:34-44` Gate 3, `:45-59` Gate 4 "Docs must reflect
  reality", `:88-97` Gate 8 "Testing baseline discipline"). Today a developer
  who runs `make test-fast` runs the entire suite minus one test;
  `make test-gui` and `make test-network` select nothing at all; and two
  markers that are applied to real tests are never declared. Every future agent
  who trusts those commands is wrong about the repository. Your job: make the
  test tooling tell the truth, and prove it.

## Read

- `C:\Sythion\sythion\.orchestra\plans\002.md` - **THE PLAN. Read it fully
  first.** It is the contract: the four files that change and exactly how, the
  decision the brief required (markers get APPLIED, no Makefile target is
  removed), the matching rules, the reproduction, the restated acceptance
  criteria, the risks, and the five open questions WITH their defaults.
- `C:\Sythion\sythion\.orchestra\backlog.md` - item 002's six acceptance
  criteria.
- `C:\Sythion\sythion\.orchestra\goal.md` - the goal, the off-limits list, the
  baseline notes.
- `C:\Sythion\sythion\.orchestra\reports\baseline-001.md` - the orchestrator's
  **measured** baseline. This is your before-picture, and it is measured, not
  remembered.
- `C:\Sythion\sythion\Makefile` - every target.
- `C:\Sythion\sythion\backend\tests\conftest.py` - the marker declarations and
  the shared fixtures.
- `C:\Sythion\sythion\backend\tests/acceptance/` - the acceptance sub-suite
  and its own `conftest.py`.
- `C:\Sythion\sythion\backend\requirements.txt` and
  `backend/requirements.lock.txt` - read only; the test-tooling pins matter to
  the plan's `asyncio_mode` reasoning.

## Write

- The four files the plan names: `pytest.ini` (new, repo root), `Makefile`,
  `backend/tests/conftest.py`, `backend/tests/acceptance/test_gui_fixture.py`.
- `C:\Sythion\sythion\.orchestra\reports\002-implement-1.md` - your evidence
  report (shape below).
- Your commits, on the branch below.

**Do not write anywhere else.** In particular: not `docs/TESTING.md` (item 003
owns it), not `backend/requirements.txt`, not `backend/requirements.lock.txt`,
not any runtime module under `backend/` outside `backend/tests/`, not
`.github/` (item 001 owns it), not `backend/tests/test_phase105_concurrency.py`,
`test_phase1075_hardening.py` or `test_phase125_planning_validation.py`
(item 004 owns them), not `follow-ups.md` (the orchestrator writes that).

## Context

- Current status: item 002 is `planned` in `backlog.md` and `board.json`.
- Branch: **`orchestra/002-test-selection-truth`**, branched from `master`.

### Working directory - USE A DEDICATED WORKTREE

Two other Implementers are working at the same time (items 001 and 004). You
must not share a working tree:

```powershell
cd C:\Sythion\sythion
git worktree add C:\Users\orcha\AppData\Local\Temp\opencode\wt-002 -b orchestra/002-test-selection-truth master
cd C:\Users\orcha\AppData\Local\Temp\opencode\wt-002
```

Work ONLY inside that worktree. `C:\Sythion\sythion` is the orchestrator's
tree - do not check out branches there, do not commit there. Write your report
to the **main tree's** `.orchestra\reports\002-implement-1.md` (absolute path
below), not to the worktree's copy. The checkout takes a couple of minutes
(~4,200 tracked files in the vendored runtime).

### Facts already established - do not re-derive them

From the orchestrator's measured baseline (`reports/baseline-001.md`):

- `python -m pytest backend/tests --collect-only -q` -> **1609** collected.
- Full suite: `1564 passed, 45 skipped`, exit 0, wall time **389-436 s** across
  two clean runs. (A third run died with a native access violation - see the
  hazard below.)
- Acceptance suite: `191 passed, 2 skipped`.
- `-m gui`, `-m network`, `-m acceptance` each collect **0** of 1609.
  `-m "not acceptance"` collects all 1609. `-m "not slow and not flaky and not
  integration and not gui and not network"` collects 1608 (deselects exactly
  the one `slow`-marked test,
  `test_phase105_concurrency.py::TestFairnessSlotAllocation::test_long_running_fairness`).
- Exactly **3** `PytestUnknownMarkWarning`s: `concurrency` at
  `test_phase105_concurrency.py:39`, `concurrency` at
  `test_phase1075_hardening.py:35`, `gated_soak` at `test_phase115_soak.py:15`.
- `backend/tests/conftest.py:31-47` declares eight markers: `slow`, `flaky`,
  `integration`, `acceptance`, `gui`, `network`, `process_kill`,
  `provider_e2e`.
- pytest 8.4.2's header prints `rootdir: C:\Sythion\sythion` and **no
  `configfile:` line**; `asyncio: mode=Mode.STRICT`; pytest-asyncio 0.23.8.
- The `provider_e2e` tests carry
  `@pytest.mark.skipif(os.environ.get("SYTHION_RUN_PROVIDER_E2E") != "1")`, so
  they SKIP unless that variable is set. **`make test-network` must not make
  credentialed provider calls.** Keep that property.

### IMPORTANT - criterion 4's absolute number may legitimately move

Criterion 4 says the collected-test count must be "identical before and after
the change (1609 ... at the time of the change)". Item 004 - implemented in
parallel - ADDS TWO TESTS (its guard module) to `backend/tests/`, so if item
004 is merged into `master` before yours, the base you rebase onto will
collect **1611**, not 1609.

The invariant the criterion is really about is: **your change does not alter
what the suite collects.** So:
- Measure the "before" count **on your own branch's base**, and the "after"
  count **on your branch** - same base, same interpreter, back to back.
- If the base has moved to 1611 because item 004 landed, that is CORRECT and
  expected. Report both numbers and the base commit you used. Do not "fix" it
  by changing a selection, and do not hardcode 1609 anywhere.
- The Committer rebases your branch onto the current `master` before merging.
  If that rebase changes the collected count, the Verifier will re-measure.
  Say clearly in your report what the count is on your branch.

### Known hazard - the suite is not deterministic on this machine

Across six observed full-suite runs there were **2 native crashes of two
different kinds**:
- a BURST of COM faults (277x `0x80040155`, 18x `0x8001010d`) on the
  `sythion-uia-mta` thread in `_UIADriver._find_window`
  (`backend/tools/gui_tools.py:417`/`:426`) - this one does NOT kill the
  process;
- `Windows fatal exception: access violation` (`0xC0000005`) inside native
  sqlite3, in the two `tfn` threads of
  `test_phase105_concurrency.py::TestBudgetAtomicity::test_concurrent_budget_does_not_exceed`
  -> `backend/agents/coordination.py:1127` - this one DOES kill the
  interpreter with no pytest summary.

Neither reproduces in isolation (25/25 and 30/30 single-test runs were clean);
the smallest reproducer is the FULL SUITE. If your full-suite run dies that
way: **re-run it**, record BOTH attempts verbatim, and report the crash as a
pre-existing condition on `master` - not as your failure. Do not try to fix it.
Do not report it as your failure either.

## Constraints

- Work only on the assigned item and branch. No unrelated edits.
- Commit small and often with clear messages.
- **Never push.** Never deploy, publish, or touch real data.
- **Never run destructive commands:** `rm -rf`, `git reset --hard`,
  `git clean -fd`, `git checkout -- .`, `git stash drop`, force-push, or
  anything that deletes files git does not track.
- Do not run `make clean` (it deletes directories). Do not run
  `scripts/stage_python_runtime.py` (it `rmtree`s the committed runtime). Do
  not run migrations against `backend/sythion.db` or
  `%LOCALAPPDATA%\Sythion (Synth)`. Do not run `npx tauri build`.
- **Do not run the no-argument `python -m pytest` collection on `master`** - it
  walks the whole root directory and would import the vendored runtime's
  third-party test modules. The plan says this; the plan is right.
- **No warning-to-error filter.** The root configuration must contain NO
  `filterwarnings` setting at all, in particular no
  `filterwarnings = error::DeprecationWarning`. The suite emits a large number
  of `datetime.utcnow()` DeprecationWarnings; turning them into errors would
  redden the whole run and is explicitly out of scope.
- **`asyncio_mode` must be `strict`** - the mode the header already reports
  today. Changing it would change how the suite behaves.
- The conftest guard you add must be a NO-OP on the merged tree (the suite
  exits 0) and you must DEMONSTRATE that it fires: temporarily mis-mark a
  test, show the guard aborting with `ERROR:` and a non-zero exit, then undo
  the mis-marking and show the suite green again. Record both outputs.
- The `pytest_itemcollected` mechanism is load-bearing: pytest's marker
  deselection is `tryfirst`, so markers added in
  `pytest_collection_modifyitems` are INVISIBLE to `-m`. Apply the rule in
  `pytest_itemcollected` as the plan specifies. If you change this, every
  `-m` selection in the item silently selects nothing.
- The `network` marker must not cause credentialed provider calls. The
  `provider_e2e` tests must keep skipping unless `SYTHION_RUN_PROVIDER_E2E=1`.
- Do not add any file other than `pytest.ini`.
- Do not ask the user anything. If something needs a decision you cannot
  make, say so in the report and stop.

## Output contract

Write `C:\Sythion\sythion\.orchestra\reports\002-implement-1.md` with this
shape (the template is at `.orchestra/report-template.md`):

```
# Report: implement - 002 - attempt 1

## What I did
<the exact commands you ran, the commits you made (sha + message), the files you touched, the base commit you branched from>

## Evidence
<every claim with its evidence: command + output, file:line, test name>

## Acceptance criteria
| # | Criterion | Result | Evidence |
|---|---|---|---|
| 1 | ... | ... | ... |
(1..6, from the backlog item)

## Marker populations after the change
| Marker | Declared where | Applied to (files) | Collected count | Exit code of `-m <marker>` |
|---|---|---|---|---|
(10 rows - one per marker)

## The Makefile selections
| Target | Marker expression | Collected | Deselected |
|---|---|---|---|
(4 rows)

## Findings
<anything surprising; anything the Reviewer or Verifier needs to know>

## Not checked
<REQUIRED. What you did not verify, and why.>
```

- **Paste the ACTUAL command output.** A claim without output is not a claim.
- For each of the six criteria, run the plan's own check command and paste the
  output. If a criterion is not met, say so plainly - never restate the
  criterion as if it were met.
- Include the before/after full-suite numbers side by side, with the base
  commit you used, and report the warning totals for both runs.
- Record any file you touched outside the four named ones, and why.
- State what you did not check.
- Reply with a short summary: the commits, the criteria results, the marker
  populations. The FILE is the deliverable.

## Previous attempt (if any)

- None. This is attempt 1.
