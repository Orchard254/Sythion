# Brief: verify - 002 - attempt 1

- Item: 002 Makefile test selections and pytest configuration stop claiming what they do not do
- Role: verifier
- Goal (the slice that matters): Sythion's "better" includes **verification
  before claims** and **honest debt reporting** (`goal.md`,
  `docs/QUALITY_GATES.md:34-44` Gate 3 "Verification before claims", `:45-59`
  Gate 4 "Docs must reflect reality", `:88-97` Gate 8 "Testing baseline
  discipline"). The repository's test tooling must tell the truth. **You are
  the last gate before this reaches `master`.** Your job is to independently
  decide whether the tooling really tells the truth - not to re-run the
  implementer's and the reviewer's evidence and call it a day.

## Read

- `C:\Sythion\sythion\.orchestra\plans/002.md` - the plan (the contract).
- `C:\Sythion\sythion\.orchestra\backlog.md` - item 002's acceptance criteria.
- `C:\Sythion\sythion\.orchestra\reports/002-implement-1.md` - round 1's report.
- `C:\Sythion\sythion\.orchestra\reports/002-implement-2.md` - the redo's
  report, including the recovery dispatch's independent verification appended
  at the end.
- `C:\Sythion\sythion\.orchestra\reviews/002-r1.md` - the round-1 review (its
  three findings).
- `C:\Sythion\sythion\.orchestra\reviews/002-r2.md` - the round-2 review
  (`VERDICT: ACCEPT`).
- `C:\Sythion\sythion\.orchestra\critiques.md` - the orchestrator's critiques.
- The branch: `orchestra/002-test-selection-truth`, commits `0f27d93` and
  `cc03a7b`. Diff against `master`:
  `git -C C:\Sythion\sythion diff master...orchestra/002-test-selection-truth`
- The four files it touches: `pytest.ini`, `Makefile`,
  `backend/tests/conftest.py`, `backend/tests/acceptance/test_gui_fixture.py`.

## Write

- `C:\Sythion\sythion\.orchestra\verifications/002-v1.md` - the ONLY file you
  may write. Use `.orchestra/verification-template.md` as the shape.

**Your file's FIRST LINE must be exactly `VERDICT: FIXED` or `VERDICT: NOT
FIXED` or `VERDICT: REGRESSED`.** Nothing may precede it. **Your report must
also contain a line `- Verified commit: <sha>`** and a line `- Tier:
NORMAL|EXTERNAL|SENSITIVE|SENSITIVE+EXTERNAL`.

## Context

- Current status: item 002 is `accepted` pending your verification, then it
  goes to the Committer.
- Branch: `orchestra/002-test-selection-truth`, tip `cc03a7b`.
- Plan: `plans/002.md`.

### Where to work

The branch is **already checked out** at
`C:\Users\orcha\AppData\Local\Temp\opencode\wt-002`. Use it for inspection
and for running checks. Do NOT create another worktree for the same branch
(git will refuse). Do not commit or modify anything there - except a
throwaway probe file, which you must DELETE when you are done, and the root
`pytest.ini`, which you may rename away TEMPORARILY for the fallback check
and must then restore **byte-identically** (verify with `git hash-object`
against `HEAD:pytest.ini`, and confirm `git status --short` is empty).

### What has already been established - do NOT merely re-derive it

- The Reviewer closed all three round-1 findings **by reproduction** and
  re-measured everything itself:
  - `pytest.ini:46` says the soak workload is "NOT gated by an environment
    flag and runs in the default suite", and `backend/tests/test_phase115_soak.py`
    contains no `environ`, `getenv`, `skip` or `SYTHION` at all.
  - With the root `pytest.ini` renamed away: a FULL-TREE collection exits **4**
    with the documented message; `pytest --markers` shows none of the ten
    markers; a partial run exits 0; a MARKED partial run produces **0**
    unknown-mark warnings (so the fallback is live, not dead code).
  - In a scratch copy: an unregistered second live-GUI module exits 4 with a
    **true** message; the same module **registered** exits **0** with `gui`=5;
    the other direction and the missing-module direction are also true; the
    old phrase `"does not describe"` occurs **0** times.
  - Marker populations: `gui`=4, `network`=5 (node-for-node identical to
    `provider_e2e`), `acceptance`=193 (node-for-node identical to the path
    selection), `slow`=1, `flaky`/`integration`=0, `process_kill`=8,
    `concurrency`=53, `gated_soak`=1. Makefile selections: `test-fast`
    1599/10, `test-unit` 1416/193, `test-gui` 4, `test-network` 5,
    `test-acceptance` 193, all exit 0. `asyncio: mode=Mode.STRICT`; no
    effective `filterwarnings` and no `addopts`; zero
    `PytestUnknownMarkWarning` - against a base that emits exactly **3** and
    reports **no `configfile:` line**.
  - The pinned toolchain (pytest 7.4.4 / pytest-asyncio 0.23.3) in the exact
    CI shape (`cd backend; pytest tests`) gives 1609 collected and exit 0.
  - The merged tree (master + item 002) collects **1611**, exit 0, guard
    silent.
- **The `network` marker CANNOT cause a credentialed call** - the Reviewer ran
  the `network` selection and all 5 tests SKIPPED. Confirm.
- The Reviewer reproduced the 002/006 conftest conflict and applied the
  resolution the orchestrator had independently derived - keep both appended
  blocks plus the trailing newline - and verified the tree collects **1613**,
  exit 0.
- **THE ORCHESTRATOR'S OWN MERGE RECIPE, measured:** merge `007`, then
  `002`, then `006`; resolve the one conflict in `backend/tests/conftest.py` by
  keeping BOTH sides and adding the trailing newline. The result collects
  **1614** with each of the five blocks present exactly once and every guard
  intact.

### Pre-existing conditions - do NOT blame the branch for these

1. **A clean checkout has no `backend/.env`** (gitignored), so
   `backend/tests/test_setup_readiness.py::test_readiness_states` fails in any
   worktree. That is item 006's subject.
2. **The suite is not deterministic here** - 2 native crashes in 6 observed
   full-suite runs, of two different kinds; neither reproduces in isolation.
   A native crash in your run is pre-existing, not a regression - but you must
   RECORD it and re-run.
3. **`make` is not installed on this machine** - run the recipe commands
   verbatim and say so.
4. The plan's `1564 passed` baseline has moved to `1566` on current `master`
   (item 004 added two tests). Item 002 adds ZERO tests.

## What YOU must verify - the things nobody has yet

1. **THE ITEM'S CENTRAL CLAIM, checked by an independent route.** The claim
   is: *the test-selection tooling tells the truth.* Do not rely on the
   implementer's or the reviewer's evidence. Design YOUR OWN check. The
   strongest available shape is a **behavioural audit**: for every marker the
   configuration declares, enumerate the tests that actually carry it and
   compare that set against what the description claims - and check BOTH
   directions, because a description that is wrong in the "more than claimed"
   direction is as false as one wrong in the other. The `gui` population is
   the interesting case: the description names a registry, so verify that the
   registry and the population agree exactly.
2. **The guard really has teeth, and the probe is capable of failing.** The
   guard aborts a full-tree collection with exit 4 on drift. Show that (a) a
   real drift makes it abort, and (b) removing the drift makes it pass again
   - i.e. the check distinguishes "the guard works" from "the check is
   broken". A guard that has never been seen to fail is not a guard.
3. **The `network` marker still cannot cause a credentialed call.** Run the
   `network` selection and confirm every test SKIPS without a credential. Do
   NOT set `SYTHION_RUN_PROVIDER_E2E=1`.
4. **The CI shape stays green.** In the exact shape `.github/workflows/ci.yml`
   uses (`cd backend; pytest tests`), under the PINNED toolchain
   (pytest 7.4.4 / pytest-asyncio 0.23.3 - a throwaway venv exists at
   `C:\Users\orcha\AppData\Local\Temp\opencode\venv-pinned`), the suite must
   collect and exit 0. This is the cross-item risk: item 002 must not redden
   the pipeline item 001 repaired.
5. **The merged tree still works.** Confirm that merging item 002 into
   `master` yields a tree that collects cleanly with the guard silent, and
   that the four files item 002 touches are the ONLY files it touches - no
   production module under `backend/` outside `backend/tests/`, no
   `docs/`, no `.github/workflows/ci.yml`.
6. **Nothing was deleted, skipped, xfailed or marked to make the suite
   green.** Check the diff for any removal of a test, a skip, an `xfail` or a
   marker.

## Constraints

- **Read-only with respect to the branch.** The only file you may write is
  `verifications/002-v1.md`. Any probe file must live OUTSIDE the repository,
  or be deleted before you finish. If you rename the root `pytest.ini` aside,
  restore it byte-identically and verify with `git hash-object`.
- Do not write `follow-ups.md` - the orchestrator owns it.
- Never mark FIXED on something you did not inspect. State what you did not
  check.
- Do not push, deploy, publish, or touch real data. Do not run `make clean`,
  `scripts/stage_python_runtime.py`, migrations, or `npx tauri build`.
- Do not ask the user anything.

## Output contract

Write `C:\Sythion\sythion\.orchestra\verifications/002-v1.md`, first line
exactly `VERDICT: FIXED` or `VERDICT: NOT FIXED` or `VERDICT: REGRESSED`,
then:

```
- Verified commit: <sha>
- Tier: <NORMAL|EXTERNAL|SENSITIVE|SENSITIVE+EXTERNAL>

# Verification of 002

## Acceptance criteria
| # | Criterion | Result (met / not met / not verified) | Evidence |
(all six, from the backlog item)

## The central claim, verified independently
<your own behavioural audit: every declared marker, the set of tests that
 actually carry it, and the comparison with what the description claims, in
 both directions>

## The guard has teeth
<the before/after: drift aborts with exit 4, and removing the drift passes>

## The network marker cannot cause a credentialed call
## The CI shape stays green
## The merged tree still works
## Nothing was deleted, skipped, xfailed or marked
## Findings
## Not checked
<REQUIRED>
```

## Previous attempt (if any)

- None. This is verification round 1.
