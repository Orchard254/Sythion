"""Read-only projection of reasoning receipts associated with a durable goal.

This is an observability surface, not an autonomous scheduler or recovery
command. It never infers task completion from LLM output and never replays work.
Historical tasks with no receipts remain explicitly *unobserved* by this view.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from backend.services.reasoning_journal import (
    unresolved_reasoning_jobs,
)
from backend.services.reasoning_recovery import assess_reasoning_recovery

_PHASES = ("started", "response_received", "timed_out", "failed", "cancelled")
_EVENT_TYPES = frozenset(f"reasoning.{p}" for p in _PHASES)
_TERMINAL_GOALS = frozenset({"completed", "failed", "abandoned"})


def project_goal_reasoning(
    goal_id: str,
    goal_status: str,
    task_snapshots: Sequence[Mapping[str, Any]],
    *,
    total_tasks: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Derive a truthful snapshot from task state and *committed* events.

    Each task snapshot must have ``task_id``, ``state``, ``association``, and
    ``events`` in increasing ledger sequence order. The total count includes
    all associated tasks, while only the supplied window is examined. No
    model calls, tool calls or database mutations occur here.
    """
    if not goal_id or not isinstance(goal_id, str):
        raise ValueError("goal_id is required")
    if not isinstance(goal_status, str) or not goal_status:
        raise ValueError("goal_status is required")
    if type(total_tasks) is not int or total_tasks < len(task_snapshots):
        raise ValueError("total_tasks must include the supplied task window")

    evaluated_at = now if now is not None else datetime.utcnow()
    if not isinstance(evaluated_at, datetime):
        raise ValueError("now must be a datetime")

    recovery_states: Counter[str] = Counter()
    recovery_reviews = 0
    possibly_running = 0
    uncertain_effects = 0
    task_items: list[dict[str, Any]] = []
    phase_counts: Counter[str] = Counter()
    state_counts: Counter[str] = Counter()
    missing_receipts = 0
    unresolved_task_count = 0
    for snapshot in task_snapshots:
        if not isinstance(snapshot, Mapping):
            raise ValueError("task snapshot must be a mapping")
        task_id = snapshot.get("task_id")
        state = snapshot.get("state")
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("task snapshot needs task_id")
        if not isinstance(state, str) or not state:
            raise ValueError("task snapshot needs state")
        events = snapshot.get("events", ())
        if not isinstance(events, (list, tuple)):
            raise ValueError("task snapshot events must be an ordered list")
        local: Counter[str] = Counter()
        for event in events:
            if not isinstance(event, Mapping):
                continue
            kind = event.get("event_type")
            data = event.get("event_data")
            if kind in _EVENT_TYPES and isinstance(data, Mapping):
                job_id = data.get("job_id")
                if isinstance(job_id, str) and job_id.strip():
                    local[kind.split(".", 1)[1]] += 1
        unresolved = unresolved_reasoning_jobs(events)
        recovery = assess_reasoning_recovery(
            state, len(unresolved), snapshot.get("ownership"), now=evaluated_at
        )
        recovery_states[recovery["state"]] += 1
        recovery_reviews += int(recovery["review_required"])
        possibly_running += int(recovery["may_still_be_running"])
        uncertain_effects += int(recovery["external_effect_uncertain"])
        for phase, count in local.items():
            phase_counts[phase] += count
        state_counts[state] += 1
        if not local["started"]:
            missing_receipts += 1
        if unresolved:
            unresolved_task_count += 1
        task_items.append({
            "task_id": task_id,
            "state": state,
            "association": snapshot.get("association", "direct"),
            "recovery": recovery,
            "reasoning": {
                "receipt_state": (
                    "unresolved" if unresolved else
                    "recorded" if local["started"] else "not_recorded"
                ),
                "started": local["started"],
                "response_received": local["response_received"],
                "timed_out": local["timed_out"],
                "failed": local["failed"],
                "cancelled": local["cancelled"],
                "unresolved_count": len(unresolved),
            },
        })

    truncated = total_tasks > len(task_items)
    if unresolved_task_count:
        attention = "inspect_unresolved_receipts"
    elif goal_status in _TERMINAL_GOALS:
        attention = "goal_terminal"
    elif goal_status == "paused":
        attention = "goal_paused"
    elif state_counts["waiting_for_user"]:
        attention = "await_user"
    elif state_counts["failed"] or state_counts["interrupted"]:
        attention = "review_task_failure"
    elif state_counts["running"]:
        attention = "observe_running_work"
    elif truncated:
        attention = "more_tasks_not_inspected"
    else:
        attention = "inspect_goal_plan"

    return {
        "goal_id": goal_id,
        "goal_status": goal_status,
        "task_window": {
            "total": total_tasks,
            "returned": len(task_items),
            "truncated": truncated,
        },
        "task_states": dict(sorted(state_counts.items())),
        "reasoning_receipts": {
            **{phase: phase_counts[phase] for phase in _PHASES},
            "unresolved_tasks": unresolved_task_count,
            "tasks_without_start_receipts": missing_receipts,
        },
        "attention": attention,
        "recovery_preflight": {
            "evaluated_at": evaluated_at.isoformat(),
            "review_required_tasks": recovery_reviews,
            "possibly_running_tasks": possibly_running,
            "external_effect_uncertain_tasks": uncertain_effects,
            "states": dict(sorted(recovery_states.items())),
            "automatic_replay_allowed": False,
            "window_complete": not truncated,
        },
        "tasks": task_items,
        "note": (
            "Reasoning receipts report call lifecycle, not decision validity, "
            "tool authorization, goal completion, or whether an unresolved "
            "invocation is still running. Never replay effects from this report."
        ),
    }


def read_goal_reasoning_continuity(db: Any, goal_id: str, *, limit: int = 50) -> dict[str, Any]:
    """Read a bounded goal-scoped task window and its reasoning-only events.

    Task ownership cannot be inferred from plan *display* links. Only direct
    objective association and the plan's explicit evidence_task_id count;
    conflicting task.objective_id is excluded even if a step points at it.
    No database writes, planner readiness refresh or goal transitions occur.
    """
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer from 1 through 100")

    from sqlalchemy import and_, or_
    from backend.models.database import Objective, PlanStep, Task, TaskEvent
    from backend.runtime.events import decode_event_data
    from backend.runtime.task_ownership import TaskOwnershipRow

    goal = db.query(Objective).filter_by(id=goal_id).first()
    if goal is None:
        raise LookupError("goal not found")

    evidence_ids = {
        task_id
        for (task_id,) in db.query(PlanStep.evidence_task_id).filter(
            PlanStep.objective_id == goal_id,
            PlanStep.evidence_task_id.isnot(None),
        ).all()
        if task_id
    }
    association = Task.objective_id == goal_id
    if evidence_ids:
        association = or_(
            association,
            and_(Task.id.in_(evidence_ids), Task.objective_id.is_(None)),
        )
    query = db.query(Task).filter(association)
    total = query.count()
    tasks = query.order_by(Task.updated_at.desc(), Task.id.asc()).limit(limit).all()
    ids = [task.id for task in tasks]
    events_by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    owners_by_task: dict[str, dict[str, Any]] = {}
    observed_at = datetime.utcnow()
    if ids:
        for owner in db.query(TaskOwnershipRow).filter(
            TaskOwnershipRow.task_id.in_(ids)
        ).all():
            raw_uncertain = owner.uncertain_action_ids
            # A malformed/unknown historical value is conservatively treated
            # as uncertain. Never expose action IDs, nonces or worker IDs.
            try:
                import json
                parsed = json.loads(raw_uncertain) if raw_uncertain is not None else []
                uncertain = not (isinstance(parsed, list) and not parsed)
            except (TypeError, ValueError):
                uncertain = True
            owners_by_task[owner.task_id] = {
                "status": owner.status,
                "expires_at": owner.expires_at,
                "active_action": bool(owner.active_action_id),
                "uncertain_actions": uncertain,
            }
    if ids:
        event_rows = db.query(TaskEvent).filter(
            TaskEvent.task_id.in_(ids),
            TaskEvent.event_type.in_(_EVENT_TYPES),
        ).order_by(TaskEvent.task_id.asc(), TaskEvent.sequence_num.asc()).all()
        for row in event_rows:
            events_by_task[row.task_id].append({
                "event_type": row.event_type,
                "event_data": decode_event_data(row.event_data),
            })

    snapshots = []
    for task in tasks:
        ownership = owners_by_task.get(task.id)
        if task.active_action_id:
            # The task projection may retain a pending external action even
            # when its ownership row is absent or already released. Never
            # label that historical effect as clean/settled.
            ownership = {**(ownership or {"status": "UNKNOWN"}), "active_action": True}
        snapshots.append({
            "task_id": task.id,
            "state": task.state,
            "association": "direct" if task.objective_id == goal_id else "plan_evidence",
            "events": events_by_task[task.id],
            "ownership": ownership,
        })

    return project_goal_reasoning(
        goal_id, goal.status, snapshots, total_tasks=total, now=observed_at,
    )
