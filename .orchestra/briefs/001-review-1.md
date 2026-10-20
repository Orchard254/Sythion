# Brief: review - 001 - attempt 1

- Item: 001 CI actually gates this repository
- Role: reviewer
- Goal (the slice that matters): Sythion's "better" includes **release and
  operator readiness** and **verification before claims** (`goal.md`,
  `docs/QUALITY_GATES.md:34-44`, `:88-97`). This repo's release-confidence
  signal is its CI pipeline. Your job: judge the DIFF against the plan and the
  criteria - not to re-run the whole verification, which is the Verifier's job
  next.

## Read

- `C:\Sythion\sythion\.orchestra\plans\001.md` - the plan the implementation
  was supposed to follow. Note its "## Corrections from attempt 1" section: the
  plan was redone once because criterion 4 originally contradicted the plan's
  own pin-guard step. The corrected criterion 4 uses **Rule P**; the loose
  pattern `pip install.*pytest` is NOT the check.
- `C:\Sythion\sythion\.orchestra\backlog.md` - item 001's six acceptance
  criteria, in the restated form.
- `C:\Sythion\sythion\.orchestra\reports\001-implement-1.md` - the
  implementer's evidence.
- `C:\Sythion\sythion\.orchestra\critiques.md` - the orchestrator's critique
  of the implementer's report (ACCEPT) and of the plan's two rounds.
- `C:\Sythion\sythion\.orchestra\goal.md` - the goal and the off-limits list.
- The branch itself: `orchestra/001-ci-gates-repo`, diff against `master`:
  `git -C C:\Sythion\sythion diff master...orchestra/001-ci-gates-repo`
- The workflow it changes: `C:\Sythion\sythion\.github\workflows/ci.yml`
  (the whole file, all four jobs - not just the diff).

## Write

- `C:\Sythion\sythion\.orchestra\reviews\001-r1.md` - the ONLY file you may
  write. Use `.orchestra/review-template.md` as the shape.

**Your review file's FIRST LINE must be exactly `VERDICT: ACCEPT` or
`VERDICT: IMPROVE`.** `verify.py` parses that line literally. Nothing may
precede it - no heading, no comment, no blank line.

## Context

- Current status: item 001 is `building`; the implementer has reported; you
  are the next stage. The orchestrator has already independently checked the
  diff's shape (exactly two files, one commit `9ef7153`) and re-run Rule P
  against the branch (0 offenders) and against master (2 offenders).
- Branch: `orchestra/001-ci-gates-repo`. Base: `master`.
- Plan: `plans/001.md`.
- Previous findings: the plan's attempt 1 was REDO'd for a self-contradiction
  between criterion 4 and its own proposed guard; attempt 2 fixed it with Rule
  P. The implementer confirmed the defect was real and reproduced it.

**Facts already established - do not re-derive them:**

- The branch's diff is exactly `.github/workflows/ci.yml` (19 insertions,
  7 deletions) and `frontend/package.json`.
- Rule P on the branch: 0 offenders. Rule P on master: 2 offenders
  (`ci.yml:28`, `ci.yml:62`).
- The LOOSE pattern `Select-String 'pip install.*pytest'` matches 1 line on the
  branch - the guard's own source line 26. That is EXPECTED and is why the
  plan specifies Rule P. If the implementer had used the loose pattern as
  criterion 4's check, the item would fail. Check which pattern the
  implementation and the criterion actually use.
- The import command is byte-identical to master's, with
  `working-directory: ${{ github.workspace }}`; from the repo root it exits 0,
  from `backend/` it exits 1 with `ModuleNotFoundError`.
- `backend/api/routes.py` imports `backend.*` absolutely, and
  `backend/main.py:17-25` uses package-relative imports, so `import main` from
  `backend/` ALSO fails. Only the repo-root invocation works - the plan's
  "move the working directory, keep the command" decision is the only correct
  one.
- The `provider_e2e` job is gated by `if: github.event_name ==
  'workflow_dispatch' && vars.RUN_PROVIDER_E2E == '1'` plus an
  `environment:` and an injected secret.
- `backend/requirements.txt:43-44` pins `pytest==7.4.4` and
  `pytest-asyncio==0.23.3`.

## Constraints

- **Read-only.** Never edit code. You may write only `reviews/001-r1.md`.
- Do not trust the implementer's claims. Look at the diff yourself.
- Do not re-run the whole verification - that is the Verifier's stage. But DO
  run whatever cheap checks you need to judge the diff (read the files, run
  the rule, check the YAML structure).
- For each acceptance criterion, state met / not met / **not verified**, with
  evidence. "Not verified" is an honest answer; "met" without evidence is not.
- Ask: does this serve the goal? Is there a simpler way? What did it break? Is
  it scope creep? Does it do what the plan said, and is the plan still right?
- **The `provider-e2e` job is the thing to look hardest at.** A credentialed
  job that can now be reached by a manual dispatch, in a repository where
  `vars.RUN_PROVIDER_E2E` is not visible from the checkout. Judge whether the
  change makes that job more or less reachable than it was, and whether the
  residual risk is acceptable and correctly recorded.
- **Also judge the pin-guard step on its merits.** It is a new mechanism that
  can make every push red if it is wrong. Ask: is it a no-op on the merged
  file? Can it match itself? Can it false-positive on a legitimate future
  change? Is a guard that lives inside the file it guards a good idea?
- If the diff removes a check, a deny, an assertion or a guard, treat the
  change as SENSITIVE regardless of what the classifier says. (Here the
  classifier says EXTERNAL - `.github/workflows/**` is an external path - so
  the in-repo change merges and the outside action goes to `follow-ups.md`.)
- Queue any outside action in `follow-ups.md` as `[deferred]` or `[required]`
  in your report - do NOT edit `follow-ups.md` yourself; the orchestrator
  writes it. Note that the item's criterion 6 requires the orchestrator to
  record the GitHub observation, and the implementer correctly did not write
  it.
- IMPROVE findings must be specific, file-referenced and ordered by
  importance.
- Never mark PASS on something you did not inspect. State what you did not
  check.
- Do not push, deploy, publish, or touch real data. Do not run the workflow.
  Do not ask the user anything.

## Output contract

`C:\Sythion\sythion\.orchestra\reviews\001-r1.md`, first line exactly
`VERDICT: ACCEPT` or `VERDICT: IMPROVE`, then:

```
# Review of 001 round 1

## Acceptance criteria
| # | Criterion | Result (met / not met / not verified) | Evidence |
|---|---|---|---|
| 1 | ... (all six) |

## Goal fit
## Sensitivity
<!-- tier: NORMAL | EXTERNAL | SENSITIVE | SENSITIVE+EXTERNAL
     invariant:
     pinned by: <what pins it>
     verified: yes | no
     If EXTERNAL, list the outside action to be queued in follow-ups.md. -->

## Findings (most important first)
1. <file:line> - problem - what to change

## Deferred
## Needs human?
## Not checked
```

## Previous attempt (if any)

- None. This is review round 1.
