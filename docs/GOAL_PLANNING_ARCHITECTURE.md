# Goal & Planning Architecture

**Status:** Active (Phase 2). Builds on `PROTECTED_RUNTIME_CONTRACT.md` and
`RUNTIME_KERNEL_ARCHITECTURE.md`, both of which remain authoritative. This
document describes the goal/planning layer that now sits ABOVE the runtime
kernel.

> The Planner determines **what should happen**.
> The Runtime Kernel determines **whether/how an action may execute**.
> The LLM is a reasoning component — never the source of truth for goal
> completion, step completion, evidence, or authorization.

---

## Components

### GoalManager (`services/goal_manager.py`)

Owns goal lifecycle over the existing `objectives` table (the Goal
persistence layer; no duplicate goal table).

State machine (deterministic; invalid transitions raise
`InvalidGoalTransitionError`):

```text
ACTIVE ──► PAUSED ──► ACTIVE
   │  │      │  │
   ▼  ▼      ▼  ▼
COMPLETED / FAILED / ABANDONED   (terminal — no exits)
```

- `completed` is only reachable through eligibility rules: all required
  (non-cancelled) steps completed from kernel evidence AND explicit
  completion criteria verified (owner action).
- Progress is derived purely from persisted step state:
  `completed_required_steps / total_required_steps`. No model percentages.

### Planner (`services/planner.py`)

Owns decomposition validation, plan versions, dependency semantics,
readiness, next-step selection, and replanning. It never executes anything.

Candidate plans may originate from the user/API or from the LLM via the
Phase 1 `ModelClient` boundary (`Planner.request_model_plan`). Every
candidate passes:

```text
LLM/user proposal → schema validation (pydantic CandidateStepModel)
                  → dependency validation (missing/self/cycle/duplicate)
                  → persistence as a NEW immutable plan version
```

Malformed proposals are rejected and recorded as `plan.proposal.rejected`
ledger events. Raw model text never becomes plan state.

### Plan (`goal_plans` table)

Versioned per goal: `(goal_id, version)` unique; status
`active | superseded | rejected`; `source` (user/model/deterministic);
free-text `reason`. Superseded versions are retained forever (replanning
audit trail). One active version per goal at a time.

### Plan Step (`plan_steps`, extended)

State machine:

```text
PENDING ⇄ READY ⇄ RUNNING ──► COMPLETED | FAILED
   └────► BLOCKED ──► READY          FAILED ──► READY (explicit retry)
any open state ──► CANCELLED
COMPLETED/CANCELLED terminal
```

Fields: stable `step_key` (unique per plan), description, priority (1–5),
`depends_on` (JSON key list), `attempt_count`, `evidence_task_id`,
`failure_reason`, timestamps. Legacy statuses (`in_progress`, `skipped`)
remain readable for pre-plan rows.

## Dependency semantics

B becomes `READY` exactly when every key in `depends_on` is `COMPLETED`
(`refresh_readiness`, deterministic, event `plan_step.ready`). Validation
rejects missing references, self-dependencies, duplicates, and cycles before
persistence.

## Priority semantics

Next-step selection (`select_next_step`) orders READY steps by:
1. priority ascending (1 = most important),
2. `step_order` ascending (stable tie-break),
3. `id` ascending.

The model cannot influence this ordering beyond what was validated into the
persisted plan.

## Execution flow

```text
Goal → Planner.select_next_step() → TaskOrchestrator.start_next_step()
  → TaskStateStore.create_task(idempotency_key=goalstep:<step>:<attempt>)
  → Runtime Kernel worker (existing protected loop) executes
  → kernel verification produces evidence
  → PostTaskPipeline.apply_plan_outcome → Planner.complete_step_from_evidence()
       (requires task terminal-completed + CompletionVerifier clean)
  → goal progress recalculated → auto-complete only if rules satisfied
  → failure → owner-triggered replan creates plan vN+1
```

There is still NO autonomous scheduler: advancing a goal happens through an
existing task run or the explicit `POST /api/v1/goals/{id}/advance`.

## Evidence relationship

Step completion reuses the kernel's own artifacts — task state, immutable
task events, verification results, and `CompletionVerifier.precondition_error`
— by reference. There is no parallel evidence system. A model claim of
"I completed this step" is structurally insufficient everywhere:
the `objective_complete_step` tool refuses plan-backed steps without a
verified execution task (`STEP_EVIDENCE_REQUIRED`), and the orchestrator's
outcome path requires the same gate.

## Runtime & model boundaries

- The planning layer imports no tool executor and calls no `get_adapter()`
  (enforced by test).
- Replanning with model reasoning goes through `ModelClient.decide` only.
- All goal/plan audit events append to the existing immutable ledger under
  synthetic scope ids `goal:<goal_id>` (documented convention; reuses
  per-scope strict ordering instead of a second event store).

## API surface (minimum)

| Endpoint | Purpose |
|---|---|
| `POST /goals` | create goal (+ validated optional plan) |
| `GET /goals`, `GET /goals/{id}` | list/inspect incl. progress + active plan |
| `GET /goals/{id}/plans` | plan version history (audit) |
| `POST /goals/{id}/pause\|resume\|abandon` | lifecycle (validated transitions) |
| `POST /goals/{id}/advance` | deterministic next-step selection → runtime task |
| `POST /goals/{id}/replan` | new validated version (explicit steps or model-proposed) |

`PATCH /objectives/{id}` no longer accepts raw `status` writes; statuses go
through the GoalManager machine (invalid → 409).

## Ownership decision (compatibility)

The existing `Objective`/`PlanStep` tables BECAME the goal/step persistence
(rebuilt migration adds `'failed'` goal state, ready/running/cancelled step
states, and plan columns). `work_state.py` remains as a legacy projection for
pre-plan objectives but defers to the Planner for plan-backed steps
(`plan_id IS NOT NULL`) and never modifies terminal goals. After Phase 2
there is one authoritative owner: GoalManager (lifecycle) + Planner (steps),
with work_state explicitly non-authoritative for plan-backed goals.

## Future scheduler boundary

Per-goal scheduling later needs only: iterate ACTIVE goals → orchestrator
`start_next_step` under per-goal leases. No kernel changes required; the
single-runner production constraint is unchanged in Phase 2.
