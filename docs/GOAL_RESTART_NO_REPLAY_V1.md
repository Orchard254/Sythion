# Goal Restart V1 — Preserve Progress, Never Replay Unknown Effects

**Status (2026-11-09):** Code committed for fail-closed startup
classification and goal readiness. Local pure-policy checks executed;
real TaskRunner/SQLite subprocess and Windows acceptance tests still pending.

## Why this change was necessary

Prior `TaskOrchestrator.recover_step` behavior treated interrupted and
cancelled tasks as automatically *recoverable*: it wrote the RUNNING plan step
back to READY and committed a `plan_step.recovery.resolved` event. A missing
task was put back into PENDING. A subsequent scheduled goal session could
start a **new** task for work whose old external effect was unknown.

The task-level resume admission gate already refuses ambiguous effects, but
making a *new plan task* bypassed that task-specific gate. This was a
cross-component gap: safe task admission alone did not imply safe goal
recovery.

## New startup policy

`backend/services/goal_restart_policy.py` provides a fixed, read-only
classification for the task linked to a RUNNING plan step:

| Persisted task evidence | Startup result | New attempt? |
| --- | --- | --- |
| completed | Existing completion-verifier reconciliation | No replay |
| failed | Existing durable failed-step reconciliation | No replay |
| interrupted / cancelled | Manual review required; step stays RUNNING | No |
| missing task | Missing evidence review required; step stays RUNNING | No |
| created / running / waiting_for_user / paused | Defer to task ownership/resume mechanism | No |
| unexpected state | Defer for investigation | No |

This policy does not assert that an external effect happened or did not
happen. Terminal tasks still need their existing independent completion
verifier; matching task-to-step evidence linkage and goal scope are checked
before terminal reconciliation.

The startup recovery pass is observational for deferred states: it does not
append a new plan event, increment attempt counters, clear uncertainty,
consume a retry, create a replacement task or manufacture permission. A
second startup yields the same deferred classification until actual
authoritative state changes.

## Goal selection and interface

- The pure `project_goal_work` now suppresses all new candidate steps
  whenever any step in the active plan is RUNNING/in progress, even if other
  independent steps are READY or a failed step would otherwise qualify
  for a retry.
- `TaskOrchestrator.execute_session` returns
  `unresolved_step_review_required` rather than initiating retry on a
  mixed RUNNING/FAILED plan. It similarly refuses invalid dependency plans.
- `read_goal_work_preview` reports `recovery_review_required` with a
  fixed, non-sensitive explanation when a RUNNING step is interrupted,
  cancelled, unlinked, associated with a missing task, or bound to a
  foreign goal. It contains no task text, raw action IDs or secrets.
- This is deliberately a **single-in-flight-step** policy per active goal.
  Different goals can still be selected by the existing bounded scheduler.

## Progress across sessions

Verified completed steps remain completed. A planned three-step sequence
can finish its first step in database session A; a new connection/session B
will select the second, then third, *without rerunning step one*. A separate
case keeps a crashed/interrupted first step unselectable after startup and
prevents new goal tasks until the original task is reconciled.

These cases are added as integration tests using real Sythion ORM,
TaskRunner, Planner and SQLite. They do **not** establish a full OS process
kill/Windows run until executed in the complete repository.

## Test and verification matrix

**Executed locally in a partial, isolated Python environment:**

```bash
python -m pytest -q \
  backend/tests/test_goal_worker_binding.py \
  backend/tests/test_goal_worker_handoff.py \
  backend/tests/test_goal_restart_policy.py
# 42 passed
```

13 of those passing cases specifically test the new restart classifier.
The local checkout contains pure modules, not the complete Sythion ORM.

**Added/updated in the repository, not executed here:**

- `test_work_orchestration.py`: deferred interrupted/cancelled/missing
  states, repeated pass idempotency, uncertain old action, task-to-step
  mismatch and foreign-goal evidence.
- `test_process_crash_recovery.py`: OS process-kill acceptance now
  expects the step to stay RUNNING across startup passes.
- `test_isolated_crash_recovery_regression.py`: file-backed SQLite
  reconnect verifies no task duplication, no scheduler candidates and
  no automatic step admission after restart.
- `test_goal_work_preview.py` and
  `test_goal_work_preview_integration.py`: prevent independent-step
  advancement; distinguish review-required task states with private
  details excluded.
- `test_goal_execution_vertical_slice.py`: blocks independent work
  and retry mutation during an unresolved step; verifies successful
  completion across two *different* SQLite connections.

## Remaining work

1. Execute full backend, file-backed SQLite and actual OS process-kill
   tests in a complete Sythion checkout; fix any failures.
2. Prove the current approved task-resume route safely continues an
   interrupted task after independent review without minting a replacement
   task. No automatic task clearance is implemented by this patch.
3. Test actual Windows/Tauri shutdown/force-kill paths, runner lease
   ownership, SQLite reopen and external-action receipts. Never blindly
   retry unknown effects.
4. When adding goal-level recovery UI, ensure the user can inspect the
   blocked task but cannot grant execution permission by editing UI flags.

**Principle:** Restart can reconcile verified terminal evidence. Restart
must **not** grant permission to repeat work with unknown effects.
