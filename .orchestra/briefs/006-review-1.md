# Brief: review - 006 - attempt 1

- Item: 006 The readiness test must not borrow the developer's gitignored `backend/.env`
- Role: reviewer
- Goal (the slice that matters): Sythion's "better" includes **verification
  before claims** and **release and operator readiness** (`goal.md`,
  `docs/QUALITY_GATES.md:34-44` Gate 3, `:88-97` Gate 8). A test suite whose
  result depends on a file that is deliberately NOT in the repository is not a
  release-confidence signal: a GitHub Actions runner is a clean checkout, so
  the pipeline item 001 repaired would go RED on its first real run. Your job:
  judge the DIFF against the plan and the criteria - not to re-run the whole
  verification, which is the Verifier's job next.

## Read

- `C:\Sythion\sythion\.orchestra\plans/006.md` - the plan the implementation
  was supposed to follow. Note its central design decision: the isolation
  assigns only CLEAN values, computed with the env FILE disabled
  (`Settings(_env_file=None)`), and it carries a `provider_e2e` carve-out.
- `C:\Sythion\sythion\.orchestra\backlog.md` - item 006's acceptance criteria.
- `C:\Sythion\sythion\.orchestra\reports/006-implement-1.md` - the
  implementer's evidence.
- `C:\Sythion\sythion\.orchestra\critiques.md` - the orchestrator's critique
  of the implementer's report (ACCEPT) and the checks it made itself.
- The branch itself: `orchestra/006-readiness-test-isolation`, diff against
  `master`: `git -C C:\Sythion\sythion diff master...orchestra/006-readiness-test-isolation`
- The two files it touches: `backend/tests/conftest.py` and
  `backend/tests/test_setup_readiness.py`.

## Write

- `C:\Sythion\sythion\.orchestra\reviews\006-r1.md` - the ONLY file you may
  write. Use `.orchestra/review-template.md` as the shape.

**Your review file's FIRST LINE must be exactly `VERDICT: ACCEPT` or
`VERDICT: IMPROVE`.** Nothing may precede it - no heading, no comment, no
blank line. **Your report must also contain a line `- Reviewed commit:
<sha>`** and a line `- Tier:
NORMAL|EXTERNAL|SENSITIVE|SENSITIVE+EXTERNAL` giving the classifier's tier for
`master...orchestra/006-readiness-test-isolation` (run
`python C:\Users\orcha\.config\opencode\skills\dev-orchestrator\scripts\classify.py
master...orchestra/006-readiness-test-isolation` from the repo root and report
what it says).

## Context

- Current status: item 006 is `in-review`; the implementer has reported; you
  are the next stage.
- Branch: `orchestra/006-readiness-test-isolation`, commits `8746b8a` and
  `d89fb95`, based on `7cf679e`.
- Plan: `plans/006.md`.
- Previous findings: the implementer found and repaired an orphaned base
  commit left by the run's accidental-amend incident, so it rebased onto the
  current `master`.

### Where to work

The branch is **already checked out** at
`C:\Users\orcha\AppData\Local\Temp\opencode\wt-006`. Use it read-only for
inspection and for running checks. Do NOT create another worktree for the same
branch (git will refuse). Do not commit or modify anything there. There are
also three detached scratch worktrees (`wt-006-before`, `wt-006-after`,
`wt-006-teeth`) left by the implementer - you may read them, but do not delete
them; the orchestrator owns the cleanup.

### Facts already established - do not re-derive them

- The diff is exactly two files: `backend/tests/conftest.py` (+66) and
  `backend/tests/test_setup_readiness.py` (+77). **No production module is
  touched** - `backend/core/config.py`, `backend/api/routes.py` and
  `backend/core/secrets.py` are unchanged.
- The clean-value claim, measured by the orchestrator in both trees:
  - worktree (no `backend/.env`): `bool(Settings(_env_file=None).ollama_api_key)`
    = **False**, singleton = False;
  - developer's tree (`.env` present): `bool(Settings(_env_file=None).ollama_api_key)`
    = **False**, singleton = **True**.
  So the snapshot is genuinely the clean-checkout value and the developer's file
  is never read to obtain it.
- `settings` is already imported in the conftest (`:15`), and the new
  `Settings` import is guarded with the same `try/except ImportError` fallback
  the file already uses for the standalone `backend/` layout.
- The implementer's measurements: `.env`-free base `1 failed, 7 passed` ->
  branch `10 passed`; full suite in the `.env`-free worktree `1568 passed, 45
  skipped`, exit 0; **identical outcome sets** with and without a `.env` -
  36 triples for the four named files, **1613 triples** for the full suite.
- Negative controls: removing the pin fails the readiness test; removing the
  neutralisation fails the isolation test.
- Master already carries an autouse fixture (`_guarded_autonomy_default`) that
  patches `settings` for different attributes, so the new autouse fixture
  follows an existing pattern rather than introducing a new mechanism.

### Pre-existing conditions - do NOT blame the branch for these

1. **A clean checkout has no `backend/.env`** (gitignored), so
   `test_setup_readiness.py::test_readiness_states` fails in any worktree.
   That IS this item's subject.
2. **The suite is not deterministic here** - 2 native crashes in 6 observed
   full-suite runs, of two different kinds; neither reproduces in isolation.
   The implementer hit one GUI/UIA flake and one native `0xC0000005` crash and
   recorded both, with a clean third attempt.
3. **`make` is not installed on this machine**, so Makefile targets cannot be
   executed as such.

## The questions you must answer

1. **Is the fix correct at the mechanism?** The settings singleton is built
   once at import (`backend/core/config.py:179`), so the fix has to pin the
   attribute, not the variable. Verify that the implementation does exactly
   that, and that it does not merely paper over the symptom.
2. **Does the fix make the test pass for the WRONG reason?** "The credential
   is pinned" is not the same as "the readiness endpoint correctly reports
   ready when a credential is configured". The implementation adds explicit
   assertions on `body["checks"]["ollama_api_key_ok"]` in both the healthy and
   the incomplete block, plus a two-directional test and an assertion that the
   endpoint never echoes the credential. Judge whether the test still
   distinguishes "credential configured" from "credential absent" - and
   whether the new test would fail if the endpoint hard-coded its answer.
3. **THE SCOPE QUESTION - answer this one carefully.** The isolation
   neutralises three fields, not one: `ollama_api_key`, **`selected_model`**
   and **`file_roots`**. The item is titled "the readiness test must not borrow
   the developer's `backend/.env`", and `file_roots` is a **safety-gate
   field** - `goal.md`'s off-limits list names file-root grants explicitly.
   So ask: (a) is broadening the isolation to three fields justified by the
   goal, or is it scope creep? (b) does replacing a developer's configured
   file roots with the clean default change what any test observes - the
   implementer measured identical outcome sets, but did they check the
   file-root tests specifically, and do those tests assert against the
   ambient value or against their own fixture? (c) could neutralising
   `file_roots` make a test PASS for the wrong reason - i.e. a file-root
   restriction test that would have failed with the developer's real roots
   now sees `[]` and passes trivially? Look for that specifically and report
   what you find. If you think the scope is too broad, say so and say what
   the narrower version would be.
4. **Is the `provider_e2e` carve-out correct?** Those modules gate themselves
   at IMPORT time, before any fixture runs, so the carve-out is claimed to be
   necessary. Verify the reasoning, and check that the carve-out does not leave
   a hole: is there any other test that could write to the real vault through
   the developer's ambient key? (Item 007, in flight, is about exactly that -
   but the carve-out is this item's, so judge it here.)
5. **Is the new isolation test meaningful?** Its own docstring says it is "a
   DISCRIMINATOR in a tree that HAS a `backend/.env` and a tautology in a clean
   checkout". Judge whether that is an acceptable limitation or whether the
   test should be strengthened - and note that the CI runner is a clean
   checkout, so the CI value of that test is zero unless the file is present.
6. **Does the change combine cleanly with item 002?** Item 002 (in review,
   merging imminently) also edits `backend/tests/conftest.py`. The orchestrator
   checked that 002's hunks are in the UPPER region of the file (the
   `pytest_configure` marker block and the new guard hooks) with a tail
   byte-identical to master's, while 006's hunk is appended at the very END -
   so the hunks do not overlap. Confirm that, and - if you can do it cheaply -
   verify the COMBINED file works: apply 002's conftest version on top of 006's
   in a **scratch copy outside the repository** and run a collection and the
   readiness test there. Never write into the branch or the worktree.
7. **Scope discipline.** The item was scoped to tests only. Confirm no
   production module and no documentation file was touched, and that
   `follow-ups.md` was NOT edited by the implementer (criterion 6's text
   change is the orchestrator's to make - the implementer was right to put
   the replacement text in its report instead).

## Constraints

- **Read-only.** Never edit code. The only file you may write is
  `reviews/006-r1.md`.
- Do not trust the implementer's claims. Look at the diff yourself.
- For each acceptance criterion, state met / not met / **not verified**, with
  evidence. "Not verified" is an honest answer; "met" without evidence is not.
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

`C:\Sythion\sythion\.orchestra\reviews/006-r1.md`, first line exactly
`VERDICT: ACCEPT` or `VERDICT: IMPROVE`, then:

```
- Reviewed commit: <sha>
- Tier: <NORMAL|EXTERNAL|SENSITIVE|SENSITIVE+EXTERNAL>

# Review of 006 round 1

## Acceptance criteria
| # | Criterion | Result (met / not met / not verified) | Evidence |
|---|---|---|---|
| 1 | ... (all of them) |

## The scope question
<your judgement on the three neutralised fields, with evidence>

## The provider_e2e carve-out
## The isolation test's value
## Interaction with item 002
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
