# Multi-Agent Coordination Architecture (Phase 10)

## 1. Overview

Phase 10 evolves the bounded Specialist architecture into a coordinated multi-specialist system with controlled parallel execution.

```
                         MAIN AGENT
                             |
                     Cognitive Decision
                             |
                    Multi-Delegation Plan
                             |
                 +-----------+-----------+
                 v           v           v
            Specialist A Specialist B Specialist C
                 |           |           |
                 +-------+---+---+-------+
                         |        |
                 dependency / result graph
                         |
                         v
                  Result Aggregator
                         |
                         v
                    Main Agent
                         |
                         v
                  Final Goal/Task
```

The Runtime Kernel remains authoritative for every actual action.

## 2. DelegationPlan

Durable `delegation_plans` row (Phase 10):

```
DelegationPlan
    id
    parent_goal_id
    parent_task_id
    status           DRAFT -> VALIDATED -> ACTIVE -> COMPLETED/FAILED/CANCELLED
    version
    objective
    specialist_count
    completion_policy  ALL_REQUIRED | MAJORITY | OPTIONAL_RESULTS
    parallelism_policy_json
    budget_json
```

A plan owns a set of specialist delegations via `delegations.plan_id`. It supports multiple specialists, dependencies, result requirements, completion criteria, cancellation, failure policy. It does not create a second Goal system — Goal/PlanStep remain authoritative for user-visible work.

Atomic creation (`DelegationPlanService.create_plan`): validates role validity, capability availability, fan-out limits, dependencies, cycles, budgets, model requirements BEFORE persistence. Either the whole valid delegation graph is persisted or nothing is persisted except an explicitly auditable rejected proposal (FAILED plan row). No partially created specialist graph. Uses `_plan_lock` (in-process) + SQLite writer serialization.

Fan-out limits enforced at creation (from `core/config.py`):

```
max_specialists_per_plan               = 5
max_active_specialists_per_parent      = 5
max_total_specialist_model_calls       = 50
max_total_specialist_steps             = 25
max_total_specialist_time_seconds      = 1800
```

All values configurable via `SYTHION_*` env.

## 3. Delegation Graph

`delegations.depends_on_json` stores dependency delegation ids. `DelegationGraph` validates:

- missing-reference detection
- cycle detection (DFS)
- deterministic readiness: dependency must be VERIFIED or INTEGRATED to satisfy result dependency. Only VERIFIED results may unlock dependents. Unverified results cannot unlock.

Example:

```
Specialist A ------+
                    v
                Specialist C
                    ^
                    |
Specialist B ------+
```

C cannot begin until A and B are VERIFIED. LLM may recommend roles but structural readiness is deterministic.

## 4. Dependency Scheduling

Specialist becomes READY only when:

- dependencies VERIFIED
- capability available
- model requirement satisfiable
- budget exists (plan shared budget remaining)
- concurrency slot exists

`DelegationPlanService.ready_delegations()` returns deterministic READY set sorted by priority, created_at, id. Fairness: priority + starvation prevention. No specialist monopolizes slots.

Scheduling is deterministic selection policy: priority asc, then `created_at`, then `id`. `next_ready()` returns the first.

## 5. Controlled Parallelism

```
ParallelismPolicy
    enabled
    max_parallel_specialists
    max_parallel_model_calls
    max_parallel_runtime_actions
    stage
```

Default `parallelism_enabled = False` => Stage 0 serialized. Must be explicitly enabled.

Stages:

```
Stage 0: coordination only, serialized (profile lease)
Stage 1: parallel reasoning-only specialists (system_observation, activity_recall)
Stage 2: parallel non-destructive Runtime capabilities (read-only + memory + goal_planning)
Stage 3: additional capabilities after explicit concurrency safety validation
```

GUI operations always serialized (`gui_automation` never concurrent).

Default production policy remains Stage 0. Stage 1 requires `parallelism_enabled=True`.

## 6. Per-Specialist Execution Ownership

```
Profile/Orchestrator Lease (RunnerLease id=1)
        |
        +-- Specialist Execution Slot A (specialist_execution_slots)
        +-- Specialist Execution Slot B
        +-- Specialist Execution Slot C
```

Parent/Main orchestration ownership (`RunnerLease`) is preserved. Specialist execution ownership uses isolated `specialist_execution_slots` rows (owner_nonce, expires_at) under the plan. Does not create two competing authorities over the same Task — specialist tasks have independent ids.

Each Specialist Task has independent cancellation, budget, execution context, event sequence, result, recovery state. Failure/cancellation in A does not cancel B unless dependency graph requires it.

Budget is shared plan budget: `DelegationPlanService._budget_available` checks `max_total_steps/model_calls` against sum of `spent` across delegations. Specialist cannot bypass parent budget.

## 7. Task Isolation & Cancellation

Hierarchical cancellation:

```
Parent cancelled -> all child specialists cancelled (deterministic)
Child cancelled -> parent continues unless completion policy says otherwise
```

No specialist may cancel another specialist directly — only MAIN may cancel via `cancel_delegation`/`cancel_plan` with `require_main_actor` guard.

Tests: `test_phase10_coordination` proves isolation and hierarchy.

## 8. Result Dependencies & Memory Isolation

Specialist may depend on another specialist's VERIFIED result. Dependent receives only verified dependency results via `_dependency_results` in packet constraints — not sibling private memory. `ScopedMemoryAccess` still enforces TASK/GOAL + explicit grants.

DelegationPlan context added but no shared unrestricted specialist memory.

## 9. Result Aggregation

`ResultAggregator`:

```
collect()            verified vs missing
validate()           VERIFIED has result_json
detect_conflicts()   structural divergence -> CONFLICT {source A, source B, evidence}
synthesize()         bundle for Main Agent
aggregation_confidence()  evidence_confidence weighted, agreement level, not blind average
```

Conflicts reported explicitly, not auto-resolved by recency. Main Agent remains final integration authority.

Aggregation confidence: agreement = 1 - conflicts/max_pairs; aggregation = min(avg_evidence_confidence * agreement + 0.1, 1.0) or avg_confidence * agreement. Agreement does not prove truth; evidence quality dominates.

No specialist directly overwrites another's output.

## 10. Conflict Handling

Example:

```
Research A -> X
Research B -> Y
=> CONFLICT {source A, source B, evidence}
```

Aggregator reports CONFLICT with both sources and evidence. Main Agent decides ACCEPT_PLAN_RESULT / REQUEST_REVISION / DELEGATE_ADDITIONAL / CONTINUE_DIRECTLY / ASK_USER / ABORT.

## 11. Main Agent Integration

Main Agent receives:

```
DelegationPlan status
Specialist statuses
Verified results
Conflicts
Missing results
Resource cost
```

via `ResultAggregator.synthesize()` and `plan_status_view()`. Plan completion does not automatically become Goal completion — Main Agent is strategic authority.

Completion policies:

- ALL_REQUIRED: one failed required prevents completion
- MAJORITY: needs >50% verified
- OPTIONAL_RESULTS: optional specialists may fail

Main Agent knows why (evaluate_completion reason).

## 12. Resource & Model Allocation

Coordinator tracks model calls, runtime actions, elapsed time, specialist count, context size, parent budget remaining. Uses `ModelRequirements -> ModelSelector -> ModelProvider` per specialist. If model unavailable, specialist BLOCKED/UNAVAILABLE, not silently substituted.

## 13. Parallel Runtime Safety

Verified safe for parallel:

- ToolExecutor shared state safe (stateless, ContextVar isolation)
- ContextVars isolate cancellation/task IDs
- DB sessions task-scoped (each parallel specialist uses isolated session via DatabaseManager.new_session)
- ledger sequence per task_id safe
- file operations no shared mutable execution state (different files safe)
- budgets atomic via DB

Serialized:

- GUI automation (dedicated UIA MTA worker) — always serialized
- Profile lease acquisition

If ANY subsystem not concurrency-safe, keep serialized. Validated behavior is documented in `CONCURRENCY_ARCHITECTURE.md` (Phase 10.5). Stage 2/3 remain disabled; file operations remain `SERIAL_ONLY`.

## 14. Database Concurrency (Phase 10.5 validated)

Tested under genuine concurrent asyncio and multi-process contention (not only post-run counts):

- delegation admission via conditional UPDATE (cross-process atomic);
- specialist_execution_slots with `actual_active <= limit` invariant enforced via post-insert rollback;
- budget atomic commit (`atomic_commit_spent`) enforcing plan budget even when concurrent writers race;
- event ledger `no duplicate (task_id, sequence_num)`, no lost events, per-task replay stable;
- SQLite `PRAGMA integrity_check=ok` after each stress class (transaction stress with temporary DBs).

See `CONCURRENCY_ARCHITECTURE.md` §§4-6,11,14 for exact workloads and results. Never uses the user's real production DB.

## 15. Crash Recovery

Extended to multi-specialist plans:

```
A = COMPLETED/VERIFIED preserved
B = RUNNING -> recovered from task evidence -> RESULT_READY or ASSIGNED
C = READY remains READY
```

`recover_plans()` mirrors `recover_delegations`: A remains COMPLETED, B recovered from evidence, C remains READY. No restart of A because B failed, no forged B result, no lost C.

## 16. Specialist Retry/Replacement

Failed specialist may be retried/revised/replaced by another role, bounded by `max_specialist_revisions`. Replacement creates new delegation identity/version, does not overwrite old failure record (`retry_delegation`).

## 17. Observability

Via existing `DbEventLedger`:

```
Main Agent
  +-- Delegation Plan [ACTIVE]
       +-- Research [RUNNING]
       +-- Analysis [READY - waiting for Research VERIFIED]
       +-- Review [BLOCKED - dependency not VERIFIED]
```

Events: `plan.validated`, `plan.activated`, `plan.completed`, `plan.cancelled`, `plan.delegation.created`, `agent.cancelled`, `specialist.action`, `specialist.reasoning`, `delegation.context.built`.

## 18. Security Invariants (Phase 10.5 concurrency-proven)

1. concurrency cannot bypass Runtime policy (`PolicyContext(direct_user_instruction=False)`)
2. concurrency cannot bypass budgets (atomic DB commit)
3. one specialist cannot access another's private memory (only VERIFIED dependency results)
4. one specialist cannot cancel another directly (only MAIN via `require_main_actor`)
5. one specialist cannot overwrite another result (deterministic evidence per delegation_id)
6. slot limits cannot be exceeded (`actual_active <= limit` enforced atomically)
7. unverified results cannot unlock dependents (`VERIFIED/INTEGRATED` only)
8. GUI operations remain serialized (MTA worker, stage-independent)
9. capability-specific restrictions cannot be bypassed (truth table `SERIAL_ONLY`/`UNKNOWN` → serialized)
10. process recovery cannot duplicate concurrent work (idempotency `delegation:<id>:<attempt>`, no forged result)

All 10 adversarially tested under actual concurrent execution (not only unit mocks).

## 19. Concurrency Rollout (Phase 10.5 decision)

| Stage | Meaning | Status |
|---|---|---|
| STAGE_0_SERIALIZED | Coordination only, serialized (profile lease) | **SUPPORTED** (default `parallelism_enabled=false`) |
| STAGE_1_REASONING_ONLY | Parallel reasoning-only (system_observation, activity_recall, reasoning_model) | **SUPPORTED_WITH_RESTRICTIONS** — opt-in `SYTHION_PARALLELISM_ENABLED=1`; file/GUI remain SERIAL_ONLY; provider calls proven via mocks |
| STAGE_2_NON_DESTRUCTIVE | Non-destructive runtime parallelism | **NOT_SAFE** — code exists but not validated for production |
| STAGE_3_FULL | Broader capability parallelism (except GUI) | **NOT_SAFE** |

Stage 1 is opt-in; Stages 2/3 remain disabled unless their specific capability classes are validated. General filesystem parallel safety is NOT claimed (see `CONCURRENCY_ARCHITECTURE.md` §§10,13).

Production defaults remain `parallelism_enabled=false`.
