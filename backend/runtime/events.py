"""SQLite-backed implementation of the EventLedger interface.

The immutable ``task_events`` table remains the single source of execution
truth (docs/PROTECTED_RUNTIME_CONTRACT.md, "Audit"). This class is a narrow
read/append facade over that table; it deliberately does NOT commit; appends
participate in the caller's state-transition transaction so event + state
change stay atomic. Read methods are side-effect free.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from backend.models.database import TaskEvent


class EventSequenceAllocationError(RuntimeError):
    """Raised when ``append_event`` cannot allocate a unique sequence number.

    Subclass of :class:`RuntimeError` so thread targets can propagate it
    without special-casing. Chained from the underlying exception.
    """


# Allocation is serialised by the SQLite writer lock held across the caller's
# transaction. The first statement below acquires (or waits for) the write
# lock; the subsequent SELECTs/UPDATE run on the same connection/transaction
# so no other writer can interleave. Counter stores the last allocated
# sequence number (0 = none allocated yet).


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
        # Allocate within the caller's transaction. The first DML (INSERT OR
        # IGNORE) acquires the SQLite writer lock and subsequent statements
        # on the same connection/transaction cannot be interleaved by another
        # writer. Bounded retry covers only transient lock timing.
        sequence: Optional[int] = None
        last_exc: Optional[BaseException] = None
        for attempt in range(8):
            try:
                # Seed the counter (last allocated = 0 when no rows yet); OR
                # IGNORE makes this safe under contention.
                self._session.execute(
                    text(
                        "INSERT OR IGNORE INTO task_event_sequences (task_id, next_seq) "
                        "VALUES (:task_id, COALESCE("
                        " (SELECT MAX(sequence_num) FROM task_events WHERE task_id = :task_id), 0))"
                    ),
                    {"task_id": task_id},
                )
                last_allocated = self._session.execute(
                    text(
                        "SELECT next_seq FROM task_event_sequences "
                        "WHERE task_id = :task_id"
                    ),
                    {"task_id": task_id},
                ).scalar_one()
                table_max = self._session.execute(
                    text(
                        "SELECT COALESCE(MAX(sequence_num), 0) FROM task_events "
                        "WHERE task_id = :task_id"
                    ),
                    {"task_id": task_id},
                ).scalar() or 0
                new_seq = max(int(last_allocated or 0), int(table_max or 0)) + 1
                self._session.execute(
                    text(
                        "UPDATE task_event_sequences SET next_seq = :new_seq "
                        "WHERE task_id = :task_id"
                    ),
                    {"task_id": task_id, "new_seq": new_seq},
                )
                sequence = new_seq
                break
            except (IntegrityError, OperationalError) as exc:
                msg = str(exc).lower()
                is_retryable = isinstance(exc, IntegrityError) or (
                    "locked" in msg or "busy" in msg
                )
                try:
                    self._session.rollback()
                except Exception:
                    pass
                last_exc = exc
                if not is_retryable or attempt == 7:
                    raise EventSequenceAllocationError(
                        f"failed to allocate sequence for task {task_id}"
                    ) from exc
        if sequence is None:  # pragma: no cover - defensive
            raise EventSequenceAllocationError(
                f"failed to allocate sequence for task {task_id}"
            ) from last_exc

        event = TaskEvent(
            task_id=task_id,
            event_type=event_type,
            event_data=json.dumps(event_data),
            sequence_num=int(sequence),
            created_at=datetime_utcnow(),
        )
        self._session.add(event)
        return int(sequence)

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
