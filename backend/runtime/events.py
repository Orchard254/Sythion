"""SQLite-backed implementation of the EventLedger interface.

The immutable ``task_events`` table remains the single source of execution
truth (README.md, "Audit"). This class is a narrow
read/append facade over that table; it deliberately does NOT commit -- appends
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


# Number of allocation attempts before surfacing EventSequenceAllocationError.
# Each attempt runs inside its own SAVEPOINT so a retry can never discard
# earlier, still-uncommitted appends made in the same session/transaction.
ALLOCATION_ATTEMPTS = 3

_ALLOCATION_SQL = text(
    """
    INSERT INTO task_event_sequences (task_id, next_seq)
    VALUES (
        :task_id,
        max(COALESCE(
            (SELECT MAX(sequence_num) FROM task_events WHERE task_id = :task_id), 0
        ), :pending_max) + 1
    )
    ON CONFLICT(task_id) DO UPDATE SET
        next_seq = max(
            task_event_sequences.next_seq,
            max(COALESCE(
                (SELECT MAX(sequence_num) FROM task_events WHERE task_id = :task_id), 0
            ), :pending_max)
        ) + 1
    RETURNING next_seq
    """
)


class EventSequenceAllocationError(RuntimeError):
    """Raised when ``append_event`` cannot allocate a unique sequence number.

    Subclass of :class:`RuntimeError` so thread targets can propagate it
    without special-casing. Chained from the underlying exception.
    """


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
        self._counter_table_checked = False

    def append_event(
        self,
        task_id: str,
        event_type: str,
        event_data: Dict[str, Any],
    ) -> int:
        """Allocate a gapless sequence number and register the event.

        Allocation is a single SQL statement (UPSERT ... RETURNING) that
        atomically advances the per-task counter and returns the new value.
        It runs on ``self._session`` and inside a SAVEPOINT, so:

          * the counter update participates in the caller's transaction and
            rolls back with it;
          * a contended retry rolls back only its own savepoint and never the
            earlier, already pending appends of the same transaction;
          * pending appends made earlier in this uncommitted transaction
            (sessions may run with ``autoflush=False``) are accounted for via
            ``pending_max``.
        """

        sequence: Optional[int] = None
        last_exc: Optional[BaseException] = None

        # Ensure an outer transaction is active so DDL/DML participate in the
        # caller-like rollback instead of auto-committing under legacy sqlite3
        # transaction mode (Python 3.11+). No-op when already in a transaction.
        if not self._session.in_transaction():
            self._session.execute(text("BEGIN IMMEDIATE"))

        # Transaction-resident existence check (item 017).
        _table_present = self._session.execute(
            text(
                "SELECT 1 FROM sqlite_master "
                "WHERE type = 'table' AND name = 'task_event_sequences'"
            )
        ).scalar()
        if not _table_present:
            self._session.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS task_event_sequences "
                    "(task_id VARCHAR NOT NULL PRIMARY KEY, next_seq INTEGER NOT NULL)"
                )
            )

        for attempt in range(ALLOCATION_ATTEMPTS):
            pending_max = max(
                (
                    event.sequence_num
                    for event in self._session.new
                    if isinstance(event, TaskEvent)
                    and getattr(event, "task_id", None) == task_id
                ),
                default=0,
            )

            # Savepoint: a failed allocation attempt rolls back ONLY to here,
            # preserving any earlier pending rows / appends of the outer
            # transaction (addresses multi-append rollback destructiveness).
            savepoint = self._session.begin_nested()
            try:
                sequence = int(
                    self._session.execute(
                        _ALLOCATION_SQL,
                        {"task_id": task_id, "pending_max": int(pending_max)},
                    ).scalar_one()
                )
                savepoint.commit()
                break
            except (IntegrityError, OperationalError) as exc:
                try:
                    savepoint.rollback()
                except Exception:  # pragma: no cover - defensive
                    pass
                last_exc = exc
                message = str(exc).lower()
                retryable = isinstance(exc, IntegrityError) or (
                    "locked" in message or "busy" in message
                )
                if (
                    not retryable
                    or attempt == ALLOCATION_ATTEMPTS - 1
                ):
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
        """Return the last allocated sequence for ``task_id``.

        Unlike a pure persisted-row scan this also accounts for rows that have
        been allocated (or appended) in the current transaction but not yet
        flushed/committed, which matters for ``autoflush=False`` sessions.
        """
        persisted = 0
        try:
            row = self._session.execute(
                text(
                    "SELECT COALESCE(MAX(sequence_num), 0) "
                    "FROM task_events WHERE task_id = :task_id"
                ),
                {"task_id": task_id},
            ).scalar()
            persisted = int(row or 0)
        except Exception:  # pragma: no cover - defensive fallback
            persisted = 0

        counter = 0
        try:
            row = self._session.execute(
                text(
                    "SELECT COALESCE(next_seq, 0) FROM task_event_sequences "
                    "WHERE task_id = :task_id"
                ),
                {"task_id": task_id},
            ).scalar()
            counter = int(row or 0)
        except Exception:  # pragma: no cover - defensive fallback
            counter = 0

        pending = max(
            (
                event.sequence_num
                for event in self._session.new
                if isinstance(event, TaskEvent)
                and getattr(event, "task_id", None) == task_id
            ),
            default=0,
        )
        return max(persisted, counter, pending)


def datetime_utcnow():
    """Indirection keeps imports light and mirrors existing UTC usage."""
    from datetime import datetime

    return datetime.utcnow()