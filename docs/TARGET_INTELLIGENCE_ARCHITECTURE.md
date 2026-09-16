# Target Intelligence Architecture (DOCUMENT ONLY — NOT IMPLEMENTED)

**Status:** Design direction. Nothing in this document is implemented in the
current codebase. Phase 0 delivered only the protected runtime contract
(`PROTECTED_RUNTIME_CONTRACT.md`). This document defines the layering future
phases must follow so intelligence can grow *above* the runtime without
weakening it.

---

## Layering

```text
User / Interfaces
        ↓
Personal Main Agent
        ↓
Goals / Tasks / Priorities / Dependencies
        ↓
Cognitive Layer
  ├── Understanding
  ├── Planning
  ├── Decision Making
  ├── Simulation
  └── Self Model
        ↓
Agent Orchestration
  ├── Persistent Main Agent
  ├── Specialist Agents
  └── Temporary Agents
        ↓
Capability Layer
  ├── Web Research
  ├── Computer Control
  ├── Communication
  ├── File/System Operations
  └── Future Device Capabilities
        ↓
PROTECTED RUNTIME          ← docs/PROTECTED_RUNTIME_CONTRACT.md (exists today)
        ↓
Observation / Verification
        ↓
Memory
        ↓
Learning / Evaluation
```

## Core concepts

- **Main Agent** — the persistent authority over goals, prioritization,
  delegation, and final decisions presented to the user. It answers to the
  user, owns the goal graph, and may create or dismiss specialists.
- **Specialist Agent** — a bounded worker responsible for a defined
  capability/domain (e.g. research, file operations). It acts only within its
  delegated scope and returns results to its delegator.
- **Temporary Agent** — a short-lived specialist created for a single task
  and destroyed afterwards; its traces remain in the audit ledger.
- **Tool** — a concrete capability that performs one external action with
  typed inputs/outputs (today's `BaseTool` system).
- **Runtime** — the final authority over whether an action may execute. It
  owns policy, validation, cancellation, verification, state transitions,
  secrets, and audit. No agent, model, or plan overrides it.
- **Memory** — persistent information and experience (facts, preferences,
  episodes, procedures) with explicit provenance and confidence.
- **World State** — the current observable state of the environment, obtained
  only through runtime observation tools; never assumed from memory.
- **Model** — a replaceable reasoning component behind an interface. Models
  are chosen by capability/cost routing, never by hard-coded assumption.

## Non-negotiable statement

> **The LLM is a reasoning component, not the source of truth for runtime
> state, permissions, memory integrity, task completion, or security.**

Every claim a model makes is validated against durable evidence by the
runtime before it affects state, reaches the user as fact, or counts toward
completion.

## Dependency rule

Future intelligence layers must depend on **runtime interfaces**, not on
low-level execution state. Concretely:

- Cognitive/orchestration layers request actions through runtime APIs (task
  submission, tool execution via `ToolExecutor`, event queries). They never
  write `tasks.state`, mutate ledger rows directly, reach into tool
  internals, or read/write secrets.
- All authority relationships point downward: orchestration delegates into
  capabilities; capabilities execute through the protected runtime; nothing
  bypasses the runtime upward.
- Any new privileged pathway (e.g. out-of-process custom-tool execution)
  must extend the runtime contract explicitly and ship with enforcement code
  and tests before intelligence layers may use it.

## Relationship to the current implementation

- The existing FastAPI sidecar, task/event ledger, lease, executor pipeline,
  budget system, and skill/proposal lifecycle constitute the PROTECTED
  RUNTIME layer and are preserved.
- **Phase 2 delivered the Goals/Tasks/Priorities layer**: a GoalManager
  (deterministic lifecycle), a Planner (validated versioned plans with
  dependency-aware steps, priority selection, replanning) and a
  TaskOrchestrator bridging plan steps to runtime tasks — see
  `GOAL_PLANNING_ARCHITECTURE.md`. The Cognitive Layer's LLM-planner role is
  limited to *proposing* candidate plans through the ModelClient boundary;
  validation and persistence are deterministic.
- **Phase 3 made that orchestration durable**: crash recovery at startup,
  bounded persisted retries, failure taxonomy, pause/resume protection, and
  explicitly initiated automatic continuation — see
  `WORK_ORCHESTRATION_ARCHITECTURE.md`. The future Scheduler need only
  consume the read-only runnable-goal query; the kernel remains untouched.
- **Phase 4 introduced the Persistent Main Agent + Event-Driven
  Orchestrator** at the top of the stack: a durable agent identity with its
  own lifecycle (separate from Goal/Task state), an explicit background
  policy (default OFF), durably deduplicated audited wakes, and fair,
  bounded background sessions across multiple active goals — see
  `PERSISTENT_AGENT_ARCHITECTURE.md`. The target layering is now:

```text
Persistent Main Agent
        ↓
Event-Driven Orchestrator
        ↓
Goals / Planner
        ↓
Runtime Kernel
```
- **Phase 5 added the descriptive foundation** the cognitive layer will
  reason with: a deterministic Capability Registry, a provider-neutral Model
  Registry + boundary, ResourceState, a Health framework, and the structured
  SelfModel — see `CAPABILITY_AND_MODEL_ARCHITECTURE.md`.
- **Phase 6 added evidence-based model selection and bounded fallback**: a
  deterministic ModelSelector over evaluation records with
  capability/availability filtering and audited rationale — see
  `MODEL_SELECTION_ARCHITECTURE.md`.
- **Phase 8 established the cognitive layer**: Intent understanding →
  CognitiveState → ReasoningEngine (structured artifacts, no CoT) →
  deterministic DecisionSystem → Goal/Plan/Action proposals — see
  `COGNITIVE_ARCHITECTURE.md`. The Main Agent now carries:
```text
Input
  ↓
Cognitive Layer (Intent / Context / Reasoning / Decision / Ambiguity)
  ↓
Goal / Task / Answer
  ↓
Protected Runtime
  ↓
Observation
  ↓
Memory / Learning Evidence
```
- **Phase 8.5 wired this layer into the live request path** (POST /tasks,
  reply flow, voice transport) with a durable clarification lifecycle —
  see `COGNITIVE_RUNTIME_INTEGRATION.md`.
- Today's single execution loop is the Worker beneath that layer; it remains
  the Specialist-Agent execution path candidate for later phases.
- **Phase 9 delivered bounded specialist delegation**: a first real
   multi-agent architecture with the strict hierarchy
   `Persistent Main Agent → Cognitive Layer (DELEGATE decision) → Specialist
   Agent (Research/Analysis/Coding/Review/Planning) → Protected Runtime →
   Verified Result → Main-Agent Integration`. The delegation decision,
   eligibility signals, structured `DelegationRequest`/`SpecialistResult`
   contracts, capability-bound `SpecialistRuntime` (every action through the
   kernel under the profile lease), scoped memory, cost-inclusive budgets,
   evidence-verified integration, and lease-reused recovery are documented in
   `AGENT_ARCHITECTURE.md` and `DELEGATION_ARCHITECTURE.md`. The live flow is:

```text
Persistent Main Agent
        │
   Cognitive Layer
        │
    DELEGATE
        │
        ▼
 Specialist Agent
        │
        ▼
 Protected Runtime
        │
        ▼
 Verified Result
        │
        ▼
 Main Agent
```

- **Phase 10 delivers multi-specialist coordination**: DelegationPlan with dependency graph, atomic multi-delegation admission, controlled parallelism (staged), per-specialist isolated execution slots, hierarchical cancellation, VERIFIED result dependencies, ResultAggregator with conflict detection and evidence-weighted confidence, shared plan budgets, and Main-Agent integration — see `MULTI_AGENT_COORDINATION_ARCHITECTURE.md`. The flow becomes:

```text
Main Agent
   |
   v
Delegation Plan
   |
   +-- Specialist A
   +-- Specialist B
   +-- Specialist C
          |
          v
    Result Aggregator
          |
          v
      Main Agent
```

- **Phase 11 delivers controlled learning**: every verified execution becomes a
  `StrategyOutcome` (task_class, strategy, role, model, outcome, cost, latency,
  evidence_refs) in a separate learning subsystem (never user memory). A
  deterministic `StrategySelector` scores strategies cost-aware against
  historical evidence (small-sample protection, decay, contradiction preservation)
  but never overrides Runtime authority. The Main-Agent → Cognitive →
  Delegation → ModelSelection chain now carries `Execution → Outcome →
  Learning Evidence → Strategy Evaluation → StrategySelector → Future Decision`
  with exploration safely bounded and high-risk exploration disabled. See
  `LEARNING_ARCHITECTURE.md` and `STRATEGY_SELECTION_ARCHITECTURE.md`. The flow is:

```text
Execution
   ↓
Outcome
   ↓
Learning Evidence
   ↓
Strategy Evaluation
   ↓
StrategySelector
   ↓
Future Decision
```

- **Phase 12 extends learning to planning policy** (§8-22 of PLANNING_LEARNING_ARCHITECTURE.md): verified planning lifecycles become `PlanningOutcome`s (planning_strategy, retries, replans, verified_success, replan_trigger, retry_vs_replan). A deterministic `PlanningPolicySelector` evaluates retry-vs-replan and replan-trigger effectiveness with the same small-sample protection, but remains advisory — real tasks continue under the deterministic `DEFAULT_RETRY_REPLAN_POLICY` while `planning_learning_mode=LEARNING_ONLY`. Learned recommendations are observed in shadow (`planning_shadow_decisions`) and as counterfactual hypotheses (never fabricating outcomes), and may only control production planning after the conservative promotion gate `OBSERVED→SUPPORTED→SHADOW_VALIDATED→OWNER_APPROVED→ACTIVE`, with full rollback. The extended flow is:

```text
Planning
   ↓
Execution Outcome
   ↓
Learning Evidence
   ↓
Planning Policy Evaluation
   ↓
Shadow Recommendation
   ↓
Promotion / Rollback
   ↓
Future Planning
```

- Nothing in this document authorizes implementing *unbounded* multi-agent
    behavior, recursive specialist spawning, unrestricted parallel execution,
    autonomous scheduling beyond the current policy-gated path, or learned
    delegation policies beyond the bounded empirical layer; each arrives in its own phase with its own tests.
