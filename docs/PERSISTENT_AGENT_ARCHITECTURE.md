# Persistent Agent Architecture

**Status:** Active (Phase 4). Builds on `PROTECTED_RUNTIME_CONTRACT.md`,
`RUNTIME_KERNEL_ARCHITECTURE.md`, `GOAL_PLANNING_ARCHITECTURE.md`, and
`WORK_ORCHESTRATION_ARCHITECTURE.md` — all of which remain authoritative.

> **The Main Agent owns continuity and strategic attention.**
> **The Orchestrator owns when work may be resumed.**
> **The Runtime Kernel remains the final authority over execution.**

---

## Main Agent identity

- Persisted singleton row (`main_agents`, id=1) minted once: stable
  `agent_id` (UUID), name, lifecycle state, last-wake metadata.
- Survives process restarts, model changes, task/goal turnover, provider
  outages. Changing the selected model NEVER creates a new identity.
- `SHUTDOWN` is not sticky: the next boot deterministically transitions a
  shutdown agent back to ACTIVE (`agent.started` event).

## Lifecycle states (separate domain from Goal/Task state)

```text
ACTIVE ⇄ PAUSED        (owner actions; paused ⇒ no new background work)
ACTIVE → DEGRADED      (reserved: partial-capability operation)
ACTIVE → SHUTDOWN      (process exit; resets to ACTIVE on next boot)
```

Agent state never mutates Goal or Task rows; those domains have their own
owners (GoalManager / TaskRunner).

## Background policy

- Operator switch: persisted non-secret setting
  `background_orchestration_enabled` (settings_store whitelist; default
  **OFF**). Toggled via `PUT /api/v1/agent/background`.
- The wake worker is always running so toggles apply live; it can only
  EXECUTE when the gate `MainAgentService.may_execute_background()` passes
  (policy enabled AND agent ACTIVE). Disallowed wakes are recorded as
  auditable `agent.wake.skipped` events — silence is never silent.

## Wake/event semantics

Event-driven, never timer-driven. Triggers implemented:

| Trigger | Source | Durable wake_key |
|---|---|---|
| application.startup | lifespan, once per boot | `application.startup:<uuid>` |
| task.completed / task.failed | post-task pipeline hook | `task.<state>:<task_id>` |
| user.request | `POST /agent/wake` (optional caller key) | caller key or uuid |
| goal.resumed *(planned hook)* | resume path | — |

### Durable wake state machine (Phase 4.5)

The DB row is written FIRST (status=PENDING) at submission time — the
in-memory queue is only a delivery optimization and can never lose work:

```text
event → PERSIST PENDING → queue (opt.) → CLAIM (PENDING→RUNNING, atomic)
      → COMPLETED | SKIPPED | FAILED
      → coalesced duplicates: COMPLETED(outcome=coalesced, merged_into=<id>)
stale RUNNING after worker/process death → reclaimed to PENDING (≤5 attempts,
audited agent.wake.reclaimed) else FAILED(reclaim_limit)
```

- **Deduplication**: `wake_key` UNIQUE — before/during/after execution and
  across restarts, only one logical execution is possible.
- **Coalescing**: triggers in `{task.completed, task.failed, goal.resumed,
  goal.updated}` share `coalesce_key='background_work'`; when one is claimed,
  other PENDING wakes of the same class are absorbed (`merged_into` pointer +
  `agent.wake.coalesced` event). Safe because every session re-evaluates ALL
  runnable goals at execution time. `user.request` and `application.startup`
  are never coalesced.
- **Fairness backoff**: goals track `consecutive_failed_sessions`; at
  threshold (default 2) they enter a persisted, bounded exponential backoff
  (`backoff_until`, capped at 1 h) that excludes them from BACKGROUND
  selection only; any completed step clears it; owner override via
  `GoalManager.clear_backoff`. Events: `goal.backoff.applied/.cleared`.
- **Service accounting**: `last_served_at` updates ONLY when an execution
  slot actually started a step — selection alone never counts as service.

## Execution-session boundaries

A background session = one `TaskOrchestrator.run_background_cycle()` call:
at most `max_steps_per_background_cycle` (default 5) steps across eligible
goals, each step a full protected-runtime execution (lease, budgets,
policy, verification). Deterministic stop reasons: `no_runnable_goal`,
`step_budget_reached`, `lease_unavailable`,
`step_retry_exhausted_or_permanent_failure`, `goal_became_ineligible`.
Sessions never self-perpetuate: a new session requires a NEW event.

## Goal selection & fairness

`services/goal_selector.RunnableGoalSelector` is the single structural
eligibility implementation (shared by API `/goals/runnable` and the agent):

1. ACTIVE goal + ACTIVE plan + READY step (dependencies refreshed first);
2. priority ascending;
3. same-priority fairness: least-recently-served first
   (`objectives.last_served_at`, NULL = oldest), then created_at.

Equal-priority goals therefore alternate (tested); differing priorities are
intentional precedence, not starvation. `explain_ineligibility()` reports
deterministic reasons; no LLM participates in structural eligibility.

## Multiple active goals

Persistently supported; runtime concurrency is NOT. Many active goals feed
one bounded session at a time through the profile-wide lease. Per-goal
leases remain a future swap behind the LeaseManager Protocol.

## Shutdown / restart

Shutdown: wake worker cancelled first (no new sessions), agent state
persisted as SHUTDOWN; unfinished tasks/steps are left exactly as they are —
the existing lease-expiry/interruption/recovery machinery stays authoritative
(Phase 3 startup recovery resolves them on next boot). Startup order:
runtime init → recovery → legacy migration → agent init → worker start →
one bounded startup wake IF policy enabled (default OFF).

## Main Agent vs LLM

```text
Main Agent (identity, goals, wake policy, fairness, orchestration state)
        │
        ▼
     ModelClient          ← reasoning requests only
        │
        ▼
       LLM                ← replaceable; never the source of truth
```

The agent's decisions in this phase (eligibility, ordering, waking) are
entirely deterministic application logic.

## Self-model foundation

`GET /agent` returns a deterministic snapshot assembled from runtime data:
tools installed (executor), selected model, policies, budget limits, active
goal count, last wake/session outcome. No LLM-generated inventory is
authoritative.

## Future scheduler boundary

A future scheduler need only call `RunnableGoalSelector.list_runnable_goals()`
and submit wakes through the same audited queue/policy gate. No kernel or
orchestrator changes required.
