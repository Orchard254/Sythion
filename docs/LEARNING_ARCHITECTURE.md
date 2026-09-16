# Learning Architecture (Phases 11–12)

> Phase 12 extends controlled learning from execution strategy to **planning/replanning policy** (§8-22 of PLANNING_LEARNING_ARCHITECTURE.md). The Runtime Kernel remains authoritative; learned planning policy is advisory and must earn production control.

**Status:** Active. Controlled empirical learning over strategy evidence. The Runtime Kernel, Memory authority, Goal state, and verification rules remain authoritative; learning only changes strategy recommendations.

> The system learns which strategies work better; it does not rewrite itself arbitrarily.

---

## Evidence Flow

```text
Execution Evidence
    ↓
Learning Evidence
    ↓
Strategy Evaluation
    ↓
Future Recommendation
```

Learning never directly modifies runtime policy, permissions, verification, security, Goal state, event history, or Memory authority.

## Scope

Learning covers:

- Delegation: when delegation helped vs direct, which specialist role, single vs multi-specialist cost/latency/failure.
- Strategy: direct vs delegated, single vs multiple, role selection, retry/replan usefulness, model/task-class pair where evidence exists.
- Outcome: success, failure, verification, user correction, time, cost, resource usage.

General intelligence learning is explicitly deferred.

## StrategyOutcome

Durable `strategy_outcomes` table:

```text
id, task_class, strategy_type, role, model_id, outcome, verification,
user_feedback, cost_json, latency_ms, attempts, evidence_refs_json,
evidence_fingerprint (unique), task_id, goal_id, created_at, expires_at
```

Every record is traceable to actual execution evidence via `evidence_refs` (task ids, delegation ids, ledger events). No model/user claim is accepted without valid evidence references.

## Strategy Identity

Stable, bounded taxonomy (no LLM-invented vocabulary):

```
DIRECT_EXECUTION, DIRECT_ANSWER, DELEGATE_RESEARCH, DELEGATE_ANALYSIS,
DELEGATE_CODING, DELEGATE_REVIEW, DELEGATE_PLANNING_SUPPORT,
MULTI_SPECIALIST, ASK_CLARIFICATION
```

## Outcome Classification

Deterministic values:

```
SUCCESS_VERIFIED, SUCCESS_PARTIAL, FAILED, FAILED_VERIFICATION,
CANCELLED, TIMED_OUT, USER_CORRECTED, USER_REJECTED, UNAVAILABLE
```

The model never decides whether its strategy "worked" — outcome is derived from runtime state and verification.

## Eligibility

An outcome becomes learning-eligible only when:

- execution completed or failed terminally
- verification result exists where applicable (SUCCESS_VERIFIED requires evidence_refs)
- task not interrupted before meaningful work
- evidence_fingerprint valid

Incomplete/ambiguous executions are recorded but excluded from statistics.

## Statistics

Deterministic aggregates kept alongside raw rows (raw never replaced):

```
success_rate, verified_success_rate, mean_latency_ms, mean_cost,
failure_rate, verification_failure_rate, user_correction_rate, sample_count
```

Plus provenance: which executions, how many, outcomes, verification, cost, corrections, timestamps.

## Small-Sample Protection

```
UNRATED (0), LOW_SAMPLE (1-4), SUPPORTED (5-19), ESTABLISHED (20+)
```

Unknown remains unknown; no statistical significance claimed without calculation. One success does not establish a strategy.

## Expiration / Decay

- recent evidence: last 30 days (config `strategy_recent_window_days`)
- historical: up to TTL 90 days (`strategy_evidence_ttl_days`)
- decayed: beyond TTL excluded from selection but retained for audit
- Decay philosophy mirrors Memory but systems stay separate.

## Contradictions

Conflicting evidence is preserved, not overwritten. Selector exposes both sets in `evidence_summary`.

## User Feedback

Correction text is classified deterministically:

```
INTERPRETATION_ERROR, STRATEGY_ERROR, DELEGATION_ERROR,
SPECIALIST_ROLE_ERROR, OUTPUT_QUALITY_ERROR, PREFERENCE_FEEDBACK
```

A single correction is evidence, not an automatic behavior change. It influences the `user_correction_rate` gradually.

## Experiments

Placeholder `strategy_experiments` table for controlled A/B comparisons:

```
strategy_A, strategy_B, task_class, constraints, sample_limit, risk_limit, outcome
```

Only owner-approved or low-risk automatic experiments are permitted; broad autonomous experimentation is deferred.

## Separation

- User memory: facts, preferences, beliefs, episodes, procedures.
- Strategy learning evidence: system learning evidence (separate subsystem, separate table).
- Runtime: authoritative for policy, permissions, verification, Goal state, event history.

## Security Invariants

Learning cannot: bypass Runtime policy, grant capabilities, change permissions, alter Goals, mark results successful, override hard policy with low-sample evidence, let stale evidence dominate fresh, treat stored records as instructions, create specialists recursively, or bypass risk controls via exploration.

## Persistence

SQLite-backed via `Base.metadata.create_all` + explicit migration in `DatabaseManager._apply_migrations` (`CREATE TABLE IF NOT EXISTS strategy_outcomes/strategy_experiments` + indexes) for safe upgrade of pre-11 databases; survives restart, backup/restore, repeated startup idempotently. Duplicate `evidence_fingerprint` is idempotent (returns existing row). Validated in `tests/test_migration_persistence.py` (fresh/legacy/restart/backup).

## Validation (11.5)

See `LEARNING_VALIDATION.md` for capture matrix (retry/replan/goal/cancellation), drift, stability, small-sample, exploration, expiration, direct-vs-delegated, role isolation, soak workload, and acceptance baseline. Replan is recorded as `SUCCESS_PARTIAL` with `verification=replan:<reason>` to avoid premature "replan is superior" learning; retry preserves `attempts`; goal rows are distinct from step rows.

## Integration Points

- Cognitive DecisionSystem receives historical evidence as a soft tie-breaker.
- Delegation role selection consults evidence where SUPPORTED/ESTABLISHED.
- Model selection unchanged; StrategySelector may influence requirements, not provider model IDs.
- Task terminal hook (`PostTaskPipeline.record_learning_outcome`) auto-captures outcomes.

## Introspection

```
GET /api/v1/learning/strategies
GET /api/v1/learning/strategies/{strategy_type}?task_class=...
POST /api/v1/learning/outcomes  (controlled ingestion)
GET /api/v1/learning/planning/strategies
GET /api/v1/learning/planning/metrics
POST /api/v1/learning/planning/outcomes
POST /api/v1/learning/planning/select
GET /api/v1/learning/planning/policies
```

Never exposes secrets or CoT.

---

## Phase 12 — Planning-Policy Learning

See `PLANNING_LEARNING_ARCHITECTURE.md` for full design:

- `PlanningOutcome` (goal_id, plan_id, planning_strategy, retries, replans, verified_success, replan_trigger, retry_vs_replan, evidence_refs preserved)
- Replan evidence: original/new plan ids + failure reason + trigger as structured fields (§5)
- Retry-vs-replan split per failure class (§6) — never one success → policy
- `PlanningPolicySelector` deterministic (same small-sample protection, veto ordering, exploration bounds as Phase 11; §8-11, §19)
- Real-workload `LEARNING_ONLY` gate: observed/evaluated but not controlling (§14)
- Shadow decisions persisted alongside actual (§15), counterfactuals as hypotheses only (§16)
- Conservative promotion `OBSERVED→SUPPORTED→SHADOW_VALIDATED→OWNER_APPROVED→ACTIVE` (§17); every promotion reversible (§18)
- System learning remains separate from user memory (§22)

---

## Failure / Success Learning

A delegated failure with failed verification degrades the strategy's statistics gradually; a verified delegation with low cost improves them gradually. Neither blacklists nor establishes the strategy instantly. Planning variants degrade/improve similarly under the same small-sample and decay rules.
