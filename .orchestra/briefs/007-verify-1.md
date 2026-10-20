# Brief: verify - 007 - attempt 1

- Item: 007 The acceptance suite must never write to or delete the product's real credential
- Role: verifier
- Goal (the slice that matters): Sythion's "better" includes **verification
  before claims** and **honest debt reporting** (`goal.md`,
  `docs/QUALITY_GATES.md:34-44` Gate 3, `:88-97` Gate 8), and the repository's
  own contract says the real credential vault must never be touched by tests
  (`docs/ARCHITECTURE.md:358`). **You are the last gate before this reaches
  `master`.** Your job is to independently decide whether the item does what
  it claims - not to re-run the implementer's evidence and call it a day.

## Read

- `C:\Sythion\sythion\.orchestra\plans/007.md` - the plan (the contract).
- `C:\Sythion\sythion\.orchestra\backlog.md` - item 007's acceptance criteria.
- `C:\Sythion\sythion\.orchestra\reports/007-implement-1.md` - the
  implementer's first report.
- `C:\Sythion\sythion\.orchestra\reports/007-implement-2.md` - the hardening
  round's report.
- `C:\Sythion\sythion\.orchestra\reviews/007-r1.md` - the Reviewer's report.
- `C:\Sythion\sythion\.orchestra\critiques.md` - the orchestrator's critiques
  of the implementer, the reviewer and the hardening round.
- The branch: `orchestra/007-no-real-vault-writes`, four commits:
  `a0fa314` (the guard), `e1ff9b1` (the unfiltered enumeration),
  `4680e0b` (the comment correction), `468a17a` (the hardening).
  Diff against `master`:
  `git -C C:\Sythion\sythion diff master...orchestra/007-no-real-vault-writes`

## Write

- `C:\Sythion\sythion\.orchestra\verifications/007-v1.md` - the ONLY file you
  may write. Use `.orchestra/verification-template.md` as the shape.

**Your file's FIRST LINE must be exactly `VERDICT: FIXED` or `VERDICT: NOT
FIXED` or `VERDICT: REGRESSED`.** Nothing may precede it. **Your report must
also contain a line `- Verified commit: <sha>`** and a line `- Tier:
NORMAL|EXTERNAL|SENSITIVE|SENSITIVE+EXTERNAL`.

## Context

- Current status: item 007 is `accepted` pending your verification, then it
  goes to the Committer.
- Branch: `orchestra/007-no-real-vault-writes`, tip `468a17a`.
- Plan: `plans/007.md`.

### Where to work

The branch is **already checked out** at
`C:\Users\orcha\AppData\Local\Temp\opencode\wt-007`. Use it for inspection
and for running checks. Do NOT create another worktree for the same branch
(git will refuse). Do not commit or modify anything there - except a
throwaway probe file, which you must DELETE when you are done. There is also
a detached scratch worktree `wt007-master`; leave it for the orchestrator.

### What has already been established - do NOT merely re-derive it

- The diff is exactly **two** files, both under `backend/tests/acceptance/`:
  `conftest.py` and `test_secret_storage.py`. **No production module is
  touched** - `backend/core/secrets.py`, `backend/api/routes.py` and
  `backend/main.py` are unchanged.
- The orchestrator has ALREADY verified the guard's behaviour independently,
  by running five probes as a real pytest test INSIDE
  `backend/tests/acceptance/` with a fake `win32cred` installed by a plugin -
  so the acceptance conftest's own autouse guard was exercised, not a copy:
  - product target `Sythion (Synth)/ollama_api_key` -> BLOCKED;
  - product target `sythion (synth)/ollama_api_key` -> BLOCKED (the spelling
    the pre-hardening guard let through);
  - test-scoped `Sythion (Synth) [TEST]/ollama_api_key` -> ALLOWED;
  - test-scoped `sythion (synth) [test]/ollama_api_key` -> ALLOWED;
  - `object()`, `{"UserName": "probe"}` and `{"TargetName": 12345}` -> all
    REJECTED (fail-closed);
  - the delete path: product blocked in both spellings, test-scoped allowed in
    both.
  **5 passed.** You do not need to redo this. Your job is the things below.
- The Reviewer independently measured: the branch's full suite is
  `1566 passed, 45 skipped in 381.13s`, exit 0; the acceptance suite is
  `191 passed, 2 skipped`; `git status --short` is empty.
- **Windows credential target names are case-insensitive** - the orchestrator
  measured this independently by writing both spellings at a TEST-SCOPED
  target: the second write replaced the first, and the enumeration showed one
  entry carrying the lower-case spelling.
- The unfiltered enumeration currently shows **8** credentials for this user
  and **no** "Sythion" target - the product entry and the test-scoped one are
  both absent. Every probe in this run was cleaned up.

### Pre-existing conditions - do NOT blame the branch for these

1. **A clean checkout has no `backend/.env`** (gitignored), so
   `backend/tests/test_setup_readiness.py::test_readiness_states` fails in any
   worktree. That is item 006's subject.
2. **The suite is not deterministic here** - 2 native crashes in 6 observed
   full-suite runs, of two different kinds; neither reproduces in isolation;
   the smallest reproducer is the FULL SUITE. A native crash in your run is
   pre-existing, not a regression - but you must RECORD it and re-run.
3. **`make` is not installed on this machine.**
4. The plan's `1564 passed` baseline has moved to `1566` (item 004 added two
   tests). Item 007 adds zero.

## What YOU must verify - the things nobody has yet

1. **THE ITEM'S CENTRAL CLAIM, checked by an independent route.** The claim
   is: *no test writes to or deletes the product's credential target*. Do not
   rely on the implementer's probe or the orchestrator's probe. Design your
   OWN check. The strongest available shape is a **spy that records every
   write and delete target across a whole acceptance-suite run** - install it
   as a pytest plugin OUTSIDE the repository, run the acceptance suite, and
   show that NOT ONE recorded target is under the product prefix (in any
   spelling). The implementer's master run with a product-target blocker
   recorded exactly ONE product-target attempt in the whole suite - the
   round-trip test. Confirm that on the BRANCH it is ZERO.
2. **The guard really has teeth on the BRANCH as it now stands.** Confirm
   that a product-target write is blocked, that the block happens BEFORE the
   real vault is reached (a non-delegating spy recording no product-prefixed
   target is the right proof), and that removing the guard makes the probe
   pass again - i.e. the probe distinguishes "the guard is working" from
   "the probe is broken".
3. **The real round trip is STILL real.** The test must still write to and
   read back from the REAL Windows Credential Manager at the test-scoped
   target. Confirm by observing the credential actually appear in a
   read-only enumeration and disappear after the delete - and confirm the
   test goes RED if the write is neutralised.
4. **No production behaviour changed.** `git diff --name-only
   master...orchestra/007-no-real-vault-writes` lists only files under
   `backend/tests/`. `backend/core/secrets.py` is NOT modified: the target
   prefix, the store selection, `set_secret`, `has_secret` and
   `resolve_ollama_api_key` are unchanged. Confirm it by reading the diff,
   not by trusting the report.
5. **Nothing was deleted, skipped, xfailed or marked to make the suite
   green.** Check the diff for any removal of a test, a skip, an `xfail`, or a
   marker, and confirm the collected count and the acceptance suite's outcome
   set are unchanged against `master`.
6. **The vault is clean.** A read-only enumeration before and after your runs
   must show the same set of targets and no "Sythion" entry. If your own
   probes write anything, it must be at a TEST-SCOPED target and you must
   delete it.

## Constraints

- **VAULT DISCIPLINE - the most important constraint.**
  - You MAY write to and delete from a **test-scoped** target
    (`Sythion (Synth) [TEST]/...`).
  - You MUST NOT write to or delete from the **product** target
    (`Sythion (Synth)/...`) in ANY spelling.
  - Never print a credential value. Listing target NAMES is fine.
  - A read-only `CredEnumerate` check is permitted.
  - **Do not deliberately run the offending test on `master` to reproduce the
    bug** - use a fake-vault stand-in. If you believe a real-vault
    reproduction is necessary, say so in your report and explain why the
    stand-in is insufficient - do not simply run it against the real vault.
- **Read-only with respect to the branch.** The only file you may write is
  `verifications/007-v1.md`. Any probe file you add must be OUTSIDE the
  repository, or deleted before you finish. Leave `git status --short` empty.
- Do not write `follow-ups.md` - the orchestrator owns it.
- Never mark FIXED on something you did not inspect. State what you did not
  check.
- Do not push, deploy, publish, or touch real data. Do not run `make clean`,
  `scripts/stage_python_runtime.py`, migrations, or `npx tauri build`.
- Do not ask the user anything.

## Output contract

Write `C:\Sythion\sythion\.orchestra\verifications/007-v1.md`, first line
exactly `VERDICT: FIXED` or `VERDICT: NOT FIXED` or `VERDICT: REGRESSED`,
then:

```
- Verified commit: <sha>
- Tier: <NORMAL|EXTERNAL|SENSITIVE|SENSITIVE+EXTERNAL>

# Verification of 007

## Acceptance criteria
| # | Criterion | Result (met / not met / not verified) | Evidence |
(all six, from the backlog item)

## The central claim, verified independently
<your own spy across a whole acceptance-suite run: the recorded targets and
 the count of product-prefixed attempts>

## The guard has teeth
<the before/after: blocked on the branch, unblocked when the guard is
 removed - i.e. the probe is capable of failing>

## The round trip is still real
<the read-only enumeration showing the credential appear and disappear>

## No production behaviour changed
## Nothing was deleted, skipped, xfailed or marked
## The vault is clean
<the before/after read-only enumeration>

## Findings
## Not checked
<REQUIRED>
```

## Previous attempt (if any)

- None. This is verification round 1.
