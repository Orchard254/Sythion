# Brief: implement - 003 - attempt 1

- Item: 003 Documentation states the measured baseline and drops deleted modules
- Role: implementer (implementer-003)
- Goal (the slice that matters): Sythion's "better" includes **verification
  before claims** and **honest debt reporting** (`goal.md`,
  `docs/QUALITY_GATES.md:34-44` Gate 3 "Verification before claims", `:45-59`
  Gate 4 "Docs must reflect reality", `:88-97` Gate 8 "Testing baseline
  discipline", Gate 9 "honest handling of debt"). Today every documented
  baseline in this repository is wrong and two documents name modules that
  were deleted years ago. Your job is to make the documentation stop lying -
  and to make it impossible for it to start lying again.

## Read

- `C:\Sythion\sythion\.orchestra\plans/003.md` - **THE PLAN.** Read it fully.
  It is 916 lines and it decides every question you are about to face: the
  two scripts' design, the exit-code contract, the `--check` rules, the
  per-document edits with line numbers, the risk analysis, and the decisions
  already taken. Do not re-derive its design; implement it.
- `C:\Sythion\sythion\.orchestra\backlog.md` - item **003**, its Why, and its
  seven acceptance criteria. These are the contract.
- `C:\Sythion\sythion\.orchestra\research.md` - candidates **R-004** and
  **R-017**.
- `C:\Sythion\sythion\.orchestra\critiques.md` - the orchestrator's critique
  of the Planner's plan (ACCEPT), which records three corrections you must
  apply and two facts you should know.
- `C:\Sythion\sythion\.orchestra\goal.md` - the goal, the off-limits list.
- The documents you will change: `README.md`, `docs/TESTING.md`,
  `docs/PROJECT_STATUS.md`, `docs/ROADMAP.md`, `docs/OPERATIONS.md`,
  `docs/CODEBASE_BOUNDARIES.md`, `backend/services/README.md`,
  `TESTING_GUIDE.md`.
- `C:\Sythion\sythion\scripts\` - where the two new scripts go. Read a couple
  of the existing scripts to match their style (module docstring saying WHY,
  `argparse`, meaningful exit codes).
- `C:\Sythion\sythion\Makefile` and
  `C:\Sythion\sythion\.github\workflows\ci.yml` - what the docs must describe.

## Write

- `C:\Sythion\sythion\.orchestra\reports/003-implement-1.md` - the evidence
  report. **This is the deliverable.**
- `C:\Sythion\sythion\scripts\measure_test_baseline.py` (new)
- `C:\Sythion\sythion\scripts\check_doc_paths.py` (new)
- The documents listed above.
- Commits on a new branch `orchestra/003-docs-baseline`, cut from `master`.

## Context

- Current status: item 003 is `planned` in both `backlog.md` and
  `board.json`.
- Branch: **`orchestra/003-docs-baseline`** - create it from `master`. Do not
  create it from another item's branch.
- Plan: `plans/003.md`.

### THREE CORRECTIONS TO THE PLAN - the orchestrator found these, apply them

**CORRECTION 1 - the plan's exit-code contract contradicts itself.** The plan
states it twice with DIFFERENT numbers: the CLI table says exit **3** is a
usage error and exit **4** is the interpreter/platform mismatch, while the
"how a reviewer tells the documents are stale" section says exit **3** is the
interpreter/platform mismatch. **The contract you implement is:**

| Exit | Meaning |
|---|---|
| 0 | measurement succeeded / the documents agree |
| 1 | `--check` mode: the documents DISAGREE with the measurement (stale) |
| 2 | no complete measurement could be made (crash, native fault, failure, no summary) - **inconclusive** |
| 3 | usage error (no Makefile target, unreadable root, invalid `--measured` file) |
| 4 | `--check` mode: the running interpreter/platform differs from the one the documents name - **inconclusive** |

Use that table in the script's docstring, in `--help`, and in the report. The
plan's own text must not contradict itself.

**CORRECTION 2 - the plan's reproduction row R4 undercounts.** The plan says
`git grep -nE "\b(692|683|659)\b|196 passed" -- README.md docs/ TESTING_GUIDE.md`
gives **14** matches. It gives **15**. The README contributes THREE matching
lines, not two: the badge at `README.md:6` and the prose at `README.md:103`
and `README.md:104`. Record **15**. (Every other row the orchestrator re-ran
matched the plan exactly: R5 = 8, R6 = 1, R7 = 6, R8 = 1.)

**CORRECTION 3 - a dependency the plan does not state, which constrains WHEN
you may run the measurement.** The plan says the measurement comes from "a
run on the branch tip, after item 002 has merged". There is a second,
harder constraint the orchestrator discovered:

- The plan's script runs the **full backend suite**, read out of the Makefile's
  `test` target (`python -m pytest backend/tests -q` - no `-m` filter, so the
  acceptance sub-suite runs).
- `backend/tests/acceptance/test_secret_storage.py::test_credential_manager_round_trip`
  writes to and then DELETES the developer's REAL Windows Credential Manager
  entry `Sythion (Synth)/ollama_api_key` - until item 007 lands.
- The plan's own retry design makes it worse: up to 3 full-suite attempts per
  suite.

**So: you may build and demonstrate the scripts, but the FINAL measurement
that the documents quote must be taken on a tree that already carries item
007's fix.** Item 007 is in review right now and will merge shortly. If it
has NOT merged by the time you need the measurement, say so in your report
and take the measurement on a tree where the guard is present, or wait - but
NEVER take a full-suite measurement on a tree without the guard, and never
deliberately run the offending test against the real vault.

### What the orchestrator has already verified for you - do not re-derive it

- Every factual claim in the plan is TRUE. The orchestrator re-ran them:
  `git log --diff-filter=D -- backend/tools/web_tools.py` -> `2469c09`;
  the same for `response_strategy.py` and `speech_act_classifier.py` ->
  `db10f86`; `docs/CODEBASE_BOUNDARIES.md:53` lists `backend/tools/web_tools.py`;
  `backend/services/README.md:55-56` names the two deleted modules as BARE
  filenames; `docs/PROJECT_STATUS.md:44` contains the prose shorthand
  "backend/acceptance"; `backend/tests/acceptance/test_canonical_file_checks.py:49,68`
  are the two symlink-privilege skips; `docs/OPERATIONS.md:143` records the
  unrunnable `PYTHONPATH=/c/Ai/sythion:...` invocation; `TESTING_GUIDE.md:21-22`
  repeats `692 / 32` and `196 / 2` and contradicts itself.
- The merged `pytest.ini` (item 002's branch) declares exactly the ten
  canonical markers the plan names, with the populations the plan gives.
- The Makefile's `test` and `test-acceptance` recipes are
  `python -m pytest backend/tests -q` and
  `python -m pytest backend/tests/acceptance -q` - simple enough to parse.
- The current `master` collects **1611** tests. **Item 006 adds 2 tests**
  (two new tests in `backend/tests/test_setup_readiness.py`); items 002 and 007
  add ZERO. So the merged tree will collect **1613**.
  **The number you put in the documents must be the one the script measures
  on the tree you measured - not 1611, not 1613, but whatever the script
  prints.** If you measure a tree that does not yet include item 006, say so
  in the report, because the `--check` mode will then flag the documents as
  stale as soon as item 006 merges. This is exactly the drift the item exists
  to detect, so DETECTING it is a success, not a failure - but you must report
  it truthfully.
- `pytest_version` locally is **8.4.2**; CI pins **Python 3.11**; the
  reference machine is CPython **3.14.7**.

### Pre-existing conditions - do NOT blame the branch for these

1. **A clean checkout has no `backend/.env`** (gitignored), so
   `backend/tests/test_setup_readiness.py::test_readiness_states` fails in any
   worktree. That is item 006's subject.
2. **The suite is not deterministic here** - 2 native crashes in 6 observed
   full-suite runs, of two different kinds; neither reproduces in isolation;
   the smallest reproducer is the FULL SUITE. The plan's script is designed
   to handle exactly this (child process, three-condition COMPLETE test,
   fresh-process retry). Record every attempt, including the crashed ones.
3. **`make` is not installed on this machine** (no binary, no WSL), so the
   Makefile targets cannot be executed as such - run the recipe commands
   verbatim and say so.
4. The plan's `1564 passed` baseline has moved to `1566` on current `master`
   (item 004 added two tests).

## The work, in the order the plan requires

1. **Write `scripts/measure_test_baseline.py`** exactly as the plan specifies:
   standard library only, `argparse`, JSON to stdout, the exit-code table
   above. The three rules that make it trustworthy must be stated IN the
   script's docstring: *a published number comes from a completed run*; *no
   count is hard-coded*; *no warning count is published*.
   - It runs each suite in a **child process** and counts a run only if the
     child exited 0 AND printed a parseable summary AND contained **no
     native-fault marker**. The third condition is separate on purpose: the
     COM/UIA fault burst (`0x80040155`) does NOT kill the process, so exit
     code alone would publish the numbers of a run that was bleeding native
     exceptions.
   - Fault markers to look for: `Windows fatal exception`, `Fatal Python
     error:`, the faulthandler dump header (`Thread 0x... [` /
     `Current thread 0x...`).
   - Retry in a **fresh process** (default `--attempts 3`), and record EVERY
     attempt in the JSON including the crashed ones.
   - The commands come from the **Makefile**, not retyped; if there is no
     `test` / `test-acceptance` target, exit 3 rather than invent a command.
   - `collected` is measured; record `collected_source`; a disagreement
     between the two ways of measuring it is `"status": "inconsistent"` and
     publishes nothing.
   - The JSON carries `git.commit`, `python_version`, `platform`,
     `pytest_version`, `ci_python_version` (read out of
     `.github/workflows/ci.yml`, never typed) and the per-suite breakdown.
   - **No warning count, ever.**
2. **Demonstrate the script's FAIL path deterministically** - the plan's D1
   and D2, using a **sandbox Makefile OUTSIDE the repository** (that is what
   `--makefile PATH` is for): D1's recipe dereferences a null pointer through
   `ctypes` -> the child dies -> the script must print `"status": "crashed"`,
   the exit code, **no counts**, and exit 2. D2's recipe points at a
   nonexistent path -> exit 4 -> `"status": "failed"`, **no counts**, exit 2.
   "A guard that has never been seen to fail is not a guard" applies here too.
3. **Write `scripts/check_doc_paths.py`** exactly as the plan specifies:
   standard library only; code-font references only; the five classes
   (MODULE / DIRECTORY / DOCUMENT / MODULE BY NAME / MARKDOWN LINK TARGET);
   `ALLOWED_ABSENT` starts **EMPTY** and you must NOT add an entry to make a
   failure disappear - if a documented module does not exist, the DOCUMENT is
   wrong, and the fix is to correct the document. An entry whose path now
   exists is reported as a stale allowlist entry.
   - **Demonstrate BOTH paths**: exit 0 with `missing paths: 0` on the four
     real documents, and exit 1 naming the missing path and its line number
     on a sandbox COPY outside the repository with one bad bullet added.
4. **Make the document edits** the plan specifies, per file, per line range.
   The canonical baseline block goes in all five baseline documents, with the
   numbers COPIED OUT OF THE SCRIPT'S JSON - never typed from this brief,
   never typed from `master`, never typed from memory. **One quote per
   document**, with the README badge as the one deliberate second quote.
   - The per-skip breakdown in `README.md:103` is **DELETED, not re-derived**:
     it was never measured, and inventing one would be the exact defect this
     item exists to remove.
   - The "Currently 6/6 live" claim is removed and replaced by a statement
     that the live smoke test is a manual check with **no recorded executed
     run in this repository**.
   - `docs/CODEBASE_BOUNDARIES.md:53` loses `backend/tools/web_tools.py` -
     a THIRD deleted module (`2469c09`) that the brief did not name but the
     plan found. If you name a replacement it must resolve on disk.
   - `docs/OPERATIONS.md:137-146`'s unrunnable `PYTHONPATH=/c/Ai/sythion:...`
     invocation is replaced by the two invocations that verifiably work.
   - `TESTING_GUIDE.md`'s stale block becomes a **pointer** to
     `docs/TESTING.md`, not a sixth copy of the number.
   - The plan includes two corrections BEYOND the seven criteria (the
     `TESTING_GUIDE.md` block and the `docs/OPERATIONS.md` path) and one
     deliberate exclusion (`docs/CONVERSATIONAL_PROTOCOL_PLAN.md`, which
     proposes the deleted modules as future work and is a plan, not a claim).
     Keep that split, and record all three decisions in the report.
5. **Wire the tables to the MERGED tree** - criterion 7. Read the merged
   `Makefile`, `pytest.ini` and `ci.yml` and write the "Common Test
   Commands" table, the "Pytest Markers" table and the "CI Pipeline" section
   from what those files SAY, with `file:line` for each claim. The tables must
   describe the merged state, including item 002's new
   `check-test-selections` target and the ten canonical markers with their
   real populations.

## Constraints

- **Standard library only.** No new dependency, nothing added to
  `backend/requirements.txt` or `backend/requirements.lock.txt`.
- **Do not touch any production module.** The only new executable code is the
  two scripts under `scripts/`. No runtime behaviour changes.
- Do NOT edit `Makefile`, `pytest.ini`, `backend/tests/conftest.py`,
  `.github/workflows/ci.yml`, or any `backend/**` file. The scripts READ the
  Makefile and the workflow; they do not change them.
- Do NOT write `.orchestra/follow-ups.md` - the orchestrator owns it. Put any
  replacement text you would want in your report instead.
- **Do not hard-code a count anywhere in `scripts/measure_test_baseline.py`.**
  `git grep -nE "1564|1609|692|683|659|196" -- scripts/measure_test_baseline.py`
  must print nothing. And do not put those numbers in a "forbidden values"
  list either - a structural check against a measurement needs no legacy
  constants.
- **Never push.** Never deploy, publish, or touch real data.
- **Never run destructive commands:** `rm -rf`, `git reset --hard`,
  `git clean -fd`, `git checkout -- .`, `git stash drop`, force-push.
- Do not run `make clean`, `scripts/stage_python_runtime.py`, migrations, or
  `npx tauri build`.
- **Do not ask the user anything.** If something needs a decision only a human
  can make, say so in the report and stop.
- Put any scratch files, sandbox Makefiles and harnesses OUTSIDE the
  repository, and make sure the worktree is clean when you finish
  (`git status`).

## Output contract

Write `C:\Sythion\sythion\.orchestra\reports/003-implement-1.md`:

```
# Report: implement - 003 - attempt 1

## What I did
<the commands you ran; the commits (sha + message); the files you touched>

## Evidence
<every claim with its evidence: command + output, file:line>

## Acceptance criteria
| # | Criterion | Result (met / not met / not verified) | Evidence |
(all seven, from the backlog item)

## The reproduction rows R1-R8
<the ACTUAL output of each row, on the base commit and on the branch tip;
 note R4's count is 15, not the 14 the plan claimed>

## The measurement
<the JSON the script printed, the commit it measured, the tree it measured;
 state plainly whether the tree included items 002 and 006>

## The two failure demonstrations (D1, D2)
<the sandbox Makefiles and the transcripts>

## The path check's two paths
<the pass transcript and the fail transcript>

## The document edits
<what you changed per file; the number you pasted and where it came from>

## The three corrections to the plan
<how you applied each>

## The decisions and their reasons
<the two beyond-criteria corrections and the one exclusion; the open
 questions' defaults you followed; the per-skip breakdown deletion>

## Findings
## Not checked
<REQUIRED>
```

- **Paste the ACTUAL command output.** A claim without output is not a claim.
- If a criterion is not met, say so plainly. Do not restate it as satisfied.
- Record any file you touched outside the ones named above, and why.
- Reply with a short summary. The FILE is the deliverable.

## Previous attempt (if any)

- None. This is attempt 1.
