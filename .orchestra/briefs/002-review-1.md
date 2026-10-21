# Brief: review - 002 - attempt 1

- Item: 002 Makefile test selections and pytest configuration stop claiming what they do not do
- Role: reviewer
- Goal (the slice that matters): Sythion's "better" includes **verification
  before claims** and **honest debt reporting** (`goal.md`,
  `docs/QUALITY_GATES.md:34-44` Gate 3, `:45-59` Gate 4 "Docs must reflect
  reality", `:88-97` Gate 8 "Testing baseline discipline"). A developer who
  runs `make test-fast` today runs the entire suite minus one test, and
  `make test-gui` / `make test-network` select nothing at all. Your job: judge
  the DIFF against the plan and the criteria - not to re-run the whole
  verification, which is the Verifier's job next.

## Read

- `C:\Sythion\sythion\.orchestra\plans\002.md` - the plan the implementation
  was supposed to follow, including its decision that markers get **applied**
  rather than Makefile targets removed, and its two stated findings about
  pytest: that marker deselection is `tryfirst` (so a marker added in
  `pytest_collection_modifyitems` is invisible to `-m`) and that `pytestmark`
  in a `conftest.py` does not mark that directory's tests.
- `C:\Sythion\sythion\.orchestra\backlog.md` - item 002's six acceptance
  criteria.
- `C:\Sythion\sythion\.orchestra\reports/002-implement-1.md` - the
  implementer's evidence.
- `C:\Sythion\sythion\.orchestra\critiques.md` - the orchestrator's critique
  of the implementer's report (ACCEPT), and the check it adds that the
  implementer could not make.
- The branch itself: `orchestra/002-test-selection-truth`, diff against
  `master`: `git -C C:\Sythion\sythion diff master...orchestra/002-test-selection-truth`
- The four files it touches: `pytest.ini` (new), `Makefile`,
  `backend/tests/conftest.py`, `backend/tests/acceptance/test_gui_fixture.py`.

## Write

- `C:\Sythion\sythion\.orchestra\reviews/002-r1.md` - the ONLY file you may
  write. Use `.orchestra/review-template.md` as the shape.

**Your review file's FIRST LINE must be exactly `VERDICT: ACCEPT` or
`VERDICT: IMPROVE`.** Nothing may precede it - no heading, no comment, no
blank line. **Your report must also contain a line `- Reviewed commit:
<sha>`** naming the commit you reviewed, and a line `- Tier:
NORMAL|EXTERNAL|SENSITIVE|SENSITIVE+EXTERNAL` giving the classifier's tier for
`master...orchestra/002-test-selection-truth` (run
`python C:\Users\orcha\.config\opencode\skills\dev-orchestrator\scripts\classify.py
master...orchestra/002-test-selection-truth` from the repo root and report what
it says).

## Context

- Current status: item 002 is `building`; the implementer has reported; you
  are the next stage.
- Branch: `orchestra/002-test-selection-truth`, commit `0f27d93`.
- Base branch: `master`.
- Plan: `plans/002.md`.
- Previous findings: the plan's decision was that markers get applied, no
  target removed: `acceptance` by path (the 24-module acceptance directory),
  `gui` to the one module that needs a live desktop, `network` to the
  `provider_e2e` tests **via a rule**, so that item 004's three files stay
  untouched.

### Where to work

The branch is **already checked out** at
`C:\Users\orcha\AppData\Local\Temp\opencode\wt-002`. Use it read-only for
inspection and for running checks. Do NOT create another worktree for the same
branch (git will refuse). Do not commit or modify anything there.

### Facts already established - do not re-derive them

- The diff is exactly four files, one commit `0f27d93`: `pytest.ini` (new),
  `Makefile`, `backend/tests/conftest.py`,
  `backend/tests/acceptance/test_gui_fixture.py`. No runtime module touched.
- `pytest.ini` declares all ten markers with descriptions that state each
  marker's actual population, sets `testpaths = backend/tests` and
  `asyncio_mode = strict`, and contains **no `filterwarnings` and no
  `addopts`**.
- The Makefile states what each target SELECTS, and adds
  `check-test-selections`, which runs each `-m` expression as a `--collect-only`
  pass so an empty selection fails the target (pytest exits 5) instead of
  passing silently.
- The conftest applies its two rule-based classifications in
  `pytest_itemcollected` - the hook that the plan identified as the only one
  where a marker is visible to `-m` - and the selection-contract guard fires
  only on a FULL-TREE collection, raising `pytest.UsageError` (exit 4) on
  drift. The implementer demonstrated the guard firing (exit 4) and being a
  no-op (exit 0) after undoing.
- Marker populations after the change: `slow`=1, `flaky`=0, `integration`=0,
  `acceptance`=193 (24 modules), `gui`=4 (`test_gui_fixture.py`),
  `network`=5 (the `provider_e2e` tests, same 4 files), `process_kill`=8,
  `concurrency`=53, `learning_soak`=1.
- The suite: `1564 passed / 45 skipped`, exit 0, before and after, same base,
  back to back; `1609 -> 1609` collected.

**The orchestrator ran one check the implementer could not make, and you should
confirm it independently.** Item 001 changed CI to install the **pinned** test
toolchain (`pytest==7.4.4`, `pytest-asyncio==0.23.3`) while this machine runs
8.4.2 / 0.23.8. A guard that aborts a full-tree collection on a marker
population mismatch could therefore turn the freshly-repaired CI pipeline red
on its first real run. The orchestrator built a throwaway venv with the pinned
toolchain and ran the branch's full-tree collection under it: **1609 tests
collected, exit 0, the guard did not fire.** pytest-asyncio 0.23.3 does
register the `asyncio` marker via `addinivalue_line`, so it reaches
`config.getini("markers")` on both toolchains. A throwaway venv exists at
`C:\Users\orcha\AppData\Local\Temp\opencode\venv-pinned` (pytest 7.4.4,
pytest-asyncio 0.23.3) - you may use it to reproduce.

### Pre-existing conditions - do NOT blame the branch for these

1. **A clean checkout has no `backend/.env`** (gitignored), so
   `test_setup_readiness.py::test_readiness_states` fails in any worktree. The
   implementer proved it pre-existing on a pristine `git archive master
   backend` extraction; the orchestrator proved it independently (worktree
   without `.env` -> `1 failed, 7 passed`; main tree with `.env` -> `8
   passed`). It is item 006's subject, not this item's.
2. **The suite is not deterministic here** - 2 native crashes in 6 observed
   full-suite runs, of two different kinds; neither reproduces in isolation;
   the smallest reproducer is the full suite. The implementer's AFTER run took
   28:20 against the BEFORE run's 8:11 because three other Implementers' suites
   were running concurrently; the pass/skip/warning/exit figures were identical
   regardless.
3. **`make` is not installed on this machine**, so `make check-test-selections`
   itself could not be executed - the implementer ran its four recipe commands
   verbatim instead and said so. Say whether you can do better.

## Constraints

- **Read-only.** Never edit code. The only file you may write is
  `reviews/002-r1.md`.
- Do not trust the implementer's claims. Look at the diff yourself.
- **The heart of this review:** does the change make the test tooling tell the
  truth, or does it merely move the dishonesty? Check that each Makefile
  expression selects what its comment says, that the marker populations the
  plan claims are the populations that actually exist, and that the new
  `pytest.ini` does not change what the suite collects or how it behaves.
- **Check the guard for false positives, not just for teeth.** The guard aborts
  a FULL-TREE collection with `pytest.UsageError` (exit 4) when a marker
  population drifts. Ask: what legitimate future change would trip it? Does it
  fire on a PARTIAL run (it must not)? What happens on a machine where the
  repository-root `pytest.ini` is absent (the plan says the conftest keeps a
  fallback declaration - check that the fallback really works and that the
  guard's "the root configuration does not declare the markers" check does not
  make the suite un-runnable in that state)?
- Check that the `network` marker cannot cause a credentialed provider call:
  the `provider_e2e` tests must keep skipping unless
  `SYTHION_RUN_PROVIDER_E2E=1`.
- Check that criterion 5 holds: **no `filterwarnings` filter** anywhere in the
  new configuration, and no new `error` filter.
- Check that `asyncio_mode` is `strict` - the mode the header reports today.
- For each acceptance criterion, state met / not met / **not verified**, with
  evidence.
- **Scope discipline:** the item was scoped to test tooling. Check that no
  runtime module was touched and that no documentation file was touched
  (item 003 owns `docs/TESTING.md`).
- If the diff removes a check, a deny, an assertion or a guard, treat the
  change as SENSITIVE regardless of what the classifier says.
- Do not write `follow-ups.md` - the orchestrator owns it. List anything you
  would queue in your report instead.
- Never mark PASS on something you did not inspect. State what you did not
  check.
- Do not push, deploy, publish, or touch real data. Do not ask the user
  anything.

## Output contract

`C:\Sythion\sythion\.orchestra\reviews/002-r1.md`, first line exactly
`VERDICT: ACCEPT` or `VERDICT: IMPROVE`, then:

```
- Reviewed commit: <sha>
- Tier: <NORMAL|EXTERNAL|SENSITIVE|SENSITIVE+EXTERNAL>

# Review of 002 round 1

## Acceptance criteria
| # | Criterion | Result (met / not met / not verified) | Evidence |
|---|---|---|---|
| 1 | ... (all six) |

## Marker populations and Makefile selections
| Marker / target | Claims | Reality | Evidence |
|---|---|---|---|

## Guard assessment
## Goal fit
## Sensitivity
## Findings (most important first)
1. <file:line> - problem - what to change

## Deferred
## Needs human?
## Not checked
```

## Previous attempt (if any)

- None. This is review round 1.
