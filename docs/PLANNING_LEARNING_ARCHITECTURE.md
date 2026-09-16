# Planning Learning Architecture (Phases 12 / 12.5)

**Status:** Active. Bounded empirical planning-policy learning over verified outcomes. The system learns **which planning/replanning strategy appears better** for a task class; it must **earn the right** to use that strategy in production (§17).

> Learned planning policy **≠** Runtime authority. A bad learned policy rolls back to the previous safe deterministic default (§18).

---

## Evidence Flow (§4-5, target intelligence)

```text
Planning
   ↓
Execution Outcome
   ↓
Learning Evidence (PlanningOutcome + StrategyOutcome preserved)
   ↓
Planning Policy Evaluation (deterministic metrics + small-sample protection)
   ↓
Shadow Recommendation (LEARNING_ONLY — observed, not controlling)
   ↓
Promotion / Rollback (conservative gates, owner approval)
   ↓
Future Planning
```

Raw task/plan evidence is **preserved**, never replaced by aggregates.

---

## Planning Strategy Taxonomy (§3)

Small controlled vocabulary — only these values are ever persisted or learned. LLM-generated names are rejected.

```
DIRECT_PLAN              — single-step or trivial plan
MINIMAL_PLAN             — ≤2 steps
DECOMPOSE_PLAN           — ≥5 steps, decomposed
DEPENDENCY_AWARE_PLAN    — explicit depends_on graph, parallelizable
DELEGATED_PLAN           — specialist delegation involved
REPLAN_AFTER_FAILURE     — successor plan after failure
REPLAN_BEFORE_RETRY      — replan without exhausting retries
```

Single source of truth: `backend/learning/planning_taxonomy.py::PlanningStrategy`.

## PlanningOutcome Model (§4, §12.5 provenance, §12.5 audit)

Durable `planning_outcomes` table (system learning, not user memory):

```
goal_id, plan_id, planning_strategy, task_class, goal_type,
initial_plan_steps, final_plan_steps, retries, replans,
verified_success, total_cost_json, total_latency_ms,
user_feedback, evidence_refs_json, evidence_fingerprint (unique),
original_plan_id, new_plan_id, failure_reason, replan_trigger,
outcome (9-valued), retry_vs_replan (RETRY|REPLAN|null),
dependency_complexity, risk_level, provenance (REAL_EXECUTION|SYNTHETIC_TEST|IMPORTED|SIMULATED|LEGACY),
created_at, expires_at
```

- `outcome` uses the same 9-valued taxonomy; `verified_success` is the verified completion signal.
- `retry_vs_replan` marks retry vs replan path (§6).
- `evidence_refs_json` traces to real `goal:<id>` / `plan:<id>` / `task:<id>` evidence.
- `provenance` distinguishes real owner execution from synthetic/imported/simulated/legacy evidence (§4 12.5 audit); `LEGACY` = historical row where provenance cannot be proven (migrated rows, plan-creation structural events). Selector can exclude non-real via `include_synthetic=False` and promotion gates count only `REAL_EXECUTION` (§5 audit). `REAL_EXECUTION` is assigned **only after** `execution attempted → runtime verification → authoritative outcome` (not at `plan created`); `Planner.create_plan` replan records are `LEGACY` (§12.5 audit defect 2).

Validated metrics (`PlanQualityMetrics`, §9 12.5):

```
verified_goal_success, verified_step_success, average_steps, average_plan_length,
dependency_depth, critical_path, replan_count, retry_count, replan_rate, retry_rate,
specialist_count, model_calls, runtime_actions, average_total_cost, average_total_latency,
latency, cost, user_correction_rate
```

Dependency metrics are derived deterministically from persisted `plan_steps` dependency graph (`step_count, dependency_edge_count, maximum_dependency_depth, parallel_ready_width, branch_count, critical_path_length` via `PlanningOutcomeStore._compute_dependency_metrics_for_plan`). Cost derives from `total_cost_json` actual fields (`total`, `model_calls`, `runtime_actions`, `specialist_calls`, `tokens`); unknown stays `None`. A shorter plan that fails more is not better — metrics are joint.

## Replanning Evidence (§5, §12.5 audit)

Every replan produces a structured record:

```
original_plan, failure_reason, retry_attempts, replan_trigger,
new_plan, new_plan_outcome, total_cost, total_latency, final_verification
```

Captured via `backend/learning/capture.py::record_replan_planning_outcome`. When called from `Planner.create_plan` (plan superseded), the record is `provenance=LEGACY` — a structural event, not execution evidence (§12.5 audit defect 2). `REAL_EXECUTION` replan evidence is only recorded after verified execution of the new plan. All replan records are `SUCCESS_PARTIAL` — **not** automatic success; final success depends on verified goal outcome (§5). Fields `original_plan_id`/`new_plan_id` preserve provenance.

## Retry-vs-Replan Learning (§6)

For comparable failure classes, outcomes carry `retry_vs_replan = RETRY|REPLAN`. Comparison uses:

```
verified success, cost, latency, attempts, user correction, downstream failures
```

The selector compares `retry_success_rate` vs `replan_success_rate` per `replan_trigger` and `task_class`. **One success does not establish a policy** — small-sample protection applies.

## Replan Trigger Learning (§7)

Structured trigger taxonomy (never inferred from raw error strings alone — machine-readable failure category is preferred):

```
VERIFICATION_FAILURE, EXECUTION_FAILURE, TIMEOUT,
DEPENDENCY_BLOCK, CHANGED_CONTEXT, USER_CORRECTION, RESOURCE_UNAVAILABLE
```

Derived from `FailureCategory` + `failure_code_for` (kernel `task.failed` ledger code). The system learns whether each failure class benefits from `retry | replan | ask_user | stop`. Fallback is deterministic `DEFAULT_RETRY_REPLAN_POLICY`:

```
verification/execution/timeout → retry once
dependency/changed_context      → replan
user_correction                 → ask_user
resource_unavailable            → stop
```

## Planning Policy Selector (§8)

`backend/learning/planning_selector.py::PlanningPolicySelector`

```python
select(task_context, current_plan, failure_context, historical_evidence)
 → {selected_policy, confidence, evidence_strength, alternatives, reason, mode, score}
```

- **Deterministic** scoring (success 0.40 / verification 0.20 / latency 0.15 / cost 0.10 / failure 0.15) over `PlanningOutcome` statistics.
- Ordering: `hard constraints → capability availability → Goal/Plan validity → budget/risk → historical evidence → safe deterministic fallback`.
- Learning **cannot** override user approval, Runtime policy, Goal terminal state, or verification.
- Small-sample protection reuses `UNRATED / LOW_SAMPLE / SUPPORTED / ESTABLISHED` (0 / 1-4 / 5-19 / 20+). Insufficient evidence → deterministic fallback.
- Exploration bounded: only `LOW` risk, cost ≤ `planning_max_exploration_cost`, insufficient evidence, deterministic hash bucket, safe fallback remains.

LLM may propose plan candidates; the PolicySelector determines the **policy** under which planning/replanning occurs.

## Goal-Class Context (§13)

Evidence is scoped to structured dimensions, never raw prompt text:

```
task_class, goal_type, estimated_steps, dependency_complexity,
risk_level, capability_count, delegation_available, previous_failure_class
```

## Real-Workload Observation (§14)

`planning_learning_mode` defaults to `LEARNING_ONLY`:

```
LEARNING_ONLY  → collect planning outcomes from real owner tasks; learned policies
                 are observed/evaluated but do NOT control production planning.
SHADOW         → also evaluate shadow recommendations side-by-side.
ACTIVE         → promoted policies may control planning (after gates).
```

Used for the Phase 11.5→12 transition: real workload evidence accumulates without altering behavior.

## Shadow Policy (§15)

For each real task where a learned policy exists:

```
Current deterministic policy  →  actual execution
Learned planning policy       →  shadow recommendation (persisted, no effect)
```

Both `actual_policy` and `shadow_policy` are persisted in `planning_shadow_decisions` for later divergence analysis:

```
Would learned policy have chosen differently?
Would outcome likely improve based on historical evidence?
```

Shadow **never** alters the task.

## Counterfactual Evaluation (§16)

Limited hypotheses:

```
Actual: retry → failed
Shadow: replan
 → counterfactual_candidate = replan (hypothesis, no claimed outcome)
```

`planning_counterfactuals.claimed_outcome` is **always NULL**. The API rejects any `claimed_outcome` from the caller. Counterfactuals remain hypotheses until real evidence supports a conclusion.

## Policy Promotion (§17, §12.5 hardened, §12.5 audit)

Conservative gate (Phase 12.5: synthetic/imported/legacy/unknown alone cannot promote):

```
OBSERVED ─→ SUPPORTED ─→ SHADOW_VALIDATED ─→ OWNER_APPROVED / SAFE_AUTO_PROMOTION ─→ ACTIVE
```

- `OBSERVED→SUPPORTED`: needs `SUPPORTED` strength (≥5) **plus** `real_samples >= planning_min_real_samples (5)` and `real_verified >= planning_min_real_verified_successes (3)` counted **only** from `provenance=REAL_EXECUTION` outcomes; `LEGACY`/`SYNTHETIC_TEST`/`IMPORTED`/`SIMULATED` never count (§5 audit). `5+3` means **minimum evidence to become eligible for further review**, not proven superior (§12.5 audit defect 4).
- `SUPPORTED→SHADOW_VALIDATED`: needs `shadow_validated` marker.
- `→ACTIVE`: needs `owner_approved=True`. Safe auto-promotion only when `planning_safe_auto_promotion=True` and low-risk/low-cost eligibility; otherwise rejected. High-risk always requires owner approval. The model **cannot promote its own policy**.

Readiness levels are deterministically reported via `PlanningOutcomeStore.promotion_readiness()` and `GET /planning/policies/{id}/readiness`: `UNRATED | LOW_SAMPLE | REAL_SUPPORTED (=minimum eligible, not superior) | SHADOW_VALIDATED (=eligible+shadow) | READY_FOR_OWNER_REVIEW | ACTIVE | ROLLED_BACK` (see `PLANNING_LEARNING_VALIDATION.md`).

## Rollback (§18)

Every promoted policy stores:

```
policy_version, promoted_at, evidence_summary_json, previous_policy_json, rollback_reason
```

`POST /planning/policies/{id}/rollback` restores `ROLLED_BACK` and preserves `previous_policy_json`. Deterministic defaults are always available as rollback target.

## Exploration (§19)

Allowed only when:

```
risk is LOW, cost < planning_max_exploration_cost, evidence is insufficient, deterministic fallback remains safe.
```

Never for permission/security behavior, destructive Runtime policies, high-risk tasks, or user privacy boundaries.

## User Feedback (§20)

Planning-relevant feedback classified deterministically:

```
"The plan was too complicated." → OVER_PLANNED
"The second approach was better." → REPLAN_ERROR → contributes to planning evidence
"You should have asked me first." → CLARIFICATION_MISSED
```

Kinds: `PLANNING_QUALITY, STRATEGY_QUALITY, CLARIFICATION_MISSED, OVER_PLANNED, UNDER_PLANNED, REPLAN_ERROR`. Single correction remains evidence, not automatic policy change.

## Model Independence (§21)

`PlanningPolicy` is separate from `ModelSelector`. A different model/provider can implement the same planning policy.

## Memory Separation (§22)

```
USER MEMORY  (memory_entries)  ≠  SYSTEM LEARNING (planning_outcomes / strategy_outcomes)
```

## Security (§23)

Learned planning cannot: grant capabilities, change Runtime permissions, bypass verification, mark Goals completed, modify code, promote itself, override safety with low-sample evidence, let shadow alter execution, turn counterfactuals into facts, or prevent rollback. All 10 invariants are adversarially tested.

## Persistence (§25, §12.5 §16, §12.5 audit)

SQLite via `Base.metadata.create_all` + explicit `DatabaseManager._apply_migrations` (`CREATE TABLE IF NOT EXISTS planning_outcomes(..., provenance LEGACY)/planning_policies/...` + `ALTER TABLE ... ADD COLUMN provenance DEFAULT LEGACY` + backfill legacy rows to `LEGACY` (never `REAL`) + fixup of any incorrectly backfilled `REAL` from earlier 12.5 migration). Survives fresh / pre-Phase-12 / current copy / backup/restore / repeated startup, idempotent. Backup file copy preserves `provenance` values (`LEGACY`/`REAL` etc.). `evidence_fingerprint` unique partial index makes re-ingestion idempotent.

## Validation (12.5)

See `PLANNING_LEARNING_VALIDATION.md` for provenance, coverage dashboard, shadow quality, offline calibration (historical selector evaluation), dependency/cost metric derivations, retry/replan trigger calibrations, task-class taxonomy, selector stability, exploration, and promotion readiness. `GET /planning/coverage`, `/planning/shadow/quality`, `/planning/calibration/*`, `/planning/policies/{id}/readiness`, `/planning/dependency/{plan_id}` expose validated evidence without secrets.

## Introspection

```
GET  /api/v1/learning/planning/strategies[?include_synthetic=bool]
GET  /api/v1/learning/planning/strategies/{strategy}
POST /api/v1/learning/planning/outcomes  (provenance validated)
GET  /api/v1/learning/planning/metrics
GET  /api/v1/learning/planning/coverage               (12.5)
GET  /api/v1/learning/planning/shadow/quality         (12.5)
GET  /api/v1/learning/planning/calibration/retry-replan
GET  /api/v1/learning/planning/calibration/offline
GET  /api/v1/learning/planning/calibration/selector-stability
GET  /api/v1/learning/planning/policies/{id}/readiness (12.5)
GET  /api/v1/learning/planning/dependency/{plan_id}   (12.5)
POST /api/v1/learning/planning/shadow
GET  /api/v1/learning/planning/shadow
POST /api/v1/learning/planning/counterfactual
GET  /api/v1/learning/planning/counterfactuals
GET  /api/v1/learning/planning/policies
GET  /api/v1/learning/planning/policies/{id}
POST /api/v1/learning/planning/policies/{id}/promote   (owner_approved required)
POST /api/v1/learning/planning/policies/{id}/rollback
POST /api/v1/learning/planning/select   (deterministic selector evaluation)
```
