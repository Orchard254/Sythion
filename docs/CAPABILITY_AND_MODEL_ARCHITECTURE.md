# Capability & Model Architecture

**Status:** Active (Phase 5). Builds on the Protected Runtime Contract, the
Runtime Kernel, Goal/Planning, Work Orchestration, and Persistent Agent
documents — all remain authoritative. This phase adds the *descriptive*
foundation future intelligence reasons with.

> **The registries describe. The Runtime decides.**
> A capability saying "requires permission" authorizes nothing; a model
> record claiming nothing is the honest state until evaluation exists.

---

## Core distinction: Capability vs Tool

- **Capability** — what Sythion can accomplish (e.g. `browser_control`,
  `file_operations`, `gui_automation`). Stable IDs agents reason about.
- **Tool** — one concrete mechanism implementing part of a capability
  (e.g. `browser_tabs`, `file_write`). Owned by the existing ToolExecutor
  registry; capabilities reference tools by name, never reimplement them.

```text
CapabilityRegistry ──references──► ToolExecutor registry ──► Runtime Kernel
(describe)                          (concrete mechanisms)     (authorize+execute)
```

## Capability model

`services/capabilities.py`:

- `CapabilitySpec` — id, name, category, description, tool_names, risk_class,
  requires_permission (informational), requires_external_service,
  requires_configuration, environments, deterministic/stateful flags.
- `CapabilityRegistry.list/get/capability_available/validate`.
- **Statuses** (deterministic, derived from runtime facts only):
  `AVAILABLE | UNAVAILABLE | DISABLED | DEGRADED |
   REQUIRES_PERMISSION | REQUIRES_CONFIGURATION`.
  Example: browser extension disconnected ⇒ `browser_control = UNAVAILABLE`
  — never "probably available". The LLM has no write path here.
- **Consistency**: `validate()` reports MISSING_TOOL / DUPLICATE_ID issues;
  inconsistencies are surfaced (health DEGRADED), never silently repaired.

## Permission boundary

The registry may say `requires_permission = true, risk = HIGH`. Actual
authorization stays exclusively inside the protected runtime (executor
policy gate → importance classifier → approval flow). Tested: a HIGH-risk
capability being "available" does not let an execution bypass policy.

## Model provider abstraction

```text
ModelProvider (Protocol, runtime/model_provider.py)
    provider_id / selected_model_id / list_models / health / chat
        ▲
OllamaCloudProvider (adapters/ollama_adapter.py — sole implementation)
```

All Ollama-specific knowledge (endpoints, request fields, retry semantics,
capability probing) lives in the adapter/facade pair. Verified by source-scan:
Planner, MainAgent, selector, orchestrator, self_model contain no Ollama or
adapter references. No routing; no fallback; no second provider.

### Model Registry & truthfulness

`services/system_state.ModelRegistry`:

- Records carry `model_id, provider_id, selected, available_now,
  tool_calling_supported, health_status`.
- **Truthfulness contract**: `evaluated=False` everywhere; `context_tokens=None`
  (unmeasured); `quality_claims="none"`. Name-based quality inference is
  forbidden until an evaluation system exists.
- Provider outage yields UNAVAILABLE records — agent identity and goal rows
  are never corrupted by model failure (`selected_available_now=false` is
  just a fact the Main Agent can see).

`ModelSelector` boundary reserved: routing later slots between requirement
declaration and this registry without touching Planner/Main Agent.

## SelfModel

`services/self_model.build_self_model(db)` composes, deterministically:

| Section | Source |
|---|---|
| Identity | MainAgentService (stable agent_id), backend version, architecture version |
| Runtime state | agent state, background policy, last session, lease/current task |
| Capabilities | CapabilityRegistry summary (available/unavailable/requires_permission/configuration) |
| Models | provider id/health, selected model + availability, registered count, truthfulness marker |
| Resources | resource_snapshot (platform, disk free if known, budgets, lease/goal counts) |
| Health | run_health_checks summary |

No write path from any LLM output exists (source-scanned).

## ResourceState

`resource_snapshot()`: platform/python version, data-dir disk-free when
obtainable, configured budgets, lease-held/active-goal counts. Unknowns stay
`None` — cpu/memory percentages are deliberately not collected yet.

## Health framework

`run_health_checks(include_provider=False)`: database, main_agent,
capability_consistency, configuration (+ opt-in remote provider probe).
Overall status aggregation HEALTHY/DEGRADED/UNAVAILABLE/UNKNOWN. Exposed at
`GET /agent/health`; capability introspection at
`GET /capabilities[/{id}]`; model records at `/models/registry[/{id}]`;
full self-model at `GET /agent/self-model`. No secrets ever appear in these
responses (write-only secret store untouched).

## Persistence

Nothing new persisted in this phase beyond additive columns from earlier
phases: current capability status is always *derived* from authoritative
runtime sources at call time; health results are response payloads, not
tables (historical retention can be added later without schema change).

## Future boundaries

- **Routing**: insert ModelSelector between requirements and ModelRegistry.
- **Specialist agents**: request "capabilities X,Y + model M + permissions P"
  against the registry for deterministic availability answers — spawning
  itself remains deferred.
- **Evaluation**: when built, it flips `evaluated=True` and fills measured
  fields; nothing else changes.
