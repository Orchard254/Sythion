# Cognitive Runtime Integration

**Status:** Active (Phase 8.75). The FULL Phase 8 cognitive pipeline —
IntentInterpreter → CognitiveState → ReasoningEngine → DecisionSystem →
SelectedDecision — is authoritative for live decisions on `POST /tasks`.
All runtime authority contracts remain unchanged.

> **The cognitive layer decides what kind of work is required.
> The Runtime remains responsible for actually executing it.**

---

## Decision authority (Phase 8.75)

There is exactly ONE strategic decision authority:

```text
CognitivePipeline (backend/cognitive/pipeline.py)
    interpret → assemble context → reason → decide → simulate (dry-run)
```

`ModelDecisionEngine` is **RETAINED AS WORKER** (`RETAIN_AS_WORKER`): it no
longer decides WHAT a task should be; it plans tool-level steps INSIDE the
execution loop under the cognitive decision, inside the Protected Runtime,
subject to budgets, guards, approval gates, and evidence-gated completion.

## Deterministic routing policy

Deep cognition is intentional, never accidental. The policy lives in
`cognitive/pipeline.py::requires_full_reasoning`:

| Request shape | Path |
|---|---|
| social / correction / clarification replies | lightweight (no LLM call) |
| simple deterministic commands / clear questions | lightweight |
| GOAL intent | full ReasoningEngine |
| destructive verbs (delete/format/deploy/…) | full |
| multi-step requests ("then", "step 2", ";") or very long input | full |

BOTH paths run deterministic candidates through the DecisionSystem
(hard-constraint veto → capability veto → weighted scoring), so every live
request gets a structured SelectedDecision at bounded cost; only "full"
requests additionally invoke the LLM-backed ReasoningEngine through the
Phase 6 ModelSelector → ModelProvider chain (bounded fallback, loop bound).

## Live input path

```text
User input (typed OR voice transcript — same transport)
        ↓
POST /api/v1/tasks          (existing endpoint, existing response shape)
        ↓
TaskRunner.create_task      (durable task + task.created event)
        ↓
work_state.ensure_task_objective
        ↓
CognitiveDecisionAdapter.route_new_input()   ← COGNITIVE GATE (full pipeline)
        ├─ CORRECTION intent → cognitive.correction.recorded event
        ├─ ASK_CLARIFICATION (material ambiguity) → park WAITING_FOR_USER
        ├─ REQUEST_PERMISSION / risk ≥ HIGH (+ dry-run simulation) → park
        ├─ NO_ACTION / REFUSE / DEFER / WAIT → park with reason
        └─ ANSWER / EXECUTE_STEP / CREATE_GOAL / UPDATE_GOAL → execute
        ↓
transition RUNNING → background run_task → Protected Runtime loop
```

Only freshly `CREATED` tasks pass through the gate. Idempotent replays and
non-CREATED states bypass it entirely — terminal history is never mutated.

Voice reaches the same gate: the dashboard drops the transcript into the
same new-task request field, so voice changes only the input transport.

## Decision → Runtime bridge

`services/cognitive_runtime.CognitiveDecisionAdapter` is the single bridge:

| Cognitive decision | Bridge method | Runtime primitive used |
|---|---|---|
| ASK_CLARIFICATION | `to_clarification()` → `runner.transition_state(WAITING_FOR_USER)` | existing state machine |
| REQUEST_PERMISSION / risk ≥ HIGH | `park(kind="permission")` → WAITING_FOR_USER + simulation metadata | existing state machine |
| NO_ACTION / REFUSE / DEFER / WAIT | `park(kind="wait")` → WAITING_FOR_USER with reason | existing state machine |
| answer user reply | `apply_clarification_answer()` merges answer into goal context | existing reply flow |
| correction signal | `record_correction_signal()` → ledger event | ledger only |
| create goal | GoalManager (+Planner) via canonical interfaces | no raw Objective writes |
| execute step | TaskOrchestrator / TaskStateStore.create_task | existing runtime |

The cognitive layer never executes tools and never writes task/goal rows
directly; source-scan tests enforce this.

## Clarification lifecycle

1. Ambiguous fresh input (`AMBIGUOUS`/`HIGH_IMPACT_AMBIGUITY`, non-social)
   parks the task: `CREATED → WAITING_FOR_USER` (added to ALLOWED_TRANSITIONS).
2. Durable metadata in `pending_action` JSON: kind=clarification, question,
   ranked interpretations, ambiguity level, awaiting_user_reply flag.
3. User answers via `POST /tasks/{id}/reply` (existing endpoint) — the reply
   event is appended and execution resumes on the SAME task with the merged
   context (`normalized_goal` gains a `[clarification] …` line).
4. Parked clarifications survive restarts: state + pending_action live in
   SQLite (integration-tested via a fresh session).

## Correction lifecycle

A correction-phrased input records `cognitive.correction.recorded`
(referencing the most recent terminal task) as learning evidence, then still
executes the corrected instruction — corrections are signals, not blockers.

## Background integration

Main Agent wakes (`user.request`, `task.completed/failed`) flow into the
same GoalManager/Planner/TaskOrchestrator/Runtime chain. The wake worker's
bounded cycles cannot create uncontrolled loops (Phase 4.5 bounds apply).

## Security boundaries

Source-scanned and behavior-tested:

- cognitive modules contain no tool-execution surface;
- no direct Objective/Task/Memory-row writes from cognition;
- LLM output can never mark tasks/goals complete without kernel evidence;
- clarification parking never triggers execution;
- simulation is dry-run only;
- secrets never enter cognitive state or events.

## Recovery semantics

Parked clarifications are ordinary WAITING_FOR_USER tasks: crash/stale-lease
recovery marks them interrupted if they were mid-execution (they normally
are not — nothing executes while parked), and restart leaves them waiting
with all durable metadata intact until answered or purged by retention.
