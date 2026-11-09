"""Deterministic, read-only preview of a goal's next eligible plan step.

This module does not change readiness, start tasks, schedule model calls,
write audit events or claim authorization. Execution must re-evaluate plan
state through the existing Planner under the runner lease.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any


_STEPS_IN_FLIGHT = frozenset({"running", "in_progress"})
_STEP_TERMINAL = frozenset({"completed", "failed", "cancelled", "skipped"})
_VALID_STEP_STATES = frozenset({"pending", "ready", "running", "in_progress", "blocked"}) | _STEP_TERMINAL


def _dependencies(raw: Any) -> tuple[str, ...] | None:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return None
    if raw is None:
        raw = []
    if not isinstance(raw, (tuple, list)) or any(not isinstance(x, str) or not x for x in raw):
        return None
    if len(raw) != len(set(raw)):
        return None
    return tuple(raw)


def project_goal_work(
    goal_status: str,
    steps: Sequence[Mapping[str, Any]],
    *,
    has_active_plan: bool,
    backoff_until: datetime | None = None,
    now: datetime | None = None,
    respect_backoff: bool = True,
) -> dict[str, Any]:
    """Report *effective* readiness without changing persisted BLOCKED rows.

    Only an explicit step-start operation may persist the dependency unlock.
    A READY row with incomplete dependencies is never treated as executable.
    Malformed/unknown dependencies fail closed for the entire plan, rather
    than silently selecting an unrelated apparently-ready step.
    """
    if not isinstance(goal_status, str) or not goal_status:
        raise ValueError("goal status is required")
    if type(has_active_plan) is not bool or type(respect_backoff) is not bool:
        raise ValueError("invalid preview options")
    if not isinstance(steps, (list, tuple)):
        raise ValueError("steps must be a sequence")
    observed_at = now if now is not None else datetime.now(timezone.utc).replace(tzinfo=None)
    if not isinstance(observed_at, datetime):
        raise ValueError("now must be a datetime")
    if backoff_until is not None and not isinstance(backoff_until, datetime):
        raise ValueError("backoff_until must be a datetime")
    if backoff_until is not None:
        try:
            in_backoff = backoff_until > observed_at
        except TypeError as exc:
            raise ValueError("incompatible backoff clock") from exc
    else:
        in_backoff = False

    normalized: list[dict[str, Any]] = []
    statuses: dict[str, str] = {}
    invalid = False
    for row in steps:
        if not isinstance(row, Mapping):
            invalid = True
            continue
        key, status = row.get("step_key"), row.get("status")
        step_id = row.get("id")
        deps = _dependencies(row.get("depends_on"))
        if (not isinstance(key, str) or not key or key in statuses
                or not isinstance(status, str) or status not in _VALID_STEP_STATES
                or type(step_id) is not int or step_id <= 0
                or deps is None):
            invalid = True
            continue
        statuses[key] = status
        normalized.append({
            "id": step_id,
            "step_key": key,
            "status": status,
            "description": str(row.get("description") or "")[:500],
            "priority": row.get("priority") if type(row.get("priority")) is int else 3,
            "step_order": row.get("step_order") if type(row.get("step_order")) is int else 0,
            "depends_on": deps,
        })
    # A damaged persisted plan may contain a cycle even though validated plans
    # never do. Do not manufacture progress from such a plan.
    graph = {step["step_key"]: step["depends_on"] for step in normalized}
    visit: dict[str, int] = {}  # 1 = visiting, 2 = complete
    for root in graph:
        if visit.get(root):
            continue
        stack: list[tuple[str, bool]] = [(root, False)]
        while stack:
            node, done = stack.pop()
            if done:
                visit[node] = 2
                continue
            if visit.get(node) == 2:
                continue
            if visit.get(node) == 1:
                invalid = True
                continue
            visit[node] = 1
            stack.append((node, True))
            for dep in graph[node]:
                if dep in graph:
                    if visit.get(dep) == 1:
                        invalid = True
                    elif visit.get(dep) != 2:
                        stack.append((dep, False))

    ready: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for step in normalized:
        deps = step["depends_on"]
        if step["step_key"] in deps or any(dep not in statuses for dep in deps):
            invalid = True
            continue
        missing = [dep for dep in deps if statuses[dep] != "completed"]
        if step["status"] in ("ready", "blocked"):
            if missing:
                blocked.append({"step_key": step["step_key"], "waiting_for": missing})
            else:
                ready.append(step)
    ready.sort(key=lambda s: (s["priority"], s["step_order"], s["id"]))
    selected = ready[0] if ready and not invalid else None
    completed = sum(step["status"] == "completed" for step in normalized)
    counts = {
        "total": len(steps),
        "completed": completed,
        "running": sum(step["status"] in _STEPS_IN_FLIGHT for step in normalized),
        "failed": sum(step["status"] == "failed" for step in normalized),
        "blocked": len(blocked),
        "effectively_ready": len(ready) if not invalid else 0,
    }
    if goal_status != "active":
        state = "goal_paused" if goal_status == "paused" else "goal_not_active"
    elif not has_active_plan:
        state = "plan_needed"
    elif invalid:
        state = "invalid_dependency_data"
    elif selected is not None and in_backoff and respect_backoff:
        state = "goal_backoff"
    elif selected is not None:
        state = "ready"
    elif counts["running"]:
        state = "step_in_progress"
    elif counts["failed"]:
        state = "failed_step_needs_review"
    elif normalized and all(step["status"] in ("completed", "cancelled", "skipped") for step in normalized):
        state = "steps_finished_review_goal"
    elif blocked:
        state = "waiting_for_dependencies"
    else:
        state = "no_eligible_step"

    explanation = {
        "ready": "The next plan step is eligible for execution after the normal runtime checks.",
        "goal_backoff": "An eligible step exists, but background execution is in failure backoff.",
        "goal_paused": "This goal is paused and cannot start new work.",
        "goal_not_active": "This goal is not active.",
        "plan_needed": "This goal needs a validated active plan before it can run.",
        "invalid_dependency_data": "The active plan has inconsistent step or dependency records; review it before execution.",
        "step_in_progress": "A plan step is already in progress; inspect its outcome before continuing.",
        "failed_step_needs_review": "A failed plan step needs retry or replanning review.",
        "steps_finished_review_goal": "No active steps remain. Review verified completion criteria before closing the goal.",
        "waiting_for_dependencies": "Plan steps are waiting for earlier steps to complete.",
        "no_eligible_step": "There is no eligible step; review the plan and its current states.",
    }
    return {
        "state": state,
        "goal_state": goal_status,
        "explanation": explanation[state],
        "runnable": state == "ready",
        "structurally_ready": selected is not None,
        "in_backoff": in_backoff,
        "next_step": ({
            "id": selected["id"],
            "key": selected["step_key"],
            "description": selected["description"],
            "priority": selected["priority"],
            "persisted_status": selected["status"],
            "would_unlock": selected["status"] == "blocked",
        } if selected is not None else None),
        "counts": counts,
        "blocked_steps": blocked[:10] if not invalid else [],
        "read_only": True,
        "execution_started": False,
        "authorization_issued": False,
    }


def read_goal_work_preview(db: Any, goal_id: str, *, now: datetime | None = None,
                           respect_backoff: bool = True) -> dict[str, Any]:
    """Read active goal+plan state without refreshing or committing readiness."""
    from backend.models.database import GoalPlan, Objective, PlanStep

    if db.new or db.dirty or db.deleted:
        raise RuntimeError("goal preview requires a clean ORM session")
    with db.no_autoflush:
        goal = db.query(Objective).filter_by(id=goal_id).first()
        if goal is None:
            raise LookupError("goal not found")
        plan = (db.query(GoalPlan).filter_by(goal_id=goal_id, status="active")
                .order_by(GoalPlan.version.desc()).first())
        rows = (db.query(PlanStep).filter_by(plan_id=plan.id)
                .order_by(PlanStep.step_order.asc(), PlanStep.id.asc()).all()) if plan else []
        step_data = [dict(id=s.id, step_key=s.step_key, description=s.description,
                          status=s.status, priority=s.priority, step_order=s.step_order,
                          depends_on=s.depends_on) for s in rows]
        result = project_goal_work(
            goal.status, step_data, has_active_plan=plan is not None,
            backoff_until=goal.backoff_until, now=now, respect_backoff=respect_backoff,
        )
    return {"goal_id": goal_id, "plan_version": plan.version if plan else None, **result}
