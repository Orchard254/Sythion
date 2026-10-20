# Brief: implement - 006 - attempt 1

- Item: 006 The readiness test must not borrow the developer's gitignored `backend/.env`
- Role: implementer (implementer-5)
- Goal (the slice that matters): Sythion's "better" includes **verification
  before claims** and **release and operator readiness** (`goal.md`,
  `docs/QUALITY_GATES.md:34-44` Gate 3, `:88-97` Gate 8). A test suite whose
  result depends on a file that is deliberately NOT in the repository is not a
  release-confidence signal: a GitHub Actions runner is a clean checkout, so the
  pipeline that item 001 repaired would go RED on its first real run for a
  reason that has nothing to do with the pipeline. Your job: make the suite
  independent of the developer's working tree, without weakening what the
  test proves.

## Read

- `C:\Sythion\sythion\.orchestra\plans\006.md` - **THE PLAN. Read it fully
  first.** It is the contract: the two files, the exact fix, the proof, the
  risks, the recorded judgement calls.
- `C:\Sythion\sythion\.orchestra\backlog.md` - item 006's acceptance criteria.
- `C:\Sythion\sythion\.orchestra\goal.md` - the goal, the off-limits list.
- `C:\Sythion\sythion\.orchestra\research.md` - candidate **R-019**.
- `C:\Sythion\sythion\backend\tests\test_setup_readiness.py` - the affected
  test, the file's own `_clear_local_env_key` helper (`:195-204`), and the two
  sibling tests that already call it (`:215`, `:237`).
- `C:\Sythion\sythion\backend\tests\conftest.py` - where the autouse fixture
  goes. **Append at the END of the file** - see the conflict note below.
- `C:\Sythion\sythion\backend\core\config.py` - `model_config` (`:173-176`)
  and the import-time singleton (`:179`). Read only.

## Write

- Exactly the two files the plan names: `backend/tests/test_setup_readiness.py`
  and `backend/tests/conftest.py`.
- `C:\Sythion\sythion\.orchestra\reports/006-implement-1.md` - your evidence
  report (shape below).
- Your commits, on the branch below.

**Do not write anywhere else.** Not `backend/core/secrets.py` or
`backend/api/routes.py` (both off-limits), not `backend/tests/acceptance/*`,
not `docs/*`, not `.orchestra/follow-ups.md` (the orchestrator writes that).

## Context

- Current status: item 006 is `planned` in `backlog.md` and `board.json`.
- Branch: **`orchestra/006-readiness-test-isolation`**, branched from `master`.

### Working directory - USE A DEDICATED WORKTREE

```powershell
cd C:\Sythion\sythion
git worktree add C:\Users\orcha\AppData\Local\Temp\opencode\wt-006 -b orchestra/006-readiness-test-isolation master
cd C:\Users\orcha\AppData\Local\Temp\opencode\wt-006
```

Work ONLY inside that worktree. `C:\Sythion\sythion` is the orchestrator's
tree - do not check out branches there, do not commit there. Write your report
to the **main tree's** `.orchestra\reports\006-implement-1.md`.

### CONFLICT NOTE - item 002 touches the same file

Item 002 (in review, merging imminently) also edits
`backend/tests/conftest.py`. To keep the two changes mergeable:
- **Append** your autouse fixture at the END of the file. Do not insert
  earlier and do not restructure what is already there.
- Do not touch `pytest_configure`, the marker declarations, the marker
  classification rules, or the selection-contract guard - those are item
  002's.
- The Committer rebases your branch onto the current `master` before merging,
  and will resolve a conflict if one appears. Report clearly what region of
  `conftest.py` you touched.

### Facts already established - do not re-derive them

- `backend/core/config.py:173-176` points pydantic-settings at
  `<repo>/backend/.env` with `env_prefix="SYTHION_"`; `:179` builds
  `settings = Settings()` **once, at import**. `backend/.env` is gitignored
  (`.gitignore:16`), so it exists in a developer's tree but NOT in a fresh
  clone and NOT on a CI runner.
- The `.env` file and the `SYTHION_OLLAMA_API_KEY` env var are
  **alternatives** - either alone satisfies the check; only the complete
  absence of both fails it. A 2x2 matrix established this.
- Because the singleton is built at import, **setting the env var at runtime
  has NO effect** - the fix must pin the attribute on the already-imported
  settings object, not the variable.
- Exactly ONE test in the suite depends on the missing `.env`:
  `test_setup_readiness.py::test_readiness_states` (`:144-177`). It pins nine
  of the ten readiness checks and borrows the tenth (`ollama_api_key_ok`),
  because `has_secret(OLLAMA_API_KEY)` is False against its fresh empty
  `MemoryStore`.
- The correct pattern already exists in the same file: `_clear_local_env_key`
  (`:195-204`) does `monkeypatch.setattr(core_config.settings,
  "ollama_api_key", "")`, and the two sibling tests call it (`:215`, `:237`).
  Only the test that asserts the POSITIVE state omits it.
- The fix shape was **proven**: pinning the credential on the already-imported
  settings singleton in a `.env`-free scratch tree gives `8 passed`.
- `"test-key"` is this repository's own placeholder idiom
  (`backend/tests/acceptance/test_onboarding.py:55`) and is in the payload
  checker's placeholder vocabulary, so it can never be mistaken for a
  credential.

## Constraints

- Work only on the assigned item and branch. No unrelated edits.
- Commit small and often with clear messages.
- **Never push.** Never deploy, publish, or touch real data.
- **Never run destructive commands:** `rm -rf`, `git reset --hard`,
  `git clean -fd`, `git checkout -- .`, `git stash drop`, force-push, or
  anything that deletes files git does not track.
- **Never read, copy or print a credential value.** Listing key NAMES and
  lengths is fine. The developer's real `backend/.env` must not be copied,
  printed or read for its values - the plan's approach deliberately computes
  the clean values with the env file DISABLED (`Settings(_env_file=None)`),
  and you must keep it that way.
- **The fix must not make the test pass for the WRONG reason.** "The
  credential is pinned" is not the same as "the readiness endpoint correctly
  reports ready when a credential is configured". The plan adds explicit
  assertions on `body["checks"]["ollama_api_key_ok"]` in BOTH the healthy and
  the incomplete blocks, and two new tests. Keep them.
- **The global autouse fixture must not change what other tests observe.** The
  plan's mechanism is to assign only CLEAN values, computed from
  `Settings(_env_file=None)`, and it requires you to PROVE that with a probe
  that prints only a BOOLEAN, never a value - in a worktree,
  `bool(Settings(_env_file=None).ollama_api_key)` must be `False`. The plan
  also records a `provider_e2e` carve-out because those tests' import-time gate
  runs before fixtures. Keep the carve-out.
- Do not run `make clean`, `scripts/stage_python_runtime.py`, migrations, or
  `npx tauri build`. Do not run or dispatch the GitHub workflow.
- **Known hazard:** the suite is not deterministic on this machine (2 native
  crashes in 6 observed full-suite runs, of two different kinds; neither
  reproduces in isolation; the smallest reproducer is the FULL SUITE). If a
  full-suite run dies that way, re-run it and record BOTH attempts - it is a
  pre-existing condition, not your failure.
- Do not ask the user anything. If something needs a decision you cannot
  make, say so in the report and stop.

## Output contract

Write `C:\Sythion\sythion\.orchestra\reports/006-implement-1.md`:

```
# Report: implement - 006 - attempt 1

## What I did
<the exact commands you ran, the commits you made (sha + message), the files you touched>

## Evidence
<every claim with its evidence: command + output, file:line, test name>

## Acceptance criteria
| # | Criterion | Result | Evidence |
|---|---|---|---|
| 1 | ... | ... | ... |
(all of them, from the backlog item)

## The .env-free proof
<how you ran the suite in an environment where backend/.env is ABSENT, the
 output for the base tree and for your branch, and the fact that the
 developer's real .env and credential were never read or copied>

## Findings
<anything surprising; anything the Reviewer or Verifier needs to know>

## Not checked
<REQUIRED>
```

- **Paste the ACTUAL command output.** A claim without output is not a claim.
- For each acceptance criterion, run the plan's own check command and paste
  the output. If a criterion is not met, say so plainly.
- The proof must be meaningful in BOTH states of the world: on the main tree
  `backend/.env` EXISTS, so "the test passes" proves nothing there. Run the
  suite somewhere the `.env` is ABSENT - a fresh git worktree, or a scratch
  copy of the tree - and report the base-tree result next to your branch's.
- Record any file you touched outside the two named ones, and why.
- State what you did not check.
- Reply with a short summary: the commits, the criteria results. The FILE is
  the deliverable.

## Previous attempt (if any)

- None. This is attempt 1.
