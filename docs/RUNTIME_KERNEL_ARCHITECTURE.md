# Runtime Kernel Architecture

**Status:** Active (Phase 1). Companion to `PROTECTED_RUNTIME_CONTRACT.md`,
which remains authoritative for invariants. This document describes the
kernel's structure: responsibilities, interfaces, ownership boundaries, and
the dependency direction every component must follow.

> **The runtime kernel is the final authority over execution.**
>
> **Intelligence can propose actions; the runtime decides whether and how
> they execute.**

---

## Dependency direction

```text
Interfaces / UI            (frontend, Tauri shell, CLI)
      ↓
Intelligence               (ModelDecisionEngine, prompt contract, skills,
      ↓                     memory consolidation — proposers only)
Runtime Interfaces         (backend/runtime/interfaces.py Protocols)
      ↓
Protected Runtime          (TaskRunner, DbEventLedger, LeaseManager,
      ↓                     ToolExecutor + policy/verification, budgets)
Operating System           (tools performing bounded OS actions)
```

Never the reverse. The LLM never touches the database, task state, audit
history, permissions, verification outcomes, or runtime configuration.

## Kernel components

| Responsibility | Interface / Implementation | Owner of |
|---|---|---|
| Task lifecycle state | `TaskStateStore` Protocol → `services.task_runner.TaskRunner` | `tasks.state`, mutable task columns; validates transitions (`ALLOWED_TRANSITIONS`) |
| Event ledger | `EventLedger` Protocol → `runtime.events.DbEventLedger` | Immutable `task_events`; per-task strict `sequence_num` ordering |
| Lease | `LeaseManager` Protocol → `runtime.leases.LeaseManager` | Exclusive single-runner execution; stale takeover marks tasks interrupted |
| Budgets | `core.budgets.TaskBudget` wrapped by `BudgetGuard` | Per-task action/request/token/time limits (single authority: `settings.max_actions_per_task`) |
| Policy gate | `ToolExecutor._policy_error` + `importance_classifier` | Whether an action may run at all |
| Tool execution | `tools.executor.ToolExecutor.execute_tool` | policy → schema → targets → cancel-check → execute → verify pipeline |
| Verification | Per-tool `verify()` + `CompletionVerifier` | Evidence-gated completion; result enrichment |
| Cancellation | `CancellationController` + execution-scoped `PolicyContext` | Task-scoped cancellation checks installed via ContextVar |
| Audit | `EventLedger` appends through state transitions | Durable ordered history incl. budget snapshots, approvals, guards |
| Model access | `ModelClient` Protocol → `adapters.model_client.OllamaCloudModelClient` | The only place that knows the concrete provider |

## Loop orchestration

`services.task_execution_loop.TaskExecutionLoop` is an orchestrator over
narrow components (`services.execution_components`):

- `BudgetGuard` — budget enforcement points
- `CancellationController` — builds task-scoped `PolicyContext`s
- `ExecutionGuard` — repeat-failure / spin no-progress guards
- `ActionExecutor` role — decision dispatch remains in the loop's
  `_execute_decision` (single dispatch point)
- `CompletionVerifier` — completion preconditions + result validation
- `PostTaskPipeline` — best-effort post-terminal hooks

## State ownership

- Only `TaskRunner` writes `tasks.state` and mutable task columns
  (`pending_action` included, via `clear_pending_action`).
- Objective/plan-step projections are written by `services/work_state.py`
  inside runner-invoked sync hooks; they are projections, not execution truth.
- Skills/proposals/memory stores own their own tables; none write task state.

## Event ownership

The immutable ledger stays the single execution record. Appends participate
in the transition transaction (state change + event atomic); reads are served
through `DbEventLedger`. No queues or in-memory buses are sources of truth.

## Execution-context ownership

`PolicyContext` (execution context) is created fresh per tool execution by
the `CancellationController`, carries `task_id`, direct-instruction flag,
and that task's cancellation check, and is installed around the call via a
ContextVar (`executor.execution_context`). Tool instances hold no per-task
state; concurrent executions cannot observe each other's context.

## What intelligence may request

Intelligence may, through the interfaces above:

- create tasks, propose decisions/tool calls through the engine
- read event history (read-only) for context building
- request memory/skill/proposal operations on their own stores
- ask for capability information (`system_capabilities` surface)

## What intelligence may never bypass

- the authentication boundary on any API path
- executor policy, target validation, cancellation check, verification
- `TaskRunner` ownership of task state and transitions
- the ledger as the sole execution record
- the secret store (secrets never enter model context or logs)
- lease exclusivity for mutating work

Violations to watch for in review: direct `tasks.state` writes outside the
runner, raw SQL/event inserts outside the ledger, tools reading global state,
and reasoning code importing concrete providers.
