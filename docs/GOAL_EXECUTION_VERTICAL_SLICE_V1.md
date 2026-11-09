# Goal Execution Vertical Slice V1

**Scope:** deterministic orchestration proof using the real `TaskRunner`,
`Planner`, `TaskOrchestrator`, `CompletionVerifier`, and disposable SQLite.
No live LLM, OS automation, internet requests, or autonomous wake is used.

## Why this milestone exists

Previously, an outcome already applied by the kernel post-task pipeline
returned `{"updated": false}` to the orchestrator. Both explicit
`execute_session` and background `run_background_cycle` labelled that
as `already_applied`, even if the persisted step had **failed**, remained
**running**, had an invalid evidence-task link, or failed verification.
Background accounting also treated `already_applied` as success.

This could cause a misleading success count or continuation past uncertain
work. A separate SQLAlchemy session in the kernel could also leave stale
plan/task objects cached by the orchestrator.

## Implementation

- `backend/services/goal_execution_outcome.py`: pure, bounded
  `classify_step_outcome` checks the exact evidence-task ID, persisted
  plan-step state, task terminal state and kernel verifier error. Only
  `completed`, `failed` or `unverified` are returned; no model statements
  or `updated` booleans count as evidence.
- `TaskOrchestrator._observed_step_outcome`: refreshes the goal, task and
  step from the database, checks the canonical links, and re-runs the
  existing completion verifier for success. An absent/unavailable verifier
  is **unverified**, never success.
- `apply_task_outcome`: returns `unverified` without mutating the plan
  when a scripted/real worker has not reached a completed or failed
  terminal task state. Interrupted/cancelled work still belongs to
  the existing task ownership and recovery mechanisms.
- Both explicit and background loops stop immediately with
  `stopped_reason="outcome_unverified"` rather than selecting another step
  or recording a false terminal result. A started background attempt still
  receives fair scheduling credit; an *unverified* attempt does not count
  as a failed service session.
- A previously applied verified **failure** is now reported as `failed`,
  triggering the bounded failure/retry policy rather than counting as
  success.
- The explicit session path now releases the lease exactly once when no
  step is eligible, using its existing `finally` boundary.

The existing consequence permissions, approvals, runtime leases, goal
completion criteria, event ledger, budget caps, and automatic wake
defaults are unchanged. There is no new background scheduler.

## Acceptance tests

`backend/tests/test_goal_execution_vertical_slice.py` uses the actual
ORM/planner/task runner while scripting only the OS-facing worker and
writing exclusively into a pytest `tmp_path`. Tests are deliberately
deterministic, not live provider tests.

1. Three dependent steps inspect a local numeric file, compute a summary,
   and verify the result. Inspect the output independently after execution.
   Confirm exactly three plan starts and three completions, exact evidence
   links, terminal goal state, and persistence in a new DB session.
2. Simulate the kernel post-task hook applying each result first. The
   orchestrator must still classify three verified successes and must
   not append duplicate completion events.
3. Fail the second step: the third must not execute; retry exhaustion
   pauses the goal and retains partial progress.
4. Have a worker return while its task is still RUNNING. Stop without
   manufacturing a step failure or starting dependent work.
5. Have a worker pre-apply a FAILED result. Background/explicit sessions
   must classify it as failure and apply bounded retry logic.
6. Run an unresolved background step. Record that it received a fairness
   slot, but **not** a successful or failed terminal service outcome.
7. Require unverified explicit goal-completion criteria. All three
   steps may finish, but the goal must remain ACTIVE until criteria are
   independently verified through existing authority.
8. Ensure an ineligible goal releases the runner lease once.
9. Remove the exact evidence-task link from a completed task; stop as
   `unverified`, never claim completion.
10. Simulate worker-side post-task application through a separate
    SQLite session. The orchestrator must refresh cached task/step/goal
    state and avoid duplicate completion events.

The pure classification tests in
`backend/tests/test_goal_execution_outcome.py` cover matching/missing
evidence IDs, failures, interrupted/nonterminal states, unverified
completion, and false worker claims.

## How to run in a full repository checkout

```bash
python -m pytest -q \
  backend/tests/test_goal_execution_outcome.py \
  backend/tests/test_goal_worker_binding.py \
  backend/tests/test_goal_execution_vertical_slice.py \
  backend/tests/test_work_orchestration.py \
  backend/tests/test_goal_planning.py \
  backend/tests/test_main_agent.py \
  backend/tests/test_phase125_capture_observability.py
```

Run the full backend suite afterward and then the frontend/Windows packaged
test matrix before declaring end-to-end production acceptance.

**Verification in this coding environment:** 24 pure classification tests
passed, with source compilation. The full private Sythion repository is not
locally mounted, and GitHub DNS is unavailable from the container, so the new
real-ORM integration tests were committed as **unexecuted** acceptance work.
Do not count this as a live-tool or installed-Windows success.

## Next acceptance gates

- Run the 17 real-ORM vertical-slice scenarios and address any failures.
- Add actual tool-adapter output verification within an approved test-only
  workspace, including idempotent/mutating action receipts and permission
  checks. A scripted worker is **not** a security verification of real tools.
- Simulate process termination, owner-lease loss, and resumed sessions.
- Prove a five-step chain can finish across two process sessions without
  replaying ambiguous external effects.
- Reconcile an independent goal-completion criteria verifier before
  allowing unobserved model text to close a goal.


## Registered real-worker executor wiring (2026-11-09)

The first V1 review exposed an actual integration defect: both
`TaskOrchestrator.execute_session` and `run_background_cycle` passed
`tool_executor=None` into the default production `run_task`. That worker
constructs `TaskExecutionLoop(session, runner, tool_executor)` and uses
the supplied executor to expose registered tools to the model and to
execute authorized tool calls. A `None` placeholder was not a valid
tool capability binding.

`backend/services/goal_worker_binding.py` now selects one of two paths:

- **Production default:** resolve `backend.api.dependencies.get_tool_executor`
  once **before** acquiring a runner lease or creating a task; pass that same
  registered executor to each goal worker. This preserves the application's
  existing provider/fallback registration and its tool permission gates.
- **Explicit scripted worker:** retain the existing
  `run_task_fn(None, task_id, None)` test-injection behavior without
  calling the registered tool provider.

When the production executor provider returns `None` or raises, the goal
session returns `{"executed": [], "stopped_reason": "tool_executor_unavailable"}`
without creating a task, invoking a model, or altering the goal. The
binding object hides its executor from `repr` to reduce accidental
credential disclosure. No new tool privileges or background opt-in were
added.

`backend/tests/test_goal_worker_binding.py` has **15 locally passing
unit tests** (real provider identity/once-only behavior; test injection;
missing/failed provider; malformed callback rejection; non-authorizing
result; secret-redacted representation).

Six further cases were added to the full-repository
`backend/tests/test_goal_execution_vertical_slice.py`: explicit and
background production worker injection, both executor-unavailable paths,
scripted-worker isolation, and provider exception containment. This brings
the vertical-slice matrix to **17 parameter-expanded cases**. These cases
are committed, **not executed** in the local partial environment.

The worker-session persistence scenario now uses a **file-backed SQLite
database with distinct DBAPI connections**, rather than two ORM sessions
sharing one in-memory StaticPool connection. This will make the intended
cross-connection visibility and cache-refresh regression meaningful when
executed in the complete checkout.

**Remaining blockers:** full Sythion repository execution; test adapter
tools in a disposable workspace; complete Windows packaged app and
permissions; process kill/restart tests. This change repairs a verified
argument-wiring defect but does not itself demonstrate a live model or
tool successfully completing a goal.
