"""
Durable work-state layer above the task ledger.

Objectives and plan steps are explicit, auditable planning projections that sit
above runtime task truth. Tasks/events remain the source of execution truth;
this layer tracks what the system is trying to achieve, what step is current,
and what remains pending.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.orm import Session, sessionmaker

try:
    from backend.models.database import Objective, PlanStep, Task
except ImportError:
    from models.database import Objective, PlanStep, Task

CONTINUATION_PATTERNS = (
    r"\bcontinue\b",
    r"\bresume\b",
    r"\bproceed\b",
    r"\bgo on\b",
    r"\bkeep going\b",
    r"\bcarry on\b",
    r"\bpick up\b",
    r"\bwhat you were\b",
    r"\bwhere (?:you|we) were\b",
    r"\bfinish (?:the|your) (?:task|work|job)\b",
    r"\byou were\b",
    r"\bas we discussed\b",
    r"\bprevious task\b",
    r"\blast task\b",
    r"\bcontinue where\b",
    r"\bwhat of\b",
    r"\bwhat about\b",
    r"\bhow about\b",
    r"\binstead\b",
)

# Standalone clauses that indicate a fresh standalone question or action request
# even when starting with a leading conjunction/preposition.
_STANDALONE_CLAUSE_RE = re.compile(
    r"\b(?:how(?:'s|\s+is|\s+are|\s+do|\s+can|\s+to)?|"
    r"what(?:'s|\s+is|\s+are|\s+was|\s+were|\s+time|\s+year|\s+date)?|"
    r"why|where|who|when|should\s+i|can\s+you|could\s+you|would\s+you|"
    r"tell\s+me|show\s+me|check\s+my|check\s+the|find\s+all|list\s+all|"
    r"search\s+for|open\s+|close\s+|run\s+|launch\s+|restart\s+|delete\s+|create\s+|write\s+)\b",
    re.IGNORECASE,
)

# Leading conjunctions and prepositions indicating potential short follow-ups
_LEADING_CONJUNCTION_RE = re.compile(
    r"^(?:or|and|in|to)\s+",
    re.IGNORECASE,
)

SHORT_CONTINUATION_LITERALS = {
    "1",
    "1.",
    "option 1",
    "first one",
    "same one",
    "that one",
    "the other one",
    "yes",
    "yeah",
    "yep",
    "ok",
    "okay",
    "sure",
}

SHORT_NON_CONTINUATION_LITERALS = {
    "hi",
    "hey",
    "hello",
    "yo",
    "sup",
    "thanks",
    "thank you",
    "good morning",
    "good afternoon",
    "good evening",
    "good night",
}

OPEN_STEP_STATUSES = {"pending", "in_progress", "blocked"}
ACTIVE_OBJECTIVE_STATUSES = {"active", "paused"}

# Deterministic request decomposition limits (no LLM calls at this layer).
_MAX_DECOMPOSED_STEPS = 5
_MIN_STEP_LENGTH = 12
_MAX_STEP_DESCRIPTION = 500

# Sequence connectors that signal ordered sub-tasks inside one request.
_SEQUENCE_SPLIT_RE = re.compile(
    r"\s+(?:and\s+)?(?:then|next|afterwards|after that|finally|lastly|eventually)\s*[,:]?\s+",
    re.IGNORECASE,
)

# Sentence-boundary split: period/question/exclamation followed by a capital letter, digit, or quote.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")

# Leading connector words stripped from decomposed parts ("First, do X" -> "do X").
_LEADING_CONNECTOR_RE = re.compile(
    r"^(?:first|firstly|second|secondly|third|thirdly|then|next|finally|lastly|after that)\s*[,:-]?\s+",
    re.IGNORECASE,
)


def _clean_step(text: str) -> str:
    """Normalize a decomposed step description for storage."""
    value = re.sub(r"\s+", " ", text.strip())
    value = _LEADING_CONNECTOR_RE.sub("", value).strip()
    if not value:
        return ""
    return _trim(value, limit=_MAX_STEP_DESCRIPTION)


def decompose_request(request: str) -> list[str]:
    """Deterministically split a complex request into ordered sub-steps.

    Uses explicit sequence connectors ("then", "next", "after that",
    "finally") and sentence boundaries. Conservative by design:

    - no decomposition when fewer than two meaningful parts exist
    - each part must be a real action phrase (not a fragment)
    - bounded to at most ``_MAX_DECOMPOSED_STEPS`` steps
    - never used for continuation/greeting handling (callers decide)

    Returns an empty list when the request should stay a single step.
    """
    text = (request or "").strip()
    if not text:
        return []

    parts = [p.strip() for p in _SEQUENCE_SPLIT_RE.split(text) if p.strip()]
    if len(parts) == 1:
        sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]
        if len(sentences) >= 2:
            parts = sentences

    cleaned: list[str] = []
    seen: set[str] = set()
    for part in parts:
        step = _clean_step(part)
        if len(step) < _MIN_STEP_LENGTH:
            continue
        key = step.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(step)
        if len(cleaned) >= _MAX_DECOMPOSED_STEPS:
            break

    return cleaned if len(cleaned) >= 2 else []


def is_continuation_request(request: str) -> bool:
    """Detect explicit continuation/follow-up requests without treating every
    short standalone message as continuation.
    """
    if not request or not request.strip():
        return True
    text = " ".join(request.lower().split())
    if text in SHORT_NON_CONTINUATION_LITERALS:
        return False
    if text in SHORT_CONTINUATION_LITERALS:
        return True
    if any(re.search(pattern, text) for pattern in CONTINUATION_PATTERNS):
        return True

    # Check leading conjunction / preposition follow-ups (e.g. 'or kb', 'and in gb', 'to mb')
    m = _LEADING_CONJUNCTION_RE.match(text)
    if m:
        tail = text[m.end():].strip()
        if not _STANDALONE_CLAUSE_RE.search(tail) and len(tail.split()) <= 6:
            return True

    return False


def _trim(text: Optional[str], limit: int = 120) -> str:
    value = (text or "").strip()
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def _objective_seed_text(task: Task) -> str:
    return (task.normalized_goal or task.original_request or "").strip()


def _objective_title(task: Task) -> str:
    seed = _objective_seed_text(task) or "Untitled objective"
    title = re.sub(r"\s+", " ", seed)
    return _trim(title, limit=90) or "Untitled objective"


def _current_open_step(db: Session, objective_id: str) -> Optional[PlanStep]:
    steps = (
        db.query(PlanStep)
        .filter(PlanStep.objective_id == objective_id)
        .order_by(PlanStep.step_order.asc(), PlanStep.id.asc())
        .all()
    )
    for step in steps:
        if step.status in OPEN_STEP_STATUSES:
            return step
    return None


def _next_step_order(db: Session, objective_id: str) -> int:
    latest = (
        db.query(PlanStep)
        .filter(PlanStep.objective_id == objective_id)
        .order_by(PlanStep.step_order.desc(), PlanStep.id.desc())
        .first()
    )
    return (latest.step_order + 1) if latest else 1


def ensure_task_objective(db: Session, task: Task) -> Objective:
    """Attach a task to an explicit objective, creating one when needed."""
    if task.objective_id:
        existing = db.query(Objective).filter_by(id=task.objective_id).first()
        if existing:
            return existing

    objective: Optional[Objective] = None
    if is_continuation_request(task.original_request or ""):
        objective = (
            db.query(Objective)
            .filter(Objective.status.in_(tuple(ACTIVE_OBJECTIVE_STATUSES)))
            .order_by(Objective.updated_at.desc(), Objective.created_at.desc())
            .first()
        )

    if objective is None:
        # PHASE 3.5 SUNSET: creation goes through the canonical GoalManager
        # (single creation authority + goal.created event). The attached
        # steps remain un-planned legacy rows until this path retires.
        from backend.services.goal_manager import GoalManager

        seed = _objective_seed_text(task)
        objective = GoalManager(db).create_goal(
            title=_objective_title(task),
            purpose=seed or None,
            priority=3,
        )
        objective.last_task_id = task.id
        steps = decompose_request(seed) or [seed or objective.title]
        for idx, description in enumerate(steps, start=1):
            db.add(
                PlanStep(
                    objective_id=objective.id,
                    step_order=idx,
                    description=description,
                    status="in_progress" if idx == 1 else "pending",
                    linked_task_id=task.id if idx == 1 else None,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
            )
        db.flush()
    else:
        open_step = _current_open_step(db, objective.id)
        if open_step is None:
            description = (
                _objective_seed_text(task)
                if not is_continuation_request(task.original_request or "")
                else f"Continue objective: {objective.title}"
            )
            db.add(
                PlanStep(
                    objective_id=objective.id,
                    step_order=_next_step_order(db, objective.id),
                    description=description,
                    status="in_progress",
                    linked_task_id=task.id,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
            )
        else:
            open_step.linked_task_id = task.id
            if open_step.status in {"pending", "blocked"}:
                open_step.status = "in_progress"
                open_step.blocker_reason = None
            open_step.updated_at = datetime.utcnow()
        objective.status = "active"
        objective.last_task_id = task.id
        if not objective.current_summary:
            objective.current_summary = "Continuation task attached to active objective."
        objective.updated_at = datetime.utcnow()

    task.objective_id = objective.id
    task.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(task)
    db.refresh(objective)
    
    # Verify the objective and its plan steps persisted
    try:
        engine = db.bind
        SessionLocal = sessionmaker(bind=engine)
        with SessionLocal() as verify_session:
            obj_verify = verify_session.query(Objective).filter_by(id=objective.id).first()
            if obj_verify is None:
                import logging
                logging.getLogger(__name__).warning("Verification failed: Objective not found for task %s", task.id)
                return None
            # Verify plan steps exist
            steps_verify = verify_session.query(PlanStep).filter_by(objective_id=objective.id).all()
            if not steps_verify:
                import logging
                logging.getLogger(__name__).warning("Verification failed: No plan steps for objective %s", objective.id)
                return None
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("Verification of objective creation failed: %s", exc)
        return None
    
    return objective


# Phase 2: goal statuses owned by GoalManager; once a goal reaches a
# terminal state the legacy task-driven projection must not change it.
_TERMINAL_GOAL_STATUSES = {"completed", "failed", "abandoned"}


def sync_objective_state_for_task(
    db: Session,
    task: Task,
    *,
    event_type: str,
    event_data: Optional[dict[str, Any]] = None,
) -> Optional[Objective]:
    """Project runtime task state into the higher-level objective/plan view."""
    if not task.objective_id:
        return None

    objective = db.query(Objective).filter_by(id=task.objective_id).first()
    if objective is None:
        return None
    if objective.status in _TERMINAL_GOAL_STATUSES:
        # Terminal goals stay terminal; summaries remain untouched by the
        # legacy projection. Lifecycle changes go through GoalManager.
        return objective

    # Phase 3 legacy-boundary guard: goals with a plan-backed step set are
    # owned by GoalManager/Planner. The legacy projection must NEVER mutate
    # their (or their steps') state — doing so corrupted orchestration by
    # pausing goals on transient task failures.
    plan_backed_goal = (
        db.query(PlanStep.id)
        .filter(PlanStep.objective_id == objective.id)
        .filter(PlanStep.plan_id.isnot(None))
        .first()
        is not None
    )
    if plan_backed_goal:
        return objective

    event_data = event_data or {}
    step = (
        db.query(PlanStep)
        .filter(PlanStep.objective_id == objective.id, PlanStep.linked_task_id == task.id)
        .order_by(PlanStep.step_order.desc(), PlanStep.id.desc())
        .first()
    ) or _current_open_step(db, objective.id)

    now = datetime.utcnow()
    objective.last_task_id = task.id
    objective.updated_at = now

    def _note(*values: Optional[str], default: str = "") -> str:
        for value in values:
            if value and str(value).strip():
                return _trim(str(value), 220)
        return default

    state = task.state
    # Phase 2: for plan-backed steps the Planner owns status transitions;
    # this legacy projection only annotates summaries.
    plan_backed_step = step is not None and getattr(step, "plan_id", None) is not None
    if state == "running":
        objective.status = "active"
        objective.current_summary = _note(
            event_data.get("note"),
            f"Working on: {step.description}" if step else None,
            task.original_request,
            default="Objective is in progress.",
        )
        if step is not None and not plan_backed_step:
            step.status = "in_progress"
            step.blocker_reason = None
            step.linked_task_id = task.id
            step.updated_at = now
    elif state == "waiting_for_user":
        objective.status = "active"
        objective.current_summary = _note(
            task.pending_question,
            event_data.get("question"),
            default="Awaiting user input.",
        )
        if step is not None and not plan_backed_step:
            step.status = "blocked"
            step.blocker_reason = _note(task.pending_question, event_data.get("question"))
            step.linked_task_id = task.id
            step.updated_at = now
    elif state == "completed":
        completion_note = _note(task.final_result, event_data.get("summary"), default="Step completed.")
        objective.current_summary = completion_note
        if step is not None and not plan_backed_step:
            step.status = "completed"
            step.result_summary = completion_note
            step.blocker_reason = None
            step.linked_task_id = task.id
            step.updated_at = now
        remaining = (
            db.query(PlanStep)
            .filter(PlanStep.objective_id == objective.id, PlanStep.status.in_(tuple(OPEN_STEP_STATUSES | {"failed"})))
            .count()
        )
        # If there are completion criteria, they must be verified before objective completes
        if remaining == 0:
            if objective.completion_criteria and not objective.criteria_verified:
                objective.status = "active"
                objective.current_summary = _note(
                    objective.current_summary,
                    f"All steps complete; awaiting verification of criteria: {_trim(objective.completion_criteria, 150)}",
                    default="All steps complete; awaiting criteria verification.",
                )
            else:
                objective.status = "completed"
        else:
            objective.status = "active"
    elif state == "failed":
        failure_note = _note(task.terminal_reason, event_data.get("reason"), default="Task failed.")
        objective.status = "paused"
        objective.current_summary = f"Task failed: {failure_note}"
        if step is not None and not plan_backed_step:
            step.status = "failed"
            step.result_summary = failure_note
            step.blocker_reason = failure_note
            step.linked_task_id = task.id
            step.updated_at = now
    elif state == "cancelled":
        cancel_note = _note(task.terminal_reason, event_data.get("reason"), default="cancelled_by_user")
        objective.status = "paused"
        objective.current_summary = f"Task cancelled: {cancel_note}"
        if step is not None and not plan_backed_step:
            step.status = "skipped"
            step.result_summary = cancel_note
            step.blocker_reason = None
            step.linked_task_id = task.id
            step.updated_at = now
    elif state in {"interrupted", "paused"}:
        pause_note = _note(event_data.get("note"), event_data.get("reason"), default=f"Task {state}.")
        objective.status = "paused"
        objective.current_summary = pause_note
        if step is not None and not plan_backed_step:
            step.status = "blocked"
            step.blocker_reason = pause_note
            step.linked_task_id = task.id
            step.updated_at = now

    db.commit()
    db.refresh(objective)
    
    # Verify the objective state sync persisted
    try:
        engine = db.bind
        SessionLocal = sessionmaker(bind=engine)
        with SessionLocal() as verify_session:
            obj_verify = verify_session.query(Objective).filter_by(id=objective.id).first()
            if obj_verify is None:
                import logging
                logging.getLogger(__name__).warning("Verification failed: Objective not found for task %s", task.id)
                return None
            # Verify step status if step was updated
            if step is not None:
                step_verify = verify_session.query(PlanStep).filter_by(id=step.id).first()
                if step_verify is None:
                    import logging
                    logging.getLogger(__name__).warning("Verification failed: PlanStep not found for task %s", task.id)
                    return None
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("Verification of objective sync failed: %s", exc)
        return None
    
    return objective


def build_work_context(db: Session, *, task_id: str, original_request: str) -> str:
    """Format the most relevant durable work-state for model context."""
    task = db.query(Task).filter(Task.id == task_id).first()
    objective = None
    if task and task.objective_id:
        objective = db.query(Objective).filter_by(id=task.objective_id).first()
    elif is_continuation_request(original_request):
        objective = (
            db.query(Objective)
            .filter(Objective.status.in_(tuple(ACTIVE_OBJECTIVE_STATUSES)))
            .order_by(Objective.updated_at.desc(), Objective.created_at.desc())
            .first()
        )

    if objective is None:
        return ""

    steps = (
        db.query(PlanStep)
        .filter(PlanStep.objective_id == objective.id)
        .order_by(PlanStep.step_order.asc(), PlanStep.id.asc())
        .limit(12)
        .all()
    )

    lines = [
        "CURRENT OBJECTIVE & PLAN:",
        f"- objective id: {objective.id}",
        f"- title: {objective.title}",
        f"- status: {objective.status}",
        f"- priority: {objective.priority}",
    ]
    if objective.purpose:
        lines.append(f"- purpose: {_trim(objective.purpose, 300)}")
    if objective.current_summary:
        lines.append(f"- current summary: {_trim(objective.current_summary, 500)}")
    if objective.last_task_id:
        lines.append(f"- last linked task: {objective.last_task_id}")
    current_step = _current_open_step(db, objective.id)
    if steps:
        lines.append("- plan steps:")
        for step in steps:
            is_current = current_step and current_step.id == step.id
            status_str = f"{step.status} - CURRENT" if is_current else step.status
            line = f"  {step.step_order}. [{status_str}] {_trim(step.description, 160)}"
            extras = []
            if step.linked_task_id:
                extras.append(f"task={step.linked_task_id}")
            if step.blocker_reason and step.status == "blocked":
                extras.append(f"blocked={_trim(step.blocker_reason, 150)}")
            elif step.result_summary and step.status in {"completed", "failed", "skipped"}:
                extras.append(f"result={_trim(step.result_summary, 200)}")
            if extras:
                line += " (" + "; ".join(extras) + ")"
            lines.append(line)
    return "\n".join(lines)
