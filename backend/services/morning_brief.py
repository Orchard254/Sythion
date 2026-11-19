"""Deterministic, read-only morning brief assembled from local state.

This is the Core's proactive "voice": one durable brief per local date, built
without any model call, tool execution, or authority. It reports what happened
(goals/tasks finished), what is stuck (the blocked-work briefing), the agent's
state, and local system health. Every brief is marked non-authorizing: it is
information for the owner, never approval, clearance, or proof of an effect.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

_MAX_LINES = 10

_NOTE = (
    "This is a read-only brief from local state. It is not an approval, does "
    "not grant permission, and does not prove that any external effect did or "
    "did not occur."
)


def _bound(value: Any, fallback: str, limit: int = 120) -> str:
    if not isinstance(value, str):
        return fallback
    text = " ".join(value.split())
    text = "".join(ch for ch in text if ch >= " " or ch in "\t")
    return (text or fallback)[:limit]


def _section(key: str, title: str, lines: list[str]) -> dict[str, Any]:
    return {"key": key, "title": title, "lines": lines[:_MAX_LINES]}


def build_morning_brief(db: Session, *, now: datetime | None = None) -> dict[str, Any]:
    """Compose today's brief deterministically. Performs no writes and no model calls."""
    from backend.models.database import MainAgent, Objective, Task
    from backend.services.user_briefing import build_user_briefing

    now = now or datetime.now()
    since = now - timedelta(days=1)
    date_str = now.date().isoformat()

    finished_goals = (
        db.query(Objective)
        .filter(Objective.status == "completed", Objective.updated_at >= since)
        .order_by(Objective.updated_at.desc())
        .limit(_MAX_LINES)
        .all()
    )
    finished_tasks = (
        db.query(Task)
        .filter(Task.state == "completed", Task.updated_at >= since)
        .order_by(Task.updated_at.desc())
        .limit(_MAX_LINES)
        .all()
    )

    blocked = build_user_briefing(db)
    blocker_lines = [
        f"{item['title']}: {item['reason']}" for item in blocked["items"]
    ]

    agent = db.query(MainAgent).filter(MainAgent.id == 1).first()
    if agent is None:
        agent_lines = ["Main Agent has not been initialized."]
    else:
        agent_lines = [
            f"State: {_bound(agent.state, 'unknown')}.",
            f"Last wake: {_bound(agent.last_wake_reason, 'none recorded')}.",
            f"Last outcome: {_bound(agent.last_session_outcome, 'none recorded')}.",
        ]

    try:
        from backend.services.system_state import run_health_checks

        health = run_health_checks(db_session=db)
        health_lines = [
            f"Overall: {_bound(health.get('overall'), 'unknown')}."
        ]
        for check in health.get("checks", []):
            if not isinstance(check, dict) or check.get("disabled"):
                continue
            health_lines.append(
                f"{_bound(check.get('name'), 'check')}: "
                f"{_bound(check.get('status'), 'unknown')}"
            )
    except Exception as exc:  # pragma: no cover - health is best-effort
        health_lines = [f"Health checks unavailable: {_bound(str(exc), 'error')}."]

    sections = [
        _section("goals", "Goals finished since yesterday",
                 [f"{_bound(g.title, 'untitled goal')}" for g in finished_goals]
                 or ["None reported."]),
        _section("tasks", "Tasks finished since yesterday",
                 [f"{_bound(t.id, 'task')}: {_bound(t.original_request, 'no request')}"
                  for t in finished_tasks] or ["None reported."]),
        _section("blockers", "Needs your attention",
                 blocker_lines or ["Nothing is blocked."]),
        _section("agent", "Synth", agent_lines),
        _section("system", "Local health", health_lines),
    ]

    text_lines = [f"Good morning — here is your brief for {date_str}.", ""]
    for section in sections:
        text_lines.append(f"{section['title']}:")
        text_lines.extend(f"  - {line}" for line in section["lines"])
        text_lines.append("")
    text_lines.append(_NOTE)

    return {
        "date": date_str,
        "generated_at": now.isoformat(),
        "headline": f"Good morning — here is your brief for {date_str}.",
        "sections": sections,
        "text": "\n".join(text_lines),
        "authorized": False,
        "approval_issued": False,
        "execution_started": False,
        "note": _NOTE,
    }


def read_morning_brief(db: Session, *, now: datetime | None = None) -> dict[str, Any]:
    """Return today's stored brief, or a freshly built one without persisting it."""
    from backend.models.database import MorningBrief

    now = now or datetime.now()
    date_str = now.date().isoformat()
    row = db.query(MorningBrief).filter(MorningBrief.id == date_str).first()
    if row is not None:
        try:
            return json.loads(row.payload)
        except (TypeError, ValueError):  # pragma: no cover - corrupt row
            pass
    return build_morning_brief(db, now=now)


def ensure_daily_brief(
    db: Session, *, now: datetime | None = None,
) -> tuple[dict[str, Any], bool]:
    """Get today's stored brief, creating it once if absent.

    Returns ``(brief, created)``. Idempotent on the local calendar date: a
    second call the same day returns the stored brief and ``created=False``.
    The caller owns the commit, so this composes cleanly with existing sessions.
    """
    from backend.models.database import MorningBrief

    now = now or datetime.now()
    date_str = now.date().isoformat()
    existing = db.query(MorningBrief).filter(MorningBrief.id == date_str).first()
    if existing is not None:
        try:
            return json.loads(existing.payload), False
        except (TypeError, ValueError):  # pragma: no cover - corrupt row
            db.delete(existing)
            db.flush()

    brief = build_morning_brief(db, now=now)
    db.add(MorningBrief(
        id=date_str,
        payload=json.dumps(brief, ensure_ascii=False, sort_keys=True),
        created_at=now,
    ))
    db.flush()
    return brief, True
