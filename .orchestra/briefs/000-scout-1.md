# Brief: scout - 000 - attempt 1

- Item: 000 Rank the research candidates into an actionable backlog
- Role: scout
- Goal (the slice that matters): Sythion's "better" is defined in `goal.md`:
  the runtime boundary stays strict, **claims are backed by executed evidence
  and docs match the code**, release/operator readiness, and honest debt
  reporting. `docs/QUALITY_GATES.md:34-44` (Gate 3, verification before claims),
  `:45-59` (Gate 4, docs must reflect reality) and `:88-97` (Gate 8, testing
  baseline discipline) are the repo's own words for the same thing. The backlog
  exists to make this repo's *release-confidence signal* real.

## Read

- `C:\Sythion\sythion\.orchestra\goal.md` - the goal, the off-limits list, the
  budget.
- `C:\Sythion\sythion\.orchestra\research.md` - the Researcher's candidate
  queue, R-001 .. R-018. This is your input. Do not re-research.
- `C:\Sythion\sythion\.orchestra\backlog.md` - currently empty; you fill it.
- `C:\Sythion\sythion\.orchestra\board.json` - currently `items: {}`.
- `C:\Sythion\sythion\.orchestra\critiques.md` - the orchestrator's critique of
  the Researcher's report (it was ACCEPTED, with one correction to the run
  baseline).
- `C:\Sythion\sythion\docs/QUALITY_GATES.md` - the repo's own definition of a
  professional change. This is the yardstick for "is this item worth doing".
- `C:\Sythion\sythion\docs/ROADMAP.md` - the repo's stated "Now / Next / Later /
  Deferred" priorities. An item that contradicts the roadmap's "Deferred" list
  is wrong, whatever its evidence.

## Write

- `C:\Sythion\sythion\.orchestra\backlog.md` - the ranked backlog. OVERWRITE it.
- `C:\Sythion\sythion\.orchestra\board.json` - OVERWRITE it with an `items`
  block holding every item you create, plus the unchanged `roles` block.
- `C:\Sythion\sythion\.orchestra\research.md` - **edit in place, do not
  rewrite.** Change only the `- Status:` line of each candidate block, to
  `adopted -> <item-id>` or `rejected`. When you reject one, add a
  `- Rejected reason: <...>` line. Keep every other byte of the file intact.

Do not create branches, do not commit, do not touch any file outside
`.orchestra/`.

## Context

- Current status: run start. The Researcher's report has been critiqued and
  ACCEPTED (`.orchestra/critiques.md`). No backlog items exist yet.
- Branch: `master` in `C:\Sythion\sythion`. Merges will target `master`.
- Plan: n/a
- Previous findings: none

**The orchestrator's independent checks on the Researcher's top claims**
(these are confirmed facts; use them, do not re-derive them):

- R-001 CONFIRMED: from the repo root `python -c "import backend.main; import
  backend.api.routes"` -> `IMPORT OK`, exit 0. From `backend/` the same command
  -> `ModuleNotFoundError: No module named 'backend'`, exit 1.
  `.github/workflows/ci.yml:12-14` sets `working-directory: backend` for the
  whole `backend-tests` job, so the import gate at `:35-36` is red.
- R-002 CONFIRMED: `git branch -a` -> only `master` / `origin/master`.
  `ci.yml:4-5` triggers on `branches: [main]`.
- R-003 CONFIRMED: `pytest.mark.gui`, `.network` and `.acceptance` are applied
  to **zero** tests anywhere in `backend/tests/`. `slow` is applied once
  (`test_phase105_concurrency.py:465`). `flaky` and `integration` are applied
  zero times - they appear only in a comment at `backend/tests/conftest.py:29`.
- R-016 CONFIRMED: four `assert ... or True` tautologies at
  `test_phase105_concurrency.py:530`, `:746`, `test_phase1075_hardening.py:765`,
  `test_phase125_planning_validation.py:230`.
- R-007 CONFIRMED BY TWO OBSERVERS: the orchestrator's own full-suite run
  captured the header the Researcher never saw - `Windows fatal exception: code
  0x80040155` (`CLASS_E_CLASSNOTAVAILABLE`, a COM fault) inside the
  `sythion-uia-mta` worker thread, after which the run continued to 58%. The
  Researcher saw the same fault class at `backend/tools/gui_tools.py:426`. An
  identical second run completed normally.
- R-005: the run baseline's original claim that seven markers were undeclared
  was WRONG; the Researcher corrected it. The genuinely undeclared markers are
  `concurrency` and `gated_soak`.

**Not yet independently confirmed** (a baseline agent is measuring these right
now - treat as claims, not facts):
- R-004's exact numbers: `1564 passed, 45 skipped`, `1609 collected`, `533 s`.
  The *direction* of R-004 is not in doubt - every documented figure (692 / 683
  / 659) is below even the collected count, and `docs/TESTING.md:39` claims a
  "~55s" full suite. Only the exact replacement numbers are unconfirmed.

## Constraints

- **Do not invent work.** If a candidate is not worth doing, reject it with a
  reason. A short, honest backlog beats a padded one. Do not manufacture an
  item to fill the queue.
- **Every item must have acceptance criteria that someone who was NOT in this
  conversation can check objectively.** "The code is better" is not a criterion.
  "A script exists that prints the suite's counts and `docs/TESTING.md` quotes
  the same numbers, and the script's output matches the last executed run" is.
- **Respect the off-limits list in `goal.md`.** An item that behaviourally
  changes auth, secrets, the DB schema, the public API, or a safety gate
  (file-root grants, elevated-window denial, winget restrictions, budgets)
  needs the sensitive-change gate, and most of those are not worth the run's
  budget. Items that only ADD TESTS, DOCS or OBSERVABILITY to those areas are
  fine.
- **One item = one branch = one reviewable change.** Split anything large.
- Predict the sensitivity for each item (`none`, `payments`, `access`,
  `security`, `data`, `api`, `infra`, `unknown`). `.github/workflows/**` is
  treated as an *external* path by the orchestrator's classifier: an item that
  changes a workflow still merges, but the outside action (actually running it
  on GitHub) is written to `follow-ups.md` and never performed here.
- Size S/M/L. If an item is L, split it into S/M items now.
- Risk low/med/high with one line of justification.
- Likely files: real paths or globs.
- Do not push, deploy, publish, or touch real data.
- Do not ask the user anything. If something needs a decision you cannot make,
  say so in the report and stop.

## The orchestrator's view on value - use it, but justify your own ranking

The run's budget is **5 merged items**. Rank by value against effort and risk,
best first, and make sure the top of the list is worth more than the bottom.

Signals the orchestrator sees, stated so you can argue with them:

1. **R-001 + R-002 (+ R-014, R-015) are one story, not four.** CI on this
   repository currently does not trigger, and the one gate that does exist is
   broken. The repo's own release-confidence claim rests on CI that has never
   run. Fixing "CI actually gates this repo" is high value, low risk and
   reviewable - but note that *running* it on GitHub is outside the repo and
   becomes a follow-up.
2. **R-003 + R-005 are one story.** The Makefile's `test-gui`, `test-network`
   and `test-unit` targets claim selections they do not perform, and
   `docs/TESTING.md` quotes runtimes that are wrong by ~10x. This is Gate 4
   ("docs must reflect reality") and Gate 8 ("testing baseline discipline")
   failing at once, and it is the kind of drift that makes every future agent
   wrong about the repo.
3. **R-004 is cheap and unblocks trust.** Docs claiming 692 when 1609 are
   collected is exactly the "honest handling of debt" that Gate 9 demands. It
   also has a natural *proof*: a script that emits the numbers, so the docs
   quote a measured fact instead of a remembered one. Watch out: acceptance
   criteria must be written so the numbers are re-measured at verification
   time, not hardcoded to `1564`.
4. **R-017 is a small, near-zero-risk win** against an authority doc that
   `QUALITY_GATES.md:50-53` names explicitly.
5. **R-016 is small but has teeth.** Replacing `assert ... or True` with the
   real assertion may make a test FAIL, because the tautology was hiding
   something. That is a feature, not a bug - but it means the item needs a
   clear rule for what happens when a real assertion fails: fix the code, or
   report the defect, but never re-introduce a tautology to get green.
6. **R-006 (184 `utcnow()` call sites), R-009 (~253 MB of tracked binaries),
   R-010/R-011 (release payload)** are large, touch packaging or timestamp
   semantics, and are expensive to verify. They are legitimate candidates but
   should not be near the top of a 5-item budget unless you have a strong,
   specific reason and the acceptance criteria are genuinely checkable.
7. **R-007 (the intermittent COM crash)** is the highest *severity* defect but
   the hardest to pin: it is intermittent, it is a native fault, and the
   orchestrator's own guidance for sensitive work is that a fix must pin an
   invariant. If you rank it, its acceptance criteria must be about
   *observability* (a fault becomes a visible test failure instead of a dead
   suite) rather than about eliminating a native race you cannot reproduce on
   demand. Be honest in the item about what can and cannot be proven.

## Output contract

- `backlog.md` - ONE block per item, exactly this shape:

```
### 001 Short title
- Why: <tied to goal.md and to a candidate id in research.md>
- Acceptance:
  1. <objectively checkable criterion>
  2. <objectively checkable criterion>
- Size: S | M | L
- Risk: low | med | high (one line why)
- Likely files: <paths or globs>
- Sensitivity: none | payments | access | security | data | api | infra | unknown
- Status: ready
```

  Blocks appear in value order, best first. `status.py` parses the
  `### <id> <title>` headings and the `- Status:`, `- Size:`, `- Risk:`,
  `- Likely files:` and `- Sensitivity:` fields - keep them on their own lines
  in exactly that spelling. Use `- Status: proposed` for anything you have not
  sized, and `- Status: ready` for the items you want the run to attempt.

- `board.json` - mirror every item:

```json
{
  "items": {
    "001": { "status": "ready", "owner": null, "branch": null, "round": 0, "verifyRounds": 0, "conflicts": 0, "sensitivity": "none" }
  },
  "roles": { ...unchanged, all idle... }
}
```

  `board.json` and `backlog.md` must agree on every item's status - the
  orchestrator's `verify.py` fails the run on any mismatch.

- `research.md` - edit in place: every one of the 18 candidates ends up with
  either `- Status: adopted -> <item-id>` or `- Status: rejected` plus
  `- Rejected reason: ...`. A candidate left as `queued` is an unfinished
  decision and is reported as a warning.

- Write a short report to
  `C:\Sythion\sythion\.orchestra\reports\scout-001.md` (same shape as
  `report-template.md`: What I did / Evidence / Acceptance criteria / Findings
  / **Not checked**). The Not checked section is required - state which
  candidates you did not fully verify and what you could not determine.

- Reply with: the ranked item list, which candidates you rejected and why, and
  what you did not check.

## Previous attempt (if any)

- None. This is attempt 1.
