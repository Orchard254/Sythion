"""RunnableGoalSelector — deterministic goal eligibility service (Phase 4).

Extracts goal-selection from the HTTP layer so the Main Agent, the API, and a
future scheduler all share ONE structural-eligibility implementation. The
selector never executes anything and never consults an LLM.

Ordering policy (deterministic + explainable, no readiness writes):
  1. structural eligibility (ACTIVE goal, ACTIVE plan, READY step);
  2. priority ascending (1 = most important);
  3. same-priority fairness: least-recently-served first (last_served_at,
     NULL = never served sorts oldest), then created_at, then id.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.models.database import GoalPlan, Objective, PlanStep
from backend.services.goal_manager import GoalManager
from backend.services.goal_work_preview import read_goal_work_preview


class RunnableGoalSelector:
    def __init__(self, session):
        self.db = session
        self.manager = GoalManager(session)

    # ------------------------------------------------------------------ #

    def explain_ineligibility(self, goal_id: str) -> Dict[str, Any]:
        """Explain current eligibility without changing the goal or plan."""
        try:
            preview = read_goal_work_preview(self.db, goal_id)
        except LookupError:
            return {"runnable": False, "reasons": ["goal_not_found"]}

        if preview["runnable"]:
            reasons: List[str] = []
        elif preview["state"] == "goal_paused":
            reasons = ["goal_state_paused"]
        elif preview["state"] == "goal_not_active":
            reasons = [f"goal_state_{preview['goal_state']}"]
        elif preview["state"] == "plan_needed":
            reasons = ["no_active_plan"]
        elif preview["state"] == "waiting_for_dependencies":
            reasons = ["steps_blocked_by_dependencies"]
        elif preview["state"] == "step_in_progress":
            reasons = ["step_already_running"]
        else:
            reasons = [preview["state"]]
        return {
            "runnable": preview["runnable"], "reasons": reasons,
            "goal_state": preview["goal_state"], "next_step": preview["next_step"],
            "state": preview["state"],
        }

    # ------------------------------------------------------------------ #

    def list_runnable_goals(self, respect_backoff: bool = True) -> List[Dict[str, Any]]:
        """Side-effect-free scheduler candidates in persisted fairness order.

        BLOCKED steps with complete dependencies are *virtually* eligible here;
        the Planner performs the actual audited readiness transition only in
        the lease-protected step-start path. A GET must not commit or append
        plan_step.ready events. Plan corruption never becomes permission to run.
        """
        eligible: List[Dict[str, Any]] = []
        for goal in self.db.query(Objective).filter_by(status="active").all():
            preview = read_goal_work_preview(
                self.db, goal.id, respect_backoff=respect_backoff,
            )
            if not preview["runnable"]:
                continue
            step = preview["next_step"]
            eligible.append({
                "goal_id": goal.id,
                "title": goal.title,
                "priority": goal.priority,
                "last_served_at": goal.last_served_at,
                "times_served": goal.times_served or 0,
                "created_at": goal.created_at,
                "in_backoff": preview["in_backoff"],
                "next_step": {
                    "id": step["id"],
                    "key": step["key"],
                    "priority": step["priority"],
                    "would_unlock": step["would_unlock"],
                },
            })

        epoch = datetime_min()
        eligible.sort(key=lambda g: (
            g["priority"],
            g["times_served"],
            g["last_served_at"] or epoch,
            g["created_at"] or epoch,
            g["goal_id"],
        ))
        for rank, item in enumerate(eligible, start=1):
            item["selection_rank"] = rank
        return eligible

    def pick_goal(self, respect_backoff: bool = True) -> Optional[Dict[str, Any]]:
        """The single goal that receives the current execution slot.

        Background callers use the default (backoff respected). Explicit
        owner-driven paths pass ``respect_backoff=False`` to override a
        failure backoff for their requested goal.
        """
        runnable = self.list_runnable_goals(respect_backoff=respect_backoff)
        return runnable[0] if runnable else None

    def mark_served(self, goal_id: str) -> None:
        """Record that this goal just received an execution slot (fairness).

        Uses a monotonic persisted counter so equal-priority round-robin is
        strict even when timestamps collide within the same clock tick.
        """
        from datetime import datetime

        goal = self.manager.get_goal(goal_id)
        if goal is None:
            return
        goal.last_served_at = datetime.utcnow()
        goal.times_served = (goal.times_served or 0) + 1
        goal.updated_at = datetime.utcnow()
        self.db.commit()


def datetime_min():
    from datetime import datetime

    return datetime.min