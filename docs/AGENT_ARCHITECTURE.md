# Agent Architecture (Phase 9)

Phase 9 introduces the first real multi-agent layer: a bounded specialist
system under the Persistent Main Agent. Specialists are workers, never peers.

## 1. Agent abstraction

Every logical agent is a row in `agents` (`backend/models/database.py`).

| column | meaning |
|---|---|
| `id` | uuid; MAIN rows reuse the Phase-4 `main_agents.agent_id` so there is exactly one Main Agent per profile across `main_agents` and `agents`. |
| `type` | `MAIN` | `SPECIALIST` | `TEMPORARY_SPECIALIST` — the only three types in Phase 9. |
| `role` | `RESEARCH` | `ANALYSIS` | `CODING` | `REVIEW` | `PLANNING_SUPPORT` | null (MAIN). Roles map only onto CapabilityRegistry capability IDs. |
| `state` | `CREATED` → `READY` → `RUNNING` → `WAITING` → `COMPLETED` → `EXPIRED` (plus `FAILED`/`CANCELLED`). The Main-agent availability lifecycle in `services/main_agent.py` (`active/paused/degraded/shutdown`) remains a separate domain. |
| `parent_agent_id` | always the Main Agent for specialists (`max_specialist_depth = 1`). No recursion. |
| `definition_id` | FK-ish to `specialist_definitions.id`; the reusable profile this instance was cut from. |
| `goal_id` | parent goal/task scope when the delegation was created. |
| `config_json` | per-instance budgets, ttl, model requirements (JSON). |
| `expires_at` | temporary specialists carry a TTL (`specialist_ttl_seconds`; default 3600 s). The worker never accumulates forever — `AgentService.expire_due()` moves finished temporaries to `EXPIRED`. History/ledger stays. |

State machine: `backend/agents/state.py` (`AgentLifecycle`, `DelegationStatus`, `AGENT_TRANSITIONS`, `DELEGATION_TRANSITIONS`). Invalid transitions raise `InvalidTransition` deterministically; nothing falls forward.

## 2. SpecialistDefinition vs SpecialistInstance

| entity | table | lifetime | authority |
|---|---|---|---|
| `SpecialistDefinition` | `specialist_definitions` | persistent profile — one per role (`role` unique), stores default `capabilities_json` (`["system_observation" , …]`) and `model_requirements_json` (`{"task_class": "simple_reasoning"}`, never a concrete model id) | seeded once by `AgentService.ensure_definitions()`; only the Main Agent may enable/disable it |
| `SpecialistInstance` (`AgentRecord` with type `TEMPORARY_SPECIALIST`) | `agents` | one durable row per delegation attempt, TTL-bounded | created only `FROM` an enabled definition; only the Main Agent may create/cancel it |

The Main Agent later instantiates temporary workers from these definitions; raw prompt strings never create authority.

## 3. Delegation lifecycle

One durable row per bounded delegation (`delegations`).

```
PROPOSED → VALIDATED → ASSIGNED → RUNNING → RESULT_READY → VERIFIED → INTEGRATED
                                   ↘ FAILED / CANCELLED / EXPIRED (any RUNNING branch)
                    ↘ FAILED / CANCELLED (any pre-RUNNING branch)
```

Raw model output can never jump to `INTEGRATED`: only a `VERIFIED` result is eligible for Main-Agent `integrate(ACCEPT)`. Failure branches (`FAILED/CANCELLED/EXPIRED`) are durably recorded with a `failure_reason`.

Events are carried on the existing `DbEventLedger` (`agent.delegated`, `agent.started`, `agent.progress`, `agent.completed`, `agent.failed`, `agent.cancelled`, `agent.result.accepted`, `agent.result.rejected`, `delegation.cost`, `delegation.recovery.resolved`, …) under scopes `delegation:{id}` — no second event store.

## 4. Specialist scope

A specialist receives only a bounded `SpecialistContextPacket` (`backend/agents/specialist.py`):

- delegation objective
- required capabilities (the definition's `capabilities_json` subset)
- constraints + `expected_output` (the output contract)
- budget caps (`steps/time_seconds/model_calls`)
- parent goal title (if any)
- the top-N relevant memories for the objective (scoped read, see §7)

It never receives a copy of the Main Agent's full context, goal graph, or capability set.

## 5. Capability binding

A specialist may only request tools in its binding:

```
Research   → system_observation, activity_recall
Analysis   → system_observation
Coding     → file_operations
Review     → system_observation, activity_recall
Planning   → system_observation
```

The mapping is `CapabilitySpec.tool_names ∩ registered executor tools` (`resolve_allowed_tools()`). The CapabilityRegistry remains descriptive; the runtime remains authoritative. `SpecialistRuntime.request_action` enforces the binding before anything reaches `ToolExecutor.execute_tool`, which enforces runtime policy non-bypassably (e.g. `requires_confirmation` tools still need `direct_user_instruction=True`, which a specialist can never provide). Ten adversarial tests prove the boundary.

## 6. Model assignment

Specialists request `model_requirements` (e.g. `{"task_class":"simple_reasoning"}`) and rely on `ModelSelector` → `ModelProvider` to pick the actual model. No specialist hard-binds itself to a model id; `max_specialist_model_calls` is a configuration budget enforced by `SpecialistRuntime.request_model_call()`.

## 7. Specialist memory

| memory | permission |
|---|---|
| `GLOBAL` / `USER` | read only when the delegation explicitly grants `constraints.memory_access = ["USER" | "GLOBAL"]`; otherwise invisible; write is structurally impossible — `ScopedMemoryAccess` exposes no permanent write path. Proposals land as `agent.memory.proposed` ledger events for the Main Agent. |
| `GOAL` / `TASK` parent scope | read-only allow-listed candidates via `ScopedMemoryAccess.read_relevant(query, scopes=["TASK","GOAL", …granted])` |
| specialist working memory | full access inside `TASK` scope, `source_type=MODEL_INFERENCE` (which MemoryService itself ceilings to `ASSUMPTION`/`BELIEF`, `UNVERIFIED`). Content is `spec:{agent_id}:{key}` so it cannot collide with user memories. |
| private episodic | temporary TASK-scoped rows; decayed/archived by normal decay rules. |

A specialist can never silently write a permanent user memory; memory proposals must go through `MemoryService` via the parent.

## 8. Runtime boundary

Every specialist action flows through the protected kernel:

```
Specialist → SpecialistRuntime.request_action(tool, input)
        → capability-binding check → budget check
        → ToolExecutor.execute_tool(..., PolicyContext(direct_user_instruction=False))
            → Policy → TargetValidation → Cancellation → Timing → Verify
        → evidence event `specialist.action` on the specialist task ledger
```

No specialist may call tools directly, use OS automation APIs, mutate runtime state, bypass `ToolExecutor`, or forge ledger evidence. `SpecialistResult` evidence refs (`action:N` / `context_packet`) are verified against those auditable events in `DelegationService.verify()`; missing or forged refs fail verification deterministically.

## 9. Budgets

All caps live in `core/config.py` and are the single policy source:

```
specialists_enabled          = True
max_specialists_per_task     = 2
max_specialist_depth         = 1
max_specialist_steps         = 5
max_specialist_time_seconds  = 300
max_specialist_model_calls   = 10
max_specialist_revisions     = 1
specialist_ttl_seconds       = 3600
```

`SpecialistRuntime` enforces `steps/time/model_calls` per execution; delegations that exceed `max_specialist_steps` or request a budget above policy are rejected at validation. Costs are recorded on both the delegation (`budget_json.spent`) and — when a parent task exists — on the parent-task ledger (`specialist.cost.incurred`) so parent execution always includes specialist cost (no loophole).

## 10. Result verification

After `RESULT_READY` the Main Agent does not blindly trust the specialist:

```
specialist result → structural validation (SpecialistResult.validation_errors)
                → evidence inspection (each evidence_ref must match a `specialist.action` or `delegation.context.built` event actually present for this delegation's specialist task)
                → consistency check (COMPLETED needs findings + confidence 0..1, FAILED needs failure_reason, confidence/finding/evidence shapes)
                → VERIFIED  (rejected otherwise → agent.result.rejected, never integrated)
                → Main-Agent integrate(ACCEPT|REJECT|REVISE|CONTINUE|ASK_USER)
```

Only `ACCEPT` over a `VERIFIED` result reaches `INTEGRATED` (`agent.result.accepted`); `REVISE` is bounded by `max_specialist_revisions` and re-enters `VALIDATED → ASSIGNED → RUNNING` exactly once more before becoming `REJECT` → `CANCELLED`.

## 11. Failure & recovery

A specialist failure is classified, persisted with evidence, and never silently retried forever. The parent task/goal is not failed by a specialist failure; the Main Agent may choose a different strategy (direct execution, another delegation, or asking the user).

Startup recovery reuses the **existing** lease/recovery philosophy — no second mechanism:

- a stale Runtime lease is taken over on the next `acquire_lease()`, which marks an orphaned `RUNNING` specialist task `INTERRUPTED` via the authoritative path;
- `DelegationService.recover_delegations()` then resolves each `RUNNING` delegation from its specialist task evidence without forging completion: `completed` → `RESULT_READY` (reconstructed from `specialist.action` audit trail), `failed` → `FAILED`, `cancelled/interrupted` → `ASSIGNED` (recoverable, attempt not re-counted), `missing` → `ASSIGNED`, `created/running` → `deferred` (the lease takeover owns it).

Idempotent task identity is pinned by `idempotency_key = "delegation:{id}:{attempt}"`, so a restart can never produce a duplicate specialist execution.

## 12. Concurrency

Phase 9 deliberately serializes specialist execution behind the profile-wide Runtime lease. Specialists may exist simultaneously as durable rows, but execution remains sequential. The durable `delegations`/`agents` records and the scoped packet design make future parallel execution possible without redesigning agent objects; it is explicitly deferred.

## 13. Observability

```
GET  /api/v1/agents                         — Main Agent + specialist registry + definitions
GET  /api/v1/agents/delegations             — durable delegation records
GET  /api/v1/agents/delegations/{id}        — one delegation with structured result + cost
```

All agent work is also present in the normal task/event views and ledger exports.

## 14. Security invariants (adversarially tested)

1. unassigned capability denied at the binding; 2. no direct tool path; 3. Main-Agent state guarded (`require_main_actor`); 4. no goal mutation surface on the specialist;
5. no permanent memory write; 6. executor policy not bypassable (e.g. file writes still need confirmation); 7. only Main can spawn; 8. budget enforced; 9. forged completion rejected (evidence mismatch); 10. unverified results never integrated.

See `backend/tests/test_phase9_delegation_security.py`.

---

# Phase 9.5 — Specialist Quality & Delegation Integrity

## 15. SpecialistReasoner — bounded cognitive loop

Phase 9.5 replaces the deterministic template-result path with a genuine bounded specialist loop:

```
Delegation → SpecialistContextPacket → ModelSelector → SpecialistReasoner → Candidate Work → Specialist Action/Analysis → Runtime Kernel → Evidence → SpecialistResult
```

`backend/agents/reasoner.py:SpecialistReasoner` is the dedicated reasoning interface:

- `analyze(context) → {findings, analysis, limitations, confidence, proposed_actions}`
- `propose_actions(...)` validated proposals
- `validate_proposal(proposal, runtime) → (ok, reason)`

It uses `ModelSelector` + `ModelProvider` (provider-neutral) and respects role requirements, budgets, scoped memory, and scoped capabilities. It never imports `OllamaCloudAdapter` or raw adapter singletons.

A malformed model output is repaired once (one bounded retry); transient provider failures are handled by the existing Phase 6 fallback; all attempts consume parent budgets.

## 16. Role-bounded task types

Specialists support at minimum:

- `RESEARCH` — investigate, reasoning-only, `system_observation+activity_recall`, `context_following`, max 3 actions
- `ANALYSIS` — compare/evaluate trade-offs, reasoning-only, `system_observation`, `simple_reasoning`
- `CODING` — file inspection/mutation, `file_operations`, `planning`, max 5 actions
- `REVIEW` — critique, reasoning-only, `system_observation+activity_recall`, `structured_output`
- `PLANNING_SUPPORT` — decompose goals, reasoning-only, `system_observation`, `planning`

Each role defines: purpose, input/output contract, required model capabilities, allowed capability categories, maximum actions/model_calls. A Coding specialist cannot become a Research specialist; a Research specialist cannot gain file mutation rights because the model asked for them. Delegation may *narrow* capabilities, never broaden beyond the role binding and Runtime policy (`backend/agents/state.py:ROLE_CAPABILITIES`, `ROLE_MODEL_REQUIREMENTS`, `backend/agents/reasoner.py:_role_spec`).

## 17. Substantive-result rules (anti-template)

`SpecialistResult.validation_errors()` (Phase 9.5) enforces:

- `COMPLETED` requires substantive findings — at least one finding with ≥10 chars, ≥2 words, not in the template deny-list (`context received`, `deterministic worker`, `placeholder`, etc.).
- `COMPLETED` requires explicit `limitations` (reasoning-only roles must enumerate unknowns).
- `confidence` in `[0,1]` is mandatory for `COMPLETED`; `reasoning_confidence` and `evidence_confidence` are separate fields (Requirement 17) — model self-confidence is not proof, unknown stays explicit.
- `cost` must be a dict, `recommended_next_action` may be empty but is tracked, `failure_code` must be one of the structured codes when `FAILED`.

A successful result with no substantive content never becomes `VERIFIED`. Empty template defaults may supply optional fields only.

## 18. Evidence integrity (anti-forgery)

Every claimed `evidence_ref` must map to a real ledger event:

- `action:N` → `specialist.action` with `seq=N` on `specialist_task_id`
- `context_packet` / `delegation.context.built` → `delegation.context.built` on `specialist_task_id`
- `specialist.reasoning` / `specialist.action:N` → `specialist.reasoning` or `specialist.model_call` + `specialist.action`
- `runtime.action` → same as `specialist.action`

`DelegationService.verify()` checks the `specialist_task_id` history; a specialist cannot invent IDs, cannot mark `VERIFIED` directly (only `DelegationService.verify` can), and cannot create arbitrary evidence references (Requirements 11–12). Reasoning-only specialists claim `specialist.reasoning` + `context_packet`; action-producing specialists claim `action:N` plus optional `specialist.reasoning`.

## 19. Failure taxonomy and bounded retry

Specialist failures are classified (no silent conversion to success):

- `MODEL_FAILURE`, `INVALID_SPECIALIST_OUTPUT`, `CAPABILITY_DENIED`, `RUNTIME_FAILURE`, `VERIFICATION_FAILURE`, `TIMEOUT`, `BUDGET_EXCEEDED`, `CANCELLED`

The parent Main Agent receives `{failure_reason, failure_code, cost, evidence}`. One repair attempt is allowed for `INVALID_SPECIALIST_OUTPUT`; provider fallback is bounded by Phase 6; runtime failures use normal Runtime recovery. No unlimited specialist-specific retry loops.

## 20. Revision with structured feedback

`DelegationService.integrate(decision="REVISE", feedback={problem, missing_information, required_correction, new_constraints})`:

- `REVISE` is bounded by `max_specialist_revisions` (default 1).
- Structured feedback is merged into the delegation (`new_constraints` → `constraints_json`, `required_correction` → `expected_output`) and emitted as `agent.progress` with `stage=revision_requested`.
- The specialist receives the updated context on the next `ASSIGNED → RUNNING` cycle and produces a new bounded result. No recursive specialist debates.

## 21. Isolation

- No shared mutable globals between specialists (checked: `SpecialistRuntime`, `SpecialistReasoner`, `ScopedMemoryAccess` are per-instance).
- `ContextVar` execution context (`backend/tools/executor.py`) isolates cancellation/task identity per asyncio context.
- Memory, budget, and cancellation are per-specialist: interleaving tests with two in-memory specialists prove `A` cannot affect `B` (`backend/tests/test_phase95_hardening.py:TestIsolation`).

## 22. Recovery with reasoning

Startup recovery (`DelegationService.recover_delegations`) now reconstructs reasoning evidence:

- `specialist.reasoning` events are included alongside `specialist.action`; a `completed` task with reasoning reconstructs findings as `recovered reasoning pass`; `missing` → `ASSIGNED`; `failed/cancelled/interrupted` → `FAILED`/`ASSIGNED`; `created/running` → `deferred`.
- No duplicate `specialist_task_id` (idempotency key `delegation:{id}:{attempt}`), no forged result, no corrupted parent state, evidence preserved, attempt accounting correct (`backend/tests/test_phase95_hardening.py:TestRecovery`).

See `backend/tests/test_phase95_hardening.py` for the full adversarial suite and `docs/SPECIALIST_REASONING_ARCHITECTURE.md` for the reasoning pipeline.

## 23. Security invariants (Phase 9.5 additions)

11. specialist reasoning cannot directly mark `VERIFIED`; 12. specialist reasoning cannot create arbitrary evidence references; 13. specialist role cannot broaden capabilities at runtime; 14. specialist result cannot bypass Main Agent integration; 15. concurrent delegation admission cannot exceed fan-out limit.

See `backend/tests/test_phase9_delegation_security.py` and `backend/tests/test_phase95_hardening.py`.

---

# Phase 9.75 — Capability Classes & Real Provider Execution

## 24. Specialist capability classes: observation vs control

Phase 9.75 splits the capability taxonomy so a specialist's *class* of power is explicit:

| class | capability ids | nature | risk |
|---|---|---|---|
| **SYSTEM_OBSERVATION** | `system_observation`, `activity_recall` | read-only state queries (time, battery, disk, processes, network, windows, clipboard text) + own-action recall | LOW |
| **SYSTEM_CONTROL** | `system_control` | mutating OS control — `system_power_control` (lock/sleep/restart/shutdown), `brightness_control` (set), `system_volume` (set/mute) | HIGH, permission-gated |
| FILESYSTEM | `file_operations` | read + write within approved roots (`file_*`) | MEDIUM, permission-gated |
| DESKTOP | `app_control`, `gui_automation` | app launch/close; UIA input on visible windows | MEDIUM/HIGH |
| WEB | `browser_control` | tab/page interaction via extension bridge | MEDIUM, bridge-gated |
| MEMORY/PLANNING/META | `persistent_memory`, `activity_recall`, `goal_planning`, `voice`, `reasoning_model`, `self_authoring_tools` | durable memory, goals, voice I/O, model reasoning, inert tool proposals | LOW–n/a |

Rules now pinned by tests (`test_phase975_capabilities.py`):

- observation capabilities may NEVER contain a mutating tool (allowlist enforced);
- `system_control` is HIGH-risk + `requires_permission=True`; with the control tools absent from the executor it resolves `UNAVAILABLE`;
- no specialist role binds `system_control`. Role bindings are unchanged in practice but no longer implicitly include OS control through `system_observation` — RESEARCH resolving its allowed tools cannot reach `system_power_control`, and `SpecialistRuntime.request_action("system_power_control")` raises at the kernel boundary.

## 25. Real provider execution path

The specialist reasoning path is validated end-to-end against a REAL configured provider (no mocks):

```
Main Agent DELEGATE (CognitivePipeline decision)
  → DelegationService.propose/validate   (cross-process atomic admission)
  → assign → SpecialistContextPacket
  → SpecialistReasoner
      → ModelSelector.select(role task-class requirements)   (real)
      → ModelProvider.chat(...)                              (real network)
  → structured analysis / validated action proposals
  → SpecialistRuntime.request_action → ToolExecutor          (kernel)
  → SpecialistResult → verification (ledger evidence match)
  → Main Agent integrate(ACCEPT)
```

Proven gated (`SYTHION_RUN_PROVIDER_E2E=1` + key/model configured) in `backend/tests/test_phase975_provider_e2e.py`: one reasoning-only ANALYSIS run and one action-producing CODING run whose model-proposed action executes through SpecialistRuntime into a harmless deterministic fixture tool. Budget integrity is asserted in the same tests (model calls and actions count against specialist budgets; cost lands on the delegation row). Deterministic failure counterparts (unavailable provider, malformed output, timeout, budget exhaustion) run ungated in `test_phase975_provider_failures.py`.

Known limitation (documented, not fabricated): the external provider call itself cannot be deterministically crash-tested (an HTTP request to an external service has no killable local process boundary); process-kill recovery therefore covers admission and kernel execution, while provider outages are covered by the deterministic failure suite plus Phase-6 bounded fallback.

---

# Phase 10 — Multi-Specialist Coordination & Controlled Parallelism

## 26. Multi-specialist hierarchy

```
Persistent Main Agent
        |
  DelegationPlan (durable, owns specialist delegations + dependency graph)
        |
   +----+----+----+
   |    |    |
   A    B    C   (Specialist Agents, each bounded, isolated)
   |    |    |
   result graph (VERIFIED dependencies)
        |
 Result Aggregator (conflicts, evidence-weighted confidence)
        |
   Main Agent (strategic authority)
```

## 27. Execution isolation

- Each specialist Task has independent cancellation, budget, execution context (ContextVar), event sequence, result, recovery state.
- SpecialistExecutionSlot provides per-specialist ownership under the profile/Orchestrator lease without removing the Runtime lease. Profile lease remains authoritative; slots are child ownership.
- Shared parent context: required parent Goal context + own task memory + VERIFIED dependency results + explicitly permitted USER/GLOBAL memory. Sibling private memory never copied.

## 28. Result dependencies & aggregation

- Dependency edge requires VERIFIED predecessor result; unverified never unlocks.
- `ResultAggregator.detect_conflicts` reports CONFLICT with both sources/evidence; does not auto-resolve by recency.
- Aggregation confidence weighted by evidence quality, not vote count.

## 29. Capability-specific concurrency

- `capability_is_concurrent_safe()` gates per stage. GUI (`gui_automation`) always serialized (MTA).
- Stage 0 serialized default; Stage 1 reasoning-only parallel; Stage 2 non-destructive parallel; all gated by `parallelism_enabled`.

See `MULTI_AGENT_COORDINATION_ARCHITECTURE.md`.
