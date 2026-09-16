# Work Orchestration Architecture

**Status:** Active (Phase 3). Extends `GOAL_PLANNING_ARCHITECTURE.md` (Phase
2) with durable orchestration: crash recovery, bounded retries, failure
taxonomy, pause/resume semantics, and explicitly initiated automatic
continuation. All Phase 0/1/2 runtime contracts remain authoritative and
unchanged.

> The Runtime Kernel remains the final authority over execution.
> The orchestrator decides **what work to attempt next**; it can never make
> failed or interrupted work look successful.

---

## Goal execution lifecycle

```text
ACTIVE goal + ACTIVE plan version
      │ owner trigger: POST /goals/{id}/advance   (one step)
      │              or /goals/{id}/execute       (bounded session)
      ▼
TaskOrchestrator.start_next_step()
  ├─ goal must be ACTIVE            (paused/terminal ⇒ no new work)
  ├─ Planner.select_next_step()     (deterministic: deps → priority → order)
  ├─ TaskStateStore.create_task()   (idempotency_key = goalstep:<step>:<attempt>)
  └─ Planner.mark_step_started()    (step RUNNING, attempt_count += 1)
      ▼
Protected Runtime worker executes the task
      ▼
PostTaskPipeline.apply_plan_outcome → TaskOrchestrator.apply_task_outcome()
  ├─ task COMPLETED + CompletionVerifier clean → step COMPLETED
  ├─ otherwise                                  → step FAILED (categorised)
  ▼
Goal progress recalculated → auto-complete ONLY via Phase 2 eligibility rules
```

## Recovery state machine

`recover_all()` runs at application startup (and is safe to call any time).
For every plan-backed step in `RUNNING`, the associated evidence task is
classified from persisted state.

### Recovery truth table (authoritative)

| Persisted step | Evidence-task state | Evidence quality                     | Recovery result                          | Attempt counter |
|----------------|---------------------|--------------------------------------|------------------------------------------|-----------------|
| RUNNING        | `completed`         | verifier clean                       | **COMPLETED** (evidence reconstructed)   | unchanged       |
| RUNNING        | `completed`         | verifier dirty                       | **FAILED** (`VERIFICATION_FAILURE`)      | unchanged       |
| RUNNING        | `failed`            | machine code / terminal reason       | **FAILED** (+ bounded retry policy)      | unchanged       |
| RUNNING        | `interrupted`       | —                                    | **READY** (recoverable)                  | NOT incremented |
| RUNNING        | `cancelled`         | —                                    | **READY** (recoverable)                  | NOT incremented |
| RUNNING        | missing             | —                                    | **PENDING** + recovery event             | NOT incremented |
| RUNNING        | `running`/`created` | —                                    | deferred → lease-takeover path           | unchanged       |
| COMPLETED      | any                 | repeated recovery                    | unchanged (no events, no side effects)   | unchanged       |
| FAILED         | any                 | repeated recovery                    | unchanged (retry only via explicit policy)| unchanged       |
| goal ABANDONED/terminal | any        | any                                  | no new execution ever                    | n/a             |

Rules:
- Never forged success: completion always re-runs the kernel's own gate.
- Never blind reset: terminal outcomes are reconstructed from evidence;
  only truly unresolved work returns to a recoverable state.
- Classification uses machine-readable codes from the kernel's own
  `task.failed` event (e.g. `ELAPSED_TIME_EXCEEDED` → TIMEOUT). Human
  readable text is never parsed for classification; unknown conditions stay
  conservative (EXECUTION_FAILURE, never success).
- **Idempotent**: transitions fire only from RUNNING; repeated recovery of a
  resolved step is a no-op that appends no events, duplicates no tasks,
  creates no plan versions, and never re-increments attempts.

## Failure taxonomy & retry policy

```text
EXECUTION_FAILURE | VERIFICATION_FAILURE | INTERRUPTION |
TIMEOUT | INVALID_PLAN | RECOVERY_FAILURE
```

(`services/task_orchestrator.py::FailureCategory`; deterministic mapping from
task state + terminal reason + verifier output.)

Retry policy (`RetryPolicy`, defaults from settings):

- Limit: `settings.step_max_attempts` (default 3) — persisted on the step as
  `attempt_count`.
- Retryable categories only: EXECUTION_FAILURE, VERIFICATION_FAILURE.
  INTERRUPTION/TIMEOUT are never auto-retried.
- A retry is the transition FAILED → READY plus an immutable
  `plan_step.attempt.retry` ledger event. It happens only through the
  explicit orchestration entry points (`execute_session`, or the next
  `advance`) — never implicitly inside outcome application.
- Exhaustion: `plan_step.attempt.exhausted` event (emitted exactly once, tied
  to the one-time active→paused goal transition) and the goal is PAUSED for
  owner review/replanning. No infinite loops; the LLM cannot raise limits.

## Automatic continuation ("execution session")

`POST /api/v1/goals/{id}/execute` runs one explicitly initiated session:
select → create task → run through the protected kernel → apply verified
outcome → repeat. Bounds:

- `settings.max_steps_per_goal_execution` (default 10) hard cap per session;
- existing per-task budgets and the profile-wide lease apply to every step;
- stops immediately on retry exhaustion (goal paused) or lost lease.

This is *not* a scheduler: nothing executes outside the awaited call, there
is no daemon, timer, or proactive trigger.

## Pause / Resume semantics

- PAUSED goals start no new steps (`start_next_step` refuses non-ACTIVE
  goals); already-RUNNING tasks are not destroyed — they finish or get
  recovered normally, and their outcomes still update plan state.
- Paused state is persisted; restarts never silently reactivate a goal
  (`resume_goal` is the only path back to ACTIVE).
- Terminal goals (COMPLETED/FAILED/ABANDONED) reject every lifecycle
  transition; the legacy PATCH route maps status changes onto the same
  machine (409 on invalid).

## Cancellation mapping

User stop (`/stop`, per-task cancel), goal abandonment, runtime interruption
(stale lease), and process crash remain distinct, auditable events. The
orchestration layer introduces no second cancellation mechanism; abandoning
a goal simply prevents future selection while running attempts resolve
through the normal paths.

## Lease boundary

The profile-wide runner lease remains THE production authority for task
execution (single-runner constraint unchanged). Orchestration ownership is
expressed only through attempt/idempotency keys and step states — there is
no competing lease authority. Future per-goal leases require only replacing
`LeaseManager`'s scope key; callers already go through the
`LeaseManager` Protocol.

## Scheduler boundary (design only)

`GET /api/v1/goals/runnable` lists ACTIVE goals with an eligible step — a
pure query for the future scheduler:

```text
Scheduler (future) → Work Orchestrator → Planner → Runtime Kernel
```

No periodic loop exists anywhere in this phase.

## Legacy objective sunset (Phase 3.5)

- **Creation paths** now all route through GoalManager: POST /goals,
  POST /objectives (compatibility adapter), `objective_create` tool, and the
  internal auto-projection for plain tasks. There are no raw objective
  creators left outside goal_manager.
- **Migration**: `migrate_legacy_objectives()` converts unplanned objectives
  to plan-backed goals idempotently; runs automatically at startup after
  recovery and logs a report. Conservative mapping — unverifiable
  "completed" claims become recoverable PENDING/READY steps, never trusted.
- **Sunset condition**: the legacy projection (`work_state.ensure_task_objective`
  + its step rows) may be removed once (a) every stored objective is
  plan-backed (migration reports zero skipped with `no_legacy_steps`), and
  (b) no API consumer depends on the legacy status vocabulary
  (`in_progress`/`skipped`). Tracked for Phase 4.

## Event vocabulary (existing immutable ledger, scope `goal:<goal_id>`)

`goal.created/paused/active/completed/failed/abandoned`,
`plan.created`, `plan.proposal.rejected`,
`plan_step.created/ready/started/completed/failed`,
`plan_step.recovery.detected/.resolved`,
`plan_step.attempt.retry/.exhausted`.

Agent-scope events (`agent:<agent_id>`, see PERSISTENT_AGENT_ARCHITECTURE.md):
`agent.started/paused/resumed/stopped`,
`agent.wake.requested/.started/.completed/.skipped/.coalesced/.reclaimed`,
`agent.execution.started`, `agent.background_policy_changed`,
`goal.backoff.applied/.cleared`.

## Startup ordering (Phase 4.5 — single authoritative sequence)

```text
runtime init → 1. task/lease recovery → 2. goal migration
             → 3. main-agent init → 4. wake recovery → worker start
```

Implemented in `services/bootstrap.run_startup_sequence()` (returns the
ordered step list for regression testing); `main.py` starts the wake worker
only after it completes, and drains durable PENDING wakes only when policy
is enabled.

## Observability

Every automatic decision (recovery resolution, retry, exhaustion-pause,
continuation stop-reason) produces either a ledger event (queryable via
`GET /tasks/{scope}/events` semantics) or a structured log line answering:
what goal, which step, which attempt, why triggered, what evidence, what
outcome.
