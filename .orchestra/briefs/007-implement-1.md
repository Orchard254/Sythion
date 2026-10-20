# Brief: implement - 007 - attempt 1

- Item: 007 The acceptance suite must never write to or delete the product's real credential
- Role: implementer (implementer-4)
- Goal (the slice that matters): Sythion's "better" includes **verification
  before claims** and **honest debt reporting** (`goal.md`,
  `docs/QUALITY_GATES.md:34-44` Gate 3, `:88-97` Gate 8), and the repository's
  own contract says the real credential vault must never be touched by tests
  (`docs/ARCHITECTURE.md:358`). Today a test in the acceptance suite writes to
  and then deletes the developer's REAL Windows Credential Manager entry - and
  every full-suite run in this session has triggered it. Your job: stop that,
  without weakening what the test proves.

## Read

- `C:\Sythion\sythion\.orchestra\plans\007.md` - **THE PLAN. Read it fully
  first.** It is the contract: the two files, the exact fix, the proof, the
  risks, the recorded judgement calls, and the residual side effect it
  discloses.
- `C:\Sythion\sythion\.orchestra\backlog.md` - item 007's acceptance criteria.
- `C:\Sythion\sythion\.orchestra\goal.md` - the goal, the off-limits list.
- `C:\Sythion\sythion\.orchestra\research.md` - candidate **R-020**.
- `C:\Sythion\sythion\backend\tests\acceptance\test_secret_storage.py` - the
  offending test and the `memory_secret_store` fixture.
- `C:\Sythion\sythion\backend\tests\acceptance\conftest.py` - where the guard
  goes.
- `C:\Sythion\sythion\backend\core\secrets.py` - `CredentialManagerStore`,
  `_default_store()` (`:112-121`), `_CM_TARGET_PREFIX` and `_cm_target()`
  (`:32-33`). **Read only - this file is on the off-limits list.**

## Write

- Exactly the two files the plan names:
  `backend/tests/acceptance/conftest.py` and
  `backend/tests/acceptance/test_secret_storage.py`.
- `C:\Sythion\sythion\.orchestra\reports/007-implement-1.md` - your evidence
  report (shape below).
- Your commits, on the branch below.

**Do not write anywhere else.** In particular: not `backend/core/secrets.py`,
not `backend/api/routes.py` (both off-limits), not the root
`backend/tests/conftest.py` (items 002 and 006 contend for it), not
`docs/ARCHITECTURE.md` (criterion 4 restricts the diff), not
`.orchestra/follow-ups.md` (the orchestrator writes that).

## Context

- Current status: item 007 is `planned` in `backlog.md` and `board.json`.
- Branch: **`orchestra/007-no-real-vault-writes`**, branched from `master`.

### Working directory - USE A DEDICATED WORKTREE

```powershell
cd C:\Sythion\sythion
git worktree add C:\Users\orcha\AppData\Local\Temp\opencode\wt-007 -b orchestra/007-no-real-vault-writes master
cd C:\Users\orcha\AppData\Local\Temp\opencode\wt-007
```

Work ONLY inside that worktree. `C:\Sythion\sythion` is the orchestrator's
tree - do not check out branches there, do not commit there. Write your report
to the **main tree's** `.orchestra\reports\007-implement-1.md` (absolute path
below), not to the worktree's copy.

### Facts already established - do not re-derive them

- `backend/tests/acceptance/test_secret_storage.py:139-146` uses
  `secrets_module.CredentialManagerStore()` - the REAL vault - then
  `store.set(SECRET_NAME, "sk-roundtrip-zz")` and `store.delete(SECRET_NAME)`,
  and it never requests the `memory_secret_store` fixture.
- `win32cred` IS importable on this machine, so `_default_store()`
  (`backend/core/secrets.py:112-121`) returns the REAL vault whenever the store
  has been reset to `None`.
- The file's only skip is the `win32cred` ImportError, so **every full-suite
  run executes this test**.
- The current state of the product target, checked READ-ONLY:
  `win32cred.CredEnumerate('Sythion (Synth)/ollama_api_key', 0)` -> winerror
  **1168, "Element not found"**. The entry does not exist right now. The
  `backend/.env` fallback (a 57-character key) is intact. Whether the entry
  ever existed, or whether a test run deleted it, cannot be determined from
  here - and the plan says so.
- `_cm_target()` reads `_CM_TARGET_PREFIX` **at call time**, so a test can pin
  a test-only prefix and still drive the real `win32cred` store.
- No test outside `backend/tests/acceptance/` touches the vault today: the
  `CredentialManagerStore` / `win32cred` / `set_secret` / `delete_secret`
  references under `backend/tests/` are confined to
  `test_secret_storage.py` and `test_setup_readiness.py`, and the latter uses
  `MemoryStore` exclusively.

## Constraints

- Work only on the assigned item and branch. No unrelated edits.
- Commit small and often with clear messages.
- **Never push.** Never deploy, publish, or touch real data.
- **Never run destructive commands:** `rm -rf`, `git reset --hard`,
  `git clean -fd`, `git checkout -- .`, `git stash drop`, force-push, or
  anything that deletes files git does not track.
- **VAULT DISCIPLINE - the most important constraint of this item.**
  - You MAY write to and delete from a **test-scoped** target
    (`Sythion (Synth) [TEST]/...`) - that is the point of the fix.
  - You MUST NOT write to or delete from the **product** target
    (`Sythion (Synth)/...`) at any point. If your implementation ever tries
    to, stop and fix the implementation - do not run it to "see what happens".
  - Never print a credential value. Listing target NAMES is fine; reading the
    stored secret is not.
  - A read-only `CredEnumerate` existence check is permitted, and the
    orchestrator has already done it.
  - **Do not deliberately run the offending test on `master` to reproduce the
    bug.** The plan's proof uses a throwaway FAKE-Vault pytest plugin outside
    the repository, which records every write/delete target with zero vault
    risk. Use that. If you believe you need a real-vault reproduction, say so
    in your report and explain why the fake-vault probe is insufficient -
    do not simply run it against the real vault.
- **The test must keep proving the real round trip.** Do not swap in a memory
  store, do not skip the test, do not weaken its assertions. A fix that stops
  the write and also stops the proof is a failed item, and the plan names the
  exact checks that detect it.
- The autouse guard must raise BEFORE delegating to `win32cred`, and the
  exception must NOT be a `pywintypes.error` (`CredentialManagerStore.delete`
  swallows those). The product prefix must be snapshotted at conftest import
  time, because the round-trip test patches `_CM_TARGET_PREFIX` and a
  dynamically-read prefix would disarm the guard.
- The guard must not touch `secrets_module._store`, so no other acceptance
  test observes a different secret store than before.
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

Write `C:\Sythion\sythion\.orchestra\reports/007-implement-1.md` with this
shape (the template is at `.orchestra/report-template.md`):

```
# Report: implement - 007 - attempt 1

## What I did
<the exact commands you ran, the commits you made (sha + message), the files you touched>

## Evidence
<every claim with its evidence: command + output, file:line, test name>

## Acceptance criteria
| # | Criterion | Result | Evidence |
|---|---|---|---|
| 1 | ... | ... | ... |
(all of them, from the backlog item)

## The fake-vault probe
<the throwaway plugin you used, its output on master-equivalent and on the branch, and where it lives>

## Findings
<anything surprising; the residual side effect the plan discloses - a stray
 `Sythion (Synth) [TEST]/` entry if a run dies mid-test - and what the
 operator should look for>

## Not checked
<REQUIRED>
```

- **Paste the ACTUAL command output.** A claim without output is not a claim.
- For each acceptance criterion, run the plan's own check command and paste
  the output. If a criterion is not met, say so plainly.
- Include the fake-vault probe's output for BOTH the offending behaviour and
  the fixed behaviour - that pair is the proof, and the plan says explicitly
  that a before/after enumeration alone is NOT a proof (master writes then
  deletes, so it ends in the same state and the check would pass on master
  too).
- Record any file you touched outside the two named ones, and why.
- State what you did not check.
- Reply with a short summary: the commits, the criteria results, and anything
  surprising. The FILE is the deliverable.

## Previous attempt (if any)

- None. This is attempt 1.
