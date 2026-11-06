"""Goal Manager — authoritative owner of goal (objective) lifecycle state.

Phase 2 planning layer. The GoalManager owns goal lifecycle transitions and
derives progress from persisted step state. The LLM may propose goals; it can
never establish ``completed``/``failed`` — those transitions require the
deterministic eligibility rules implemented here.

EVENT LEDGER CONVENTION: goal/plan audit events are appended to the existing
immutable ``task_events`` ledger under a synthetic scope id
``goal:<goal_id>``. This reuses the ledger's per-scope strict sequence
ordering and immutability instead of creating a second event mechanism.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from backend.models.database import Objective, PlanStep
from backend.runtime.events import DbEventLedger


class InvalidGoalTransitionError(ValueError):
    """Raised when a goal lifecycle transition is not permitted."""

    def __init__(self, current: str, requested: str):
        super().__init__(
            f"Invalid goal transition: {current} -> {requested}"
        )
        self.current = current
        self.requested = requested


# Explicit lifecycle state machine. Terminal states have no exits;
# COMPLETED → ACTIVE (the example from the architecture mandate) is refused.
GOAL_TRANSITIONS: Dict[str, set] = {
    "active": {"paused", "completed", "failed", "abandoned"},
    "paused": {"active", "completed", "failed", "abandoned"},
    "completed": set(),
    "failed": set(),
    "abandoned": set(),
}

TERMINAL_GOAL_STATUSES = {"completed", "failed", "abandoned"}


def _scope(goal_id: str) -> str:
    return f"goal:{goal_id}"


def _trim(text: Optional[str], limit: int) -> Optional[str]:
    value = (text or "").strip()
    if not value:
        return None
    return value if len(value) <= limit else value[: limit - 3].rstrip() + "..."


class GoalManager:
    """Owns goal lifecycle transitions over the existing Objective table."""

    def __init__(self, session):
        self.db = session
        self.ledger = DbEventLedger(session)

    # --- creation / reads -----------------------------------------------------

    def create_goal(
        self,
        title: str,
        purpose: Optional[str] = None,
        priority: int = 3,
        completion_criteria: Optional[str] = None,
        parent_objective_id: Optional[str] = None,
    ) -> Objective:
        now = datetime.utcnow()
        goal = Objective(
            id=str(uuid.uuid4()),
            title=_trim(title, 90) or "Untitled goal",
            purpose=_trim(purpose, 500),
            status="active",
            priority=max(1, min(5, int(priority))),
            completion_criteria=_trim(completion_criteria, 300),
            parent_objective_id=parent_objective_id,
            created_at=now,
            updated_at=now,
        )
        self.db.add(goal)
        self.db.flush()
        self.ledger.append_event(_scope(goal.id), "goal.created", {
            "title": goal.title,
            "priority": goal.priority,
        })
        self.db.commit()
        self.db.refresh(goal)
        return goal

    def get_goal(self, goal_id: str) -> Optional[Objective]:
        return self.db.query(Objective).filter_by(id=goal_id).first()

    def list_goals(
        self,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[Objective]:
        query = self.db.query(Objective)
        if status:
            query = query.filter(Objective.status == status)
        return (
            query.order_by(Objective.updated_at.desc(), Objective.created_at.desc())
            .limit(limit)
            .all()
        )

    # --- lifecycle --------------------------------------------------------------

    def transition_goal(
        self,
        goal_id: str,
        new_status: str,
        event_data: Optional[Dict[str, Any]] = None,
        note: Optional[str] = None,
    ) -> Objective:
        """Validate and apply a goal lifecycle transition (deterministic).

        Raises:
            ValueError: goal not found or unknown status.
            InvalidGoalTransitionError: transition not in GOAL_TRANSITIONS.
        """
        goal = self.get_goal(goal_id)
        if goal is None:
            raise ValueError(f"Goal {goal_id} not found")
        new_status = (new_status or "").strip().lower()
        if new_status not in GOAL_TRANSITIONS:
            raise ValueError(f"Unknown goal status: {new_status}")

        current = goal.status
        if new_status == current:
            return goal  # idempotent no-op
        if new_status not in GOAL_TRANSITIONS.get(current, set()):
            raise InvalidGoalTransitionError(current, new_status)

        goal.status = new_status
        goal.updated_at = datetime.utcnow()
        payload = dict(event_data or {})
        if note:
            goal.current_summary = _trim(note, 500)
        self.ledger.append_event(_scope(goal_id), f"goal.{new_status}", payload)
        self.db.commit()
        self.db.refresh(goal)
        # Phase 11.5: goal-level outcome capture distinct from step outcomes (§5)
        try:
            if new_status in ("completed", "failed", "abandoned"):
                from backend.learning.capture import record_goal_outcome

                lo = {"completed": "SUCCESS_VERIFIED", "failed": "FAILED", "abandoned": "CANCELLED"}.get(new_status, "FAILED")
                res = record_goal_outcome(
                    self.db,
                    goal_id=goal_id,
                    outcome=lo,
                    evidence_refs=[f"goal:{goal_id}"],
                )
                from backend.learning.capture import report_capture_result
                report_capture_result("goal_outcome", res, goal_id=goal_id, outcome=lo)
        except Exception as exc:
            from backend.learning.capture import log_capture_failure
            log_capture_failure("goal_outcome", exc, goal_id=goal_id)
        return goal

    def pause_goal(self, goal_id: str, reason: Optional[str] = None) -> Objective:
        return self.transition_goal(
            goal_id, "paused", event_data={"reason": reason} if reason else None
        )

    def resume_goal(self, goal_id: str) -> Objective:
        return self.transition_goal(goal_id, "active")

    def abandon_goal(self, goal_id: str, reason: Optional[str] = None) -> Objective:
        return self.transition_goal(
            goal_id, "abandoned", event_data={"reason": reason} if reason else None
        )

    def fail_goal(self, goal_id: str, reason: str) -> Objective:
        return self.transition_goal(
            goal_id, "failed", event_data={"reason": reason}
        )

    def complete_goal(self, goal_id: str, note: Optional[str] = None) -> Objective:
        """Complete a goal ONLY when persisted step state satisfies the rules.

        Eligibility (deterministic): the active plan's steps must all be in a
        terminal state, with every non-cancelled step completed. If explicit
        completion criteria exist they must already be verified (the owner
        verifies criteria via the existing verify-criteria endpoint).
        """
        goal = self.get_goal(goal_id)
        if goal is None:
            raise ValueError(f"Goal {goal_id} not found")

        steps = (
            self.db.query(PlanStep)
            .filter(PlanStep.objective_id == goal_id)
            .all()
        )
        open_states = {"pending", "ready", "running", "in_progress", "blocked"}
        open_steps = [s for s in steps if s.status in open_states]
        if open_steps:
            raise ValueError(
                f"Cannot complete goal {goal_id}: "
                f"{len(open_steps)} step(s) still open"
            )
        required = [s for s in steps if s.status != "cancelled"]
        not_completed = [s for s in required if s.status != "completed"]
        if required and not_completed:
            raise ValueError(
                f"Cannot complete goal {goal_id}: "
                f"{len(not_completed)} required step(s) are not completed"
            )
        if goal.completion_criteria and not goal.criteria_verified:
            raise ValueError(
                f"Cannot complete goal {goal_id}: completion criteria are not verified"
            )

        return self.transition_goal(
            goal_id,
            "completed",
            note=note or "All required steps completed.",
        )

    # --- deterministic progress ---------------------------------------------

    def record_service_outcome(self, goal_id: str, *, step_failed: bool) -> Dict[str, Any]:
        """Phase 4.5 fairness accounting: persist per-goal failure streak and
        apply bounded backoff after repeated failed sessions.

        Policy (deterministic, persisted, auditable):
          - any completed step resets the failure streak AND clears backoff;
          - each session whose step(s) failed increments the streak;
          - at ``backoff_failure_threshold`` consecutive failures the goal is
            deprioritized for min(base * 2^(n-threshold), max) seconds;
          - backoff only affects BACKGROUND selection — an owner explicitly
            executing the goal (advance/execute endpoints) bypasses it.
        """
        from datetime import timedelta

        from backend.core.config import settings

        goal = self.get_goal(goal_id)
        if goal is None:
            return {"backoff_applied": False, "reason": "goal_not_found"}

        now = datetime.utcnow()
        if not step_failed:
            changed = False
            if goal.consecutive_failed_sessions:
                goal.consecutive_failed_sessions = 0
                changed = True
            if goal.backoff_until:
                goal.backoff_until = None
                self.ledger.append_event(_scope(goal_id), "goal.backoff.cleared", {
                    "reason": "step_completed",
                })
                changed = True
            if changed:
                db_commit = getattr(self.db, "commit")
                db_commit()
            return {"backoff_applied": False,
                    "consecutive_failed_sessions": goal.consecutive_failed_sessions}

        streak = (goal.consecutive_failed_sessions or 0) + 1
        goal.consecutive_failed_sessions = streak

        threshold = max(1, settings.backoff_failure_threshold)
        applied = {"backoff_applied": False}
        if streak >= threshold:
            exponent = streak - threshold
            delay = min(
                settings.backoff_base_seconds * (2 ** exponent),
                settings.backoff_max_seconds,
            )
            goal.backoff_until = now + timedelta(seconds=delay)
            self.ledger.append_event(_scope(goal_id), "goal.backoff.applied", {
                "streak": streak,
                "backoff_until": goal.backoff_until.isoformat(),
                "delay_seconds": delay,
            })
            applied = {
                "backoff_applied": True,
                "delay_seconds": delay,
                "backoff_until": goal.backoff_until.isoformat(),
            }

        objective_note = f"failure streak {streak}"
        goal.current_summary = _trim(objective_note, 120)
        self.db.commit()
        return applied

    def in_backoff(self, goal_id: str, now: Optional[datetime] = None) -> bool:
        goal = self.get_goal(goal_id)
        if goal is None or not goal.backoff_until:
            return False
        return (now or datetime.utcnow()) < goal.backoff_until

    def clear_backoff(self, goal_id: str, reason: str = "owner_override") -> None:
        """Explicit user override: clear a backoff immediately (audited)."""
        from backend.core.config import settings  # noqa: F401 (policy anchor)

        goal = self.get_goal(goal_id)
        if goal is None:
            raise ValueError(f"Goal {goal_id} not found")
        if goal.backoff_until:
            goal.backoff_until = None
            goal.consecutive_failed_sessions = 0
            self.ledger.append_event(_scope(goal_id), "goal.backoff.cleared", {
                "reason": reason,
            })
            self.db.commit()

    def progress(self, goal_id: str) -> Dict[str, Any]:
        """Progress derived purely from persisted step state.

        Formula: completed_required / total_required where required excludes
        cancelled steps of the active plan (or all steps for legacy goals).
        No model-generated percentages.
        """
        plan = (
            self.db.query(PlanStep)
            .filter(PlanStep.objective_id == goal_id)
            .filter(PlanStep.plan_id.isnot(None))
            .all()
        )
        legacy = (
            self.db.query(PlanStep)
            .filter(PlanStep.objective_id == goal_id)
            .filter(PlanStep.plan_id.is_(None))
            .all()
        )
        steps = plan if plan else legacy
        required = [s for s in steps if s.status != "cancelled"]
        completed = sum(1 for s in required if s.status == "completed")
        total = len(required)
        return {
            "completed_steps": completed,
            "total_required_steps": total,
            "fraction": round(completed / total, 4) if total else 0.0,
            "open_step_states": sorted({
                s.status for s in steps
                if s.status in {"pending", "ready", "running", "in_progress", "blocked"}
            }),
        }


# ============================================================================ #
# Legacy objective migration (Phase 3.5 sunset)                                #
# ============================================================================ #

def migrate_legacy_objectives(db, include_empty: bool = False) -> Dict[str, Any]:
    """Deterministically convert unplanned legacy objectives into canonical
    plan-backed goals. IDEMPOTENT: objectives that already contain a
    plan-backed step are skipped, so running twice creates nothing.

    Conservative state-mapping rules (never fabricate completion):
      - legacy 'completed' WITH evidence (evidence task or a recorded
        result summary)  → step COMPLETED, evidence reference preserved;
      - legacy 'completed' WITHOUT evidence → PENDING (explicitly recoverable;
        the claim cannot be verified so it is not trusted);
      - legacy 'failed' → FAILED with blocker/failure reason preserved;
      - everything else (pending/in_progress/blocked/skipped/unknown)
        → per-plan default (PENDING first step, BLOCKED dependents), i.e.
          explicitly recoverable.
    """
    from backend.services.planner import CandidateStepModel, Planner

    planner = Planner(db)
    ledger = DbEventLedger(db)
    report: Dict[str, Any] = {"migrated": [], "skipped": []}

    objectives = (
        db.query(Objective)
        .order_by(Objective.created_at.asc())
        .all()
    )
    for objective in objectives:
        already_canonical = (
            db.query(PlanStep.id)
            .filter(PlanStep.objective_id == objective.id)
            .filter(PlanStep.plan_id.isnot(None))
            .first()
            is not None
        )
        if already_canonical:
            continue

        legacy_steps = sorted(
            db.query(PlanStep)
            .filter(PlanStep.objective_id == objective.id)
            .filter(PlanStep.plan_id.is_(None))
            .order_by(PlanStep.step_order.asc(), PlanStep.id.asc())
            .all(),
            key=lambda s: (s.step_order, s.id),
        )
        if not legacy_steps:
            report["skipped"].append({
                "objective_id": objective.id,
                "reason": "no_legacy_steps",
                "detail": "Nothing actionable to plan; left as-is.",
            })
            continue

        # Linear chain preserves the legacy ordering semantics.
        # Conservative normalization: ensure CandidateStepModel.description >= 3 chars
        # while preserving valid descriptions verbatim and legacy rows intact.
        try:
            candidates = []
            for idx, s in enumerate(legacy_steps, start=1):
                raw_desc = (s.description or "").strip()
                if len(raw_desc) >= 3:
                    clean_desc = raw_desc[:500]
                else:
                    clean_desc = f"Legacy step {idx}"
                candidates.append(
                    CandidateStepModel(
                        key=f"step_{idx}",
                        description=clean_desc,
                        depends_on=[f"step_{idx - 1}"] if idx > 1 else [],
                    )
                )
            plan = planner.create_plan(
                objective.id,
                candidates,
                source="deterministic",
                reason="legacy_objective_migration",
            )
        except Exception as exc:  # pragma: no cover - validation is deterministic
            report["skipped"].append({
                "objective_id": objective.id,
                "reason": "migration_validation_failed",
                "detail": str(exc)[:300],
            })
            continue

        new_steps = {
            s.step_key: s for s in planner.plan_steps(plan.id)
        }
        carried_completed = 0
        for idx, legacy in enumerate(legacy_steps, start=1):
            new_step = new_steps[f"step_{idx}"]
            has_evidence = bool(legacy.evidence_task_id
                                or (legacy.result_summary or "").strip())
            if legacy.status == "completed" and has_evidence:
                new_step.status = "completed"
                new_step.result_summary = legacy.result_summary
                new_step.evidence_task_id = legacy.evidence_task_id
                carried_completed += 1
            elif legacy.status == "failed":
                new_step.status = "failed"
                new_step.failure_reason = (
                    legacy.blocker_reason or "carried from legacy objective"
                )[:1000]
            # else: leave the plan default (pending/blocked) — recoverable.
            new_step.updated_at = datetime.utcnow()

        ledger.append_event(_scope(objective.id), "goal.migrated", {
            "plan_id": plan.id,
            "version": plan.version,
            "legacy_steps": len(legacy_steps),
            "carried_completed_with_evidence": carried_completed,
        })
        db.commit()
        report["migrated"].append({
            "objective_id": objective.id,
            "title": objective.title,
            "plan_version": plan.version,
            "steps": len(candidates),
            "carried_completed_with_evidence": carried_completed,
        })

    if report["migrated"] or report["skipped"]:
        import logging

        logging.getLogger(__name__).info(
            "Legacy objective migration: %d migrated, %d skipped",
            len(report["migrated"]), len(report["skipped"]),
        )
    return report
