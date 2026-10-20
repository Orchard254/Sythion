# Brief: implement - 001 - attempt 1

- Item: 001 CI actually gates this repository
- Role: implementer (implementer-1)
- Goal (the slice that matters): Sythion's release-confidence signal is its CI
  pipeline. Today the pipeline never runs on this repository's only branch
  (`master`), the one gate it contains is red for a reason unrelated to the
  code (the import gate runs from `backend/`, where `import backend.main` fails),
  the 49-test frontend suite is never executed, and CI installs a pytest
  version the repository never declared. Your job: make CI actually gate this
  repository, on a branch, with the evidence.

## Read

- `C:\Sythion\sythion\.orchestra\plans\001.md` - **THE PLAN. Read it fully
  first.** It is the contract: the exact edits, the matching rule (Rule P),
  the reproduction, the restated acceptance criteria, the risks and the four
  open questions. It contains a "## Corrections from attempt 1" section
  explaining why the plan was redone - read that too, so you do not
  reintroduce the defect it fixed.
- `C:\Sythion\sythion\.orchestra\backlog.md` - item 001's six acceptance
  criteria.
- `C:\Sythion\sythion\.orchestra\goal.md` - the goal, the off-limits list, the
  baseline notes.
- `C:\Sythion\sythion\.github\workflows/ci.yml` - the file you are changing.
  Read the WHOLE file, every job.
- `C:\Sythion\sythion\frontend/package.json` - the scripts block.
- `C:\Sythion\sythion\backend/requirements.txt` - the pinned pytest versions
  (read only; do NOT edit it).
- `C:\Sythion\sythion\.orchestra/reports/scout-001.md` - the Scout's rationale
  for this item.

## Write

- The two files the plan names: `.github/workflows/ci.yml` and
  `frontend/package.json`.
- `C:\Sythion\sythion\.orchestra\reports\001-implement-1.md` - your evidence
  report (shape below).
- Your commits, on the branch below.

**Do not write anywhere else.** In particular: not `docs/TESTING.md` (item 003
owns it), not `Makefile` (item 002 owns it), not `backend/requirements.txt`,
not any file under `backend/`, not `follow-ups.md` (the orchestrator writes
that entry at merge time, deliberately, so the branch diff stays limited to the
two files the acceptance criteria name).

## Context

- Current status: item 001 is `planned` in `backlog.md` and `board.json`.
- Branch: **`orchestra/001-ci-gates-repo`**, branched from `master`
  (`d2a24e5` + the orchestrator's `.orchestra/` commits).

### Working directory - USE A DEDICATED WORKTREE

Another Implementer is working on item 004 **at the same time**. You must not
share a working tree. Create your own:

```powershell
cd C:\Sythion\sythion
git worktree add C:\Users\orcha\AppData\Local\Temp\opencode\wt-001 -b orchestra/001-ci-gates-repo master
cd C:\Users\orcha\AppData\Local\Temp\opencode\wt-001
```

Work ONLY inside that worktree. The repository root `C:\Sythion\sythion` is
the orchestrator's tree - do not check out branches there, do not commit there.
The `.orchestra/` directory exists in your worktree too, but write your report
to the **main tree's** `.orchestra\reports\001-implement-1.md` (absolute path
below), not to the worktree's copy.

Note: `src-tauri/resources/python-runtime/` is ~4,200 tracked files, so the
worktree checkout takes a couple of minutes. That is expected.

### Facts already established - do not re-derive them

- From `C:\Sythion\sythion` (repo root): `python -c "import backend.main;
  import backend.api.routes"` -> exit 0, `IMPORT OK`. From `...\backend`: exit
  1, `ModuleNotFoundError: No module named 'backend'`.
- `git branch -a` -> `master`, `remotes/origin/master` only. There is no
  `main`.
- The word `workflow_dispatch` appears on master ONLY at `ci.yml:43`, inside
  the `provider-e2e` `if:` condition. The anchored trigger-key form
  `^\s*workflow_dispatch:\s*$` matches **0** times on master.
- Rule P applied to master finds exactly **2** offenders: `ci.yml:28` and
  `ci.yml:62`, both `python -m pip install pytest pytest-asyncio`.
- The `provider_e2e` tests are gated by
  `@pytest.mark.skipif(os.environ.get("SYTHION_RUN_PROVIDER_E2E") != "1")`,
  so they never call a provider unless that variable is set.
- The local interpreter is CPython 3.14.7; CI pins 3.11. The pinned test
  toolchain is `pytest==7.4.4` / `pytest-asyncio==0.23.3`.
- **Known hazard:** this suite intermittently aborts the interpreter with
  `Windows fatal exception: code 0x80040155` (a COM fault in the
  `sythion-uia-mta` worker thread). It has been observed twice by two
  observers. If your full-suite run dies that way, **re-run it** and record
  BOTH attempts in your report. It is a pre-existing condition on `master`,
  not something you caused - do not try to fix it, and do not report it as
  your failure.

## Constraints

- Work only on the assigned item and branch. No unrelated edits.
- Commit small and often with clear messages. The plan expects one logical
  change; one or two commits is right.
- **Never push.** Never deploy, publish, or touch real data.
- **Never run destructive commands:** `rm -rf`, `git reset --hard`,
  `git clean -fd`, `git checkout -- .`, `git stash drop`, force-push, or
  anything that deletes files git does not track.
- Do not run `make clean` (it deletes directories). Do not run
  `scripts/stage_python_runtime.py` (it `rmtree`s the committed runtime). Do
  not run migrations against `backend/sythion.db` or
  `%LOCALAPPDATA%\Sythion (Synth)`. Do not run `npx tauri build`. Do not
  dispatch the GitHub workflow.
- Before any unfamiliar command, check what it does to the local environment.
- **The `provider-e2e` job must not change behaviour.** Its `if:` condition,
  `environment:`, `defaults`, the `env:` block with
  `SYTHION_OLLAMA_API_KEY: ${{ secrets.OLLAMA_API_KEY }}` and its pytest
  invocation stay byte-identical. The only permitted change is deleting its
  unpinned `pip install pytest pytest-asyncio` line - which criterion 4
  requires of the whole file. Never move a secret into the repo. Never make a
  credentialed job reachable by a push or a pull request.
- The plan's Rule P must be applied EXACTLY as stated. If you change the
  guard's matching rule, you must change criterion 4's check the same way and
  re-run the self-consistency checks in the plan. The guard must be a no-op on
  the merged file (exit 0) and must exit 1 on master's file.
- Do not add any file. The only new artefact in the whole change is one step
  inside `ci.yml`.
- Do not ask the user anything. If something needs a decision you cannot
  make, say so in the report and stop.

## Output contract

Write `C:\Sythion\sythion\.orchestra\reports\001-implement-1.md` with this
shape (the template is at `.orchestra/report-template.md`):

```
# Report: implement - 001 - attempt 1

## What I did
<the exact commands you ran, the commits you made (sha + message), the files you touched>

## Evidence
<every claim with its evidence: command + output, file:line, test name>

## Acceptance criteria
| # | Criterion | Result | Evidence |
|---|---|---|---|
| 1 | ... | ... | ... |
(1..6, from the backlog item, restated as the plan states them)

## Findings
<anything surprising; anything the Reviewer or Verifier needs to know>

## Not checked
<REQUIRED. What you did not verify, and why.>
```

- **Paste the ACTUAL command output.** A claim without output is not a claim.
- For each of the six criteria, run the plan's own check command and paste the
  output. If a criterion is not met, say so plainly - do not restate the
  criterion as if it were met.
- Record the full-suite result under the pinned toolchain (the throwaway
  `--system-site-packages` venv with `pytest==7.4.4` / `pytest-asyncio==0.23.3`,
  as the plan describes), next to the pre-change baseline. **A difference must
  be reported, never smoothed over.**
- Record any file you touched outside the two named ones, and why.
- State what you did not check.
- Reply with a short summary: the commits, the criteria results, anything
  surprising. The FILE is the deliverable.

## Previous attempt (if any)

- None. This is attempt 1.
