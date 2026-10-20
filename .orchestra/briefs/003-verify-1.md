# Brief: verify - 003 - round 1 (the last item, on the tree it will land on)

- Item: 003 Documentation states the measured baseline and drops deleted modules
- Role: verifier (verifier-003)
- Goal (the slice that matters): Sythion's "better" includes **verification
  before claims** and **honest handling of debt** (`goal.md`,
  `docs/QUALITY_GATES.md:34-44` Gate 4, `:99-109` Gate 9). Item 003 makes the
  repository's public baseline TRUE and gives it a detector that fails when it
  stops being true. **You are the last gate before this reaches `master`.**

## The tree you are verifying

Branch `orchestra/003-docs-baseline`, tip **`28fa4be`**.

`28fa4be` is a merge of `master` into the 003 branch. This matters and you
must understand it before you start:

- `master` now carries items **001** (CI gates), **002** (test-selection
  truth), **004** (real assertions), **006** (readiness-test isolation) and
  **007** (credential-vault guard). Five of the six budgeted items are merged.
- The 003 branch's own commits (`485a797`, `346066e`, `3b0fdb0`, `4c1c579`,
  `33905f5`) are on top of that.
- **The re-measurement was taken on `b0127b0`** (the orchestrator's own tree:
  master + 002 + 006 + 007, conftest conflict resolved). `b0127b0` and
  today's `28fa4be` carry the SAME code: the difference is only in
  `.orchestra/` bookkeeping and in item 001/004's `.github/` and test changes,
  none of which alter the collected count. The orchestrator has already
  measured `28fa4be`'s tree at **1614 collected** and run the item's own
  `--check-collected` on it: `documents agree with the measurement: collected
  1614`, exit 0.
- The branch is a MERGE of master, not a rebase. The history is preserved;
  nothing was rewritten. `git merge-base --is-ancestor master
  orchestra/003-docs-baseline` exits 0.

**Do not rebase it.** If you find a problem, report it.

## The claims to verify

The plan is `C:\Sythion\sythion\.orchestra\plans\003.md` (amended 2026-10-20;
the amendments are authoritative). The seven backlog criteria are in
`C:\Sythion\sythion\.orchestra\backlog.md` (item 003). The Implementer's
report is `.orchestra/reports/003-implement-1.md` (on the branch; 1354 lines
covering two rounds).

The Reviewer already returned `VERDICT: ACCEPT` on this work
(`.orchestra/reviews/003-r1.md`) with all seven criteria PASS. **A Reviewer's
ACCEPT is a map, not evidence. Verify independently.** In particular the
Reviewer deliberately did NOT re-run the full suite (vault-safety constraint in
its brief) and supplied the measurement via `--measured` rather than measuring.
You are the stage that must decide whether the numbers are earned.

### The central claim

**The documents state the measured baseline, and the measurement script fails
when they stop stating it.**

1. **The numbers are the tree's numbers, measured by you.** The documents
   must state backend **1569 passed, 45 skipped (1614 collected)** and
   acceptance **191 passed, 2 skipped**. Verify this by MEASURING, not by
   reading: run `python scripts/measure_test_baseline.py` on the tree at
   `28fa4be` and compare its output to what the documents claim. The script
   derives its commands from the Makefile, so this also proves the Makefile
   on the tree is the one the script runs.
2. **`--check` exits 0 on this tree** and **exits 1 when the documents
   disagree with the measurement**, naming the file and the line. Prove the
   fail path on the real documents: take a copy of the tree with ONE document
   reverted to a stale number (e.g. `1613` or `1568`) and show the script
   catches it and exits 1. The orchestrator has already seen exactly this
   fire on the real documents: `README.md:103: states '1613' collected but the
   measurement collected 1614`. Reproduce it yourself - in a SCRATCH COPY, not
   in the branch.
3. **`--check-collected` (the cheap detector) is a real detector.** It must
   exit 0 on this tree and exit 1 on a tree whose documents disagree.
4. **`check_doc_paths.py` exits 0** and every module named in
   `backend/services/README.md` exists on the tip. `ALLOWED_ABSENT` must be
   empty - the escape hatch must not be pre-opened.
5. **The scripts are stdlib-only** and work under both CPython 3.11 (CI's
   pin) and 3.14 (local). Confirm by inspection plus an import test.
6. **No stale number survives** anywhere in the canonical documents:
   692, 683, 659, 196, 1564, 1568, 1609, 1613, 1683.
7. **Nothing outside the six documents and the two scripts changed**, and no
   module the plan names as deleted is still listed.

### What makes this item's evidence hard to get, and what you must do about it

**VAULT SAFETY - non-negotiable.** The measurement script runs the FULL
backend suite. Item 007's guard (now on `master`) makes that safe: it
rejects any credential write under the product prefix `Sythion (Synth)/`
before delegation. Verify the guard is present on your tree
(`backend/tests/acceptance/conftest.py`) BEFORE you run any full-suite
measurement. If it is absent, do not run the full suite - report that.

**Read-only with respect to the branch.** The only file you may write inside
the repository is `.orchestra/verifications/003-v1.md`. Scratch copies,
stale-document variants and probe files go OUTSIDE the repository, or are
deleted before you finish. Leave `git status --short` empty in your worktree
when you are done.

**The suite is not deterministic on this machine.** Across observed full-suite
runs there have been native crashes in the `0xC0000005` class that do not
reproduce in isolation - the smallest reproducer is the FULL SUITE. A crash is
pre-existing, not a regression, but you must RECORD it and re-run. The
orchestrator's own `--check-collected` on this tree completed cleanly.

**`make` is not installed** on this machine. The script executes the
Makefile's recipe lines directly with the running interpreter. That is by
design and is documented in the report; note it as a limit, not a defect.

## Read

- `C:\Sythion\sythion\.orchestra\plans\003.md` - the contract, with amendments.
- `C:\Sythion\sythion\.orchestra\backlog.md` - item 003's seven criteria.
- `C:\Sythion\sythion\.orchestra\goal.md` - the run order and the "1614" note.
- `C:\Sythion\sythion\.orchestra\reviews\003-r1.md` - the Reviewer's report.
- `C:\Sythion\sythion\.orchestra\reports\003-implement-1.md` - the Implementer's report (on the branch).
- `C:\Sythion\sythion\.orchestra\verification-template.md` - the shape.

## Write

- `C:\Sythion\sythion\.orchestra\verifications\003-v1.md` - the ONLY file you
  may write inside the repository.

**Your file's FIRST LINE must be exactly `VERDICT: FIXED` or `VERDICT: NOT
FIXED` or `VERDICT: REGRESSED`.** Nothing may precede it. It must also carry
a line `- Reviewed commit: <full sha>` and a line `- Tier: none`.

**WRITE THE REPORT EARLY AND INCREMENTALLY.** Write the file as soon as you
have your first result, then append each subsequent result as you obtain it.
A sibling verification attempt died earlier in this run on a transport limit
and lost everything because it accumulated to the end. The partial file on
disk is the deliverable that survives an interruption.

## Facts already established - do NOT merely re-derive them

- The merged tree collects **1614**; master collects 1611; item 006 adds the
  3 new tests, and items 002 and 007 add none. The orchestrator measured
  **1614** on `b0127b0` and **1614** on today's `28fa4be`.
- Item 006's Reviewer measured **1569 passed / 45 skipped** on the candidate;
  item 007's Verifier measured **1566 passed / 45 skipped** on the 007 branch
  and **1569 passed / 45 skipped** on the merged tree; item 002's Verifier
  measured **1569 passed / 45 skipped** in the exact CI shape under the pinned
  toolchain. All three agree on 1569/45 for the merged tree.
- The acceptance suite is **191 passed, 2 skipped** (193 collected). The
  documented 196 is refuted.
- The **warning count is not a stable baseline** (407,733 / 406,252 / 407,719
  across three runs of an unchanged tree) and must NOT be quoted. The
  documents must not quote it either.
- The 45-skip breakdown is **not measured** and must not be re-invented. The
  per-skip breakdown the old README carried was DELETED, not re-derived.
- The canonical block is identical in the five baseline documents, and the
  README badge is the one deliberate second quote, cross-checked by the
  script's own rule C3.
- `backend/services/README.md` names 16 `*.py` modules; all exist.
  `response_strategy.py` and `speech_act_classifier.py` (deleted by `db10f86`)
  are gone from the four criterion-6 documents.
- **Known out-of-scope finding the Reviewer recorded:** the VENDORED copy
  `src-tauri/resources/backend/services/README.md` still lists the two
  deleted modules. It is outside item 003's four documents and is a
  follow-up, not a blocker. Note it; do not treat it as a failure of this
  item.
- The orchestrator already proved the merge of this branch onto master is
  clean apart from one add/add conflict in
  `.orchestra/reports/003-implement-1.md` (the report file, whose branch
  version is the newer one), and proved `--check-collected` exits 0 on the
  merged result.

## Constraints

- **Follow `C:\Sythion\AGENTS.md`.** Call `memory_stats`, then `memory_query`,
  then `memory_prepare_task` before file work. If the memory tools are
  genuinely unavailable, **say so plainly in your report** and continue with
  the verification - but do not pretend the protocol step happened, and do
  not read the memory database directly as a substitute.
- Do not write `follow-ups.md` - the orchestrator owns it. List anything you
  would queue in your report instead.
- Never mark FIXED on something you did not inspect. State what you did not
  check.
- Do not push, deploy, publish, migrate, or touch real data. Do not run
  `make clean`, `scripts/stage_python_runtime.py`, or `npx tauri build`. No
  destructive commands (`rm -rf`, `git reset --hard`, `git clean -fd`,
  `git checkout -- .`, `git stash drop`).
- Do not ask the user anything.

## Output contract

`C:\Sythion\sythion\.orchestra\verifications/003-v1.md`, first line exactly
`VERDICT: FIXED` / `NOT FIXED` / `REGRESSED`, then:

```
- Reviewed commit: <full sha>
- Tier: none

# Verification of 003

## Acceptance criteria
| # | Criterion | Result (met / not met / not verified) | Evidence |
(all seven)

## The measurement, taken by me
## The documents agree with the measurement
## The check has teeth: the fail path, on the real documents
## The cheap detector: --check-collected
## The path check
## The scripts are stdlib-only and version-portable
## No stale number survives
## Nothing outside the item's scope changed
## The vault guard was present and the real vault was never written to
## Findings
## Not checked
```

**Paste the ACTUAL command output.** A claim without output is not a claim.
Report the exact tree and commit for every measurement. If a criterion is not
met, say so plainly.
