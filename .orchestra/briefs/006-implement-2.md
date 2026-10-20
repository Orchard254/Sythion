# Brief: implement - 006 - attempt 2 (redo of the two Reviewer findings)

- Item: 006 The readiness test must not borrow the developer's gitignored `backend/.env`
- Role: implementer (implementer-006, redo round 1)
- Goal (the slice that matters): Sythion's "better" includes **verification
  before claims** and **release and operator readiness** (`goal.md`,
  `docs/QUALITY_GATES.md:34-44` Gate 3, `:88-97` Gate 8). A test suite whose
  result depends on a file that is deliberately NOT in the repository is not a
  release-confidence signal. This redo removes the two defects the Reviewer
  found in the item's own assertions.

## Read

- `C:\Sythion\sythion\.orchestra\reviews\006-r1.md` - **THE REVIEW.** Read it
  fully. It is the contract for this round.
- `C:\Sythion\sythion\.orchestra\plans/006.md` - the plan.
- `C:\Sythion\sythion\.orchestra\reports/006-implement-1.md` - round 1's
  report (for the "before" numbers and the shape).
- `C:\Sythion\sythion\.orchestra\backlog.md` - item 006's acceptance criteria.
- The two files: `backend/tests/conftest.py`, `backend/tests/test_setup_readiness.py`.

## Write

- `C:\Sythion\sythion\.orchestra\reports/006-implement-2.md` - the evidence
  report for this redo round. **This is the deliverable.**
- Commits on top of `d89fb95`.

## Context

- Current status: item 006 is `needs-improvement`; review round 1 of a maximum
  2; the Reviewer returned `VERDICT: IMPROVE` with exactly two required
  changes.
- Branch: **`orchestra/006-readiness-test-isolation`** - worktree `wt-006` is
  already checked out at `d89fb95`. Work there; do NOT create another worktree
  for the same branch (git will refuse).
- Plan: `plans/006.md`.

### The Reviewer's two findings, in full - both are real and both are confirmed

**FINDING 1 - the new test compares the credential BY VALUE, so its failure
message PRINTS the key.** `backend/tests/test_setup_readiness.py:328-330`:

```python
assert core_config.settings.ollama_api_key == clean.ollama_api_key
assert core_config.settings.selected_model == clean.selected_model
assert core_config.settings.file_roots == clean.file_roots
```

The Reviewer reproduced this: the failure message prints the key (it saw
`E + dummy-not-a-real-credential`; in a developer's tree that value IS the
real key, and the plan's own proof uses `--junitxml`, so the leak lands in the
artefact too). The Reviewer also measured which assertion shapes echo: `assert
a == b` and `assert bool(a) == bool(b)` **both** echo the value; only
`same = a == b; assert same is True` is safe.

**FIX IT so the assertion cannot echo the credential.** The requirement is
that a failure must reveal that the values DIFFER, never WHAT they are. Use
the shape the Reviewer identified, or an equivalent that is equally safe.

**FINDING 2 - the snapshot's "copied so a test that mutates one in place cannot
corrupt the snapshot" docstring is FALSE.** `backend/tests/conftest.py:184`
claims the copy protects the snapshot; `conftest.py:216-217` hands the SAME
list object to every test. The Reviewer demonstrated the corruption leaking
into the next test.

Look at the actual code: `_clean_settings_snapshot()` does
`snapshot[name] = list(value) if isinstance(value, list) else value` - so the
snapshot's list is a copy of `clean`'s list, but the snapshot's list is then
handed to `monkeypatch.setattr(settings, name, value)` **by reference**. A test
that mutates `settings.file_roots` in place (e.g. `.append(...)`) mutates the
snapshot's list object itself, and the next test receives the corrupted value.

**FIX IT so each test receives its own copy** (or the fixture assigns a fresh
copy per test), and make the docstring say what is actually true. Note that
this is the `file_roots` field - the safety-gate field - so the corruption is
not a harmless list.

### What the Reviewer CONFIRMED - do not regress any of it

The Reviewer re-derived all of this itself, using scratch `git archive` trees
OUTSIDE the repository, and the outcome sets are unchanged by your edits.

- **All six acceptance criteria are met** as of `d89fb95`.
- **The scope question is answered, and the answer is FAVOURABLE.** The
  three-field scope is the BACKLOG'S OWN criterion 3, not implementer scope
  creep. On base code, with all three ambient fields set versus no `.env`,
  across 18 file-root / settings / model-heavy files, the ONLY outcome that
  changes is `test_readiness_states` - ambient `file_roots` and
  `selected_model` are **observationally inert**, and every file-root test
  pins its own roots. And the "passes trivially" worry the orchestrator raised
  is **backwards**: `file_roots=[]` means the home+cwd fallback
  (`backend/tools/file_tools.py:207`), so isolation makes the rejection
  assertions **STRICTER**, not looser. No test can pass for the wrong reason,
  because after isolation the effective configuration IS CI's configuration.
- **The mechanism is correct**: patching the instance ATTRIBUTE is required
  (9 production modules hold module-level `settings` references).
- **The endpoint is pinned in BOTH directions**: hard-coding `True` makes the
  new test fail; hard-coding `False` makes `test_readiness_states` fail.
- **The `provider_e2e` carve-out is correctly scoped**: the module-level
  `pytestmark` is found, a method-level marker is found, and an unmarked
  sibling is still neutralised.
- **The isolation test has ZERO CI value** - honestly documented by the
  implementer. Leave that honesty in place; do not dress it up.

### A CONFLICT THE REVIEWER FOUND THAT THE ORCHESTRATOR GOT WRONG

The Reviewer found that **items 002 and 006 CONFLICT textually at the
conftest tail**, and the orchestrator - who had told the Reviewer the hunks
did not overlap - was wrong. The orchestrator has since reproduced it:
merging `002` and then `006` produces `CONFLICT (content): Merge conflict in
backend/tests/conftest.py` at the file's last line.

The cause is exact and worth knowing: **master's `backend/tests/conftest.py`
ends WITHOUT a trailing newline.** Both branches append after
`acceptance_control_token`, and each branch's diff shows
`-    return "acceptance-control-token"` / `\ No newline at end of file` /
`+    return "acceptance-control-token"` - so both branches modify the SAME
final line (adding the newline), and git cannot order the two appended blocks.
It is a trivial conflict to resolve by hand, but it is REAL, and it must be
resolved deliberately rather than by accident.

**You do NOT have to resolve that conflict - the Committer does that at merge
time.** But you MUST make sure your fix does not make it harder, and you MUST
say in your report that it exists and how it should be resolved (the obvious
resolution: take 006's appended block and keep 002's added newline).

### Also from the Reviewer: the TIER

`classify.py master...orchestra/006-readiness-test-isolation` reports
**SENSITIVE** (22 credential/payments pattern matches, all in the two test
files). The orchestrator had recorded `none` on the board. The board will be
corrected to match the classifier - that is the orchestrator's job, not yours.
Your diff must not ADD any new credential-shaped match; keep it that way.

## Constraints

- Work only on the assigned item and branch. Commit on top of `d89fb95`. Do
  not rewrite history, do not rebase, do not amend.
- **Do not touch any production module.** The diff must stay inside
  `backend/tests/`. In particular do NOT edit `backend/core/config.py`,
  `backend/api/routes.py`, `backend/core/secrets.py` or
  `backend/tools/file_tools.py` (the last is cited only as evidence).
- Do NOT edit `.github/workflows/ci.yml`, `Makefile`, `pytest.ini`, the ROOT
  `backend/tests/conftest.py`'s upper region (that is item 002's), or any
  `docs/` file.
- Do NOT write `.orchestra/follow-ups.md` - the orchestrator owns it. Put any
  replacement text you would want in your report instead.
- **Never print a credential value.** The whole point of finding 1 is that a
  failing assertion must not echo the key. Your own test runs must not echo it
  either: when you run the test that reproduces finding 1, use a DUMMY value
  (as the Reviewer did), never the developer's real one.
- **Never push.** Never deploy, publish, or touch real data.
- **Never run destructive commands:** `rm -rf`, `git reset --hard`,
  `git clean -fd`, `git checkout -- .`, `git stash drop`, force-push. (The
  orchestrator's own scratch worktrees are the orchestrator's to clean up.)
- Do not run `make clean`, `scripts/stage_python_runtime.py`, migrations, or
  `npx tauri build`.
- **Known hazard:** the suite is not deterministic on this machine (2 native
  crashes in 6 observed full-suite runs, of two different kinds; neither
  reproduces in isolation; the smallest reproducer is the FULL SUITE). If a
  run dies that way, re-run it and record BOTH attempts - it is a pre-existing
  condition, not your failure.
- Do not ask the user anything.

## Output contract

Write `C:\Sythion\sythion\.orchestra\reports/006-implement-2.md`:

```
# Report: implement - 006 - attempt 2

## What I did
<the commands you ran; the commits (sha + message)>

## Evidence
<every claim with its evidence: command + output, file:line>

## The two Reviewer findings
| # | Finding | Status | Evidence that it is now true |
|---|---|---|---|
| 1 | value comparison echoes the credential | fixed / not fixed | ... |
| 2 | snapshot list is shared and mutable | fixed / not fixed | ... |

## Acceptance criteria
| # | Criterion | Result | Evidence |
(all six, re-checked AFTER the fix)

## What did not regress
<the Reviewer's confirmed list, re-verified with your numbers>

## The 002/006 conftest conflict
<your statement of the cause and the recommended resolution>

## The file_roots scope question
<your own conclusion, with evidence - the Reviewer's is attached above and
 you should either agree with it or refute it with evidence>

## Findings
## Not checked
<REQUIRED>
```

- **Paste the ACTUAL command output.** A claim without output is not a claim.
- For finding 1, show the failing assertion's message BEFORE and AFTER the
  fix, using a DUMMY credential, and show that the "after" message does not
  contain the value.
- For finding 2, show a test that mutates the list in place and demonstrate
  that the NEXT test still receives the clean value.
- If a finding or a criterion is not satisfied, say so plainly.
- Reply with a short summary. The FILE is the deliverable.

## Previous attempt (if any)

- Round 1: `reports/006-implement-1.md` - ACCEPTED by the orchestrator, then
  the Reviewer returned `VERDICT: IMPROVE` with the two findings above.
- Round 2 (this one): fix the two findings, keep everything the Reviewer
  confirmed, and re-prove it.
