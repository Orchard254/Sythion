# Report 000-research-6 — Researcher

## Goal / config / context checked
- Repo `C:\Sythion\sythion`, branch `master` at `15f70bb`, with uncommitted doc edits only; no source changes.
- Read `.orchestra/goal.md`, `research.md`, `backlog.md`, `log.md` tail (items 017–021 context), and required files:
  `backend/agents/coordination.py`, `backend/runtime/events.py`, `backend/tests/test_phase95_hardening.py`,
  `backend/tests/test_phase105_concurrency.py`, `backend/core/db_manager.py`.
- No internet used; all evidence local.

## Sources reached
- Local filesystem, git history, pytest subprocesses (CPython 3.14.7 / Windows), SQLite StaticPool in-memory and file DB spot checks.
- Network not used (brief constraint).

## Summary of checks
1. Admission concurrency (coordination.py `DelegationService.validate`): re-run
   `backend/tests/test_phase95_hardening.py::TestAdmissionAtomicity::test_two_callers_at_boundary_only_one_admits`
   in tight isolation. It is intermittent but reproducible: 3 failures in an 8-run loop; in a 27-run loop the failure was:
   `assert total <= 2` with `total == 3` (`max_specialists_per_task` was 2 and one existing delegation already admitted).
   Thread results in that failed run included one admitted row (`ok: True`) and one "delegation ... not found",
   while a fresh session reported `active_delegations == 3`.
2. DbEventLedger (events.py): reviewed the current atomic counter-table implementation and
   `TestEventLedgerConcurrency::test_no_duplicate_sequence_and_no_cross_contamination`.
   The latter passed 20 consecutive runs on the current tree; no additional event-loss evidence beyond
   the historical R-021 (adopted -> 008, now remediated by the UPSERT + counter table).
3. Event-ledger transaction/rollback contract: item 017's transaction-resident `sqlite_master` check
   and `BEGIN IMMEDIATE` path is present and behaves as documented in the passing stress test.
4. Release/trust-boundary surface: git history shows the last code-altering trust area was item 008
   (atomic ledger fix, cb6516f) and item 001 (CI, 3ec2d46); no new schema/migration or release-payload
   change since 15f70bb other than documentation.

## Candidate found

### R-030 Conditional fan-out admission can still exceed `max_specialists_per_task` under concurrent validation
- Source: local
- Source detail: `backend/agents/delegation.py` (`validate`, `_ADMIT_SQL`, `_admission_lock`, `BEGIN IMMEDIATE`),
  `backend/tests/conftest.py:397-418` (`test_db` StaticPool in-memory), `backend/tests/test_phase95_hardening.py:43-92`.
- Evidence: isolated pytest runs of `...TestAdmissionAtomicity::test_two_callers_at_boundary_only_one_admits`
  fail intermittently with `assert total <= 2` / `assert 3 <= 2`. The suite already uses a module-global
  `RLock` plus a conditional `UPDATE ... WHERE (SELECT COUNT(*) ...) < :limit` running under
  `BEGIN IMMEDIATE`, but two concurrent proposers can still both be admitted, yielding an active count
  of 3 on a fresh session when the limit is 2. This reproduces on the current tree despite the guard's
  stated cross-process guarantees. The exposure is a real control-boundary breach if any buyer of this
  admission path relies on `max_specialists_per_task` as the concurrency/budget ceiling.
- Files: `backend/agents/delegation.py`, `backend/tests/test_phase95_hardening.py`, `backend/tests/conftest.py`
- Severity: high
- Direction: determine whether the failure is specific to the in-memory `StaticPool` connection sharing
  in the test harness or also occurs with file-backed SQLite (production path), because the test DB
  fixture deliberately uses one shared connection. If production is affected, serialize admission with
  an explicit database-level writer lease / transaction held across the read+update, or move the
  conditional UPDATE into a single SQL statement that cannot observe the pre-commit row state.
  If it is test-harness-only, fix the fixture's thread-safety assumptions and add a file-DB
  reproduction before relaxing the test.
- Status: new (candidate for Scout, not adopted).

## Things not checked
- File-backed (non-StaticPool, per-session connection) repetition of the R-030 reproduction: designed
  but not completed in this pass, so the candidate's production vs. harness classification is open.
- Cross-process (two interpreter) admission race for R-030: not run.
- Event ledger multi-process durability and busy/locked retry exhaustion: not run beyond the in-process
  20-run stress check; prior atomic-UPSERT fix not re-audited at process level.
- Full release-payload/schema/process-boundary audit (verify_release_payload.py, staging, Rust shell): not performed.
- Full test suite and frontend suites: not re-run; baseline counts assumed unchanged at 1603 passed / 45 skipped.
- Docs' recently modified baselines (uncommitted doc edits): not re-validated.

## Recommendation of next step for Scout
- Prioritise R-030 for triage first; classify it as harness-only vs. production by reproducing it with
  a file-backed SQLite DB before any fix. If production, route to Implementer as a concurrency fix in
  `backend/agents/coordination.py`'s admission boundary. If harness-only, convert the flaky test to a
  file-backed fixture and preserve the policy invariant as documentation. Do not promote any event-ledger
  work unless a process-level reproduction appears; the atomic sequence allocator currently passes
  the concurrency stress suite.