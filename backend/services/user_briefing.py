"""Deterministic, read-only briefing of blocked work for the owner.

This assembles a "heads up" from authoritative local state. It is a briefing,
not a decision: every item is marked non-authorizing, no model text is consulted,
and nothing here can clear a blocker or approve an action. Untrusted stored text
(questions, blocker reasons, requests) is only ever rendered as bounded,
sanitized context — never as an instruction to the reader or the runtime.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

_MAX_TEXT = 160
_MAX_ITEMS = 12

_NOTE = (
    "This is a read-only summary, not an approval. It does not clear a blocker, "
    "grant a permission, or prove that any external effect did or did not occur. "
    "Decisions must go through the trusted review interface."
)


def _clean(value: Any, fallback: str) -> str:
    """Bound and sanitize untrusted stored text for display in a briefing."""
    if not isinstance(value, str):
        return fallback
    text = " ".join(value.split())
    text = "".join(ch for ch in text if ch >= " " or ch in "\t")
    if not text:
        return fallback
    return text[:_MAX_TEXT]


def _item(kind: str, ref: str, title: str, reason: str, action: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "ref": ref,
        "title": title,
        "reason": reason,
        "suggested_action": action,
        "authority": "none",
        "approved": False,
    }


def build_user_briefing(db: Session) -> dict[str, Any]:
    """Collect the owner's blocked-work briefing from durable local state.

    Sources, in fixed order for deterministic output:
      * tasks waiting for user input / held / interrupted;
      * blocked goal plan steps;
      * permission proposals awaiting an owner decision;
      * MainAgent state that is not active.
    """
    from backend.models.database import MainAgent, PlanStep, Task
    from backend.runtime.permission_proposal import PermissionProposalRow

    items: list[dict[str, Any]] = []

    tasks = (
        db.query(Task)
        .filter(Task.state.in_(("waiting_for_user", "paused", "interrupted")))
        .order_by(Task.id.asc())
        .limit(_MAX_ITEMS)
        .all()
    )
    for task in tasks:
        if task.state == "waiting_for_user":
            items.append(_item(
                "task_needs_input", task.id,
                "A task is waiting for your input",
                _clean(task.pending_question, "No question was recorded."),
                "Answer the question in the task view.",
            ))
        else:
            items.append(_item(
                "task_held", task.id,
                "A task is held",
                _clean(task.terminal_reason, f"Task state: {task.state}."),
                "Review it before resuming.",
            ))

    steps = (
        db.query(PlanStep)
        .filter(PlanStep.status == "blocked")
        .order_by(PlanStep.id.asc())
        .limit(_MAX_ITEMS)
        .all()
    )
    for step in steps:
        items.append(_item(
            "goal_step_blocked", step.step_key or f"step-{step.id}",
            "A goal step is blocked",
            _clean(step.blocker_reason, "No blocker reason was recorded."),
            "Review the goal plan.",
        ))

    proposals = (
        db.query(PermissionProposalRow)
        .filter(PermissionProposalRow.status == "awaiting")
        .order_by(PermissionProposalRow.created_at.asc())
        .limit(_MAX_ITEMS)
        .all()
    )
    for proposal in proposals:
        items.append(_item(
            "permission_awaiting", proposal.proposal_id,
            "A permission request is awaiting your decision",
            f"Tool: {_clean(proposal.tool_name, 'unknown')}. "
            "No permission has been granted.",
            "Review the proposal in Settings.",
        ))

    agent = db.query(MainAgent).filter(MainAgent.id == 1).first()
    if agent is not None and agent.state != "active":
        items.append(_item(
            "agent_not_active", "main-agent",
            "Background work is paused",
            f"Main Agent state: {_clean(agent.state, 'unknown')}.",
            "Resume it only when you intend background work.",
        ))

    items = items[:_MAX_ITEMS]
    count = len(items)
    return {
        "headline": (
            f"Heads up — {count} item(s) need your attention." if count
            else "Nothing is blocked right now."
        ),
        "items": items,
        "has_blockers": count > 0,
        "authorized": False,
        "approval_issued": False,
        "execution_started": False,
        "note": _NOTE,
    }
