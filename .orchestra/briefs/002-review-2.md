# Brief: review - 002 - attempt 2 (review round 2)

- Item: 002 Makefile test selections and pytest configuration stop claiming what they do not do
- Role: reviewer (reviewer-002, round 2 of a maximum 2)
- Goal (the slice that matters): Sythion's "better" includes **verification
  before claims** and **honest debt reporting** (`goal.md`,
  `docs/QUALITY_GATES.md:34-44` Gate 3, `:45-59` Gate 4, `:88-97` Gate 8). The
  repository's test tooling must tell the truth. Your job: judge whether the
  redo round's fix genuinely closes the three findings of review round 1, and
  whether it kept everything round 1 established.

## Read

- `C:\Sythion\sythion\.orchestra\reviews/002-r1.md` - **THE ROUND-1 REVIEW.**
  Its three findings are the contract for this round. Read it fully.
- `C:\Sythion\sythion\.orchestra\reports/002-implement-2.md` - the redo
  round's report, including the recovery dispatch's independent verification
  appended at the end.
- `C:\Sythion\sythion\.orchestra\plans/002.md` - the plan.
- `C:\Sythion\sythion\.orchestra\backlog.md` - item 002's six criteria.
- The branch: `orchestra/002-test-selection-truth`, commits `0f27d93` (round 1)
  and `cc03a7b` (the redo).
- The four files: `pytest.ini`, `Makefile`, `backend/tests/conftest.py`,
  `backend/tests/acceptance/test_gui_fixture.py`.

## Write

- `C:\Sythion\sythion\.orchestra\reviews/002-r2.md` - the ONLY file you may
  write. Use `.orchestra/review-template.md` as the shape.

**Your review file's FIRST LINE must be exactly `VERDICT: ACCEPT` or
`VERDICT: IMPROVE`.** Nothing may precede it. **Your report must also contain
a line `- Reviewed commit: <sha>`** and a line `- Tier:
NORMAL|EXTERNAL|SENSITIVE|SENSITIVE+EXTERNAL` from
`python C:\Users\orcha\.config\opencode\skills\dev-orchestrator\scripts\classify.py
master...orchestra/002-test-selection-truth`.

## Context

- Current status: item 002 is `in-review`; this is review round 2 of a
  maximum 2. The round-1 verdict was `VERDICT: IMPROVE` with three findings.
- Branch: `orchestra/002-test-selection-truth`, based on `662cbe3`.
- Plan: `plans/002.md`.

### The three findings the redo had to close

1. **`pytest.ini`'s `learning_soak` description claimed a gate the test does not
   have.** The redo rewords it to what the code does: "despite the name it is
   NOT gated by an environment flag and runs in the default suite", and also
   rewords the `concurrency` description to say how the marker is applied and
   where.
2. **`backend/tests/conftest.py`'s fallback comment promised the suite "still
   runs" without the root `pytest.ini`; it does not.** The redo KEPT the
   behaviour (the ini stays REQUIRED - relaxing it would give up the
   Reviewer's own pinned invariant that the repository has exactly ONE place
   where the selection contract lives) and made the comment TRUE: the real
   scope of the fallback, that the ini is required, that a full-tree collection
   without it aborts, and why `pytest --markers` shows none of the ten
   markers.
3. **The `gui` expectation was hard-coded to one filename, so a legitimate
   second live-GUI module aborted the suite with a FALSE message.** The redo
   drives it from a `LIVE_GUI_MODULES` registry.

### Facts already established - do not re-derive them

- The implementer's report and the recovery dispatch's independent
  verification both re-measured everything, and the numbers agree with the
  round-1 Reviewer's:
  - Marker populations: `gui`=4, `network`=5 (identical to `provider_e2e`),
    `acceptance`=193, `slow`=1, `flaky`/`integration`=0, `process_kill`=8,
    `concurrency`=53, `learning_soak`=1.
  - Makefile selections: `test-fast` -> 1599 selected / 10 deselected,
    `test-unit` -> 1416/193, `test-gui` -> 4, `test-network` -> 5,
    `test-acceptance` -> 193. All exit 0.
  - `1609` collected; `configfile: pytest.ini`; `asyncio: mode=Mode.STRICT`;
    zero `PytestUnknownMarkWarning`; no effective `filterwarnings` and no
    `addopts` anywhere.
  - The pinned toolchain (pytest 7.4.4 / pytest-asyncio 0.23.3) in the exact
    CI shape (`cd backend; pytest tests`) gives 1609 collected and exit 0, so
    the guard cannot redden the pipeline item 001 repaired.
  - All four fallback states were reproduced with the ini renamed away:
    full-tree collection -> **exit 4** with the guard's message;
    `pytest --markers` -> none of the ten markers; a partial run -> exit 0;
    a MARKED partial run -> 0 `PytestUnknownMarkWarning` (so the fallback is
    real, not dead code).
  - The `gui` fix fires in both directions: with a module REGISTERED in
    `LIVE_GUI_MODULES` the suite no longer aborts (exit 0, `gui`=5), and the
    unregistered case's message is now the true one ("not registered as a
    live-GUI module - add it to LIVE_GUI_MODULES").

### THREE NUMBERS THE ORCHESTRATOR CORRECTED - judge the corrected versions

The implementer reported `1609` collected. That is the count **at the branch
base `662cbe3`**. The orchestrator has since established:

- **Current `master` collects 1611** (item 004 added two tests). The
  implementer's own merge simulation - master's tree plus item 002's four
  files - gives **1611 collected, exit 0, guard silent**, `gui`=4,
  `test-fast` 1601/10.
- So the numbers the branch produces and the numbers the MERGED tree produces
  differ by exactly item 004's two tests. **Judge the fix against the merged
  tree's behaviour**, and treat the base-vs-master difference as expected, not
  as a defect.
- A full suite in a clean worktree is **1 failed, 1563 passed, 45 skipped** -
  and the single failure is the pre-existing item-006 `backend/.env` defect
  (the control run in the orchestrator's own checkout, where `.env` exists,
  gives 8 passed). That failure is NOT item 002's.

### Pre-existing conditions - do NOT blame the branch for these

- **A clean checkout has no `backend/.env`** (gitignored), so
  `test_setup_readiness.py::test_readiness_states` fails in any worktree.
  That is item 006's subject.
- **The suite is not deterministic here** - 2 native crashes in 6 observed
  full-suite runs, of two different kinds; neither reproduces in isolation.
- **`make` is not installed on this machine** (no binary, no WSL), so the
  Makefile targets cannot be executed as such - run the recipe commands
  verbatim and say so.

## The questions you must answer

1. **Are all three findings genuinely closed?** Judge each one yourself, by
   reproduction, not by reading the report. For finding 2, rename the root
   `pytest.ini` away TEMPORARILY, run the FULL-TREE collection, capture the
   exit code and the guard's message, then RESTORE the file and verify
   `git status` is clean. For finding 3, verify a second live-GUI module no
   longer aborts the suite and that the violation message is true in BOTH
   directions.
2. **Is the round-1 fix still intact?** Re-verify the marker populations, the
   Makefile selections, the 1609/1611 collected counts, `asyncio: mode=STRICT`,
   the absence of `filterwarnings` and `addopts`, and zero
   `PytestUnknownMarkWarning`. Report the numbers you measured.
3. **Did the redo break anything else?** The redo touched four files. Look for
   collateral damage, especially in the guard's behaviour and in the
   interaction with the root `pytest.ini` that item 002 itself introduced.
4. **The `network` marker must not be able to cause a credentialed call.**
   All 5 `network` tests are gated by `SYTHION_RUN_PROVIDER_E2E=1`. Verify
   that is still true after the redo.
5. **Interaction with the other in-flight items.** Item 006 also edits the
   ROOT `backend/tests/conftest.py`, and the orchestrator has CONFIRMED that
   items 002 and 006 CONFLICT at that file's tail - the cause is that
   master's file ends WITHOUT a trailing newline, so both branches modify the
   same final line. **Item 002 does not have to fix this** (the Committer
   resolves it at merge time), but you SHOULD confirm the conflict's exact
   shape and that the resolution is the obvious one (keep both appended
   blocks, add the trailing newline). Report what you find so the Committer
   is not surprised.
6. **The two nits the round-1 Reviewer raised** - report your view on each:
   - the `concurrency` marker is applied per test (53 times) while the marker
     itself is declared at module level (the redo's description now says so);
   - `check-test-selections` is not wired into CI. `ci.yml` is item 001's
     file and item 001 is MERGED - do not suggest editing it as part of this
     item. Say what the integration should be.
7. **Scope discipline.** Confirm the diff touches only the four named files,
   that no production module under `backend/` outside `backend/tests/` was
   touched, and that `docs/` and `.github/workflows/ci.yml` were not touched.

## Constraints

- **Read-only.** Never edit code. The only file you may write is
  `reviews/002-r2.md`.
- Do not trust the implementer's claims. Look at the diff and reproduce.
- For each acceptance criterion, state met / not met / **not verified**, with
  evidence. "Not verified" is an honest answer; "met" without evidence is not.
- Do not write `follow-ups.md` - the orchestrator owns it. List anything you
  would queue in your report instead.
- Never mark PASS on something you did not inspect. State what you did not
  check.
- Do not push, deploy, publish, or touch real data. Do not run `make clean`,
  `scripts/stage_python_runtime.py`, migrations, or `npx tauri build`.
- Do not ask the user anything.

## Output contract

`C:\Sythion\sythion\.orchestra\reviews/002-r2.md`, first line exactly
`VERDICT: ACCEPT` or `VERDICT: IMPROVE`, then:

```
- Reviewed commit: <sha>
- Tier: <NORMAL|EXTERNAL|SENSITIVE|SENSITIVE+EXTERNAL>

# Review of 002 round 2

## The three findings
| # | Finding | Closed? | Evidence that it is now true |
|---|---|---|---|
| 1 | learning_soak description | yes / no | ... |
| 2 | fallback comment vs behaviour | yes / no | ... |
| 3 | gui expectation | yes / no | ... |

## Acceptance criteria
| # | Criterion | Result (met / not met / not verified) | Evidence |
(all six)

## Marker populations and Makefile selections
<the numbers YOU measured>

## Interaction with item 006
## The two nits
## Goal fit
## Sensitivity
## Findings (most important first)
1. <file:line> - problem - what to change

## Deferred
## Needs human?
## Not checked
```

## Previous attempt (if any)

- Round 1: `reviews/002-r1.md` - `VERDICT: IMPROVE` with the three findings
  above. Round 2 is the last review round; if you cannot clear a finding,
  say precisely what remains and why.
