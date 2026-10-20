# Brief: verify - 004 - attempt 1

- Item: 004 Four tautological assertions replaced by the assertions they stood in for
- Role: verifier
- Goal (the slice that matters): Sythion's "better" includes **verification
  before claims** and **honest debt reporting** (`goal.md`,
  `docs/QUALITY_GATES.md:34-44` Gate 3, `:88-97` Gate 8). Four assertions in
  the concurrency and hardening suites ended in `or True` and could never fail.
  Your job is to answer ONE question: **is the reported problem actually gone?**
  Not "is the diff nice" - the Reviewer already judged that.

## Read

- `C:\Sythion\sythion\.orchestra\research.md` - candidate **R-016**. This is
  the ORIGINAL problem. Your verdict is about it.
- `C:\Sythion\sythion\.orchestra\plans\004.md` - the plan, including its
  restatement of criterion 6 ("no pre-existing test changes outcome; the only
  delta is the two new guard tests") and its statements about what each of the
  four correct assertions is.
- `C:\Sythion\sythion\.orchestra\backlog.md` - item 004's six criteria.
- `C:\Sythion\sythion\.orchestra\reviews/004-r1.md` - the Reviewer's judgement
  (`VERDICT: ACCEPT`) and its findings.
- `C:\Sythion\sythion\.orchestra\reports/004-implement-1.md` - the
  implementer's evidence, including the reported defect **D-1**.
- `C:\Sythion\sythion\.orchestra\reports/baseline-001.md` - the orchestrator's
  measured baseline.
- The branch: `orchestra/004-real-assertions`.

## Write

- `C:\Sythion\sythion\.orchestra\verifications\004-v1.md` - the ONLY file you
  may write. Use `.orchestra/verification-template.md` as the shape.

**Your file's FIRST LINE must be exactly `VERDICT: FIXED`, `VERDICT: NOT FIXED`
or `VERDICT: REGRESSED`.** `verify.py` parses that line literally. Nothing may
precede it.

## Context

- Current status: item 004 is `in-review`; the plan, the implementation and the
  review (`VERDICT: ACCEPT`) are done. You are the stage that decides whether
  the work actually achieved anything.
- Branch: `orchestra/004-real-assertions` (commit `05589b2`). Base: `master`.
- Plan: `plans/004.md`.
- Previous findings: none for this item.

### Where to work

The branch is **already checked out** at
`C:\Users\orcha\AppData\Local\Temp\opencode\wt-004`. Use it read-only for
inspection and for running checks; do NOT create another worktree for the same
branch (git will refuse). Do not commit or modify anything there.

Note: that worktree has **no `backend/.env`** (gitignored), so a full-suite run
there shows one extra failure - `test_setup_readiness.py::test_readiness_states`
- which is PRE-EXISTING and not this item's doing. To get a comparable
full-suite number you may run the suite from the orchestrator's main tree
`C:\Sythion\sythion` **after the branch is merged** - but at this stage, run
whatever you need in the worktree and REPORT the `.env` caveat explicitly
rather than presenting a raw count.

### Facts already established - do not re-derive them

- The diff is exactly four files, one commit: the three affected test files
  plus the new `backend/tests/test_no_tautological_asserts.py`. **No production
  module is touched.**
- `or True` remaining anywhere under `backend/tests/` on the branch -> **0**.
- The guard module -> `2 passed`. The three affected files -> `71 passed,
  5 skipped` (identical to pristine master).
- The suite delta in the worktree: `1 failed, 1563 passed, 45 skipped` before
  -> `1 failed, 1565 passed, 45 skipped` after (+2 passed, +2 collected, +0
  skipped - the two guard tests and nothing else). With the orchestrator's
  environment: `1566 passed, 45 skipped`, exit 0.
- The Reviewer proved criterion 6 BY SET COMPARISON: master collects 1609 ids,
  the branch 1611, and the difference is exactly the two guard tests.
- The master tree emits the `RuntimeWarning: coroutine
  'TestModelCallConcurrency.test_provider_calls_isolated.<locals>.fake_chat'
  was never awaited`; the branch does not.

### Two PRE-EXISTING conditions - do NOT blame the branch for these

1. **A clean checkout has no `backend/.env`** (gitignored), so
   `test_setup_readiness.py::test_readiness_states` fails in any worktree. The
   implementer proved it pre-existing on a pristine `git archive master
   backend` extraction. The orchestrator proved it independently (worktree
   without `.env` -> `1 failed, 7 passed`; main tree with `.env` -> `8
   passed`).
2. **The suite is not deterministic here** - 2 native crashes in 6 observed
   full-suite runs, of two different kinds; neither reproduces in isolation;
   the smallest reproducer is the full suite.

### One thing to establish carefully

The assertion at `backend/tests/test_phase1075_hardening.py:765` lives inside
`TestProviderE2EGated::test_two_reasoning_real_provider_isolated`, which is
marked `@pytest.mark.provider_e2e` and calls `pytest.skip("gated provider_e2e
not enabled")` unless `SYTHION_RUN_PROVIDER_E2E=1`, and additionally skips
unless the provider reports HEALTHY. **So that assertion has never been
executed by any run in this repository** - and neither was the tautology it
replaced. Establish what you can: does the test SKIP in a normal run? Is the
new assertion's code path reachable at all without a provider? Say plainly
whether this path is verified or unverified, and do NOT let it silently decide
your verdict either way - the original problem (a tautology that could never
fail) is fixed at all four sites regardless, and the provider-gated site is
separately recorded as a follow-up.

## Constraints

- **Read-only.** Never edit code. The only file you may write is
  `verifications/004-v1.md`.
- **Reproduce.** Show the original problem FAILING on `master` and PASSING on
  the branch. For this item the reproduction is concrete and cheap: `or True`
  is present at four sites on `master` and absent from the branch. Run it.
- Check each acceptance criterion against REALITY, one by one, with evidence.
- **Check that the change did not break something else:** the rest of the
  suite, the behaviour of neighbouring tests, and - importantly - that no
  pre-existing test silently changed outcome. The Reviewer's set comparison
  (1609 vs 1611 ids, difference exactly the two guard tests) is the model;
  reproduce or extend it.
- **Check the NEW assertions can actually fail.** The strongest evidence that
  a tautology is gone is that the replacement fails when the property is
  violated. The implementer demonstrated this for the guard (a probe makes it
  fail). Consider whether you can demonstrate it for the four assertions
  themselves - e.g. by temporarily breaking the production behaviour in a
  **scratch copy outside the repository**, never in the branch or the worktree.
- Verify the invariant BY NAME: **no `assert` statement under
  `backend/tests/` has a top-level `or True`, and no pre-existing test changed
  outcome.**
- The reported defect **D-1** (`DbEventLedger.append_event` losing up to 50%
  of concurrently-appended events) was deliberately NOT fixed - `events.py` is
  out of this item's scope. Confirm that no production code was changed to fix
  it, and that the item did not paper over it (the ownership assertion is the
  plan's chosen deterministic property).
- Do not write `follow-ups.md` - the orchestrator owns it. List anything you
  would queue in your report.
- Do not push, deploy, publish, or touch real data. Do not run `make clean`,
  `scripts/stage_python_runtime.py`, migrations, or `npx tauri build`.
- If the change can only be confirmed by a human, say exactly that, in one
  line. Do not mark it FIXED on a guess.
- State what you did not check.
- Do not ask the user anything.

## Output contract

`C:\Sythion\sythion\.orchestra\verifications\004-v1.md`, first line exactly
`VERDICT: FIXED` (or `NOT FIXED` / `REGRESSED`), then:

```
# Verification of 004 round 1

## The problem, and whether it is gone
- Reproduction on master (must fail): ...
- Reproduction on the branch (must pass): ...
- Verdict on the original problem:

## Acceptance criteria
| # | Criterion | Result (met / not met / not verified) | Evidence |
|---|---|---|---|
| 1 | ... (all six) |

## Did it break anything else?
- Full suite: <command and result, with the .env caveat>
- Neighbouring behaviour checked: <what and how>
- Public surface changed: <yes/no, and what>

## Invariant
- Invariant:
- Verified by:

## The provider-gated site
<is it executed? is it verified? what would be needed to verify it?>

## Needs a human?
## Not checked
```

## Previous attempt (if any)

- None. This is verification round 1.
