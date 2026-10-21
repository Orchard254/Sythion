# Report: scout - 000 - cycle 3 (fold R-025..R-027 + revisit tractable follow-ups)

## What I did

- Read `.orchestra/briefs/000-scout-3.md`, `goal.md`, `research.md`, the current
  `backlog.md`, `board.json`, the tail of `critiques.md`, `follow-ups.md`, and
  `reports/000-research-4.md` (R-025..R-027 plus the follow-up revisit).
- Read the code the candidates point at to keep the criteria checkable:
  `backend/runtime/events.py`, `backend/main.py`, `backend/tests/conftest.py`,
  `backend/tests/test_no_tautological_asserts.py`,
  `backend/tests/test_admission_concurrency_process.py`,
  `backend/agents/delegation.py`, `backend/agents/state.py`. I did not run the
  full backend suite and did not edit any production or test file.
- Overwrote `backlog.md` with a fresh ranked backlog: R-025, R-026, R-027 plus
  one promoted code-tractable follow-up (the 006 isolation-teeth/process-env item),
  then references to the deferred items instead of re-listing them. New item ids
  start at 017 and run through 020.
- Updated `board.json` by adding entries 017, 018, 019, 020 only (`status:
  proposed`, null owner/branch, zeroed counters, sensitivity/domain mirrored from
  the backlog). No existing entry was rewritten or removed.
- Wrote this report (the only other file touched).

## Decisions

1. **Folded all three new candidates** from `reports/000-research-4.md` into
   items: `017` = R-025, `018` = R-026, `019` = R-027. Each stays code-fixable
   and inside the permitted file sets; none touches the off-limits list (no schema,
   no auth, no secrets module behaviour, no public API contract).
2. **Promoted exactly one** of the ten "still code-tractable" follow-ups:
   the 006 verification gap (isolation not actually measured, and process-env
   coverage missing) becomes `020`. It is the highest-leverage one - it turns a
   green assertion into an assertion that can go red, which is the failure mode
   the whole run is built to prevent - and it touches only `backend/tests/`.
3. **Did not promote** the other nine: the tauri-build no-op is CI/release-owned
   and is in the human/external list on purpose; the vendored-README and stale-path
   items are release-payload work; the duplicated-config/registry-count/hand-maintained
   "53 tests" items are low-severity hygiene; the admission cross-process test is
   a pre-existing master failure whose root cause needs reproduction first (I ran
   it in isolation and it passed, so it is not reproducible from a single-run loop,
   which is exactly the risk the follow-up names); the BOM one-liner and soak
   docstring are one-line fixes not worth a delivery slot; and the tautology
   nesting is already R-027.
4. **Did not re-add human/external items as code items.** Board items 005, 008 and
   009 remain deferred references in the backlog; every human/external entry in
   `follow-ups.md` stays there.
5. **Kept the backlog under the 5-ready cap** (4 proposed items) rather than
   padding it with low-value single-line edits.

## Item map

| Id | Source | Sensitivity / Domain | Size / Risk | What it does |
|---|---|---|---|---|
| `017` | R-025 | sensitive / data | S / med | Stop `_counter_table_checked` from surviving a rolled-back `CREATE TABLE`; make creation transactional/re-checkable |
| `018` | R-026 | sensitive / security | S / med | Make the vault-write guard visible through `backend/main.py`'s global handler instead of being swallowed as a 500 |
| `019` | R-027 | none / none | S / low | Extend the tautology detector to nested `or True`, `and`, `not`, grouping and bare `assert True`, plus the tooth-check probes and a fixed docstring |
| `020` | item 006 follow-ups | none / none | M / low | Turn `.env` isolation into a measured, subprocess-based check and cover the process environment; document the `file_roots` swap |

## Not changed (deliberately)

- No code, test, branch, commit or payload change outside `.orchestra/`.
- `research.md` was not modified; only `backlog.md`, `board.json` and this report
  were written.
- No existing board entry was rewritten; 017-020 were appended only. Merged items
  (001, 002, 003, 004, 006, 007) and deferred items (005, 008, 009) keep their
  recorded statuses.

## Not checked

- I did not run the full backend suite, collect-only, or any test. The admission
  test's failure mode was investigated by reading; it was executed once in
  isolation and passed, which is why it is deferred rather than promoted.
- I did not reproduce R-025/R-026/R-027 by executing against the tree; they are
  static-reading candidates from `reports/000-research-4.md`, each carrying its own
  "add the missing failure-mode test" instruction in the acceptance criteria.
- I did not verify `status.py`/`verify.py` (not present in this repository).

## Files written

- `.orchestra/reports/000-scout-3.md` (this report)
- `.orchestra/backlog.md` (overwritten: 4 proposed items + deferred references)
- `.orchestra/board.json` (entries 017-020 appended, status `proposed`)

## New backlog top order

1. `017` - Event ledger counter-table flag must not stick after a rolled-back append (R-025), med, S, sensitive/data
2. `018` - Vault-write guard must not be swallowed by the FastAPI global handler (R-026), med, S, sensitive/security
3. `019` - Tautology detector must also catch nested and bare `or True` (R-027), low, S
4. `020` - `.env` isolation must be measured, not assumed, and cover the process environment (item 006 follow-ups), low, M