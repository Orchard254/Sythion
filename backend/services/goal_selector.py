"""RunnableGoalSelector — deterministic goal eligibility service (Phase 4).

Extracts goal-selection from the HTTP layer so the Main Agent, the API, and a
future scheduler all share ONE structural-eligibility implementation. The
selector never executes anything and never consults an LLM.

Ordering policy (deterministic + explainable):
  1. structural eligibility (ACTIVE goal, ACTIVE plan, READY step);
  2. priority ascending (1 = most important);
  3. same-priority fairness: least-recently-served first (last_served_at,
     NULL = never served sorts oldest), then created_at, then id.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.models.database import GoalPlan, Objective, PlanStep
from backend.services.goal_manager import GoalManager


class RunnableGoalSelector:
    def __init__(self, session):
        self.db = session
        self.manager = GoalManager(session)

    # ------------------------------------------------------------------ #

    def explain_ineligibility(self, goal_id: str) -> Dict[str, Any]:
        """Why is this goal not currently runnable? (diagnostics)"""
        goal = self.manager.get_goal(goal_id)
        if goal is None:
            return {"runnable": False, "reasons": ["goal_not_found"]}

        reasons: List[str] = []
        if goal.status != "active":
            reasons.append(f"goal_state_{goal.status}")

        plan = (
            self.db.query(GoalPlan)
            .filter_by(goal_id=goal_id, status="active")
            .order_by(GoalPlan.version.desc())
            .first()
        )
        if plan is None:
            reasons.append("no_active_plan")
        else:
            has_ready = (
                self.db.query(PlanStep.id)
                .filter(PlanStep.plan_id == plan.id)
                .filter(PlanStep.status == "ready")
                .first()
                is not None
            )
            if not has_ready:
                blocked = (
                    self.db.query(PlanStep.id)
                    .filter(PlanStep.plan_id == plan.id)
                    .filter(PlanStep.status == "blocked")
                    .count()
                )
                open_states = ("pending", "running", "in_progress")
                running = (
                    self.db.query(PlanStep.id)
                    .filter(PlanStep.plan_id == plan.id)
                    .filter(PlanStep.status.in_(open_states))
                    .count()
                )
                if blocked:
                    reasons.append("steps_blocked_by_dependencies")
                if running:
                    reasons.append("step_already_running")

        return {"runnable": not reasons, "reasons": reasons,
                "goal_state": goal.status}

    # ------------------------------------------------------------------ #

    def list_runnable_goals(self, respect_backoff: bool = True) -> List[Dict[str, Any]]:
        """Eligible goals in serving order (see module docstring).

        Dependency readiness is refreshed first so BLOCKED steps whose
        dependencies completed unlock deterministically (same rule the
        Planner applies everywhere else).

        ``respect_backoff=True`` excludes goals in their persisted failure
        backoff window (background selection). Explicit user-driven
        execution passes False to override.
        """
        from datetime import datetime as _dt

        from backend.services.planner import Planner

        planner = Planner(self.db)
        now = _dt.utcnow()
        eligible: List[Dict[str, Any]] = []
        for goal in self.db.query(Objective).filter_by(status="active").all():
            if respect_backoff and goal.backoff_until and goal.backoff_until > now:
                continue
            plan = (
                self.db.query(GoalPlan)
                .filter_by(goal_id=goal.id, status="active")
                .order_by(GoalPlan.version.desc())
                .first()
            )
            if plan is None:
                continue
            planner.refresh_readiness(plan.id)
            step = (
                self.db.query(PlanStep)
                .filter(PlanStep.plan_id == plan.id)
                .filter(PlanStep.status == "ready")
                .order_by(PlanStep.priority.asc(), PlanStep.step_order.asc(),
                          PlanStep.id.asc())
                .first()
            )
            if step is None:
                continue
            eligible.append({
                "goal_id": goal.id,
                "title": goal.title,
                "priority": goal.priority,
                "last_served_at": goal.last_served_at,
                "times_served": goal.times_served or 0,
                "created_at": goal.created_at,
                "in_backoff": bool(goal.backoff_until and goal.backoff_until > now),
                "next_step": {
                    "id": step.id,
                    "key": step.step_key,
                    "priority": step.priority,
                },
            })

        epoch = datetime_min()
        eligible.sort(key=lambda g: (
            g["priority"],
            g["times_served"],                # least-served first (strict RR)
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