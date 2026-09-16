# Live Learning Calibration & Production Evidence Validation (Phase 12.75)

**Date:** 2026-08-26  
**Status:** VALIDATED  
**Mode:** `planning_learning_mode = LEARNING_ONLY` (default, authoritative production execution remains strictly deterministic)

---

## 1. Executive Summary

Phase 12.75 establishes the live learning calibration framework and production evidence validation mechanisms required before Phase 13.

Crucially, **learned planning policies remain non-authoritative in production (`LEARNING_ONLY`)**. No learned policy may alter production planning, Goal lifecycle, or Runtime behavior merely because synthetic suites pass.

---

## 2. Real Evidence Collection & Provenance Isolation

Evidence provenance is strictly tracked across five bounded classifications:
- `REAL_EXECUTION`: Captured exclusively after verified production task and step execution.
- `SYNTHETIC_TEST`: Automated unit, integration, and test fixtures.
- `LEGACY`: Historical or unprovenanced data.
- `SIMULATED`: Offline simulation runs.
- `IMPORTED`: External dataset ingests.

### Key Rules:
- Only `REAL_EXECUTION` outcomes contribute to `real_sample_count`, `real_verified_success_count`, and live promotion gates.
- Synthetic and legacy rows are completely segregated in coverage reports, shadow advantage calculations, and calibration metrics.
- Idempotency is enforced via deterministic SHA256 fingerprints across distinct terminal execution observations.

---

## 3. Real Shadow Validation & Advantage Truthfulness

Shadow decisions record what the learned `PlanningPolicySelector` would have recommended versus the deterministic execution choice:
- Persists: `actual_policy`, `shadow_policy`, `would_diverge`, `actual_outcome`.
- **Truthfulness Invariant:** If the shadow policy differs from the actual execution policy, the system only reports `expected_advantage = "TRUE"` when independent historical **`REAL_EXECUTION`** evidence demonstrates `SUPPORTED` or `ESTABLISHED` evidence strength with a strictly higher verified success rate (+0.05).
- If historical real evidence is absent or insufficient (<3 real samples or `UNRATED`/`LOW_SAMPLE`), `expected_advantage` is truthfully reported as **`"UNKNOWN"`** (never fabricated or assumed `TRUE`).
- Synthetic evidence never counts toward shadow advantage or shadow validation.

---

## 4. Authoritative Specialist Execution Telemetry

Specialist metrics are derived directly from authoritative `Delegation` database records, eliminating the prior gap where specialist counts were inferred from JSON cost metadata:
- **`specialists_created`**: Count of distinct delegation entities proposed/created for the Goal/Plan.
- **`specialists_executed`**: Count of distinct delegations that entered runtime execution (`ASSIGNED`, `RUNNING`, `RESULT_READY`, `VERIFIED`, `FAILED`). Proposed-but-unexecuted delegations are excluded.
- **`specialists_completed`**: Count of distinct delegations reaching `VERIFIED` / successful completion.
- **`specialists_failed`**: Count of distinct delegations terminating in `FAILED`.
- **De-duplication**: Retries of the same logical delegation request (matching fingerprint or attempt > 1) are never double-counted as new specialists.
- **`PlanQualityMetrics.specialist_count`**: Authoritative average executed specialists per outcome/plan.

---

## 5. Real Data Quality & Malformed Record Rejection

Durable validation rejects or quarantines malformed learning outcome records:
- Missing `goal_id` / `plan_id` reference on goal-level outcomes.
- Missing evidence references.
- Impossible plan step counts (`initial_plan_steps` or `final_plan_steps` < 0 or > 1000).
- Negative latency (`total_latency_ms < 0`).
- Negative cost values (in scalar or dictionary fields).
- Impossible retry/replan counts (< 0 or > 1000).
- Impossible specialist counts (< 0).
- Unknown provenance types.

No silent mutations or repairs are performed on invalid records.

---

## 6. Real Retry vs Replan Calibration

Using strictly `REAL_EXECUTION` outcomes, calibration reports:
- `verified_recovery_rate`
- `mean_cost`
- `mean_latency`
- `mean_attempts`
- `user_correction_rate`

Broken down by:
1. `task_class`
2. `replan_trigger`
3. `planning_strategy`

---

## 7. Real Evidence Expiration & Retention

The configured retention schedule is enforced:
- **90-day TTL**: Records past TTL expire and are excluded from active statistics and promotion decisions.
- **30-day Recent Window**: Tracks recent performance drift.
- **45-day Half-Life**: Used for time-decayed evidence weighting.
- The live readiness dashboard explicitly reports `active_real_evidence`, `expired_real_evidence`, and `decayed_real_evidence`.

---

## 8. Production Live Readiness Dashboard (`GET /planning/live-readiness`)

The live readiness endpoint returns the complete determinism state:
- `real_sample_count`: Non-expired real execution samples.
- `real_verified_successes`: Real samples with verified success.
- `eligible_real_samples`: Valid, non-expired real outcomes.
- `shadow_sample_count`: Total shadow evaluations.
- `shadow_divergence_count`: Count of divergent shadow choices.
- `shadow_validated`: Boolean indicator if real shadow validation criteria are met.
- `promotion_eligible`: Boolean indicator if real sample thresholds are satisfied.
- `policy_readiness`:
  - `NOT_READY`: 0 real samples or uninitialized.
  - `LOW_REAL_EVIDENCE`: 1-4 real samples or <3 verified successes.
  - `REAL_SUPPORTED`: ≥5 real samples and ≥3 verified successes, pending shadow validation.
  - `SHADOW_VALIDATED`: Real evidence thresholds and real shadow validation satisfied.
  - `READY_FOR_OWNER_REVIEW`: Ready for explicit owner promotion (never auto-promoted).
- `task_classes[]`: Per-class real sample counts and readiness levels.
- `strategies[]`: Per-strategy real sample counts, verified successes, and success rates.
- `expiration`: Active, expired, and decayed real counts.

---

## 9. Manual Promotion Lifecycle

Even when all real evidence and shadow validation thresholds are satisfied:
- Automatic promotion in production is disabled (`planning_safe_auto_promotion = False`).
- Promoted status remains `READY_FOR_OWNER_REVIEW`.
- Only explicit owner approval through the authenticated administrative control path can promote a policy to `ACTIVE`.
- `ROLLED_BACK` policies preserve full historical versions and rollback reasons.

---

## 10. Phase 13 Readiness Gate Status (Phase 12.8 Production Verification)

| Criterion | Requirement | Current Production State | Status |
|:---|:---|:---|:---|
| Real Sample Count | $\ge 5$ | 3 real samples (`REAL_EXECUTION`) | **LOW_REAL_EVIDENCE (3/5)** |
| Real Verified Successes | $\ge 3$ | 2 verified successes (`SUCCESS_VERIFIED`) | **LOW_REAL_EVIDENCE (2/3)** |
| Shadow Validation | Real workload evaluation | 0 shadow decisions (truthfulness intact) | **NOT_READY (0 shadow decisions)** |
| Specialist Telemetry | Authoritative Delegation DB | Integrated & Verified against `Delegation` table | **READY** |
| Data Quality Gate | Validation & rejection | Integrated & Verified (no silent mutations) | **READY** |
| Legacy Migration | Conservative sanitization | Resolved: 444 legacy objectives migrated safely | **READY** |
| Test Suites | Full backend + acceptance | All suites green, 0 failed | **PASS** |
| Overall Phase 13 Readiness | All gates green | `phase13_ready = False` (real evidence gate open) | **NO (PHASE 13 BLOCKED)** |
