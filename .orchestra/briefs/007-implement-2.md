# Brief: implement - 007 - attempt 2 (hardening round)

- Item: 007 The acceptance suite must never write to or delete the product's real credential
- Role: implementer (implementer-007, hardening round 1)
- Goal (the slice that matters): Sythion's "better" includes **verification
  before claims** and **honest debt reporting** (`goal.md`,
  `docs/QUALITY_GATES.md:34-44` Gate 3, `:88-97` Gate 8), and the repository's
  own contract says the real credential vault must never be touched by tests
  (`docs/ARCHITECTURE.md:358`). The guard this item installs is the thing that
  makes that claim true. **This round closes two holes in that guard.**

## Read

- `C:\Sythion\sythion\.orchestra\reviews/007-r1.md` - **THE REVIEW.** Read it
  fully. Your two findings are findings 1 and 2 of its "Findings" list.
- `C:\Sythion\sythion\.orchestra\reports/007-implement-1.md` - your own
  previous report (for the shape, the harness code, and the "before" numbers).
- `C:\Sythion\sythion\.orchestra\plans/007.md` - the plan.
- `C:\Sythion\sythion\.orchestra\backlog.md` - item 007's acceptance criteria.
- The two files: `backend/tests/acceptance/conftest.py`,
  `backend/tests/acceptance/test_secret_storage.py`.

## Write

- `C:\Sythion\sythion\.orchestra\reports/007-implement-2.md` - the evidence
  report for this round. **This is the deliverable.**
- Commits on top of `4680e0b`.

## Context

- Current status: item 007 is `in-review`; the Reviewer returned
  `VERDICT: ACCEPT` with **eight findings, NONE of them a criterion
  failure**. This round is not a redo of a failed criterion - it closes two
  hardening gaps the Reviewer found, at the orchestrator's direction.
- Branch: **`orchestra/007-no-real-vault-writes`** - worktree `wt-007` is
  already checked out at `4680e0b`. Work there; do NOT create another
  worktree for the same branch (git will refuse).
- Plan: `plans/007.md`.

### THE TWO HOLES TO CLOSE

**HOLE 1 - the prefix comparison is CASE-SENSITIVE, but Windows credential
target names are NOT.** `backend/tests/acceptance/conftest.py:77`:

```python
if isinstance(target, str) and target.startswith(_PRODUCT_TARGET_PREFIX):
```

The Reviewer measured that writing `Sythion (Synth) [TEST]/caseprobe` and then
`sythion (synth) [test]/caseprobe` produced **ONE** credential - the second
write replaced the first. **The orchestrator reproduced this independently**
(writing with the product's own credential shape from
`backend/core/secrets.py:64-73`): after writing both spellings,
`CredEnumerate(None, 0)` showed exactly ONE entry, carrying the **lower-case**
spelling. So Windows credential slots are **case-insensitive**.

The consequence: a future test that wrote `sythion (synth)/ollama_api_key`
would land in the product's credential slot and would **NOT** trip the
guard. Nothing in the suite does that today (the prefix is a constant), so it
is not reachable today - but the guard's whole claim is "the suite must NEVER
write to the product's credential", and a guard defeated by the case of a
string constant does not make that claim.

**FIX: compare case-insensitively.** The obvious shape is to compare
lower-cased forms. State in a short comment WHY - that Windows credential
target names are case-insensitive, so `sythion (synth)/...` and
`Sythion (Synth)/...` are the same slot - so a future reader does not "fix"
the comparison back to case-sensitive.

**BE CAREFUL:** the product prefix is `Sythion (Synth)/` and the test-scoped
prefix is `Sythion (Synth) [TEST]/`. These differ at the character after
`Sythion (Synth)` - `/` versus ` [`. A case-insensitive comparison must
STILL let the test-scoped prefix through. **Prove that** with a test or a
transcript: a write to `Sythion (Synth) [TEST]/...` is still ALLOWED, and a
write to `Sythion (Synth)/...` is still BLOCKED - in both the original and
the lower-cased spelling.

**HOLE 2 - the guard FAILS OPEN when it cannot read the target.**
`backend/tests/acceptance/conftest.py:89`:

```python
target = cred.get("TargetName", "") if hasattr(cred, "get") else ""
```

For a credential object that is not dict-like, this yields `""`. `""` does not
start with the product prefix, so the check PASSES, and the call is
**DELEGATED** to the real vault. A non-dict credential carrying the product
target would slip straight past the guard.

**FIX: fail CLOSED.** When the target cannot be determined, the guard must
REJECT, not delegate. `CredentialManagerStore.set` builds a plain dict
(`backend/core/secrets.py:64-73`), so the normal path is unaffected - but the
guard's rule should be "reject unless you can PROVE the target is not a
product target".

**BE CAREFUL:** `guarded_delete(target, *args, **kwargs)` receives the target
as a plain STRING, so its target is always determinable. Do not break the
delete path. And do not accidentally reject a legitimate test-scoped write.

### WHAT MUST NOT REGRESS - the Reviewer confirmed all of this

- The guard has teeth: 3/3 product-target attempts blocked (a store `set`, a
  store `delete`, and the disarm attempt of a DIRECT `CredWrite` with the
  prefix patched), plus an API-driven write; a non-delegating spy recorded
  **no** product-prefixed target, proving interception happens BEFORE
  delegation. **Re-run this proof after your change** - the case-insensitive
  comparison must not accidentally weaken it, and the fail-closed change must
  not break the legitimate path.
- The round trip is still real: 10/10 against the real vault; with the write
  neutralised the test goes RED at `test_secret_storage.py:177`; with the
  enumerator blinded it goes RED at `:197`. **Re-verify both.**
- The acceptance suite is `191 passed, 2 skipped` with identical per-test
  outcome sets.
- The branch's full suite is `1566 passed, 45 skipped` (the Reviewer measured
  this independently: `1566 passed, 45 skipped in 381.13s`, exit 0).
- `git status --short` in the worktree is empty - no test is deleted, skipped,
  xfailed or marked.
- The guard is complete for the namespace the product uses: the only vault
  route in the tree is `backend/core/secrets.py`; `CredRename` on generic
  credentials fails with winerror 50.

### The Reviewer's OTHER SIX findings - do NOT fix them in this round

They are recorded, and most are out of this round's scope. Do not "improve"
them here; the orchestrator has queued them.

3. The guard is acceptance-scoped - a test OUTSIDE `backend/tests/acceptance/`
   writing to the vault is unguarded. Moving the guard to the root
   `backend/tests/conftest.py` is the right follow-up, and that file is still
   contended by items 002 and 006. **Do not touch the root conftest.**
4. `backend/main.py:274`'s global `@app.exception_handler(Exception)` would
   turn the guard's exception into a 500 response if a future acceptance test
   drove a vault write through `main.app`. No acceptance test uses `main.app`
   today. **Note the constraint in the conftest's docstring** if you do so
   without widening the diff - your judgement, but say what you did.
5. `test_secret_storage.py:196`'s `except Exception` around the enumeration is
   broader than the 1168 it is about. Cosmetic; it still fails loudly. Leave
   it, or narrow it - **your judgement, but say which and why.**
6. The `CredWrite` wrapper signature matches the real one - no action.
7. The product prefix is snapshotted twice, in two modules. Deliberate and
   correct. **Do not de-duplicate it.**
8. Doc debt in `docs/ARCHITECTURE.md:358` and `docs/ACCEPTANCE_TESTS.md:199` -
   item 003's file and the follow-up list's. **Do not edit `docs/`.**

## Constraints

- Work only on the assigned item and branch. Commit on top of `4680e0b`. Do
  not rewrite history, do not rebase, do not amend.
- **The diff must stay inside `backend/tests/acceptance/`.** Do NOT edit
  `backend/core/secrets.py`, `backend/api/routes.py`, `backend/main.py`, the
  ROOT `backend/tests/conftest.py` (items 002 and 006 contend for it), or any
  `docs/` file.
- Do NOT write `.orchestra/follow-ups.md` - the orchestrator owns it.
- **VAULT DISCIPLINE - the most important constraint.**
  - You MAY write to and delete from a **test-scoped** target
    (`Sythion (Synth) [TEST]/...`).
  - You MUST NOT write to or delete from the **product** target
    (`Sythion (Synth)/...`) at any point, in any spelling.
  - Never print a credential value. Listing target NAMES is fine.
  - A read-only `CredEnumerate` existence check is permitted.
  - **Do not deliberately run the offending test on `master` to reproduce the
    bug.** Use a fake-vault stand-in.
- The test must keep proving the real round trip - do not swap in a memory
  store, do not skip the test, do not weaken its assertions.
- **Never push.** Never deploy, publish, or touch real data.
- **Never run destructive commands:** `rm -rf`, `git reset --hard`,
  `git clean -fd`, `git checkout -- .`, `git stash drop`, force-push.
- Do not run `make clean`, `scripts/stage_python_runtime.py`, migrations, or
  `npx tauri build`.
- **Known hazard:** the suite is not deterministic on this machine (2 native
  crashes in 6 observed full-suite runs, of two different kinds; neither
  reproduces in isolation; the smallest reproducer is the FULL SUITE). If a
  run dies that way, re-run it and record BOTH attempts.
- Do not ask the user anything.

## Output contract

Write `C:\Sythion\sythion\.orchestra\reports/007-implement-2.md`:

```
# Report: implement - 007 - attempt 2

## What I did
<the commands you ran; the commits (sha + message); the files you touched>

## Evidence
<every claim with its evidence: command + output, file:line>

## The two holes
| # | Hole | Status | Evidence that it is now closed |
|---|---|---|---|
| 1 | case-sensitive prefix comparison | closed / not closed | ... |
| 2 | fails open on an unreadable credential | closed / not closed | ... |

## Both spellings, both directions
<the transcript proving: a product-target write is BLOCKED in the original
 AND the lower-cased spelling, and a test-scoped write is STILL ALLOWED in
 both spellings>

## What did not regress
<the Reviewer's confirmed list, re-verified with your numbers>

## The other six findings
<what you decided about each, if anything - most are out of scope>

## Findings
## Not checked
<REQUIRED>
```

- **Paste the ACTUAL command output.** A claim without output is not a claim.
- For hole 1, show the BEFORE behaviour (the lower-cased product target is
  NOT caught) and the AFTER behaviour (it IS caught) - the before-behaviour
  proof must be done with a FAKE vault or a spy, never against the real
  product target.
- For hole 2, show a non-dict credential object being rejected.
- If a hole is not closed, say so plainly.
- Reply with a short summary. The FILE is the deliverable.

## Previous attempt (if any)

- Round 1: `reports/007-implement-1.md` - accepted by the orchestrator; the
  Reviewer then returned `VERDICT: ACCEPT` with eight non-blocking findings.
- Round 2 (this one): close holes 1 and 2.
