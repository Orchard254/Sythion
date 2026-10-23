# Implement brief 022-implement-1

You are the Implementer for Sythion item 022.

## Working copy
- Worktree: `C:\Sythion\sythion-022`
- Branch: `orchestra/022-concurrency-admission` (checked out there)
- Base: `master` at ` 15f70bb`

## Goal
Implement plan `C:\Sythion\sythion\.orchestra\plans\022.md`: prove and document the harness-only classification of the intermittent fan-out admission bypass by adding a file-backed concurrency test and a documentation invariant in `backend/agents/delegation.py`. No production behaviour or SQL changes.

## Acceptance criteria from plan (must all be verified)
See plan sections "How it will be proven" and "Acceptance criteria, restated objectively".

## Files to change (allowed)
- `C:\Sythion\sythion-022\backend\agents\delegation.py` documentation comment only
- `C:\Sythion\sythion-022\backend\tests\test_admission_concurrency_filebacked.py` new
- `C:\Sythion\sythion-022\backend\tests\test_phase95_hardening.py` docstring note only
- nowhere else

## Constraints
- No production logic/SQL/schema/API change.
- No destructive git commands.
- Run commands in worktree. Record command/output evidence in report.
- Set item 022 status to `building` in backlog/board (main repo; you may edit .orchestra in main tree too? Better edit only worktree's .orchestra if present. The orchestrator will update state/critique in main later; but brief says mirror status.)
- You may edit `.orchestra` state in the worktree? Since tests run in worktree, `.orchestra` is included. Do so cautiously.
- State what you did not check.

## Output contract
- report: `C:\Sythion\sythion\.orchestra\reports\022-implement-1.md`
- commits on branch orchestra/022-concurrency-admission
- small commits with clear messages
- EOF.
