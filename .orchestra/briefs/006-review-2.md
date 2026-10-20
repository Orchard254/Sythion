# Brief: review - 006 - attempt 2 (review round 2)

- Item: 006 The readiness test must not borrow the developer's gitignored `backend/.env`
- Role: reviewer (reviewer-006, round 2 of a maximum 2)
- Goal (the slice that matters): Sythion's "better" includes **verification
  before claims** and **release and operator readiness** (`goal.md`,
  `docs/QUALITY_GATES.md:34-44` Gate 3, `:88-97` Gate 8). A test suite whose
  result depends on a file that is deliberately NOT in the repository is not a
  release-confidence signal. Your job: decide whether the redo round actually
  closed the two findings of review round 1, and whether it kept everything
  round 1 established.

## Read

- `C:\Sythion\sythion\.orchestra\reviews/006-r1.md` - **THE ROUND-1 REVIEW.**
  Its two findings are the contract for this round.
- `C:\Sythion\sythion\.orchestra\reports/006-implement-2.md` - the redo's
  report, with its fourteen labelled evidence sections (E0-E13).
- `C:\Sythion\sythion\.orchestra\plans/006.md` - the plan.
- `C:\Sythion\sythion\.orchestra\backlog.md` - item 006's acceptance criteria.
- The branch: `orchestra/006-readiness-test-isolation`, five commits:
  `8746b8a` and `d89fb95` (round 1), `96130b8`, `009a455`, `f144557` (the
  redo).
- The two files: `backend/tests/conftest.py`,
  `backend/tests/test_setup_readiness.py`.

## Write

- `C:\Sythion\sythion\.orchestra\reviews/006-r2.md` - the ONLY file you may
  write. Use `.orchestra/review-template.md` as the shape.

**Your review file's FIRST LINE must be exactly `VERDICT: ACCEPT` or
`VERDICT: IMPROVE`.** Nothing may precede it. **Your report must also contain
a line `- Reviewed commit: <sha>`** and a line `- Tier:
NORMAL|EXTERNAL|SENSITIVE|SENSITIVE+EXTERNAL` from
`python C:\Users\orcha\.config\opencode\skills\dev-orchestrator\scripts\classify.py
master...orchestra/006-readiness-test-isolation`.

## Context

- Current status: item 006 is `in-review`; this is review round 2 of a
  maximum 2. The round-1 verdict was `VERDICT: IMPROVE` with two findings.
- Branch: `orchestra/006-readiness-test-isolation`, tip `f144557`.
- Plan: `plans/006.md`.

### The two findings the redo had to close

1. **`test_setup_readiness.py:328-330` compared the credential BY VALUE**, so
   its failure message PRINTED the key (the Reviewer reproduced it: the message
   printed `E + dummy-not-a-real-credential`; in a developer's tree that value
   IS the real key, and the plan's own proof uses `--junitxml`, so the leak
   lands in the artefact too). The Reviewer measured which assertion shapes
   echo: `assert a == b` and `assert bool(a) == bool(b)` **both** echo; only
   `same = a == b; assert same is True` is safe.
   **The redo** compares via a boolean and asserts on that, generalised across
   the three fields, with the field name in the message and no local variable
   ever holding the credential.
2. **`conftest.py`'s docstring claimed the copy protected the snapshot, but the
   fixture handed the SAME list object to every test** - so a test mutating
   `settings.file_roots` in place corrupted the value every later test was
   isolated from. The Reviewer demonstrated the corruption leaking into the
   next test.
   **The redo** assigns a FRESH copy per test and rewrites the docstring to say
   the per-test copy is what protects the snapshot.

### Facts already established - do not re-derive them

- The orchestrator **verified both fixes independently**:
  - Running BOTH assertion shapes **under pytest** (where the rendering
    happens - a plain Python `assert` does not render operands, so the
    orchestrator's first attempt, outside pytest, showed neither shape echoing
    and was WRONG) with a SENTINEL value in place of the credential:
    the OLD shape produced `E assert 'SENTINEL-CAN...E-DO-NOT-ECHO' == 'some-other-value'`
    and the diff `E - some-other-value` / `E + SENTINEL-CANARY-VALUE-DO-NOT-ECHO`
    - the full value rendered; the NEW shape produced only
    `E AssertionError: ollama_api_key differs from the value a clean tree has`
    and `E assert False is True` - no value at all.
  - **NEGATIVE CONTROL for finding 2:** reverting ONLY the one-line per-test
    copy makes `test_the_clean_settings_snapshot_survives_an_in_place_mutation`
    FAIL (`1 failed, 10 passed`); with the fix restored, `11 passed`. So the
    new regression test has real teeth.
  - The orchestrator's own negative control left a UTF-8 BOM in the worktree
    (from PowerShell's `Set-Content -Encoding utf8`), which it detected and
    repaired byte-exactly with `git checkout --`. The worktree is clean now.
- The implementer's report carries fourteen labelled evidence sections, each a
  measurement rather than an assertion, including both findings' BEFORE and
  AFTER states.
- The classifier says **SENSITIVE** for this branch - the orchestrator has
  re-confirmed it. The board will mirror it. The third commit `f144557` is a
  two-line WORDING change whose stated reason is that the two new messages
  contained the word "checkout", which the classifier counts as a `payments`
  pattern match; the change is so the branch adds NO new credential-shaped
  match. **Judge whether that is a reasonable thing to do** and whether the
  diff's sensitivity is honestly stated.
- The implementer built and verified the 002/006 conftest conflict resolution
  itself, reaching the same conclusion the orchestrator reached independently:
  keep both blocks and add the trailing newline.

### Pre-existing conditions - do NOT blame the branch for these

1. **A clean checkout has no `backend/.env`** (gitignored), so
   `test_setup_readiness.py::test_readiness_states` fails in any worktree.
   That IS this item's subject.
2. **The suite is not deterministic here** - 2 native crashes in 6 observed
   full-suite runs, of two different kinds; neither reproduces in isolation.
3. **`make` is not installed on this machine** - run the recipe commands
   verbatim and say so.
4. The plan's `1564 passed` baseline has moved to `1566` on current `master`
   (item 004 added two tests). Item 006 ADDS two tests, so its own count is
   `1568`.

## The questions you must answer

1. **Are both findings genuinely closed?** Judge by reproduction, not by
   reading the report. For finding 1, force a failure of that test **with a
   SENTINEL value standing in for the credential** - never with the
   developer's real key - and confirm the failure output contains NO value.
   For finding 2, confirm that a test mutating `settings.file_roots` in place
   does not corrupt what the NEXT test receives.
2. **Did the redo break anything else?** The redo touched two files. Look for
   collateral damage - especially in the isolation fixture, the new
   `clean_settings_snapshot` fixture, and the `provider_e2e` carve-out.
3. **Is the new regression test meaningful, or is it a tautology?** It proves
   the snapshot survives an in-place mutation. Could it pass for the wrong
   reason - for example if the isolation were removed entirely? Judge it.
4. **What did NOT regress?** Re-verify and report YOUR numbers: all six
   acceptance criteria; the marker populations if any are affected; the
   `.env`-absent and `.env`-present outcomes for
   `backend/tests/test_setup_readiness.py`; the outcome sets across the
   file-root / settings / model-heavy files; the full suite's count; and the
   `provider_e2e` carve-out's scope.
5. **The scope question - is the answer still sound?** Round 1 concluded that
   neutralising `file_roots` and `selected_model` is justified, that ambient
   values are observationally inert, and that `file_roots = []` means the
   home+cwd fallback so the isolation is STRICTER not looser. The redo did
   not change the scope. Do you still agree? Say so or refute it with
   evidence.
6. **The 002/006 conftest conflict.** Confirm the conflict's shape and that
   the recommended resolution (keep both blocks, add the trailing newline) is
   right, so the Committer is not surprised. Item 002 does not have to fix it.
7. **Scope discipline.** Confirm the diff touches only the two named files,
   that no production module and no documentation file was touched, and that
   `follow-ups.md` was NOT edited by the implementer (criterion 6's text
   change is the orchestrator's to make - the implementer was right to put the
   replacement text in its report instead).

## Constraints

- **Read-only.** Never edit code. The only file you may write is
  `reviews/006-r2.md`. If you need a probe file, put it OUTSIDE the
  repository, or delete it and leave `git status --short` empty.
- Do not trust the implementer's claims. Look at the diff yourself.
- For each acceptance criterion, state met / not met / **not verified**, with
  evidence. "Not verified" is an honest answer; "met" without evidence is not.
- **Never print a credential value.** Use a SENTINEL value when you need a
  failing assertion - never the developer's real key.
- Do not write `follow-ups.md` - the orchestrator owns it.
- Never mark PASS on something you did not inspect. State what you did not
  check.
- Do not push, deploy, publish, or touch real data. Do not run `make clean`,
  `scripts/stage_python_runtime.py`, migrations, or `npx tauri build`.
- Do not ask the user anything.

## Output contract

`C:\Sythion\sythion\.orchestra\reviews/006-r2.md`, first line exactly
`VERDICT: ACCEPT` or `VERDICT: IMPROVE`, then:

```
- Reviewed commit: <sha>
- Tier: <NORMAL|EXTERNAL|SENSITIVE|SENSITIVE+EXTERNAL>

# Review of 006 round 2

## The two findings
| # | Finding | Closed? | Evidence that it is now closed |
|---|---|---|---|
| 1 | value comparison echoes the credential | yes / no | ... |
| 2 | snapshot list is shared and mutable | yes / no | ... |

## Acceptance criteria
| # | Criterion | Result (met / not met / not verified) | Evidence |
(all six)

## Is the new regression test meaningful?
## What did not regress
<your numbers>

## The scope question
## Interaction with item 002
## Sensitivity
## Goal fit
## Findings (most important first)
1. <file:line> - problem - what to change

## Deferred
## Needs human?
## Not checked
```

## Previous attempt (if any)

- Round 1: `reviews/006-r1.md` - `VERDICT: IMPROVE` with the two findings
  above. Round 2 is the last review round; if you cannot clear a finding, say
  precisely what remains and why.
