# Brief: verify - 001 - attempt 1

- Item: 001 CI actually gates this repository
- Role: verifier
- Goal (the slice that matters): Sythion's "better" includes **release and
  operator readiness** and **verification before claims** (`goal.md`,
  `docs/QUALITY_GATES.md:34-44`, `:88-97`). The repository's release-confidence
  signal is its CI pipeline. Your job is to answer ONE question: **is the
  reported problem actually gone?** Not "is the diff nice" - the Reviewer
  already judged that.

## Read

- `C:\Sythion\sythion\.orchestra\research.md` - candidates **R-001**, **R-002**,
  **R-014**, **R-015**. These are the ORIGINAL problems. Your verdict is about
  them, not about the branch's general quality.
- `C:\Sythion\sythion\.orchestra\plans\001.md` - the plan and its restated
  acceptance criteria. Note its "## Corrections from attempt 1" section: the
  criterion-4 check is **Rule P**, not the loose pattern
  `pip install.*pytest`.
- `C:\Sythion\sythion\.orchestra\backlog.md` - item 001's six criteria.
- `C:\Sythion\sythion\.orchestra\reviews/001-r1.md` - the Reviewer's judgement
  (`VERDICT: ACCEPT`) and its seven findings, including the two non-blocking
  ones about the guard's message and its inherited fail-safe.
- `C:\Sythion\sythion\.orchestra\reports/001-implement-1.md` - the
  implementer's evidence.
- `C:\Sythion\sythion\.orchestra\reports/baseline-001.md` - the orchestrator's
  measured baseline.
- The branch: `orchestra/001-ci-gates-repo`.

## Write

- `C:\Sythion\sythion\.orchestra\verifications\001-v1.md` - the ONLY file you
  may write. Use `.orchestra/verification-template.md` as the shape.

**Your file's FIRST LINE must be exactly `VERDICT: FIXED`, `VERDICT: NOT FIXED`
or `VERDICT: REGRESSED`.** `verify.py` parses that line literally. Nothing may
precede it.

## Context

- Current status: item 001 is `in-review`; the plan is done, the implementation
  is done, the review is ACCEPT. You are the stage that decides whether the
  work actually achieved anything.
- Branch: `orchestra/001-ci-gates-repo` (commit `9ef7153`). Base: `master`.
- Plan: `plans/001.md`.
- Previous findings: the plan was redone once (attempt 1's criterion 4
  contradicted its own pin-guard step); the implementer confirmed the defect
  was real and reproduced it.

### Where to work

The branch is **already checked out** at
`C:\Users\orcha\AppData\Local\Temp\opencode\wt-001`. Use that directory for
read-only inspection and for running the checks; do NOT try to create another
worktree for the same branch (git will refuse - the branch is already
checked out). Do not commit anything there. Do not modify any file in it.

### Facts already established - do not re-derive them

- The diff is exactly two files, one commit:
  `git diff --name-only master...orchestra/001-ci-gates-repo` ->
  `.github/workflows/ci.yml`, `frontend/package.json`.
- **Rule P** = a pip-install line is one whose stripped form BEGINS WITH
  `pip install` or `python -m pip install`; it is an OFFENDER if it mentions
  `pytest` and lacks `==`.
  - Rule P on the BRANCH: 0 offenders. Rule P on MASTER: 2 offenders
    (`ci.yml:28`, `ci.yml:62`, both `python -m pip install pytest
    pytest-asyncio`).
  - The LOOSE pattern `Select-String 'pip install.*pytest'` matches exactly 1
    line of the branch's file - the guard's own source line 26. That is
    EXPECTED. It is the proof that the implementation is not using the loose
    pattern.
- The import command is byte-identical to master's (`-ceq` -> identical).
  From the repo root it exits 0; from `backend/` it exits 1 with
  `ModuleNotFoundError: No module named 'backend'`.
- The `provider-e2e` job's `if:` / `environment:` / `defaults` / `env:` block
  (with `${{ secrets.OLLAMA_API_KEY }}`) / pytest invocation are byte-identical
  to master's. The only change to that job is the deleted unpinned install
  line, which criterion 4 requires of the whole file.
- `frontend/package.json`: `"test": "vitest run"`, `"test:watch": "vitest"`.
  `package-lock.json` untouched and still in sync.
- `backend/requirements.txt:43-44` pins `pytest==7.4.4`,
  `pytest-asyncio==0.23.3`.

### Pre-existing conditions - do NOT blame the branch for these

1. **A clean checkout has no `backend/.env`** (it is gitignored,
   `.gitignore:16`), so `test_setup_readiness.py::test_readiness_states` fails
   in any worktree or fresh clone. The orchestrator reproduced it: worktree
   without `.env` -> `1 failed, 7 passed`; main tree with `.env` -> `8 passed`.
   **A GitHub Actions runner is a clean checkout, so the pipeline item 001
   repairs will be RED on its first real run** - that is a PRE-EXISTING
   condition, it is already escalated to a separate item (006), and the plan
   explicitly forbade fixing it inside item 001. Report it in your
   verification, but it is not a `NOT FIXED` verdict for this item unless your
   reproduction shows item 001 CAUSED it.
2. **The suite is not deterministic on this machine.** Across six observed
   full-suite runs: 2 native crashes of two different kinds - a burst of COM
   faults (`0x80040155`) on the `sythion-uia-mta` thread (does NOT kill the
   process) and a native sqlite3 access violation (`0xC0000005`) in
   `test_phase105_concurrency.py::TestBudgetAtomicity::test_concurrent_budget_does_not_exceed`
   (DOES kill the process). Neither reproduces in isolation; the smallest
   reproducer is the full suite. If a run dies that way, re-run it, record
   both attempts, and do not report it as this item's failure.
3. **The `tauri-build` job's "Assert the shell binary was produced" step is a
   NO-OP** - `Test-Path` is a cmdlet, does not set `$LASTEXITCODE`, and the
   step exits 0 whether or not the binary exists. The orchestrator verified
   this by reproducing the runner's pwsh wrapper. It is PRE-EXISTING, out of
   this item's plan scope, and already recorded as a new candidate.

## Constraints

- **Read-only.** Never edit code. The only file you may write is
  `verifications/001-v1.md`.
- **Reproduce.** For each original problem, show it FAILING on `master` and
  PASSING on the branch. If you cannot reproduce the failure on `master`, you
  cannot claim it is fixed - say so.
- Check each acceptance criterion against REALITY, one by one, with evidence -
  not "the code looks right". For the file-level criteria, run the plan's own
  check commands. For criterion 3, actually run `npm test` in `frontend/` and
  paste the output. For criterion 4, run Rule P on both files and execute the
  guard's `run:` block extracted from the merged YAML, against BOTH the merged
  file and master's file.
- Check that the change did not break something else. At minimum: the rest of
  the workflow file is still valid and the other three jobs are unchanged;
  `frontend/package.json` still parses and the lockfile is still in sync; and
  the backend suite is not made worse. The suite is NOT part of this item's
  criteria, so a full-suite run is optional - but if you run one, report it
  with the `.env` caveat above.
- If the fix depends on an invariant, verify that invariant BY NAME. For this
  item the invariant is: **the workflow's trigger, step working directories,
  install sources and test commands are exactly what the plan specifies, and
  the pin-guard is a no-op on the merged file.**
- **Read-only means read-only.** Do not write `follow-ups.md` - the
  orchestrator owns it. List anything you would queue in your report.
- Do not push, deploy, publish, run the workflow, dispatch it, or touch real
  data. Do not run `make clean`, `scripts/stage_python_runtime.py`, migrations,
  or `npx tauri build`.
- If the change can only be confirmed by a human (here: the actual GitHub
  Actions run), say exactly that, in one line, and say what a human would need
  to do. Do NOT mark it FIXED on a guess - but note that the in-repo
  reproduction IS sufficient to establish the in-repo problems are gone.
- State what you did not check.
- Do not ask the user anything.

## Output contract

`C:\Sythion\sythion\.orchestra\verifications\001-v1.md`, first line exactly
`VERDICT: FIXED` (or `NOT FIXED` / `REGRESSED`), then:

```
# Verification of 001 round 1

## The problem, and whether it is gone
- R-001 (import gate): reproduction on master (must fail): ... reproduction on the branch (must pass): ...
- R-002 (trigger): ...
- R-014 (frontend suite): ...
- R-015 (pytest pin): ...
- Verdict on the original problem:

## Acceptance criteria
| # | Criterion | Result (met / not met / not verified) | Evidence |
|---|---|---|---|
| 1 | ... (all six) |

## Did it break anything else?
- Full suite: <command and result, or "not run - with reason">
- Neighbouring behaviour checked: <what and how>
- Public surface changed: <yes/no, and what>

## Invariant
- Invariant:
- Verified by:

## Needs a human?
## Not checked
```

## Previous attempt (if any)

- None. This is verification round 1.
