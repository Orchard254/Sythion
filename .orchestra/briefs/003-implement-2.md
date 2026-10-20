# Brief: implement - 003 - attempt 2 (re-measure on the final merged tree)

- Item: 003 Documentation states the measured baseline and drops deleted modules
- Role: implementer (implementer-003, second dispatch)
- Goal (the slice that matters): Sythion's "better" includes **verification
  before claims** and **honest debt reporting** (`goal.md`,
  `docs/QUALITY_GATES.md:34-44` Gate 3, `:45-59` Gate 4, `:88-97` Gate 8).
  The documentation must state a number that is TRUE, and the numbers move.

## Why this dispatch exists

Your first round produced good work and the orchestrator has verified a great
deal of it. But **the measurement you took is one test out of date**, and the
orchestrator's own drift detector caught it.

**What happened.** You measured the merged tree and wrote `1568 passed, 45
skipped` (1613 collected) into the documents. That measurement was TRUE of the
tree you measured. But item 006 has since had its **redo round**, which added a
**third** new test to `backend/tests/test_setup_readiness.py`:
`test_the_clean_settings_snapshot_survives_an_in_place_mutation`. So the merged
tree now collects **1614**, not 1613.

**The detector caught it, with a precise message:**
```
python scripts/measure_test_baseline.py --check-collected
-> README.md:103: states '1613' collected but the measurement collected 1614
-> exit 1
```
**This is your mechanism working exactly as designed** - the cheap ~100-second
drift detector found a one-test drift in a document a human had just written
with a measurement in hand, and named the file and the line. The orchestrator
recorded that as the strongest evidence yet that the item's core idea is sound.
It also caught the ORCHESTRATOR's own record, which had said 1613 in two
places - both corrected.

**YOUR JOB: re-measure on the tree that is now final, and update the numbers
that come out of it. Nothing else needs to change.**

## Read

- `C:\Sythion\sythion\.orchestra\briefs\003-implement-1.md` - your first
  brief (still the contract for the scripts and the document edits).
- `C:\Sythion\sythion\.orchestra\plans/003.md` - the plan, including the
  orchestrator's amendment at the top (the count in it is now **1614**).
- `C:\Sythion\sythion\.orchestra\critiques.md` - the orchestrator's critique
  of your round 1 (ACCEPTED, with the drift finding) and the mid-flight note
  about the `--check` regex.

## Write

- `C:\Sythion\sythion\.orchestra\reports/003-implement-1.md` - the evidence
  report. If a report file already exists from your first round, UPDATE it
  rather than starting a new one - keep the good parts and correct the numbers.
- A commit on top of `485a797` on `orchestra/003-docs-baseline`.

## Context

- Current status: item 003 is `building`.
- Branch: **`orchestra/003-docs-baseline`** (worktree `wt-003`), currently at
  `485a797`.
- **THE TREE TO MEASURE ON: `orchestra/003-final-merged`, tip `b0127b0`.**
  This is a branch the orchestrator built and VERIFIED for exactly this
  purpose: it merges items 007, 002 and 006 into `master` and resolves the
  one conftest conflict by keeping both sides. The orchestrator has confirmed:
  - it collects **1614** tests, exit 0;
  - `backend/tests/test_setup_readiness.py` contains all THREE of item 006's
    new tests, including
    `test_the_clean_settings_snapshot_survives_an_in_place_mutation`;
  - the resolved `backend/tests/conftest.py` has **no conflict markers**, ends
    with a newline, and contains each of 002's and 006's blocks exactly once;
  - **it carries item 007's HARDENED vault guard**
    (`target.lower().startswith(_PRODUCT_TARGET_PREFIX_LOWER)`), so a
    full-suite measurement on this tree is SAFE - it will not write to or
    delete the developer's real credential. **This is why you must measure on
    this tree and not on your old `orchestra/003-measure-merged` branch**,
    which predates item 006's redo AND carries the pre-hardening guard.

## What to do

1. **Create a worktree for `orchestra/003-final-merged`** - a NEW worktree, at
   a path of your choosing under
   `C:\Users\orcha\AppData\Local\Temp\opencode\`. The branch is not checked out
   anywhere yet, so `git worktree add <path> orchestra/003-final-merged` will
   work. **Do not check it out in `wt-003`** - that worktree already has
   `orchestra/003-docs-baseline` checked out, and git refuses to check out one
   branch twice.
2. **Overlay YOUR scripts and YOUR documents onto that tree** (the way you did
   with `orchestra/003-measure-merged`), so the measurement runs against the
   merged code with your documentation in place.
3. **Re-measure.** The full-suite measurement is SAFE on this tree - it carries
   item 007's guard. Run the measurement and record the JSON, the commit it
   measured, and the tree it measured.
4. **Paste the NEW numbers into the documents** - in all five baseline
   documents and the README badge - taking them from the JSON, not from this
   brief and not from your old measurement. The collected count should now be
   **1614**; the passed/skipped pair will be whatever the script prints.
5. **Demonstrate that `--check` now exits 0** against the final tree with the
   final numbers. That is the objective check for criterion 2.
6. **Demonstrate `--check-collected` exits 0** as well.
7. **Commit** the updated documents on `orchestra/003-docs-baseline` on top of
   `485a797`.
8. **Write the report.**

## Constraints

- **The rest of your work stands.** The orchestrator has already verified and
  recorded, in `critiques.md`:
  - criterion 1's grep is **EMPTY** in both scripts - no hard-coded count, not
    even in a "forbidden values" list;
  - the exit-code contract is exactly the amended one (`0` ok / `1` stale /
    `2` inconclusive / `3` usage / `4` environment);
  - the three trustworthiness rules are in the script's own docstring;
  - `check_doc_paths.py` is correct - `ALLOWED_ABSENT` starts EMPTY and the
    pass path gives `missing paths: 0`, exit 0;
  - **no wrong number survives in any of the six edited documents** - the old
    `692 / 683 / 659 / 196 / 32 skipped` figures are gone;
  - **the deleted modules are gone from all four criterion-6 documents**,
    including the third one the plan found (`backend/tools/web_tools.py`);
  - the `--check` regex defect the orchestrator found mid-flight (the platform
    group swallowed the trailing `" by"`, so `--check` could never exit 0) was
    **fixed by you** before the orchestrator's second measurement - the
    orchestrator has re-verified that `--check` now exits 0 against your real
    numbers. **Do not reintroduce it.**
  Do not rewrite these. Your round-2 change should be the NUMBERS and the
  report.
- **Standard library only.** No new dependency, nothing added to
  `backend/requirements*.txt`.
- Do NOT edit `Makefile`, `pytest.ini`, `backend/tests/conftest.py`,
  `.github/workflows/ci.yml`, or any `backend/**` file.
- Do NOT write `.orchestra/follow-ups.md` - the orchestrator owns it.
- Put any scratch files OUTSIDE the repository. Leave every worktree you
  create CLEAN when you are done (`git status --short` empty), and remove any
  worktree you create that is no longer needed - EXCEPT the one you measure
  in, which the orchestrator will inspect.
- **The measurement is SAFE on `orchestra/003-final-merged`** because it
  carries item 007's guard. You may write to and delete from a TEST-SCOPED
  credential target (`Sythion (Synth) [TEST]/...`). You must NOT write to or
  delete from the PRODUCT target (`Sythion (Synth)/...`) in any spelling. Never
  print a credential value.
- **Never push.** Never deploy, publish, or touch real data.
- **Never run destructive commands:** `rm -rf`, `git reset --hard`,
  `git clean -fd`, `git checkout -- .`, `git stash drop`, force-push.
- Do not run `make clean`, `scripts/stage_python_runtime.py`, migrations, or
  `npx tauri build`.
- **Known hazard:** the suite is not deterministic here (2 native crashes in 6
  observed full-suite runs, of two different kinds; neither reproduces in
  isolation). If a run dies that way, re-run it and record BOTH attempts.
- Do not ask the user anything.

## Output contract

Write `C:\Sythion\sythion\.orchestra\reports/003-implement-1.md` (updating your
round-1 report, not replacing it):

```
# Report: implement - 003 - attempt 2

## What changed in this round
<the re-measurement: the tree, the commit, the JSON, the numbers before and
 after, and the commit>

## The drift the detector caught
<the --check-collected transcript that showed 'states 1613 collected but the
 measurement collected 1614', and the transcript showing it now passes>

## The measurement
<the JSON the script printed; the commit it measured; the tree it measured>

## --check now exits 0
## The reproduction rows R1-R8
<the ACTUAL output of each row on the branch tip; note R4's count is 15>

## Acceptance criteria
| # | Criterion | Result | Evidence |
(all seven)

## The document edits
<what you changed per file, and the number you pasted and where it came from>

## The two failure demonstrations (D1, D2)
## The path check's two paths
## Findings
## Not checked
```

- **Paste the ACTUAL command output.** A claim without output is not a claim.
- If a criterion is not met, say so plainly.
- Reply with a short summary. The FILE is the deliverable.

## Previous attempt (if any)

- Round 1: `reports/003-implement-1.md` - the scripts, the document edits and
  the first measurement. ACCEPTED on everything except that the measurement
  predated item 006's redo round, which added a third test.
