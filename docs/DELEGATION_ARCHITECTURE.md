# Delegation Architecture (Phase 9)

## 1. Delegation decision

The **cognitive layer** decides. `CognitivePipeline._deterministic_candidates()` offers a `DELEGATE` candidate and `DecisionSystem.evaluate()` selects it deterministically before anything reaches the runtime.

```
User input
    ↓
IntentInterpreter (category + confidence + ambiguity)
    ↓
CognitivePipeline._assemble_context (SelfModel capability truth)
    ↓
ReasoningEngine (lightweight vs full — model is a reasoning component, never the decision authority)
    ↓
deterministic candidates: ANSWER / EXECUTE_STEP / CREATE_GOAL / DELEGATE / …
    ↓
DecisionSystem (hard-constraint veto >> weighted scoring)
    ↓
CognitiveDecisionAdapter.route_new_input bridges the SelectedDecision
    over the only durable task lifecycle (via TaskRunner)
```

Phase 9 distinguishes **six** decision outcomes at the adapter boundary:

```
EXECUTE_DIRECTLY / EXECUTE_STEP / CREATE_GOAL / UPDATE_GOAL / ANSWER  → worker
ASK_CLARIFICATION / REQUEST_PERMISSION / REFUSE / DEFER / WAIT         → WAITING_FOR_USER park
DELEGATE                                                               → DelegationService durable path
```

`DELEGATE` is never offered as the LLM's sole fantasy: it appears only when deterministic structural signals justify it **and** the runtime says delegation is actually possible.

## 2. Eligibility

Deterministic, structural, and always gated by real runtime state.

```python
def delegation_signals(text) -> {multi_step, analysis_oriented, long}
  multi_step:       re "then|after that|step N|;|, …"
  analysis_oriented:re "analyze|compare|evaluate|research|…| which … best|trade-offs"
  long:             >40 words

def delegation_available(db) -> bool:
  settings.specialists_enabled and exists enabled SpecialistDefinition
```

A `DELEGATE` candidate is emitted only when the input category is `INFORMATIONAL` or `COMMAND` and

```
analysis_oriented  OR  (multi_step and long)  OR  long
```

is true **and** `delegation_available(db)` is true. `DelegationService.validate()` then applies the remaining deterministic gates: enabled definition exists, requested `required_capabilities` are a subset of the role's binding, `max_specialists_per_task` not yet reached, and requested per-delegation budgets are ≤ policy caps. No arbitrary natural-language prompt can conjure a missing specialist or capability into existence.

## 3. Structured request

```
DelegationRequest(
    parent_agent_id,   # MAIN only (AuthorityError otherwise)
    specialist_role,   # RESEARCH|ANALYSIS|CODING|REVIEW|PLANNING_SUPPORT
    objective,         # the bounded mission (2000 chars)
    goal_id, task_id,  # parent linkage (optional)
    required_capabilities, model_requirements,
    constraints,       # includes memory_access grants, step plan, etc.
    expected_output,   # the output contract (500 chars)
    budget, deadline,
)
```

Duplicate delegations (same parent + role + case-insensitive objective while a prior one is still active) are rejected deterministically; no unbounded fan-out.

## 4. Delegation lifecycle

```
PROPOSED  ──validate──▶  VALIDATED  ──assign──▶  ASSIGNED  ──run(kernel)──▶  RUNNING
                                                              │
                                        ┌────────────────────┼────────────────────┐
                                        │                    │                    │
                                   RESULT_READY          FAILED/CANCELLED      EXPIRED
                                        │                    (terminal)
                                        ▼
                                     VERIFIED ──ACCEPT──▶  INTEGRATED  (terminal)
                                        │
                                        └──REVISE──▶ VALIDATED  (≤max_specialist_revisions times, else REJECT)
                                        └──REJECT/CONTINUE/ASK_USER──▶ CANCELLED
```

* No specialist recursion: `max_specialist_depth = 1` enforced at creation (`parent must be MAIN`).
* No unbounded fan-out: `max_specialists_per_task` (default 2) enforced at `validate()` by counting `ACTIVE_DELEGATION_STATES`.
* `Raw model output → INTEGRATED` is impossible: `assert_delegation_transition(RESULT_READY→VERIFIED)` then `assert_delegation_transition(VERIFIED→INTEGRATED)` are the only admissible paths.

All stages emit `DbEventLedger` events under `delegation:{id}` — reusing the existing ledger — so the whole path is auditable and recoverable.

## 5. Scoped context (what the specialist actually sees)

The bounded `SpecialistContextPacket` (JSON on `delegations.context_packet_json`, ≤ ~3000 chars):

```
objective, role, required_capabilities, constraints, expected_output,
budget, parent_goal_summary (title only), relevant_memory (top-5 scored USER/GOAL/TASK memories)
```

Scored retrieval is `MemoryService.retrieve(query=objective, scopes=["TASK","GOAL"] + granted, limit=5)`; `USER`/`GLOBAL` memories appear only when `constraints.memory_access` grants them. The specialist never receives a snapshot of the Main Agent's full context — context is bounded by construction.

## 6. Execution through the protected kernel

`DelegationService.run()` acquires the profile-wide lease, creates the specialist runtime task (`idempotency_key="delegation:{id}:{attempt}"` → no duplicate specialist execution), transitions it `CREATED→RUNNING`, and builds a `SpecialistRuntime` that is the **only** surface the specialist touches:

```
SpecialistRuntime.request_action(tool, input)
    — refuses unassigned tools (CapabilityViolation) at binding time
    — enforces max_specialist_steps / max_specialist_time_seconds
    — calls ToolExecutor.execute_tool(..., PolicyContext(direct_user_instruction=False))
        so runtime policy (e.g. file writes need confirmation) remains non-bypassable
    — records specialist.action evidence per step
```

Idempotent task identity means a crash between `attempt=1` and `RESULT_READY` never produces two specialist tasks.

## 7. Cost model

Costs are tracked per execution in `SpecialistRuntime` (`actions_used`, `model_calls_used`, `elapsed_seconds`) snapshotted at every stage and stored as:

- `delegations.budget_json.spent` on the delegation row, and
- `specialist.cost.incurred` ledger events on the **parent** task when a parent task was bound, so parent execution always includes specialist cost (`DelegationService._record_cost`). There is no accounting loophole.

Parent completion never proceeds on unverified specialist work: `verify()` is a precondition of `integrate(ACCEPT)` (enforced by `assert_delegation_transition`), which is a precondition of `tasks.state=COMPLETED` via `TaskRunner.transition_state`. No forged `task.completed` can be emitted.

## 8. Result contract

```
SpecialistResult {
  status:  COMPLETED | FAILED
  summary: 1-sentence human summary
  findings: […≤20×300]               — per-step summarized evidence
  evidence_refs: [action:N | context_packet] — must match recorded ledger events
  confidence: 0.0…1.0                 — mandatory for COMPLETED, fraction of successful steps
  limitations: […]                    — deterministic caveats (including the Phase-9 worker note)
  recommended_next_action: str
  cost: {actions, model_calls, elapsed_seconds, caps}
  failure_reason: str                 — mandatory for FAILED
}
```

`SpecialistResult.validation_errors()` is the single structural gate. Arbitrary model prose never becomes trusted state.

Verification (`DelegationService.verify`) checks:

1. structural shape;
2. that every `action:N` ref has a matching `specialist.action` event for this delegation's specialist task;
3. that `context_packet` refs have a matching `delegation.context.built` event.

Only then → `VERIFIED`; failure → `agent.result.rejected` and the delegation must not be integrated.

## 9. Main-Agent integration

Only the Main Agent (`require_main_actor`) may call `integrate(decision)`:

| decision | effect | note |
|---|---|---|
| `ACCEPT` | `VERIFIED → INTEGRATED`, `agent.result.accepted`, `Agent COMPLETED`, parent task `COMPLETED` with `[delegated:role] …` final result | |
| `REJECT` / `CONTINUE` / `ASK_USER` | `→ CANCELLED`, `agent.result.rejected` | parent is not completed by the specialist |
| `REVISE` | `VERIFIED → VALIDATED` (once, bounded by `max_specialist_revisions`) to `ASSIGNED → RUNNING` again | further `REVISE` → `REJECT` |

A specialist failure never fails the parent goal automatically; delegation failure stays isolated and classified, and the Main Agent may retry directly or ask the user.

## 10. Failure & recovery

Startup uses the **same** lease-takeover machinery as goals/plans:

- a stale `RUNNING` specialist task interrupted by the next `acquire_lease()` is observed as `interrupted`;
- `DelegationService.recover_delegations()` reads the specialist task evidence without executing anything and resolves: `completed` → `RESULT_READY` (result rebuilt from `specialist.action` + `specialist.reasoning` trail), `failed` → `FAILED`, `cancelled/interrupted` → `ASSIGNED` (recoverable, attempt not re-counted), `missing` → `ASSIGNED`, `created/running` → `deferred` (the lease takeover owns it).

Repeated recovery passes are a no-op — the delegation is no longer `RUNNING`.

---

# Phase 9.5 — Quality, Atomic Admission & Reasoning Integration

## 11. Atomic admission (fan-out enforcement)

The fan-out limit (`max_specialists_per_task`, default 2) is now enforced **atomically at the persistence boundary**:

- `DelegationService.validate()` holds a serialized admission section (`_admission_lock`, `threading.RLock`) so concurrent callers cannot both observe `count=1` and admit a second specialist when the limit is 2.
- The count check and the `PROPOSED → VALIDATED` transition occur in the same serialized section; a post-commit re-check (`active_delegations_for_task` after `VALIDATED`) rolls back to `FAILED` if a cross-process race slipped past the in-process lock (SQLite's file lock serializes writers anyway).
- No second global lease authority was introduced; the profile-wide `RunnerLease` remains the only lease, and admission uses an in-process serialized section plus SQLite's writer serialization — the smallest robust mechanism for the existing SQLite architecture.

Invariant: *No transaction may successfully admit a delegation if admitting it would exceed the configured active-specialist/fan-out limit.* Adversarial concurrency tests (`test_phase95_hardening.py:TestAdmissionAtomicity`) prove the limit is never exceeded with 2 callers at the boundary, more callers than the limit, duplicate idempotency keys, and validation-failure concurrent with a valid delegation.

## 12. Specialist execution lifecycle (with reasoning)

```
Delegation Admission (atomic validate)
        ↓
Specialist Creation (ASSIGNED + SpecialistContextPacket)
        ↓
Specialist Execution (bounded reasoning + protected kernel)
        ↓
  ┌───────────────────────────────┐
  │  SpecialistReasoner (model)   │
  │  → structured findings &      │
  │    proposed actions           │
  └───────────┬───────────────────┘
              ↓           ↓
     reasoning-only   action-producing
              ↓           ↓
        Evidence    Kernel (capability/budget/policy) → Evidence
              └───────────┬───────────┘
                          ↓
                   SpecialistResult
                          ↓
                  Verification (evidence-matched)
                          ↓
                  Main-Agent Integration (ACCEPT/REJECT/REVISE/CONTINUE/ASK_USER)
```

Admission proves the delegation is allowed. Execution then performs the work. Execution never bypasses admission because the specialist already exists — `run()` asserts `ASSIGNED` and the lease guards kernel entry. See `docs/SPECIALIST_REASONING_ARCHITECTURE.md`.

## 13. Result quality & integration boundary

Verification means *structurally and evidentially valid*, not *strategically correct*. The Main Agent remains the integration authority:

- `VERIFIED` requires substantive findings (at least one non-template finding with ≥10 chars and ≥2 words), explicit `limitations`, `confidence` in `[0,1]`, and evidence refs that match real `specialist.action` / `specialist.reasoning` / `delegation.context.built` events.
- `CONFIDENCE` is split: `reasoning_confidence` (model self-assessment) vs `evidence_confidence` (success-rate derived) vs `confidence` (evidence-backed result). Model self-confidence is never proof; unknown stays explicit.
- Failures are classified (`MODEL_FAILURE`, `INVALID_SPECIALIST_OUTPUT`, `CAPABILITY_DENIED`, `RUNTIME_FAILURE`, `VERIFICATION_FAILURE`, `TIMEOUT`, `BUDGET_EXCEEDED`, `CANCELLED`) and surfaced to the parent; a specialist failure never becomes a successful completion.
- Bounded retry: one repair attempt for malformed model output; provider fallback via Phase 6; all attempts consume parent budgets.
- `REVISE` carries structured feedback (`problem`, `missing_information`, `required_correction`, `new_constraints`) which is merged into the delegation for the next bounded attempt; `max_specialist_revisions` caps the loop.

Only `ACCEPT` on `VERIFIED` reaches `INTEGRATED`; all other outcomes are audited as `agent.result.accepted/rejected` and the specialist is `CANCELLED`.

## 14. Future concurrency boundary (Phase 9.5)

Phase 9.5 still serializes specialist execution behind the single profile lease (no unrestricted concurrent Runtime execution, no specialist-spawning-specialists). The durable rows and scoped packets remain concurrent-capable; Phase 9.5 only hardens the **admission** boundary so future concurrency will not need a redesign of Agent/Delegation objects. Parallel `tool_executor.execute_tool` would require only a bounded lease-partition policy and a per-specialist concurrency cap.

See `docs/AGENT_ARCHITECTURE.md` and `docs/SPECIALIST_REASONING_ARCHITECTURE.md` for the agent/state tables, memory scoping, budgets, substantive-result rules, evidence, revision, failure, and isolation.

---

# Phase 9.75 — Specialist Production Readiness

## 15. Cross-process atomic admission (persistence boundary)

Phase 9.5's in-process lock + post-commit rollback is replaced by a **single conditional UPDATE** that establishes the fan-out invariant at the write boundary (`backend/agents/delegation.py`, `_ADMIT_SQL`):

```sql
UPDATE delegations
SET status = 'VALIDATED', updated_at = :now
WHERE id = :did
  AND status = 'PROPOSED'
  AND ( SELECT COUNT(*) FROM delegations
        WHERE status IN ('VALIDATED','ASSIGNED','RUNNING','RESULT_READY','VERIFIED')
          AND ((:tid IS NOT NULL AND task_id = :tid)
            OR (:gid IS NOT NULL AND goal_id = :gid)) ) < :limit;
```

- **Slots are consumed by ADMITTED states only.** `PROPOSED` never counts: a proposal has not claimed a specialist yet. The invariant is exact — `admitted <= max_specialists_per_task`, with no off-by-one and no mutual blocking of pending proposals.
- SQLite serializes writers, so no two processes can both observe a passing count and admit over-limit state. `rowcount == 0` means refusal: the row's authoritative status is re-read fresh — `VALIDATED` ⇒ idempotent success, `PROPOSED` ⇒ deterministic `FAILED` with a `max_specialists_per_task` reason, anything else ⇒ "no longer admissible".
- The post-commit recount remains **as defensive validation only**; it is never the correctness mechanism.
- Duplicate idempotency gained the same cross-process guarantee: `delegations.fingerprint` (SHA-256[:16] of `parent|role|objective`) plus the partial UNIQUE index `uq_delegations_fingerprint_active` (active states only) makes double proposal across processes impossible at commit time; `propose()` maps the `IntegrityError` to `duplicate_delegation`. Legacy rows carry `NULL` fingerprints, which the partial index ignores.

Multi-process proof lives in `test_phase975_admission_multiproc.py`: independent OS processes contend on one file DB (exact boundary, above-boundary contention, duplicate objective, terminal-state accounting, `PRAGMA integrity_check`). Kill-during-admission recovery is proven gated in `test_phase975_admission_recovery.py` (process terminated inside the uncommitted admission transaction ⇒ no partial `VALIDATED`, no phantom slot, clean re-admission after restart via the existing `recover_delegations()` machinery).

## 16. Capability risk split (SYSTEM_OBSERVATION ≠ SYSTEM_CONTROL)

`system_observation` is now **purely read-only** (`system_time/battery/disk/processes/network_status/active_window/window_list/file_roots/capabilities/power/clipboard_read`). All mutating OS controls moved to the new HIGH-risk, permission-gated **`system_control`** capability:

| tool | mutation |
|---|---|
| `system_power_control` | lock / sleep / restart / shutdown |
| `brightness_control` | set display brightness |
| `system_volume` | set master volume / mute |

No `ROLE_CAPABILITIES` binding references `system_control`; roles therefore lost nothing they legitimately had while RESEARCH/ANALYSIS/REVIEW/PLANNING_SUPPORT no longer implicitly inherit OS control tools through observation. Pinned by `test_phase975_capabilities.py` (taxonomy audit of all 13 specs, read-only allowlist for observation, kernel-boundary denial of `system_power_control` to an observation-bound specialist).

## 17. Provider-backed validation & CI gating

Real-provider specialist E2E (`test_phase975_provider_e2e.py`) exercises `Main Agent DELEGATE → atomic admission → SpecialistContextPacket → real ModelSelector → real ModelProvider → SpecialistReasoner → SpecialistResult → verification → integrate(ACCEPT)` twice with a real model: once reasoning-only (ANALYSIS) and once action-producing (CODING proposing a harmless deterministic `fixture_observation` fixture tool through SpecialistRuntime/ToolExecutor). Evidence assertions require a genuine non-fallback `specialist.reasoning` ledger event and `specialist.action:N` events matching the result refs.

CI strategy mirrors the process-kill convention: tests are marked `provider_e2e` and skip unless `SYTHION_RUN_PROVIDER_E2E=1` AND provider key/model resolve (key via `SYTHION_OLLAMA_API_KEY` or Credential Manager; model via `SYTHION_SELECTED_MODEL` or onboarding). Normal CI never incurs cost; the dedicated `provider-e2e` GitHub Actions job runs only on explicit credentialed `workflow_dispatch`. Deterministic mock failure tests (provider unavailable, malformed output + bounded repair, timeout, budget exhaustion) run in every normal invocation (`test_phase975_provider_failures.py`) — the real-provider suite complements them, it does not replace them.

See `docs/SPECIALIST_PRODUCTION_READINESS.md` for the full readiness report.

---

# Phase 10 — Multi-Delegation

## 18. Multi-delegation admission

`DelegationPlanService.create_plan` atomically creates a plan + its delegation graph. Validation covers role validity, capability availability, fan-out limits (`max_specialists_per_plan`, `max_active_specialists_per_parent`, `max_total_*`), dependencies, cycles, budgets, model requirements. Whole graph or nothing.

## 19. Scheduling & aggregation

`DelegationGraph` + `ready_delegations` deterministically schedules READY specialists (priority → created_at → id). `ResultAggregator` collects VERIFIED results, detects CONFLICT, computes evidence-weighted confidence, and synthesizes evidence for Main Agent. See `MULTI_AGENT_COORDINATION_ARCHITECTURE.md`.

## 20. Failure/replacement

Completion policies `ALL_REQUIRED|MAJORITY|OPTIONAL_RESULTS`. Failed specialist may be retried via `retry_delegation` (new identity, bounded by `max_specialist_revisions`). Parent cancellation cascades; child cancellation is isolated.

## 21. Parallelism stages

Stage 0 serialized (default), Stage 1 reasoning-only parallel, Stage 2 non-destructive parallel. GUI serialized always. `ParallelismPolicy.from_settings()` governs.
