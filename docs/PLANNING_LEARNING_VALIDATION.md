# Planning Learning Validation (Phase 12.5)

**Date:** 2026-08-26  
**Mode:** `planning_learning_mode = LEARNING_ONLY` (default, never controls production planning)

Validates Phase 12 foundations before Phase 13 controlled live use. Real workload is observed via `LEARNING_ONLY` shadow; synthetic evidence is explicitly tagged and cannot promote.

---

## 1. Real vs Synthetic Evidence (§4, §15)

`planning_outcomes.provenance` CHECK (`REAL_EXECUTION`, `SYNTHETIC_TEST`, `IMPORTED`, `SIMULATED`) is set at creation by `PlanningOutcomeStore.record(..., provenance=...)` (`backend/learning/planning_store.py:40`, `backend/learning/capture.py:360`). Ingestion via `POST /planning/outcomes` validates provenance (`backend/api/learning_routes.py:223`). Fresh DBs create column via `CREATE TABLE` (`backend/core/db_manager.py:468`); legacy DBs gain `ALTER TABLE ... ADD COLUMN provenance` with backfill to `REAL_EXECUTION` and index `ix_planning_outcomes_provenance` (`backend/core/db_manager.py:506`).

Selector can filter: `statistics(..., include_synthetic=False)` excludes synthetic (`backend/learning/planning_store.py:230`). Dashboard splits `real_sample_count` / `synthetic_sample_count` (`backend/learning/planning_store.py:380`).

**Leakage prevention:** tests use isolated `sqlite:///:memory:` and explicit `provenance=SYNTHETIC_TEST` (`backend/tests/test_phase125_planning_validation.py:20`). Real workload remains distinguishable.

---

## 2. Coverage Dashboard (§3)

`PlanningOutcomeStore.coverage_report()` (`backend/learning/planning_store.py:340`) returns deterministic report:

```
total_outcomes, real_sample_count, synthetic_sample_count,
task_classes[{task_class, sample_count, real_sample_count, synthetic_sample_count,
              verified_success_rate, replan_rate, retry_rate, mean_latency, evidence_strength}],
strategies[{planning_strategy, sample_count, real_sample_count, synthetic_sample_count,
            verified_success_rate, mean_cost, evidence_strength, shadow_divergence_rate,
            retry_success_rate, replan_success_rate}],
shadow_divergence_rate, shadow_total, shadow_divergent
```

Exposed at `GET /planning/coverage`. No secrets or CoT exposed. Required fields from spec (`task_class, strategy, sample_count, verified_success_rate, replan_rate, retry_rate, mean_cost, mean_latency, shadow_divergence_rate, evidence_strength, real_sample_count, synthetic_sample_count`) are present.

Current real workload after phase: **synthetic-only in CI** (isolated). Real traffic in production will populate `real_sample_count` incrementally while still `LEARNING_ONLY`; dashboards already distinguish.

---

## 3. Shadow Evaluation Quality (§6, §15)

`PlanningOutcomeStore.shadow_quality_report()` (`backend/learning/planning_store.py:400`) measures:

```
total, agreement, divergence, expected_advantage, details[
  goal_id, task_class, actual_policy, shadow_policy, would_diverge,
  actual_outcome, has_expected_advantage, actual_real_success_rate, shadow_real_success_rate
]
```

`has_expected_advantage` is true **only** when shadow strategy has `SUPPORTED`/`ESTABLISHED` real evidence (≥3 real) and strictly higher `real_success_rate` (+0.05) — otherwise `null`. No claim that shadow would have succeeded without independent historical support (`backend/learning/planning_shadow.py:9`).

Shadow rows stored in `planning_shadow_decisions` with both `actual_policy` and `shadow_policy` (`backend/models/database.py:733`). Shadow never mutates actual execution.

Exposed at `GET /planning/shadow/quality`.

---

## 4. Promotion Thresholds — Synthetic Cannot Promote (§5, §17-18)

From `backend/core/config.py:172`:

```
planning_min_real_samples = 5
planning_min_real_verified_successes = 3
```

Gate in `PlanningPolicyStore.promote()` (`backend/learning/planning_store.py:440`):

```
SUPPORTED and beyond require real_count >= 5 and real_verified >= 3
(enforced by counting planning_outcomes where provenance=REAL_EXECUTION and not expired)
+ shadow_validated flag for SHADOW_VALIDATED
+ owner_approved for ACTIVE (safe_auto_promotion default OFF)
```

Thus `provenance=SYNTHETIC_TEST` rows are counted in `sample_count` for statistics but **excluded** from promotion eligibility. Tested in `test_synthetic_only_blocked_promotion`.

Promotion still requires `evidence_summary.sample_count >=5` (small-sample) plus real counts.

---

## 5. Metric Definitions (§8-9)

### Dependency complexity (deterministic, from persisted `plan_steps`)

Helper `_compute_dependency_metrics_for_plan(plan_id, db)` (`backend/learning/planning_store.py:55`):

```
step_count = count(plan_steps where plan_id=...)
dependency_edge_count = sum(len(depends_on))
maximum_dependency_depth = longest path length via topological sort (sources depth 1)
parallel_ready_width = max nodes sharing same depth
branch_count = nodes with out-degree >1
critical_path_length = maximum_dependency_depth
```

Aggregated in `quality_metrics()` as `dependency_depth` / `critical_path` averages (`backend/learning/planning_store.py:315`).

### Plan cost (from persisted execution data, unknown stays unknown)

`PlanningOutcomeStore.statistics` and `quality_metrics` parse `total_cost_json` keys deterministically:

```
if total exists → use it
else if model_calls/runtime_actions/specialist_calls/tokens exist →
  cost = model_calls*0.2 + runtime_actions*0.05 + specialist_calls*0.3 + tokens*0.0001
else → unknown (None)
```

Also aggregates `model_calls`, `runtime_actions`, `specialist_calls` averages, plus `total_latency_ms`, `retries`, `replans` directly from stored columns. Unknown values remain `None` (never fabricated zero).

### Expanded PlanQualityMetrics

`PlanQualityMetrics` (`backend/learning/planning_store.py:34`) now includes:

```
verified_goal_success, verified_step_success, average_steps, average_plan_length,
dependency_depth, critical_path, replan_count, retry_count, replan_rate, retry_rate,
specialist_count, model_calls, runtime_actions, average_total_cost, average_total_latency,
latency, cost, user_correction_rate, step_success_rate
```

Exposed at `GET /planning/metrics`.

---

## 6. Retry vs Replan Calibration (§10)

`retry_replan_calibration(task_class)` (`backend/learning/planning_store.py:460`) over **real execution only**:

```
overall_retry / overall_replan: {sample_count, verified_recovery_rate, mean_cost, mean_latency, mean_attempts, user_correction_rate}
per_trigger[VERIFICATION_FAILURE...RESOURCE_UNAVAILABLE]: {retry: metrics, replan: metrics}
```

No automatic policy mutation — returns evidence for Phase 13.

Exposed at `GET /planning/calibration/retry-replan`.

---

## 7. Replan Trigger Calibration (§11)

Same structure via `replan_trigger_calibration()` alias; per-trigger retry vs replan recovery, cost, latency. Output is evidence, not mutation.

---

## 8. Task-Class Taxonomy (§12)

Structured dimensions remain (`task_class, goal_type, estimated_steps, dependency_complexity, risk_level, capability_count, delegation_available, previous_failure_class`) (`backend/learning/planning_taxonomy.py:104`). 12.5 decision: **keep breadth** — `general`/`goal`/`command` etc. are not split on single-sample variance; distinct classes already produce materially different planning behavior (retry vs replan weights differ by trigger/task_class). Documented here; no code split applied. Revisit only after real-sample SUPPORTED evidence per class.

---

## 9. Selector Robustness (§13)

Helpers in `backend/learning/planning_calibration.py:30`:

- `selector_stability_check`: same evidence, shuffled candidate order → same selection (deterministic `_rank_key` by `(-score, -sample_count, strategy)` in `backend/learning/planning_selector.py:184`).
- `selector_perturbation_stability`: adding one `SYNTHETIC_TEST` row does not cause wild oscillation when evidence is already SUPPORTED.

Exposed at `GET /planning/calibration/selector-stability`. Tested in `test_selector_stable_under_*`.

Deterministic `PlanningPolicySelector` (hash-bucket seeded by `task_class:date`, daily bucket `backend/learning/planning_selector.py:245`) plus small-sample fallback ensures stability.

---

## 10. Real-Workload Exploration (§14)

Exploration remains controlled (`backend/learning/planning_selector.py:225`):

```
LOW risk + cost <= planning_max_exploration_cost + insufficient evidence + deterministic hash bucket → eligible
MEDIUM → conservative (no auto-promotion)
HIGH/CRITICAL → blocked via planning_high_risk_threshold
Budget-exhausted → blocked
```

Every decision reason includes `mode=EXPLORE` and `exploration: true` in `evidence_summary`; alternatives retain scores. For `LEARNING_ONLY` real traffic, exploration is recorded only in shadow/diagnostics, never in production policy store unless explicitly observed.

---

## 11. Migration / Persistence (§16)

Schema verified for:

1. fresh DB — `Base.metadata.create_all` creates `planning_outcomes(provenance)` etc.
2. pre-Phase-12 DB — `ALTER TABLE planning_outcomes ADD COLUMN provenance DEFAULT 'REAL_EXECUTION'` + backfill + index
3. current DB copy — repeated startup idempotent (`CREATE INDEX IF NOT EXISTS`)
4. backup/restore — SQLite file copy preserves provenance column and values
5. repeated startup — idempotent (`CREATE TABLE IF NOT EXISTS` + `if "provenance" not in cols`)

`evidence_fingerprint` unique partial index survives all. Tested via `test_phase125_persistence_survives`-style plus manual `DatabaseManager` construction against temp files.

---

## 12. Real-Workload Soak / Gated Observation (§17)

`LEARNING_ONLY` remains default. Soak mode:

- observes owner tasks → `record_planning_outcome` with `provenance=REAL_EXECUTION`
- generates shadow via `evaluate_shadow` (stored in `planning_shadow_decisions`)
- never alters actual policy (actual path uses `DEFAULT_RETRY_REPLAN_POLICY`)
- produces aggregate evidence via coverage/shadow/calibration endpoints

Safe to leave enabled indefinitely (no autonomous experiments).

Duration at validation: synthetic soak only; real traffic soak awaits production enablement.

---

## 13. Promotion Readiness Report (§18)

`PlanningOutcomeStore.promotion_readiness(policy)` (`backend/learning/planning_store.py:520`) returns:

```
policy_id, policy_key, status, readiness_level, real_samples, real_verified_successes,
min_real_samples_required, min_real_verified_required, shadow_validated,
evidence_strength, eligible_next, promotion_eligibility
```

Levels (deterministic, based on real counts + status):

```
UNRATED, LOW_SAMPLE, REAL_SUPPORTED, SHADOW_VALIDATED, READY_FOR_OWNER_REVIEW, ACTIVE, ROLLED_BACK
```

Exposed at `GET /planning/policies/{id}/readiness`. No auto-promotion.

---

## 14. Phase 13 Boundary (§19)

Phase 12.5 establishes trustworthy evidence; Phase 13 expected transition remains:

```
REAL WORKLOAD → PLANNING EVIDENCE → SHADOW POLICY → VALIDATION → OWNER-APPROVED PROMOTION → CONTROLLED LIVE LEARNING
```

Phase 13 must not begin by immediately turning learning on — promotion remains owner-gated.

---

## 15. Phase 12.75 Live Calibration Updates

In Phase 12.75:
- Added `GET /planning/live-readiness` endpoint and `PlanningOutcomeStore.live_readiness()` returning comprehensive live readiness across task classes, strategies, shadow validation, and evidence expiration.
- Hardened shadow advantage truthfulness: where shadow diverges from actual execution, `expected_advantage` is reported as `"UNKNOWN"` unless independent historical `REAL_EXECUTION` evidence provides `SUPPORTED`/`ESTABLISHED` proof of higher verified success rate.
- Fixed `specialist_count` telemetry by computing metrics directly from authoritative `Delegation` records (`created`, `executed`, `completed`, `failed`), preventing cost inference and retry double-counting.
- Added data quality validation in `PlanningOutcomeStore.record()` that rejects malformed records (missing goal/plan refs, impossible step/retry counts, negative costs/latency, unknown provenance).
- Added multi-dimensional retry vs replan real calibration breakdowns (`task_class`, `replan_trigger`, `planning_strategy`).

---

## Test Evidence

```
python -m pytest backend/tests/test_phase125_planning_validation.py -q  → 23 passed
python -m pytest backend/tests/test_phase12_planning_learning.py -q  → 42 passed
python -m pytest backend/tests/test_phase1275_live_learning_calibration.py -q  → 13 passed
python -m pytest backend/tests -q -m "not gui and not process_kill"  → 1389 passed, 37 skipped
```

Coverage includes provenance (5 kinds), synthetic-only blocked, insufficient real blocked, sufficient real + shadow + owner path, dependency depth/critical path, authoritative delegation telemetry, data quality validation, live readiness dashboard, shadow agreement/divergence/advantage truthfulness, offline calibration non-execution, retry/replan real calibration, selector determinism/ordering/perturbation, leakage isolation, and readiness levels.
