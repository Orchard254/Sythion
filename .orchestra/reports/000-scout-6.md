# Report: scout - 000 - scout-6 (adopt R-030 into the backlog)

## What I did

- Read `.orchestra/briefs/000-scout-6.md`, `goal.md`, `research.md`,
  `backlog.md`, `board.json`, `follow-ups.md`, and the R-030 evidence in
  `research.md` (plus `backend/agents/delegation.py`,
  `backend/tests/test_phase95_hardening.py`,
  `backend/tests/test_admission_concurrency_process.py`,
  `backend/tests/conftest.py` for the admission path).
- Adopted R-030 as new item `022` (the next unused id after 021), added its
  block to `backlog.md`, appended a matching `022` entry to `board.json`
  (status `proposed`), and changed only the `- Status:` line of R-030 in
  `research.md` to `adopted -> 022`.
- Wrote this report. No source, test, branch or commit was touched.

## Decisions

1. **Adopted R-030 as item 022.** The fan-out cap
   (`max_specialists_per_task`) is the only bound on concurrent specialist
   admission per task/goal. An intermittent bypass that reaches
   `active_delegations == 3` at a limit of 2 is a runtime-boundary defect in
   either production or the harness, and the candidate is the cheapest honest
   way to close the unknown rather than leave it deferred. Rank: 1 (top
   active item), above the already-merged 017-021.
2. **Size M, risk high.** The fix cannot be sized honestly until the
   production-vs-harness classification in criterion 1 is done; M covers both
   the reproduction matrix and the smallest production fix (serialize the
   count+update at the writer boundary) or the test-only fix (correct the
   `StaticPool` shared-connection assumption) if it is harness-only.
3. **Did not reject.** Rejection was only warranted if the candidate were
   already covered by an existing follow-up or off-limits; neither applies -
   the existing admission follow-ups (`follow-ups.md`, admission process item)
   are about reproducing/reporting the failure, while item 022 is the item that
   performs that reproduction and closes whichever branch results. No
   manufacturing: no new acceptance shape was invented beyond what R-030 and
   the observed test failures already describe.
4. Rejected branch guard: criterion 3 (production fix) and criterion 4
   (harness-only fix) are mutually exclusive and both are bounded; the item
   may not apply both. Criterion 1 must classify before either.

## Item map

| Id | Source | Sensitivity / Domain | Size / Risk | What it does |
|---|---|---|---|---|
| `022` | R-030 | sensitive / data | M / high | Classify the intermittent fan-out-admission bypass (`assert total <= 2` with `total == 3`) against file-backed SQLite, then either serialize admission at the writer boundary in production or correct the harness's shared-connection assumption, without touching schema or public API contracts |

## Not changed (deliberately)

- `research.md` candidate body is untouched; only the `- Status:` line of
  R-030 changed.
- No source, test, workflow, docs beyond `.orchestra/` files were modified.
- Existing backlog items 017-021 are untouched (their blocks, statuses and
  merge references stay as they are).
- No new code-tractable follow-up was promoted; the existing admission items in
  `follow-ups.md` remain referenced, not re-listed.

## Not checked

- I did not run R-030 or its tests; all evidence comes from the candidate
  record, `follow-ups.md` and static reading of
  `backend/agents/delegation.py` and `backend/tests/test_phase95_hardening.py`
  / `test_admission_concurrency_process.py`. No database was opened or mutated.
- I did not reproduce the bypass against a file-backed SQLite DB; that
  classification is criterion 1 of the new item, and is left to its implementer.
- I did not verify `status.py`/`verify.py` against the updated files; the field
  layout matches the existing `### <id> <title>` plus `- Status:` /
  `- Size:` / `- Risk:` / `- Likely files:` / `- Sensitivity:` / `- Domain:`
  convention used by items 017-021.
- I did not check whether a `023` id collision exists downstream; 022 is the
  first id after the highest currently recorded (021).

## Files written

- `.orchestra/reports/000-scout-6.md` (this report)
- `.orchestra/backlog.md` (added item 022 block, updated top order)
- `.orchestra/board.json` (added item `022`, status `proposed`)
- `.orchestra/research.md` (R-030 `- Status:` line only)

## New backlog top order

1. `022` - fan-out admission invariant under concurrency (R-030) - high, M,
   sensitive/data
2. `017` through `021` - all merged (see item blocks for merge SHAs)