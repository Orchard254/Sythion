# Specialist Reasoning Architecture (Phase 9.5)

## 1. Purpose

Phase 9 established the **structure** of delegation; Phase 9.5 makes the
specialist a **real bounded worker** that performs genuine cognitive work
through a model, not a template. The specialist never becomes an
unrestricted agent: every step is bounded, validated, and auditable.

## 2. SpecialistContext (bounded input)

`backend/agents/specialist.py:SpecialistContextPacket`

```python
SpecialistContextPacket(
    objective: str,                     # the bounded mission
    specialist_agent_id: str,
    role: SpecialistRole,               # RESEARCH|ANALYSIS|CODING|REVIEW|PLANNING_SUPPORT
    required_capabilities: List[str],   # role binding subset
    model_requirements: Dict,           # e.g. {"task_class": "simple_reasoning"} (never a concrete model id)
    constraints: Dict,                  # caller narrowing, memory_access grants, new_constraints on revise
    expected_output: str,               # output contract (≤500 chars)
    budget: {steps, time_seconds, model_calls},
    parent_goal_summary: Optional[str], # title only, bounded
    relevant_memory: List[Dict],        # top-5 scored memories, scoped read
)
```

The packet is ≤ ~3k JSON, stored on `delegations.context_packet_json`.
It never copies the Main Agent's full context, goal graph, or secrets.

## 3. SpecialistReasoner (bounded cognitive loop)

`backend/agents/reasoner.py:SpecialistReasoner`

```
Delegation → ContextPacket → ModelSelector → SpecialistReasoner → Candidate Work → SpecialistRuntime (kernel) → Evidence → SpecialistResult
```

The reasoner is a **dedicated reasoning interface**, not a second full cognitive architecture:

- `analyze(context) → {findings, analysis, limitations, confidence, proposed_actions, repaired, fallback}`
- `propose_actions(...)` is implicit in `analyze`
- `validate_proposal(proposal, runtime) → (ok, reason)` enforces the four gates before any tool runs

It uses:

- `ModelSelector` (deterministic evidence-based selection over `task_class`)
- `ModelProvider` (`backend/runtime/model_provider.py:ModelProvider` protocol)
- the role's `SpecialistTaskSpec` (purpose, contracts, allowed capabilities, maxima)
- the delegation's `budget` and `allowed_tools`
- `ScopedMemoryAccess` (working memory only)

It must **not** import:

- `OllamaCloudAdapter`
- raw adapter singletons
- low-level runtime state (`runner_lease`, `Task` internals beyond the ledger)

### 3.1 Role task specs

`backend/agents/reasoner.py:_role_spec`

| role | purpose | input contract | output contract | allowed capabilities | max actions | max model_calls | mode |
|---|---|---|---|---|---|---|---|
| `RESEARCH` | investigate open-ended questions | `objective`+`constraints` | `findings`+`limitations` | `system_observation`, `activity_recall` | 3 | 5 | reasoning-only |
| `ANALYSIS` | compare/evaluate trade-offs | options + criteria | comparative assessment | `system_observation` | 2 | 5 | reasoning-only |
| `CODING` | implement/fix/inspect files | coding task + acceptance | changes/observations + evidence | `file_operations` | 5 | 5 | action-producing |
| `REVIEW` | critique artifact/plan | artifact + rubric | issues + guidance | `system_observation`, `activity_recall` | 2 | 5 | reasoning-only |
| `PLANNING_SUPPORT` | decompose goals | goal + policy | steps+dependencies | `system_observation` | 2 | 5 | reasoning-only |

A Coding specialist cannot gain `browser_control` because the model asked for it; a Research specialist cannot gain `file_write`. Delegation may *narrow* the set (via `constraints.required_capabilities`), never broaden beyond `ROLE_CAPABILITIES[role]`.

### 3.2 Prompt and output contract

For reasoning-only roles:

```json
{
  "findings": ["..."],
  "analysis": "...",
  "limitations": ["..."],
  "confidence": 0.0,
  "recommended_next_action": "..."
}
```

For action-producing roles:

```json
{
  "findings": ["..."],
  "proposed_actions": [
    {"capability": "file_operations", "tool": "file_create", "purpose": "create report", "input": {"path": "x.txt", "content": "..."}}
  ],
  "analysis": "...",
  "limitations": ["..."],
  "confidence": 0.0
}
```

Proposals are **not authorization**; each is validated before it reaches the kernel.

### 3.3 Bounded retry

- Malformed JSON → one repair attempt (re-prompt with "STRICT JSON only").
- Transient provider failure → Phase 6 `ModelSelector.next_fallback`.
- All attempts consume `max_specialist_model_calls` and wall time.

No unlimited loops; budgets are checked before every model call.

## 4. Action proposals → Runtime requests

```
Model JSON proposed_actions
        ↓
validate_proposal
  ├─ capability in ROLE_CAPABILITIES[role]? else drop
  ├─ tool in resolve_allowed_tools(required_capabilities, executor)? else drop
  ├─ tool registered? else drop (unavailable → capability becomes unavailable)
  ├─ budget steps/time remaining? else BUDGET_EXCEEDED
  ├─ cancellation? else CANCELLED
  └─ target validation (Tool.validate_targets) deferred to executor
        ↓
SpecialistRuntime.request_action(tool, input)
  ├─ capability binding check → CapabilityViolation
  ├─ budget enforcement → BudgetExceeded
  ├─ ToolExecutor.execute_tool(..., PolicyContext(direct_user_instruction=False))
  │     → Policy → TargetValidation → Cancellation → Timing → Verify
  └─ ledger event `specialist.action` {seq, tool, success, error_code}
```

A specialist cannot request a tool outside its resolved set, cannot bypass `direct_user_instruction=False` (so `file_write`/`file_delete` still need confirmation and are denied), and cannot forge the `seq`.

## 5. Zero-action (reasoning-only) work

`RESEARCH`/`ANALYSIS`/`REVIEW`/`PLANNING_SUPPORT` may produce findings with no tool calls:

```
Specialist Model → Structured Findings → Evidence (specialist.reasoning + delegation.context.built) → Result validation
```

The result must be **model-derived**, not a static template. Template defaults may supply empty optional fields (`recommended_next_action`), never substantive `findings`. Substantive findings are required (≥10 chars, ≥2 words, not in the template deny-list). A fallback is used when the model is unavailable, but it is marked as `Deterministic fallback: verify against source data`.

## 6. Evidence

Every claimed `evidence_ref` must correspond to a real ledger event:

- `action:N` → `specialist.action` on `specialist_task_id` with `seq=N`
- `context_packet` → `delegation.context.built` on `specialist_task_id`
- `specialist.reasoning` → `specialist.reasoning` or `specialist.model_call` on `specialist_task_id`
- `delegation.context.built` → same as `context_packet` (alias)
- `runtime.action` → `specialist.action` (alias)
- `specialist.action:N` → `specialist.action` with `seq=N` (alias)

`DelegationService.verify()` inspects `ledger.event_history(specialist_task_id)`; an invented `action:99` or missing `context_packet` fails verification and the delegation stays `RESULT_READY` (never `VERIFIED`). The specialist cannot invent IDs and cannot call `verify` itself (only `DelegationService` can transition to `VERIFIED`).

## 7. Result verification and confidence

`backend/agents/delegation.py:SpecialistResult.validation_errors()` enforces:

- `status` ∈ {`COMPLETED`,`FAILED`}, `summary` non-empty
- `findings` substantive (anti-template), `evidence_refs` non-empty for `COMPLETED`
- `confidence` ∈ [0,1] for `COMPLETED`; `reasoning_confidence` and `evidence_confidence` are separate fields
- `limitations` explicit (at least one)
- `cost` is a dict, `failure_reason`+`failure_code` for `FAILED`
- `evidence_refs` format and `failure_code` vocabulary (`MODEL_FAILURE` etc.)

`verify()` then checks:

1. structural shape,
2. every `action:N` has a matching `specialist.action`,
3. `context_packet`/`specialist.reasoning` has a matching context/reasoning event,
4. substantive findings rule.

Only then → `VERIFIED`. A successful result with no substantive content never becomes `VERIFIED`.

Confidence semantics (Requirement 17):

- `reasoning_confidence` = model self-assessment (may be `None` if the model omitted it)
- `evidence_confidence` = fraction of successful actions (`ok/total`)
- `confidence` = evidence-backed result confidence (`evidence_confidence` when actions exist, else `reasoning_confidence`)

Model self-confidence is never proof; unknown stays `None` and the Main Agent receives `limitations` alongside `confidence`.

## 8. Revision

`DelegationService.integrate(decision="REVISE", feedback={problem, missing_information, required_correction, new_constraints})`

- Bounded by `max_specialist_revisions` (default 1); further `REVISE` → `REJECT` → `CANCELLED`.
- New constraints are merged into `constraints_json`, the correction hint into `expected_output`, and the whole feedback is recorded in `agent.progress` (`stage=revision_requested`).
- The next `validate → assign → run` cycle rebuilds the context packet with the updated constraints; the specialist receives the feedback and produces a new bounded result.

No recursive specialist debates; `REVISE` re-enters `VALIDATED` exactly once.

## 9. Budgets

All caps are the single policy source (`backend/core/config.py`):

```
max_specialist_steps, max_specialist_time_seconds,
max_specialist_model_calls, max_specialist_revisions, specialist_ttl_seconds
```

`SpecialistRuntime` enforces `steps/time/model_calls` per execution; `DelegationService.run` enforces `steps` pre-check; `SpecialistReasoner` enforces `model_calls` before every `chat`. Costs are recorded on both the delegation and the parent task ledger (`specialist.cost.incurred`). All attempts (including retries) consume the budget.

## 10. Failure taxonomy

```
MODEL_FAILURE, INVALID_SPECIALIST_OUTPUT, CAPABILITY_DENIED,
RUNTIME_FAILURE, VERIFICATION_FAILURE, TIMEOUT, BUDGET_EXCEEDED, CANCELLED
```

Each maps to a `failure_code` on `SpecialistResult(FAILED)` and a `task.failed` event with `code`. A specialist failure is never converted into `COMPLETED`; the parent `Goal`/`Task` is not failed, and the Main Agent may `REVISE`, `CONTINUE_DIRECTLY`, or `ASK_USER`.

## 11. Isolation and recovery

- `SpecialistRuntime` is per-delegation (`agent_id`, `delegation_id`, `task_id`, `allowed_tools`, `ledger`, `actions_used`); no shared mutable `ContextVar` beyond the isolated `execution_context` per tool call.
- `ScopedMemoryAccess` is per-delegation (`TASK` scope with `spec:{agent_id}:{key}` keys; `USER`/`GLOBAL` only when `constraints.memory_access` grants them, otherwise invisible).
- Interleaving two specialists on the same in-memory DB proves budgets, memory, and ledger events do not leak.

Recovery (`DelegationService.recover_delegations`) reuses the existing lease philosophy:

- `completed` → `RESULT_READY` (rebuilt from `specialist.action` + `specialist.reasoning` trail)
- `failed` → `FAILED`, `cancelled/interrupted` → `ASSIGNED`, `missing` → `ASSIGNED`, `created/running` → `deferred`
- No duplicate `specialist_task_id` (key `delegation:{id}:{attempt}`), no forged result, no corrupted parent.

See `backend/tests/test_phase95_hardening.py` and `backend/tests/test_phase9_process_kill.py` for the recovery tests.
