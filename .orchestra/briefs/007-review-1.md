# Brief: review - 007 - attempt 1

- Item: 007 The acceptance suite must never write to or delete the product's real credential
- Role: reviewer
- Goal (the slice that matters): Sythion's "better" includes **verification
  before claims** and **honest debt reporting** (`goal.md`,
  `docs/QUALITY_GATES.md:34-44` Gate 3, `:88-97` Gate 8), and the repository's
  own contract says the real credential vault must never be touched by tests
  (`docs/ARCHITECTURE.md:358`). A test suite that deletes the developer's
  stored credential is not a release-confidence signal. Your job: judge the
  DIFF against the plan and the criteria - not to re-run the whole
  verification, which is the Verifier's job next.

## Read

- `C:\Sythion\sythion\.orchestra\plans/007.md` - **THE PLAN.** It is the
  contract: the two files, the exact fix, the proof, the risks, the recorded
  judgement calls, and the residual side effect it discloses.
- `C:\Sythion\sythion\.orchestra\backlog.md` - item 007's acceptance criteria.
- `C:\Sythion\sythion\.orchestra\reports/007-implement-1.md` - the
  implementer's evidence.
- `C:\Sythion\sythion\.orchestra\critiques.md` - the orchestrator's critique
  of the implementer's report (ACCEPT), and the Researcher's critique that
  produced candidate R-020.
- The branch itself: `orchestra/007-no-real-vault-writes`, three commits:
  `a0fa314` (the guard), `e1ff9b1` (the unfiltered enumeration),
  `4680e0b` (the comment correction). Diff against `master`:
  `git -C C:\Sythion\sythion diff master...orchestra/007-no-real-vault-writes`
- The two files it touches: `backend/tests/acceptance/conftest.py`,
  `backend/tests/acceptance/test_secret_storage.py`.
- `C:\Sythion\sythion\backend\core\secrets.py` - `CredentialManagerStore`,
  `_default_store()` (`:112-121`), `_CM_TARGET_PREFIX` and `_cm_target()`
  (`:32-33`). **Read only - off-limits.**

## Write

- `C:\Sythion\sythion\.orchestra\reviews/007-r1.md` - the ONLY file you may
  write. Use `.orchestra/review-template.md` as the shape.

**Your review file's FIRST LINE must be exactly `VERDICT: ACCEPT` or
`VERDICT: IMPROVE`.** Nothing may precede it - no heading, no comment, no
blank line. **Your report must also contain a line `- Reviewed commit:
<sha>`** and a line `- Tier:
NORMAL|EXTERNAL|SENSITIVE|SENSITIVE+EXTERNAL` giving the classifier's tier for
`master...orchestra/007-no-real-vault-writes` (run
`python C:\Users\orcha\.config\opencode\skills\dev-orchestrator\scripts\classify.py
master...orchestra/007-no-real-vault-writes` from the repo root and report
what it says).

## Context

- Current status: item 007 is `in-review`; the implementer has reported; you
  are the next stage.
- Branch: `orchestra/007-no-real-vault-writes`, commits `a0fa314`, `e1ff9b1`,
  `4680e0b`.
- Plan: `plans/007.md`.
- Previous findings: the first round was interrupted by a network fault after
  committing but before writing the report; a recovery dispatch verified the
  committed work, applied one comment correction, and wrote the report.

### Where to work

The branch is **already checked out** at
`C:\Users\orcha\AppData\Local\Temp\opencode\wt-007`. Use it for inspection
and for running checks. Do NOT create another worktree for the same branch
(git will refuse). Do not commit or modify anything there. There is also a
detached scratch worktree `wt007-master`; you may read it, but do not delete
it - the orchestrator owns the cleanup.

### Facts already established - do not re-derive them

- The diff is exactly two files, both under `backend/tests/acceptance/`. **No
  production module is touched** - `backend/core/secrets.py` and
  `backend/api/routes.py` are unchanged.
- The orchestrator read the committed diff and it matches the plan:
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
    the patched attribute), keeps every round-trip assertion, and adds a
    read-only self-check.
- The correction commit `4680e0b` is COMMENT-ONLY (8 insertions, 6 deletions,
  all inside a comment block) and the orchestrator read it: the old comment
  wrongly claimed the prefix-filtered `CredEnumerate(prefix, 0)` "cannot tell
  'absent' from 'broken enumerator'" and implied the orchestrator's own
  absence check was inconclusive. The orchestrator had MEASURED, read-only:
  - unfiltered `CredEnumerate(None, 0)` -> 8 credentials visible, **none** of
    their target names contains "Sythion" -> the product credential is
    genuinely ABSENT, and so is the test-scoped one;
  - `CredEnumerate(<EXACT target name of an existing credential>, 0)` -> **1**,
    so the EXACT-MATCH form WORKS;
  - `CredEnumerate(<directory-style PREFIX of that same existing credential>,
    0)` -> **1168**, so the DIRECTORY-PREFIX form really is broken on this
    machine.
  The new comment says exactly that, and the misleading implication is gone.
  The BEHAVIOUR was deliberately left unchanged (unfiltered enumerate + Python
  filtering), which is the most robust choice - it cannot be defeated by
  either quirk.
- The implementer's proof, which you are to judge rather than re-run:
  - **Fake-vault probe** (throwaway harness OUTSIDE the repository) recording
    every write/delete target: on master it records
    `write/delete Sythion (Synth)/ollama_api_key` and the harness exits 1; on
    the branch it records the test-scoped target and exits 0.
  - **The guard has teeth**: a transient probe covering a store write, a store
    delete, and a DIRECT `CredWrite` with the prefix patched is blocked 3 out
    of 3 on the branch, exit 1; the non-delegating spy records NO
    product-target call, so the real vault was unreachable BY CONSTRUCTION, not
    merely unwritten. The guard also fires against master's own offending test
    file run inside the branch tree.
  - **The real round trip is preserved**: the tripwire shows the target
    really appears in the real Windows Credential Manager enumeration after
    the write and is gone after the delete; with the write neutralised the
    test goes RED.
  - **The rest of the acceptance suite is unaffected**: `191 passed, 2
    skipped` before and after with IDENTICAL per-test outcome sets; the full
    suite on the branch is `1566 passed, 45 skipped`; master's full suite with
    a product-target blocker differs in EXACTLY ONE test - the offending one.
- The implementer states that the product target was never written, deleted or
  read; no credential value was ever printed; master was never run against the
  real vault.

### Pre-existing conditions - do NOT blame the branch for these

1. **A clean checkout has no `backend/.env`** (gitignored), so
   `backend/tests/test_setup_readiness.py::test_readiness_states` fails in any
   worktree. That is item 006's subject, not this item's.
2. **The suite is not deterministic here** - 2 native crashes in 6 observed
   full-suite runs, of two different kinds; neither reproduces in isolation.
3. **`make` is not installed on this machine.**
4. The plan's `1564 passed` baseline has moved to `1566` on current `master` -
   item 004 added two tests. Item 007 adds zero.

## The questions you must answer

1. **Does the guard actually close the hole?** Read the fixture and judge
   whether there is a path by which a test could still reach the product
   target. Specifically: the guard wraps `win32cred.CredWrite` and `CredDelete`
   - is there any other API in `backend/core/secrets.py` (or elsewhere) that
   writes to the vault and is NOT wrapped? Look for `CredWrite`, `CredDelete`,
   `CredRename`, `CredSet` and anything that reaches `win32cred` by another
   route. Report what you find, including if the answer is "nothing".
2. **Is the guard's scope right?** It intercepts by TARGET PREFIX. Does it
   intercept too much (blocking legitimate test-scoped writes that should be
   allowed)? Does it intercept too little (a product target that does not start
   with the prefix)? What happens with an empty target name?
3. **Does the test still prove a REAL round trip?** This is the crux. The
   test now writes to the REAL Windows Credential Manager at a test-scoped
   target and reads it back. Confirm the round trip is real - that the
   credential genuinely lands in the vault and is genuinely read back - and
   that the assertions were not weakened into something that would pass
   against a mock or an in-memory store. The implementer reports that with the
   write neutralised the test goes RED; that is the property to check.
4. **Is the read-only self-check non-vacuous?** It enumerates the vault and
   asserts the test-scoped entry is (or is not) present. Could it pass
   vacuously - for example if the enumeration returned nothing at all and the
   assertion were written to accommodate that? Read the assertion, then judge
   whether it would still be meaningful if the vault were empty.
5. **Is the exception type right?** The guard raises an `AssertionError`
   subclass rather than a `pywintypes.error`, because
   `CredentialManagerStore.delete` swallows the latter. Check that the chosen
   type is not swallowed by any OTHER error handler in the path, and that it
   cannot be mistaken for an ordinary test failure. A guard that raises
   something the code catches is not a guard.
6. **Is the import-time snapshot actually necessary and actually correct?** The
   product prefix is captured at conftest import time because the round-trip
   test patches `_CM_TARGET_PREFIX` at runtime. Verify that reasoning, and
   verify that no other module could mutate the captured value.
7. **Scope discipline.** Confirm the diff touches only the two named test
   files, that no production module and no documentation file was touched,
   and that `follow-ups.md` was NOT edited by the implementer.
8. **Does it combine with the other in-flight items?** Items 002 and 006 also
   edit `backend/tests/conftest.py` - but that is the ROOT conftest, a
   different file from `backend/tests/acceptance/conftest.py`. Confirm item
   007's two files are untouched by items 002 and 006, and that the acceptance
   conftest does not import anything the other items change.

## Constraints

- **Read-only.** Never edit code. The only file you may write is
  `reviews/007-r1.md`.
- Do not trust the implementer's claims. Look at the diff yourself.
- For each acceptance criterion, state met / not met / **not verified**, with
  evidence. "Not verified" is an honest answer; "met" without evidence is not.
- **VAULT DISCIPLINE - the most important constraint of this item.**
  - You MAY write to and delete from a **test-scoped** target
    (`Sythion (Synth) [TEST]/...`).
  - You MUST NOT write to or delete from the **product** target
    (`Sythion (Synth)/...`) at any point. If something in the implementation
    looks like it would try to, say so in your review - do NOT run it to "see
    what happens".
  - Never print a credential value. Listing target NAMES is fine; reading the
    stored secret is not.
  - A read-only `CredEnumerate` existence check is permitted.
  - **Do not deliberately run the offending test on `master` to reproduce the
    bug.** Use a fake-vault stand-in if you need to see the failure. If you
    think a real-vault reproduction is necessary, say so in your review and
    explain why the fake stand-in is insufficient - do not simply run it
    against the real vault.
- If the diff removes a check, a deny, an assertion or a guard, treat the
  change as SENSITIVE regardless of what the classifier says.
- Do not write `follow-ups.md` - the orchestrator owns it. List anything you
  would queue in your report instead.
- Never mark PASS on something you did not inspect. State what you did not
  check.
- Do not push, deploy, publish, or touch real data. Do not run `make clean`,
  `scripts/stage_python_runtime.py`, migrations, or `npx tauri build`.
- Do not ask the user anything.

## Output contract

`C:\Sythion\sythion\.orchestra\reviews/007-r1.md`, first line exactly
`VERDICT: ACCEPT` or `VERDICT: IMPROVE`, then:

```
- Reviewed commit: <sha>
- Tier: <NORMAL|EXTERNAL|SENSITIVE|SENSITIVE+EXTERNAL>

# Review of 007 round 1

## Acceptance criteria
| # | Criterion | Result (met / not met / not verified) | Evidence |
|---|---|---|---|
(all of them, from the backlog item)

## Does the guard close the hole?
## Is the round trip still real?
## Is the self-check non-vacuous?
## Is the exception type right?
## Is the import-time snapshot correct?
## Goal fit
## Sensitivity
## Findings (most important first)
1. <file:line> - problem - what to change

## Deferred
## Needs human?
## Not checked
```

## Previous attempt (if any)

- None. This is review round 1.
