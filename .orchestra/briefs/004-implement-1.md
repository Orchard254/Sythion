# Brief: implement - 004 - attempt 1

- Item: 004 Four tautological assertions replaced by the assertions they stood in for
- Role: implementer (implementer-2)
- Goal (the slice that matters): Sythion's "better" includes **verification
  before claims**. Four assertions in the concurrency and hardening suites end
  in `or True`, so they are unconditionally true and can never fail - each
  inside a suite whose name promises exactly the property it does not check.
  The repository advertises guarantees it does not have. Your job: replace them
  with the assertions they stood in for, prove the assertions are real, and
  prove they cannot be silently broken again.

## Read

- `C:\Sythion\sythion\.orchestra\plans\004.md` - **THE PLAN. Read it fully
  first.** It is the contract: for each of the four sites it states what the
  CORRECT assertion actually is and why, how the invariant is pinned, the
  guard's design, the risks, and the rule for what to do if a real assertion
  fails.
- `C:\Sythion\sythion\.orchestra\backlog.md` - item 004's six acceptance
  criteria (note criterion 6's restatement in the plan - a guard that runs in
  the suite necessarily adds tests, so the delta is the two new guard tests).
- `C:\Sythion\sythion\.orchestra\goal.md` - the goal, the off-limits list, the
  baseline notes.
- `C:\Sythion\sythion\backend\tests\test_phase105_concurrency.py` - sites at
  **:530** and **:746**, the never-awaited `fake_chat` coroutine around :60
  and :730, and the test `test_provider_calls_isolated`.
- `C:\Sythion\sythion\backend\tests\test_phase1075_hardening.py` - site at
  **:765**.
- `C:\Sythion\sythion\backend\tests\test_phase125_planning_validation.py` -
  site at **:230**.
- The production code each assertion stands in for, as the plan names it
  (`SpecialistReasoner._fallback_artifact` and the reasoner's artifact shape;
  the shadow-policy structure; the event ledger).

## Write

- The files the plan names: the three affected test files and the new
  `backend/tests/test_no_tautological_asserts.py`.
- `C:\Sythion\sythion\.orchestra\reports\004-implement-1.md` - your evidence
  report (shape below).
- Your commits, on the branch below.

**Do not write anywhere else.** In particular: not `backend/tests/conftest.py`
(item 002 owns it), not `backend/runtime/events.py` or any production module
(the plan deliberately keeps the ledger race OUT of scope), not
`backend/requirements.txt`, not `docs/`.

## Context

- Current status: item 004 is `planned` in `backlog.md` and `board.json`.
- Branch: **`orchestra/004-real-assertions`**, branched from `master`
  (`d2a24e5` + the orchestrator's `.orchestra/` commits).

### Working directory - USE A DEDICATED WORKTREE

Another Implementer is working on item 001 **at the same time**. You must not
share a working tree. Create your own:

```powershell
cd C:\Sythion\sythion
git worktree add C:\Users\orcha\AppData\Local\Temp\opencode\wt-004 -b orchestra/004-real-assertions master
cd C:\Users\orcha\AppData\Local\Temp\opencode\wt-004
```

Work ONLY inside that worktree. The repository root `C:\Sythion\sythion` is
the orchestrator's tree - do not check out branches there, do not commit there.
The `.orchestra/` directory exists in your worktree too, but write your report
to the **main tree's** `.orchestra\reports\004-implement-1.md` (absolute path
below), not to the worktree's copy.

Note: `src-tauri/resources/python-runtime/` is ~4,200 tracked files, so the
worktree checkout takes a couple of minutes. That is expected.

### Facts already established - do not re-derive them

- The four tautologies, verbatim:
  - `backend/tests/test_phase105_concurrency.py:530` -
    `assert len(tids_in_hist) == 1 or True  # history per tid already isolated`
  - `backend/tests/test_phase105_concurrency.py:746` -
    `assert not fail_res.get("fallback") is False or True  # fallback handled`
  - `backend/tests/test_phase1075_hardening.py:765` -
    `assert analysis.get("delegation_id") is None or True  # reasoner returns findings`
  - `backend/tests/test_phase125_planning_validation.py:230` -
    `assert "actual_policy" in d and "shadow_policy" in d and "actual_outcome" in d or True`
- The dead `side_effect` assignment at
  `backend/tests/test_phase105_concurrency.py:730` evaluates
  `fake_chat([{"role":"user","content":"x"}], model_id="model-b")` and is never
  used (the test switches to `provider2` on the next line), which is why the
  run emits `RuntimeWarning: coroutine
  'TestModelCallConcurrency.test_provider_calls_isolated.<locals>.fake_chat'
  was never awaited`.
- **Exactly 8 test files under `backend/tests/` carry a UTF-8 BOM**:
  `test_goal_planning.py`, `test_kernel_concurrency.py`, `test_main_agent.py`,
  `test_phase45_hardening.py`, `test_work_orchestration.py`,
  `test_work_state.py`, `test_model_failure.py`, `test_target_validation.py`.
  An AST guard that uses plain `read_text(encoding="utf-8")` will ERROR on
  them. Use `tokenize.open`.
- `backend/runtime/events.py:64` allocates event sequence numbers by
  read-then-write: `sequence = max(last.sequence_num if last else 0,
  pending_max) + 1`. Two sessions appending to the same `task_id` can collide
  on the unique index; the worker thread's `IntegrityError` is swallowed by
  `threading`, so the test can still report "1 passed" while half the events
  are lost. **This is a real defect and it is deliberately OUT of this item's
  scope.** Pin the deterministic isolation property (ownership), not the racy
  count.
- The local interpreter is CPython 3.14.7; CI pins 3.11. pytest 8.4.2.
- The suite baseline is `1564 passed, 45 skipped, 1609 collected` - the
  orchestrator's own measurement is pending; **re-measure it yourself** and use
  your own numbers.
- **Known hazard:** this suite intermittently aborts the interpreter with
  `Windows fatal exception: code 0x80040155` (a COM fault in the
  `sythion-uia-mta` worker thread). Observed twice by two observers. If your
  full-suite run dies that way, **re-run it** and record BOTH attempts. It is
  a pre-existing condition on `master`, not something you caused - do not try
  to fix it, and do not report it as your failure.

## Constraints

- Work only on the assigned item and branch. No unrelated edits.
- Commit small and often with clear messages.
- **Never push.** Never deploy, publish, or touch real data.
- **Never run destructive commands:** `rm -rf`, `git reset --hard`,
  `git clean -fd`, `git checkout -- .`, `git stash drop`, force-push, or
  anything that deletes files git does not track.
- Do not run `make clean`. Do not run `scripts/stage_python_runtime.py`. Do
  not run migrations against `backend/sythion.db` or
  `%LOCALAPPDATA%\Sythion (Synth)`. Do not run `npx tauri build`.
- Before any unfamiliar command, check what it does to the local environment.
- **THE RULE THAT MATTERS MOST.** The tautology was hiding a real outcome.
  When you replace it with the real assertion, the real assertion wins. If it
  FAILS, the correct outcome is a fix in the production code **or a reported
  defect in your report** - NEVER a restored `or True`, NEVER a loosened
  assertion, NEVER a skipped test, NEVER a deleted test. Criterion 1 makes
  the cheat detectable (`grep -rn "or True" backend/tests` must match
  nothing), but do not rely on the checker to stop you.
  The plan states this rule in advance and you must follow it. The plan also
  tells you to pin the DETERMINISTIC property where the count is racy - follow
  the plan, and if you find the plan's chosen property is also flaky, say so in
  your report rather than weakening it.
- The guard must not spell the pattern out in its own source, or criterion 1's
  literal grep matches it.
- Do not add any file other than the guard module the plan names.
- Do not ask the user anything. If something needs a decision you cannot
  make, say so in the report and stop.

## Output contract

Write `C:\Sythion\sythion\.orchestra\reports\004-implement-1.md` with this
shape (the template is at `.orchestra/report-template.md`):

```
# Report: implement - 004 - attempt 1

## What I did
<the exact commands you ran, the commits you made (sha + message), the files you touched>

## Evidence
<every claim with its evidence: command + output, file:line, test name>

## Acceptance criteria
| # | Criterion | Result | Evidence |
|---|---|---|---|
| 1 | ... | ... | ... |
(1..6, from the backlog item, with criterion 6 in its restated form)

## The four replacements
| Site | Before | After | Why this is the right assertion |
|---|---|---|---|
| test_phase105_concurrency.py:530 | ... | ... | ... |
(4 rows)

## Findings
<anything surprising - including ANY defect you found and did not fix, and why>

## Not checked
<REQUIRED. What you did not verify, and why.>
```

- **Paste the ACTUAL command output.** A claim without output is not a claim.
- For each of the six criteria, run the plan's own check command and paste the
  output. If a criterion is not met, say so plainly.
- Prove the guard has teeth: temporarily introduce a tautology somewhere,
  show the guard FAILING, then remove it and show the guard passing. Record
  both outputs. This is what makes criterion 5 a check rather than a claim.
- Report the before and after full-suite numbers side by side.
- Record any file you touched outside the ones named, and why.
- State what you did not check.
- Reply with a short summary: the commits, the criteria results, and anything
  surprising you found. The FILE is the deliverable.

## Previous attempt (if any)

- None. This is attempt 1.
