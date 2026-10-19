"""SQLite-backed implementation of the EventLedger interface.

The immutable ``task_events`` table remains the single source of execution
truth (docs/PROTECTED_RUNTIME_CONTRACT.md, "Audit"). This class is a narrow
read/append façade over that table; it deliberately does NOT commit — appends
participate in the caller's state-transition transaction so event + state
change stay atomic. Read methods are side-effect free.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from backend.models.database import TaskEvent


def decode_event_data(raw: Any) -> Dict[str, Any]:
    """Decode stored event JSON, tolerating legacy malformed payloads."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        try:
            return json.loads(str(raw).replace("\\", "\\\\"))
        except (json.JSONDecodeError, TypeError):
            return {"raw": str(raw)}


class DbEventLedger:
    """EventLedger implementation over the SQLAlchemy session."""

    def __init__(self, session: Session):
        self._session = session

    def append_event(
        self,
        task_id: str,
        event_type: str,
        event_data: Dict[str, Any],
    ) -> int:
        last = (
            self._session.query(TaskEvent)
            .filter_by(task_id=task_id)
            .order_by(TaskEvent.sequence_num.desc())
            .first()
        )
        # Account for events appended earlier in THIS uncommitted transaction
        # (sessions may run with autoflush=False): their rows are invisible
        # to the query above but still occupy sequence numbers.
        pending_max = max(
            (
                e.sequence_num
                for e in self._session.new
                if isinstance(e, TaskEvent) and e.task_id == task_id
            ),
            default=0,
        )
        sequence = max(last.sequence_num if last else 0, pending_max) + 1
        event = TaskEvent(
            task_id=task_id,
            event_type=event_type,
            event_data=json.dumps(event_data),
            sequence_num=sequence,
            created_at=datetime_utcnow(),
        )
        self._session.add(event)
        return sequence

    def _query(self, task_id: str):
        return (
            self._session.query(TaskEvent)
            .filter_by(task_id=task_id)
            .order_by(TaskEvent.sequence_num.asc())
        )

    def _to_dict(self, event: TaskEvent) -> Dict[str, Any]:
        return {
            "id": event.id,
            "event_type": event.event_type,
            "event_data": decode_event_data(event.event_data),
            "sequence_num": event.sequence_num,
            "created_at": event.created_at,
        }

    def event_history(self, task_id: str) -> List[Dict[str, Any]]:
        return [self._to_dict(e) for e in self._query(task_id).all()]

    def events_after(self, task_id: str, after_sequence: int) -> List[Dict[str, Any]]:
        query = self._query(task_id)
        if after_sequence:
            query = query.filter(TaskEvent.sequence_num > after_sequence)
        return [self._to_dict(e) for e in query.all()]

    def latest_sequence(self, task_id: str) -> int:
        row = (
            self._session.query(TaskEvent.sequence_num)
            .filter_by(task_id=task_id)
            .order_by(TaskEvent.sequence_num.desc())
            .first()
        )
        return row[0] if row else 0


def datetime_utcnow():
    """Indirection keeps imports light and mirrors existing UTC usage."""
    from datetime import datetime

    return datetime.utcnow()
