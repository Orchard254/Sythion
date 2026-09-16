# Learning Validation (Phase 11.5)

**Status:** Validated. Phase 11 learning substrate hardened before Phase 12 autonomous planning.

---

## Outcome Capture Matrix

| Path | Captured | evidence_refs | strategy_type | task_class | role | cost/latency/attempts |
|------|----------|---------------|---------------|------------|------|----------------------|
| SUCCESS (task completed verified) | yes `PostTaskPipeline.record_learning_outcome` | `task:<id>` | DIRECT_* | from intent | — | cost from budget snapshot, attempts 1 |
| FAILED | yes | `task:<id>` | DIRECT_* | from intent | — | cost/latency if present |
| FAILED_VERIFICATION | yes `verification=failed` | `task:<id>` | mapped | intent | — | — |
| TIMEOUT (ELAPSED_TIME_EXCEEDED) | yes `OUTCOME=TIMED_OUT` | `task:<id>` | mapped | intent | — | — |
| CANCELLED (task/goal/step) | yes `record_cancellation_outcome` | `task/goal/plan_step` | mapped | goal/command | — | attempts 1 |
| USER_CORRECTED | yes `feedback=INTERPRETATION_ERROR` → USER_CORRECTED | `task:<id>` | original | intent | — | — |
| USER_REJECTED | yes `USER_REJECTED` | `task:<id>` | — | — | — | — |
| RETRY attempt 1 FAILED | yes `record_retry_outcome` with `attempt=1` | `task,plan_step` | DIRECT_EXECUTION | goal | — | per-attempt |
| RETRY attempt 2 SUCCESS_VERIFIED | yes distinct fingerprint `attempt=2` | same | same | goal | — | distinct row |
| REPLAN Plan v1→v2 | yes `record_replan_outcome` `verification=replan:<reason>` | `goal,plan:v1,plan:v2` | revised | goal | — | total cost |
| GOAL completed/failed | yes `record_goal_outcome` separate from steps | `goal:<id>` | DIRECT_EXECUTION | goal | — | aggregated |
| DELEGATION single | yes via task capture `DELEGATE_*` | `task, delegation` | DELEGATE_* | informational/command | RESEARCH… | specialist cost |
| MULTI_SPECIALIST | yes `MULTI_SPECIALIST` | `task, delegation_plan` | MULTI_SPECIALIST | goal | — | sum |

No terminal execution bypasses capture unless ineligible (e.g., `SUCCESS_VERIFIED` without evidence → rejected, not silently skipped).

## Retry Evidence

`record_retry_outcome` preserves `attempt` and uses fingerprint `retry|task|step|attempt|outcome` so attempt 1 and 2 are distinct rows but same-attempt re-emit is idempotent. Selector distinguishes `eventually succeeded after retry` (two rows) from `immediate success` (one row). Test `test_retry_preserves_attempt_context`.

## Replan Evidence

`Planner.create_plan` now emits `record_replan_outcome` when `previous` exists. Stores original/new plan ids, reason as `verification=replan:<reason>`, outcome `SUCCESS_PARTIAL` so selector does not learn "replan is superior" from one sample. Test `test_replan_preserves_original_and_revised`.

## Goal-Level Evidence

`GoalManager.transition_goal` emits `record_goal_outcome` on terminal (`completed`→`SUCCESS_VERIFIED`, `failed`→`FAILED`, `abandoned`→`CANCELLED`) with `goal_id` and `task_id=None` distinct from step rows. Test `test_goal_vs_step_distinction`.

## Deduplication Audit

Fingerprint `evidence_fingerprint` UNIQUE partial index: duplicate terminal/retry/replan/user-correction events suppressed; genuinely distinct executions (different task_id/attempt) preserved. Tests `test_deduplication_preserves_distinct_runs`, `test_retry` idempotent.

## Provenance

Every `SUCCESS_VERIFIED` requires `evidence_refs` (eligibility check). Provenance via `store.provenance(id)` returns task_class, strategy, role, outcome, verification, cost, latency, evidence_refs, fingerprint, timestamps. Test `test_provenance_rejects_fabricated_evidence`.

## Soak Test Methodology

Gated `test_phase115_soak.py::test_learning_soak_mixed_workload` generates 200 random trials across 4 task_classes × 6 strategies × 5 outcomes with realistic cost/latency mixes (simple direct, informational, goal, delegation, specialist, retry, failure, correction, exploration). Inspects counts, verified success, failure, correction, mean cost/latency, evidence_strength, stale/decayed, selector stability. No claim of significance; exposes distribution.

## Selector Stability

`test_selector_stability_deterministic` — three consecutive selects same evidence/candidates → same strategy; unrelated evidence (different task_class) does not flip `command` selection.

## Learning Drift

`test_learning_drift` — 10 DIRECT successes → DIRECT wins; then 12 DELEGATE successes → DELEGATE wins, history (10) retained. Decay: TTL 90d, recent window 30d — document `strategy_evidence_ttl_days=90`, `strategy_recent_window_days=30`, half-life 45d. `test_decay_behavior_documented` validates.

## Small-Sample Rules

1 success → LOW_SAMPLE, 2 → LOW_SAMPLE, 5 → SUPPORTED, 20 → ESTABLISHED. Selector confidence `None` until SUPPORTED. Tests `test_small_sample_remains_unrated_or_low`, `test_small_sample_safety`.

## Exploration Validation

HIGH/CRITICAL never explores (regardless of evidence/rate). Cost limit enforced (`strategy_max_exploration_cost`). Tests `test_exploration_high_critical_never`, low-risk exploration via date-bucket hash deterministic.

## Expiration / Decay

Recent 30d, decayed >90d excluded from stats but raw retained. Mixed recent/old test ensures expired does not dominate. `test_expiration_mixed_recent_old`.

## Direct vs Delegated Calibration

Equal verification but cheaper DIRECT beats expensive delegated. Test `test_direct_vs_delegated_cost_calibration`.

## Specialist Role Isolation

RESEARCH evidence scoped to `role=RESEARCH`; CODING stats remain UNRATED. Test `test_role_evidence_isolated`.

## Acceptance Baseline

`comtypes` `Typelib different than module` — stale `comtypes.gen` cache after 1.4.16 upgrade. Fixed by proactive cache clear in `gui_tools._import_uia()` / `_session()` with retry and manual cache purge at startup. After fix, UIA imports succeed (`python -c "from comtypes.gen import UIAutomationClient"` ok). GUI fixture tests remain skippable on headless but no longer fail due to import; environment-only if Windows UIA unavailable at runtime is documented.

## Persistence / Migration

`DatabaseManager._apply_migrations` now explicitly ensures `strategy_outcomes` and `strategy_experiments` via `CREATE TABLE IF NOT EXISTS` + indexes, idempotent across restarts. Tests `test_migration_persistence.py`: fresh, legacy (drop+upgrade), restart idempotent, backup/restore. `Base.metadata.create_all` still runs first but migration is authoritative for upgrades.

## Safety Re-verified

All 10 invariants re-tested in `test_phase115_validation.py` §21: low-sample cannot override hard, stale cannot dominate, feedback cannot rewrite policy, exploration cannot bypass risk.

---

## Upgrade Path

Existing Sythion DBs: on next startup `DatabaseManager` creates missing tables without loss; `evidence_fingerprint` UNIQUE prevents duplicate ingestion after crash recovery.
