# Brief: verify - 006 - attempt 2 (recovery of a dead attempt)

- Item: 006 The readiness test must not borrow the developer's gitignored `backend/.env`
- Role: verifier (verifier-006, attempt 2 — the first attempt DIED before writing its report)
- Goal (the slice that matters): Sythion's "better" includes **verification
  before claims** and **release and operator readiness** (`goal.md`,
  `docs/QUALITY_GATES.md:34-44` Gate 3, `:88-97` Gate 8). A test suite whose
  result depends on a file that is deliberately NOT in the repository is not a
  release-confidence signal. **You are the last gate before this reaches
  `master`.**

## Why this dispatch exists

The first verification attempt **died on a subscription limit** — a
transport failure, not a finding. It wrote no report, so the item cannot
advance on it. It did, however, build a sound harness and leave evidence on
disk. **Your job is to finish the verification, reuse what is already proven
where it is sound, and write the report.**

### What the dead attempt already built and PROVED

**Reuse these — do not rebuild them from scratch.** All under
`C:\Users\orcha\AppData\Local\Temp\opencode\`:

| Artefact | What it is |
|---|---|
| `verify006_boot.py` | a pytest boot shim that (a) installs a **non-delegating product-target blocker** on `win32cred.CredWrite`/`CredDelete`/`CredRead` — raising **before** delegation for any target under `sythion (synth)/` (case-insensitive) — and (b) **scrubs every `SYTHION_*` variable from the child process**, and (c) reproduces `python -m pytest`'s cwd import path. **This is the vault-safety mechanism the brief demands.** |
| `verify-006-base/` | detached worktree at `7cf679e` (the pre-item base) |
| `verify-006-v1/` | detached worktree at `f144557` (the candidate tip) |
| `verify-006-controls/` | detached worktree at `f144557` with two untracked probe files: `backend/tests/test_verifier006.py` (a two-test sequential-mutation probe, a `provider_e2e` marker-boundary probe, and an own-readiness probe) and `backend/tests/test_verifier006_module.py` |
| `verify-006-integration/` | detached worktree at `b0127b0` — the **fully merged tree** (master + 007 + 002 + 006, conftest conflict resolved) |
| `verify006-evidence/` | its JSON + log + JUnit artefacts for every run below |

**Its measured results (read them yourself before citing them):**

| Run | Result |
|---|---|
| base `7cf679e`, readiness module, no `.env` | **exit 1** — `1 failed, 7 passed` (the defect) |
| candidate `f144557`, readiness + file-roots + settings store/API, no `.env` | **exit 0** — `37 passed` |
| the same, with a DUMMY `.env` | **exit 0** — `37 passed` (identical count) |
| candidate, **CI shape** (`cd backend; pytest tests`) under the pinned toolchain, readiness module | **exit 0** — `11 passed` |
| candidate, **CI shape**, full collection | **exit 0** — `1614 tests collected` |
| merged tree `b0127b0`, full collection | **exit 0** — `1614 tests collected` |

**The CI-shape gap the Reviewer left open is already closed by this evidence**
— that was the Reviewer's most valuable contribution to this item and the
dead attempt closed it.

### What is STILL MISSING — your job

1. **The full-suite run on the candidate, with the outcome-set DELTA against
   master.** The dead attempt collected the integration tree but did not run
   the full suite. The Reviewer measured `1569 passed, 45 skipped` on the
   candidate and `1 failed, 1565 passed, 45 skipped` on master, with the
   delta being exactly `test_readiness_states` failed→passed plus the three
   new tests. **Reproduce that delta yourself.** Run it in the integration
   tree (or the candidate tree) **through `verify006_boot.py`**, so the
   product-target blocker is active — the candidate branch alone has **no**
   vault guard.
2. **The credential-echo property, independently.** Force the readiness
   assertion to fail with a **SENTINEL** standing in for the credential, and
   show the value appears **0 times in the console output and 0 times in the
   JUnit XML**. The Reviewer measured 0/0 on the candidate and 3 hits in the
   XML on the old tip `d89fb95` — reproduce both directions.
3. **The shared-snapshot property, independently.** The probe file already
   exists in `verify-006-controls/`. Run it on the candidate: the second test
   must receive `[]`, not the first test's appended value. Then run the
   same probe on `d89fb95` and show it DOES receive the leaked value.
4. **The `provider_e2e` carve-out boundary** — the probe exists; run it and
   report module-level, method-level, unmarked-sibling and ordinary-test.
5. **Your own negative controls.** The Reviewer found that the new regression
   test is meaningful for the shared-snapshot property but is a **tautology
   with respect to "the isolation removed entirely"** — in a clean tree with
   the isolation deleted the whole file is green. Confirm or refute that, and
   say plainly what the pair of tests does and does not pin. **The honest
   statement is worth more than a clean-sounding one.**

## Read

- `C:\Sythion\sythion\.orchestra\briefs\006-verify-1.md` - the original
  brief (still the contract).
- `C:\Sythion\sythion\.orchestra\plans/006.md` - the plan.
- `C:\Sythion\sythion\.orchestra\backlog.md` - item 006's six criteria.
- `C:\Sythion\sythion\.orchestra\reports/006-implement-2.md` - the implementer's report.
- `C:\Sythion\sythion\.orchestra\reviews/006-r1.md` and `reviews/006-r2.md` -
  the two reviews. They are a **map, not evidence**; verify independently.
- `C:\Sythion\sythion\.orchestra\verification-template.md` - the shape.

## Write

- `C:\Sythion\sythion\.orchestra\verifications/006-v1.md` - the ONLY file you
  may write.

**Your file's FIRST LINE must be exactly `VERDICT: FIXED` or `VERDICT: NOT
FIXED` or `VERDICT: REGRESSED`.** Nothing may precede it. **Your report must
also contain a line `- Reviewed commit: <full sha>`** and a line `- Tier:
SENSITIVE`.

**WRITE THE REPORT EARLY AND INCREMENTALLY.** The previous attempt lost
everything it had proven because it died before the final write. Write the
report file as soon as you have your first result, then append each
subsequent result to it as you obtain it. If you are interrupted again, the
partial report on disk is the deliverable that survives.

## Context

- Current status: item 006 is `verifying`; this is verification attempt 2.
- Branch: `orchestra/006-readiness-test-isolation`, tip `f144557`
  (`f1445575655261351989953ccf3d4247cde40ca1`).
- Plan: `plans/006.md`.

### Facts already established - do NOT merely re-derive them

- The diff is exactly two files: `backend/tests/conftest.py` and
  `backend/tests/test_setup_readiness.py`. No production module, no `docs/`,
  no `.github/workflows/ci.yml`.
- The Reviewer closed both round-1 findings by reproduction, re-measured every
  marker population and Makefile selection, and confirmed the 002/006
  conflict's shape and the recommended resolution (take 002's conftest, append
  006's block, keep the trailing newline) - which yields **1614 collected,
  exit 0** and readiness **11 passed**. The orchestrator independently
  reproduced the same resolution and the same 1614.
- The Reviewer's count is **1569 passed / 45 skipped** (the redo added a
  THIRD new test, so the brief's 1568 was stale). Item 006 adds **3** tests
  in total; 002 and 007 add **0**; the merged tree collects **1614**.
- The `assert bool(a) == bool(b)` shape **also** echoes the credential; only
  `same = a == b; assert same is True` is safe. The branch uses the safe one.
- The classifier says **SENSITIVE** (2 files, 25 pattern matches). The board
  mirrors it.

### Pre-existing conditions - do NOT blame the branch for these

1. **The suite is not deterministic here** - native crashes in the
   `0xC0000005` class, none reproducing in isolation; the smallest
   reproducer is the FULL SUITE. A native crash in your run is pre-existing,
   not a regression - but you must RECORD it and re-run.
2. **A clean checkout has no `backend/.env`** (gitignored). That IS this
   item's subject.
3. **`make` is not installed** on this machine.
4. The plan's `1564` baseline is the pre-item-004 number.

## Constraints

- **VAULT SAFETY - non-negotiable.** Any run that could touch the credential
  vault MUST go through `verify006_boot.py` (or an equivalent blocker
  installed **before** delegation). You MAY write to and delete from a
  TEST-SCOPED target. You MUST NOT write to or delete from the PRODUCT target
  in any spelling. Never print a credential value. A read-only
  `CredEnumerate` check is permitted. **Do not run the unguarded full or
  acceptance suite on the candidate branch** — candidate 006 alone has no
  vault guard; use the integration tree at `b0127b0` or the boot shim.
- **Read-only with respect to the branch.** The only file you may write is
  `verifications/006-v1.md`. Probe files you add must be OUTSIDE the
  repository, or deleted before you finish. Leave `git status --short` empty
  in the repository. **Do not modify another agent's worktree** - `wt-006`
  belongs to the implementer's branch.
- **Follow `C:\Sythion\AGENTS.md`.** Call `memory_stats`, then
  `memory_query`, then `memory_prepare_task` before file work. If the memory
  tools are genuinely unavailable, **say so plainly in your report** and
  continue with the verification - but do not pretend the protocol step
  happened, and do not read the memory database directly as a substitute.
- Do not write `follow-ups.md` - the orchestrator owns it. List anything you
  would queue in your report instead.
- Never mark FIXED on something you did not inspect. State what you did not
  check.
- Do not push, deploy, publish, or touch real data. Do not run
  `make clean`, `scripts/stage_python_runtime.py`, migrations, or
  `npx tauri build`. No destructive commands (`rm -rf`, `git reset --hard`,
  `git clean -fd`, `git checkout -- .`, `git stash drop`).
- Do not ask the user anything.

## Output contract

`C:\Sythion\sythion\.orchestra\verifications/006-v1.md`, first line exactly
`VERDICT: FIXED` / `NOT FIXED` / `REGRESSED`, then:

```
- Reviewed commit: <full sha>
- Tier: SENSITIVE

# Verification of 006

## Acceptance criteria
| # | Criterion | Result (met / not met / not verified) | Evidence |
(all six)

## The central claim, verified independently
## The credential never appears in a failure message
<the sentinel run, console AND junitxml, candidate vs the old tip>
## The clean snapshot is not shared between tests
## The full-suite outcome-set delta against master
## The provider_e2e carve-out boundary
## The CI shape (cd backend; pytest tests, pinned toolchain)
## The negative controls, and what the tests do NOT pin
## The vault was never written to
## Findings
## Not checked
```

**Paste the ACTUAL command output.** A claim without output is not a claim.
Report the exact tree, commit and overlay for every measurement. If a
criterion is not met, say so plainly.

## Previous attempt (if any)

- Attempt 1: the verifier **died on a subscription limit** before writing
  any report. Its harness and evidence survive at
  `C:\Users\orcha\AppData\Local\Temp\opencode\verify006_*` and
  `verify006-evidence/`; this brief lists what it proved and what it did not
  finish. **Re-verify the parts you cite; do not transcribe them.**
