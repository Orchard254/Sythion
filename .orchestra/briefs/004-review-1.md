# Brief: review - 004 - attempt 1

- Item: 004 Four tautological assertions replaced by the assertions they stood in for
- Role: reviewer
- Goal (the slice that matters): Sythion's "better" includes **verification
  before claims** and **honest debt reporting** (`goal.md`,
  `docs/QUALITY_GATES.md:34-44` Gate 3 "Verification before claims",
  `:88-97` Gate 8 "Testing baseline discipline"). Four assertions in the
  concurrency and hardening suites ended in `or True`, so they were
  unconditionally true and could never fail - each inside a suite whose name
  promised exactly the property it did not check. Your job: judge the DIFF
  against the plan and the criteria - not to re-run the whole verification,
  which is the Verifier's job next.

## Read

- `C:\Sythion\sythion\.orchestra\plans\004.md` - the plan the implementation
  was supposed to follow. Note what it says for EACH of the four sites about
  what the CORRECT assertion is, and its restatement of criterion 6 ("no
  pre-existing test changes outcome; the only delta is the two new guard
  tests" - because a guard that runs in the suite necessarily adds tests).
- `C:\Sythion\sythion\.orchestra\backlog.md` - item 004's six acceptance
  criteria.
- `C:\Sythion\sythion\.orchestra\research.md` - candidate **R-016**.
- `C:\Sythion\sythion\.orchestra\reports/004-implement-1.md` - the
  implementer's evidence.
- `C:\Sythion\sythion\.orchestra\critiques.md` - the orchestrator's critique
  of the implementer's report (ACCEPT).
- The branch itself: `orchestra/004-real-assertions`, diff against `master`:
  `git -C C:\Sythion\sythion diff master...orchestra/004-real-assertions`
- The production code each assertion stands in for, as the plan names it:
  `backend/agents/coordination.py` (`atomic_commit_spent`),
  `backend/runtime/events.py` (`DbEventLedger.append_event`, and its `_to_dict`
  projection), the `SpecialistReasoner` and its `_fallback_artifact`, and the
  shadow-policy structure.

## Write

- `C:\Sythion\sythion\.orchestra\reviews\004-r1.md` - the ONLY file you may
  write. Use `.orchestra/review-template.md` as the shape.

**Your review file's FIRST LINE must be exactly `VERDICT: ACCEPT` or
`VERDICT: IMPROVE`.** `verify.py` parses that line literally. Nothing may
precede it - no heading, no comment, no blank line.

## Context

- Current status: item 004 is `building`; the implementer has reported; you
  are the next stage.
- Branch: `orchestra/004-real-assertions` (commit `05589b2`). Base: `master`.
- Plan: `plans/004.md`.
- Previous findings: none for this item.

### Where to work

The branch is **already checked out** at
`C:\Users\orcha\AppData\Local\Temp\opencode\wt-004`. Use it read-only for
inspection and for running checks. Do NOT create another worktree for the same
branch (git will refuse). Do not commit or modify anything there.

### Facts already established - do not re-derive them

- The diff is exactly four files, one commit: the three affected test files
  plus the new `backend/tests/test_no_tautological_asserts.py`. **No production
  module is touched.**
- `or True` remaining anywhere under `backend/tests/` on the branch -> **0**.
- The guard module -> `2 passed`. The three affected files -> `71 passed,
  5 skipped` (identical to pristine master, re-measured by the implementer).
- Suite delta: `+2 passed, +2 collected, +0 skipped` - the two guard tests and
  nothing else.
- Exactly **8** test files under `backend/tests/` carry a UTF-8 BOM, so an AST
  guard must use `tokenize.open`, not `read_text(encoding="utf-8")`.
- `backend/runtime/events.py:64` allocates event sequence numbers by
  read-then-write: `sequence = max(last.sequence_num if last else 0,
  pending_max) + 1`. Combined with a unique index on (task_id, sequence), two
  sessions appending to the same `task_id` can collide; the worker thread's
  `IntegrityError` is swallowed by `threading`, so a test can report "1
  passed" while half the events are lost. The implementer measured this
  (5 or 10 of 10 events surviving across 8 rounds) and REPORTED it as defect
  D-1 rather than fixing it - which is what the brief required, because
  `backend/runtime/events.py` is out of this item's scope.

### Pre-existing conditions - do NOT blame the branch for these

1. **A clean checkout has no `backend/.env`** (gitignored), so
   `test_setup_readiness.py::test_readiness_states` fails in any worktree. The
   implementer proved it pre-existing on a pristine `git archive master
   backend` extraction. In the implementer's worktree the suite therefore read
   `1 failed, 1563 passed, 45 skipped` before and `1 failed, 1565 passed,
   45 skipped` after; with the orchestrator's environment (which has the
   `.env`) it is `1566 passed, 45 skipped`, exit 0. That single failure is
   NOT this item's doing.
2. **The suite is not deterministic here** - 2 native crashes in 6 observed
   full-suite runs, of two different kinds; neither reproduces in isolation.

## Constraints

- **Read-only.** Never edit code. The only file you may write is
  `reviews/004-r1.md`.
- Do not trust the implementer's claims. Look at the diff yourself.
- **The heart of this review is: are the four new assertions actually TRUE
  properties of the system, or are they merely different-shaped tautologies?**
  For each of the four, read the production code and judge whether the
  assertion would fail if the property were violated. An assertion that
  cannot fail is the exact defect this item exists to remove, so holding the
  new ones to the same standard is the whole point.
- Check that the assertions are **deterministic**. The plan deliberately pins
  an ownership property rather than an event COUNT, because the count is racy
  (the sequence-number race). Check that the implementer followed that, and
  that no new assertion reintroduces flakiness.
- Check that the guard in `backend/tests/test_no_tautological_asserts.py`
  actually has teeth and no false positives: does it detect a top-level `or
  True` in an assert? Does it avoid false-positives on legitimate assertions
  (e.g. `assert x or y` where the `or` is inside a call)? Does it handle the
  8 BOM-carrying files? Does it avoid matching its own source?
- **Scope discipline:** the item was scoped to tests only. Check that no
  production module was touched, and that the reported defect D-1 was left
  alone rather than silently absorbed.
- For each acceptance criterion, state met / not met / **not verified**, with
  evidence.
- If the diff removes a check, a deny, an assertion or a guard, treat the
  change as SENSITIVE regardless of what the classifier says. (Here the
  classifier says NORMAL - tests only - but the item's whole point is
  assertions, so judge them as if it mattered.)
- Do not write `follow-ups.md` - the orchestrator owns it. List anything you
  would queue in your report.
- Never mark PASS on something you did not inspect. State what you did not
  check.
- Do not push, deploy, publish, or touch real data. Do not ask the user
  anything.

## Output contract

`C:\Sythion\sythion\.orchestra\reviews\004-r1.md`, first line exactly
`VERDICT: ACCEPT` or `VERDICT: IMPROVE`, then:

```
# Review of 004 round 1

## Acceptance criteria
| # | Criterion | Result (met / not met / not verified) | Evidence |
|---|---|---|---|
| 1 | ... (all six) |

## The four new assertions
| Site | Property asserted | Could it fail? | Evidence from the production code |
|---|---|---|---|
(4 rows)

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
