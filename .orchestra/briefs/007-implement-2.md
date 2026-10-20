# Brief: implement - 007 - attempt 2 (recovery of the interrupted round)

- Item: 007 The acceptance suite must never write to or delete the product's real credential
- Role: implementer (implementer-4, recovery dispatch)
- Goal (the slice that matters): Sythion's "better" includes **verification
  before claims** and **honest debt reporting** (`goal.md`,
  `docs/QUALITY_GATES.md:34-44` Gate 3, `:88-97` Gate 8), and the repository's
  own contract says the real credential vault must never be touched by tests
  (`docs/ARCHITECTURE.md:358`). A test suite that deletes the developer's
  stored credential is not a release-confidence signal.

## Why this dispatch exists

The first round was interrupted by a **network fault** (`ECONNRESET`) after the
work was committed but **before the report was written**. Nothing was lost:
- The fix is committed as **`a0fa314`** and **`e1ff9b1`** on
  `orchestra/007-no-real-vault-writes`.
- The worktree `C:\Users\orcha\AppData\Local\Temp\opencode\wt-007` is
  **clean** (no uncommitted changes).

Your job is to **verify the committed work, fix the one defect the
orchestrator found in it (below), gather the evidence, and write the report**.

## Read

- `C:\Sythion\sythion\.orchestra\plans\007.md` - **THE PLAN.** It is the
  contract: the two files, the exact fix, the proof, the risks, the recorded
  judgement calls, and the residual side effect it discloses.
- `C:\Sythion\sythion\.orchestra\backlog.md` - item 007's acceptance criteria.
- `C:\Sythion\sythion\.orchestra\research.md` - candidate **R-020**.
- `C:\Sythion\sythion\.orchestra\critiques.md` - the orchestrator's critique
  of the Researcher's pass that produced R-020.
- `C:\Sythion\sythion\backend\core\secrets.py` - `CredentialManagerStore`,
  `_default_store()` (`:112-121`), `_CM_TARGET_PREFIX` and `_cm_target()`
  (`:32-33`). **Read only - off-limits.**

## Write

- `C:\Sythion\sythion\.orchestra\reports/007-implement-1.md` - the evidence
  report (shape below). **This is the deliverable.**
- A commit on top of `e1ff9b1` for the correction described below.

## Context

- Current status: item 007 is `building`; the implementation is committed; the
  report and one correction are outstanding.
- Branch: **`orchestra/007-no-real-vault-writes`** - worktree `wt-007` is
  already checked out at `e1ff9b1`. Work there; do not create another
  worktree for the same branch (git will refuse).
- Plan: `plans/007.md`.

### The orchestrator has read the committed diff. It is good.

- `backend/tests/acceptance/conftest.py` - an autouse fixture that wraps
  `win32cred.CredWrite` / `CredDelete`, checks the target against
  `_PRODUCT_TARGET_PREFIX`, and raises BEFORE delegating. The product prefix
  is snapshotted **at conftest import time** (correct - the round-trip test
  patches `_CM_TARGET_PREFIX`, and a call-time read would disarm the guard).
  The exception is an `AssertionError` subclass, deliberately not a
  `pywintypes.error` (`CredentialManagerStore.delete` swallows those). Reads
  are not intercepted. `secrets_module._store` is not touched.
- `backend/tests/acceptance/test_secret_storage.py` - the round-trip test now
  pins `_CM_TARGET_PREFIX` to `"Sythion (Synth) [TEST]/"`, pins `_store` to
  the real store, asserts the composed target is the test-scoped one and is
  NOT under the product prefix (checked against the import-time snapshot, not
  the patched attribute - correct), keeps every round-trip assertion, and
  adds a read-only self-check.

All of that matches the plan. Keep it.

### THE ONE DEFECT THE ORCHESTRATOR FOUND - a false claim in a code comment

In `test_secret_storage.py`, the comment above the self-check says:

> The enumeration is deliberately UNFILTERED and filtered here. The
> prefix-filtered form, `CredEnumerate(prefix, 0)`, returns winerror 1168 on
> this machine even for credentials that demonstrably exist (it fails for
> every prefix tried), so it cannot tell "absent" from "broken enumerator".

**The conclusion in that comment is wrong, and the orchestrator measured it
(read-only, no credential value read, no write, no delete):**

| call | result |
|---|---|
| `CredEnumerate(None, 0)` (unfiltered) | **8** credentials visible to this user; **none** of their target names contains "Sythion" |
| `CredEnumerate(<EXACT target name of an existing credential>, 0)` | **1** - the exact-match filtered form **works** |
| `CredEnumerate(<directory-style PREFIX of that same existing credential>, 0)` | **1168** - the prefix form **is** broken on this machine |

Two consequences:

1. The **directory-prefix** form really is broken here. But the form the
   orchestrator's original check used was the **exact-match** form, with the
   exact target name `Sythion (Synth)/ollama_api_key` - and that form works.
   So the orchestrator's original 1168 **does** mean the credential is
   **absent**; it was never inconclusive, and the comment's implication that
   it was is false.
2. The product credential is **confirmed absent**: the unfiltered enumeration
   sees 8 credentials and none is a Sythion target. The test-scoped entry
   `Sythion (Synth) [TEST]/ollama_api_key` is **also absent** right now, i.e.
   the committed test cleans up after itself.

**What to do:** the ACTION in the code - enumerating unfiltered and filtering
in Python - is the most robust choice available, and you should KEEP it. The
**justification** must be corrected so the comment tells the truth: say
precisely that the *directory-prefix* form is broken on this machine, that the
*exact-match* form works, and that the unfiltered form is used because it
cannot be defeated by either quirk - not because the orchestrator's check was
inconclusive. Remove the misleading implication.

Do not change the behaviour of the self-check; change the claim.

### What still has to be PROVEN (and was the interrupted round's job)

1. **The guard has teeth.** Demonstrate that an acceptance test attempting to
   write to or delete a product-prefixed target is BLOCKED - the guard's
   exception is raised and the real vault is never reached. Do this in a way
   that cannot touch the real vault (a fake `win32cred` stand-in, or a
   scratch copy), and paste the output.
2. **The fake-vault probe.** The plan's proof is a throwaway pytest plugin
   OUTSIDE the repository that records every write/delete target: on master it
   must record `Sythion (Synth)/ollama_api_key` and fail; on the branch it
   must record the test-scoped target and pass. The plan says explicitly that
   a before/after enumeration alone is **NOT** a proof - master writes then
   deletes, so it ends in the same state and that check would pass on master
   too. Paste the probe's output for BOTH trees.
3. **The round-trip test still proves a real round trip** - it writes to and
   reads back from the REAL Windows Credential Manager, at the test-scoped
   target, and the read-only self-check sees it there.
4. **The rest of the acceptance suite is unaffected** - the guard does not
   change what other acceptance tests observe. Show the acceptance suite
   result before and after, and the per-test outcome comparison if the
   numbers differ.

## Constraints

- Work only on the assigned item and branch. Commit on top of `e1ff9b1`; do
  not rewrite history, do not rebase, do not amend the existing commits.
- **VAULT DISCIPLINE - the most important constraint of this item.**
  - You MAY write to and delete from a **test-scoped** target
    (`Sythion (Synth) [TEST]/...`) - that is the point of the fix.
  - You MUST NOT write to or delete from the **product** target
    (`Sythion (Synth)/...`) at any point. If your implementation ever tries
    to, stop and fix the implementation - do not run it to "see what happens".
  - Never print a credential value. Listing target NAMES is fine; reading the
    stored secret is not.
  - A read-only `CredEnumerate` existence check is permitted.
  - **Do not deliberately run the offending test on `master` to reproduce the
    bug.** Use the fake-vault probe. If you believe you need a real-vault
    reproduction, say so in your report and explain why the fake-vault probe is
    insufficient - do not simply run it against the real vault.
- **The test must keep proving the real round trip.** Do not swap in a memory
  store, do not skip the test, do not weaken its assertions.
- Do not touch `backend/core/secrets.py` or `backend/api/routes.py` (both on
  the goal's off-limits list), the ROOT `backend/tests/conftest.py` (items 002
  and 006 contend for it), `docs/ARCHITECTURE.md`, or
  `.orchestra/follow-ups.md` (the orchestrator writes that).
- **Never push.** Never deploy, publish, or touch real data.
- **Never run destructive commands:** `rm -rf`, `git reset --hard`,
  `git clean -fd`, `git checkout -- .`, `git stash drop`, force-push.
- Do not run `make clean`, `scripts/stage_python_runtime.py`, migrations, or
  `npx tauri build`.
- **Known hazard:** the suite is not deterministic on this machine (2 native
  crashes in 6 observed full-suite runs, of two different kinds; neither
  reproduces in isolation; the smallest reproducer is the FULL SUITE). If a
  full-suite run dies that way, re-run it and record BOTH attempts - it is a
  pre-existing condition, not your failure.
- Do not ask the user anything.

## Output contract

Write `C:\Sythion\sythion\.orchestra\reports/007-implement-1.md`:

```
# Report: implement - 007 - attempt 1

## What I did
<the exact commands you ran, the commits (sha + message), the files you touched>

## Evidence
<every claim with its evidence: command + output, file:line>

## Acceptance criteria
| # | Criterion | Result | Evidence |
|---|---|---|---|
| 1 | ... | ... | ... |
(all of them, from the backlog item)

## The fake-vault probe
<the throwaway plugin, its output on the offending behaviour and on the fixed
 behaviour, and where it lives>

## The guard has teeth
<the demonstration that a product-target write is blocked, and the proof the
 real vault was never reached>

## The correction to the enumeration comment
<what you changed and why - the exact-match form works, the directory-prefix
 form is broken, the unfiltered form is used because it is not defeated by
 either quirk>

## Findings
<anything surprising; the residual side effect the plan discloses - a stray
 `Sythion (Synth) [TEST]/` entry if a run dies mid-test - and what the
 operator should look for>

## Not checked
<REQUIRED>
```

- **Paste the ACTUAL command output.** A claim without output is not a claim.
- If a criterion is not met, say so plainly.
- Record any file you touched outside the two named ones, and why.
- Reply with a short summary. The FILE is the deliverable.

## Previous attempt (if any)

- The first round was interrupted by a network fault after committing
  `a0fa314` and `e1ff9b1` and before writing this report. This dispatch
  recovers the evidence, applies the one correction, and writes the report.
