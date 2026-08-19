"""
Read-only activity recall over Sythion's runtime history.

This tool does not touch the durable user-memory store. Instead it queries the
runtime ledger and conversation transcript so the assistant can answer questions
like "what were you doing at 8am" without conflating that history with stored
preferences or profile facts.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Generic, TypeVar

try:
    from backend.api.schemas import ActivityRecallInput, ToolOutput
    from backend.models.database import ConversationTurn, TaskEvent, get_db_manager
    from backend.tools.executor import BaseTool
except ImportError:
    from api.schemas import ActivityRecallInput, ToolOutput
    from models.database import ConversationTurn, TaskEvent, get_db_manager
    from tools.executor import BaseTool

_ACTIVITY_SUMMARY_CAP = 220


def _as_naive_utc(dt: datetime | None) -> datetime | None:
    """Normalize a possibly tz-aware datetime to naive UTC."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        from datetime import timezone

        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _bound_activity_summary(text: Any) -> str:
    """Bound a single activity summary line to the default tool cap."""
    if isinstance(text, (dict, list)):
        text = json.dumps(text, ensure_ascii=False, default=str)
    text = str(text or "")
    if len(text) > _ACTIVITY_SUMMARY_CAP:
        text = text[:_ACTIVITY_SUMMARY_CAP - 3] + "..."
    return text


def _event_summary(event_data: Any) -> str:
    """Extract only the bounded model_summary (fall back to `output` text)."""
    try:
        data = json.loads(event_data) if isinstance(event_data, str) else (event_data or {})
    except (ValueError, TypeError):
        data = {}
    summary = data.get("model_summary")
    if summary is None:
        summary = data.get("output", "")
    return _bound_activity_summary(summary)


class ActivityRecallTool(BaseTool[ActivityRecallInput]):
    """Recall Sythion's own past activity across tasks within a time range."""

    name = "activity_recall"
    description = "Recall Sythion's own past actions and activity (tool calls and what you asked) within a time range; for questions like 'what were you doing at 8am'"
    input_schema = ActivityRecallInput

    async def execute(self, input_data: ActivityRecallInput) -> ToolOutput:
        try:
            start = _as_naive_utc(input_data.start_time)
            end = _as_naive_utc(input_data.end_time) or datetime.utcnow()
            max_results = max(1, min(25, int(input_data.max_results or 10)))
            exclude_ids = set(input_data.exclude_task_ids or [])
            if start is None or end < start:
                return ToolOutput(
                    success=False,
                    error={
                        "code": "INVALID_TIME_RANGE",
                        "message": "start_time is required and end_time must not be before start_time",
                    },
                    duration_ms=0,
                )

            db_mgr = get_db_manager()
            with db_mgr.get_session() as session:
                events = []
                q = (
                    session.query(TaskEvent)
                    .filter(TaskEvent.created_at >= start)
                    .filter(TaskEvent.created_at <= end)
                    .order_by(TaskEvent.created_at.desc())
                )
                tool_name = (input_data.tool_name or "").strip().lower()
                for ev in q:
                    if ev.task_id in exclude_ids:
                        continue
                    summary = _event_summary(ev.event_data)
                    entry = {
                        "kind": "tool",
                        "task_id": ev.task_id,
                        "event_type": ev.event_type,
                        "created_at": ev.created_at.isoformat() if ev.created_at else None,
                        "summary": summary,
                    }
                    if tool_name:
                        if ev.event_type != "tool.executed":
                            continue
                        try:
                            data = json.loads(ev.event_data) if isinstance(ev.event_data, str) else (ev.event_data or {})
                        except (ValueError, TypeError):
                            data = {}
                        if str(data.get("tool_name", "")).lower() != tool_name:
                            continue
                        entry["tool_name"] = str(data.get("tool_name", ""))
                    else:
                        try:
                            data = json.loads(ev.event_data) if isinstance(ev.event_data, str) else (ev.event_data or {})
                        except (ValueError, TypeError):
                            data = {}
                        entry["tool_name"] = str(data.get("tool_name", "")) or None
                    events.append(entry)
                    if len(events) >= max_results:
                        break

                if not tool_name:
                    turn_q = (
                        session.query(ConversationTurn)
                        .filter(ConversationTurn.created_at >= start)
                        .filter(ConversationTurn.created_at <= end)
                        .order_by(ConversationTurn.created_at.desc())
                        .all()
                    )
                    for t in turn_q:
                        if t.task_id in exclude_ids:
                            continue
                        events.append({
                            "kind": "turn",
                            "task_id": t.task_id,
                            "role": t.role,
                            "created_at": t.created_at.isoformat() if t.created_at else None,
                            "summary": _bound_activity_summary(t.summary),
                        })
                    events.sort(key=lambda e: e.get("created_at") or "", reverse=True)
                    events = events[:max_results]

                summary = (
                    f"Found {len(events)} activities between "
                    f"{start.isoformat()} and {end.isoformat()}"
                )
                return ToolOutput(
                    success=True,
                    data={"count": len(events), "events": events},
                    model_summary=summary,
                    evidence={"source": "database_activity_recall"},
                )
        except Exception as e:
            return ToolOutput(
                success=False,
                error={"code": "ACTIVITY_RECALL_ERROR", "message": str(e)},
                duration_ms=0,
            )

    async def verify(self, input_data: ActivityRecallInput, output: ToolOutput) -> bool:
        return output.success

    def get_policy_requirements(self) -> dict[str, Any]:
        return {
            "risk_level": "none",
            "requires_confirmation": False,
            "mutating": False,
        }
