"""Small, explicitly-transactional, per-task ownership primitive.

This is a foundation only.  It is not wired into production runners, routing,
or OS-action execution.  Ownership is one durable row per ``task_id`` on the
SQLAlchemy session supplied by the caller.

Transaction contract
---------------------
Callers own the transaction.  Every method issues SQL (or returns a
read-only snapshot) and NEVER commits, rolls back, or invalidates unrelated
caller state.  This makes the primitive safe to compose with other writes:

* ``Manager.method(...)`` participates in an open caller transaction when
  ``session.in_transaction()`` is true.
* Otherwise it begins an immediate transaction and leaves it open for the
  caller to commit/rollback.  No method ever commits.
* Read-only methods never mutate state and never expire/refresh caller rows.
* Unique-row insertion conflicts resolve only the savepoint created for that
  insert; the caller's transaction and pending writes remain intact.

Acquisition/replacement uses atomic SQL predicates.  A row is only acquired
when the database accepts the insert or conditional update.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Iterable, List, Optional

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

try:
    from backend.models.database import Base
except ImportError:  # pragma: no cover - alternate invocation layout
    from models.database import Base

ACTIVE = "ACTIVE"
RELEASED = "RELEASED"
EXPIRED = "EXPIRED"
RECOVERED = "RECOVERED"
DEFAULT_TASK_OWNERSHIP_TTL_SECONDS = 30


def _loads_ids(raw: Optional[str]) -> List[str]:
    if raw is None:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return [str(raw)]
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _dumps_ids(ids: Iterable[str]) -> str:
    return json.dumps(list(ids))


@dataclass
class TaskOwnership:
    task_id: str
    owner_nonce: str
    worker_id: str
    status: str
    fencing_token: int
    version: int
    acquired_at: datetime
    heartbeat_at: datetime
    expires_at: datetime
    process_id: Optional[int] = None
    active_action_id: Optional[str] = None
    uncertain_action_ids: List[str] = None
    recovered_from_nonce: Optional[str] = None
    recovery_reason: Optional[str] = None
    recovered_at: Optional[datetime] = None
    release_outcome: Optional[str] = None

    def __post_init__(self) -> None:
        if self.uncertain_action_ids is None:
            self.uncertain_action_ids = []

    @property
    def uncertain_action_id(self) -> Optional[str]:
        return self.uncertain_action_ids[-1] if self.uncertain_action_ids else None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["uncertain_action_id"] = self.uncertain_action_id
        for key, value in list(data.items()):
            if isinstance(value, datetime):
                data[key] = value.isoformat()
        return data


@dataclass
class AcquisitionResult:
    acquired: bool
    ownership: Optional[TaskOwnership] = None
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.acquired

    @property
    def success(self) -> bool:
        return self.acquired

    @property
    def nonce(self) -> Optional[str]:
        return self.ownership.owner_nonce if self.ownership else None

    @property
    def fencing_token(self) -> Optional[int]:
        return self.ownership.fencing_token if self.ownership else None

    @property
    def uncertain_action_ids(self) -> List[str]:
        return self.ownership.uncertain_action_ids if self.ownership else []

    @property
    def uncertain_action_id(self) -> Optional[str]:
        return self.ownership.uncertain_action_id if self.ownership else None

    def __bool__(self) -> bool:
        return bool(self.acquired)


from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    String,
    Text,
    and_,
    or_,
)
from sqlalchemy.orm import Session

try:
    from backend.models.database import Base
except ImportError:  # pragma: no cover - alternate invocation layout
    from models.database import Base


class TaskOwnershipRow(Base):
    __tablename__ = "task_ownerships"

    task_id = Column(String, primary_key=True)
    owner_nonce = Column(String, nullable=False)
    worker_id = Column(String, nullable=False)
    process_id = Column(Integer, nullable=True)
    status = Column(String, nullable=False, default=ACTIVE)
    acquired_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    heartbeat_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)
    fencing_token = Column(Integer, nullable=False, default=1)
    version = Column(Integer, nullable=False, default=1)
    active_action_id = Column(String, nullable=True)
    uncertain_action_ids = Column(Text, nullable=True)
    recovered_from_nonce = Column(String, nullable=True)
    recovery_reason = Column(Text, nullable=True)
    recovered_at = Column(DateTime, nullable=True)
    released_at = Column(DateTime, nullable=True)
    release_outcome = Column(String, nullable=True)

    __table_args__ = __import__("sqlalchemy").CheckConstraint(
        "status IN ('ACTIVE','RELEASED','EXPIRED','RECOVERED')",
        name="check_task_ownership_status",
    ),


def _row_to_record(row: TaskOwnershipRow) -> TaskOwnership:
    return TaskOwnership(
        task_id=row.task_id,
        owner_nonce=row.owner_nonce,
        worker_id=row.worker_id,
        status=row.status,
        fencing_token=int(row.fencing_token),
        version=int(row.version),
        acquired_at=row.acquired_at,
        heartbeat_at=row.heartbeat_at,
        expires_at=row.expires_at,
        process_id=row.process_id,
        active_action_id=row.active_action_id,
        uncertain_action_ids=_loads_ids(row.uncertain_action_ids),
        recovered_from_nonce=row.recovered_from_nonce,
        recovery_reason=row.recovery_reason,
        recovered_at=row.recovered_at,
        release_outcome=row.release_outcome,
    )


def _fetch_row(session: Session, task_id: str) -> Optional[TaskOwnershipRow]:
    # Query the current session/database view.  Do not expire arbitrary
    # caller-staged objects.
    return (
        session.query(TaskOwnershipRow)
        .filter_by(task_id=task_id)
        .populate_existing()
        .one_or_none()
    )


def _expired(row: TaskOwnershipRow, now: datetime) -> bool:
    return row.expires_at is not None and now > row.expires_at


def _append_uncertain(ids: List[str], action_id: Optional[str]) -> List[str]:
    result = list(ids)
    if action_id and action_id not in result:
        result.append(action_id)
    return result


class TaskOwnershipManager:
    def __init__(
        self,
        session: Session,
        *,
        default_ttl_seconds: int = DEFAULT_TASK_OWNERSHIP_TTL_SECONDS,
        clock: Optional[Callable[[], datetime]] = None,
    ):
        if session is None:
            raise ValueError("session is required")
        self._session = session
        self.default_ttl_seconds = int(default_ttl_seconds)
        self._clock = clock or (lambda: datetime.utcnow())

    def _now(self) -> datetime:
        return self._clock()

    def _ttl(self, ttl_seconds: Optional[int]) -> int:
        if ttl_seconds is None:
            return self.default_ttl_seconds
        value = int(ttl_seconds)
        if value <= 0:
            raise ValueError("ttl_seconds must be positive")
        return value

    def _ensure_transaction(self) -> bool:
        # Only the presence of a physical transaction matters. A SQLAlchemy
        # Session can report in_transaction() after SELECTs while Python's
        # sqlite3 driver has not issued any BEGIN. Mutations must use a
        # physical immediate transaction created here, not a deferred/unsafe
        # caller transaction.
        dbapi_connection = self._session.connection().connection.dbapi_connection
        try:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()
        except Exception:
            pass
        if dbapi_connection.in_transaction:
            return False
        self._session.execute(text("BEGIN IMMEDIATE"))
        return True

    # ---------- read-only reads ----------
    def get(self, task_id: str) -> Optional[TaskOwnership]:
        if not task_id:
            return None
        row = _fetch_row(self._session, task_id)
        return _row_to_record(row) if row is not None else None

    def uncertain_actions(self, task_id: str) -> List[str]:
        if not task_id:
            return []
        row = _fetch_row(self._session, task_id)
        if row is None:
            return []
        return list(_loads_ids(row.uncertain_action_ids))

    # ---------- mutations ----------
    def acquire(
        self,
        task_id: str,
        worker_id: str,
        *,
        ttl_seconds: Optional[int] = None,
        process_id: Optional[int] = None,
    ) -> AcquisitionResult:
        if not task_id:
            return AcquisitionResult(acquired=False, reason="missing_task_id")
        if worker_id is None:
            return AcquisitionResult(acquired=False, reason="missing_worker_id")
        worker_id = str(worker_id)
        ttl = self._ttl(ttl_seconds)
        now = self._now()
        pid = process_id

        self._ensure_transaction()
        try:
            savepoint = self._session.begin_nested()
            new_row = TaskOwnershipRow(
                task_id=task_id,
                owner_nonce=str(uuid.uuid4()),
                worker_id=worker_id,
                process_id=pid,
                status=ACTIVE,
                acquired_at=now,
                heartbeat_at=now,
                expires_at=now + timedelta(seconds=ttl),
                fencing_token=1,
                active_action_id=None,
                uncertain_action_ids=None,
            )
            self._session.add(new_row)
            self._session.flush()
            savepoint.commit()
        except IntegrityError:
            try:
                savepoint.rollback()
            except Exception:
                pass
            try:
                self._session.expunge(new_row)
            except Exception:
                pass
        else:
            return AcquisitionResult(
                acquired=True,
                ownership=_row_to_record(new_row),
                reason="acquired_new",
            )

        row = _fetch_row(self._session, task_id)
        if row is None:
            return AcquisitionResult(acquired=False, reason="contention")

        if row.status == EXPIRED or (row.status == ACTIVE and _expired(row, now)):
            return AcquisitionResult(
                acquired=False,
                ownership=_row_to_record(row),
                reason="recovery_required",
            )

        if row.status in (RELEASED, RECOVERED):
            old_uncertain = _loads_ids(row.uncertain_action_ids)
            values = {
                "owner_nonce": str(uuid.uuid4()),
                "worker_id": worker_id,
                "process_id": pid,
                "status": ACTIVE,
                "acquired_at": now,
                "heartbeat_at": now,
                "expires_at": now + timedelta(seconds=ttl),
                "fencing_token": int(row.fencing_token) + 1,
                "version": int(row.version) + 1,
                "active_action_id": None,
                "uncertain_action_ids": _dumps_ids(old_uncertain),
            }
            found = (
                self._session.query(TaskOwnershipRow)
                .filter(
                    TaskOwnershipRow.task_id == task_id,
                    TaskOwnershipRow.status.in_((RELEASED, RECOVERED)),
                )
                .update(values, synchronize_session=False)
            )
            if found == 1:
                fresh = _fetch_row(self._session, task_id)
                return AcquisitionResult(
                    acquired=True,
                    ownership=_row_to_record(fresh),
                    reason="acquired_released",
                )
            row = _fetch_row(self._session, task_id)

        if row.status == EXPIRED or (row.status == ACTIVE and _expired(row, now)):
            return AcquisitionResult(
                acquired=False,
                ownership=_row_to_record(row),
                reason="recovery_required",
            )

        if row.status == ACTIVE and not _expired(row, now):
            reason = "already_owned"
            if row.worker_id == worker_id:
                reason = "already_owned_by_this_worker"
            return AcquisitionResult(
                acquired=False,
                ownership=_row_to_record(row),
                reason=reason,
            )

        return AcquisitionResult(
            acquired=False,
            ownership=_row_to_record(row),
            reason="ownership_not_acquirable",
        )

    def renew(
        self,
        task_id: str,
        nonce: str,
        *,
        fencing_token: Optional[int] = None,
        ttl_seconds: Optional[int] = None,
    ) -> bool:
        if not task_id or not nonce:
            return False
        self._ensure_transaction()
        ttl = self._ttl(ttl_seconds) if ttl_seconds is not None else self.default_ttl_seconds
        now = self._now()
        filters = [
            TaskOwnershipRow.task_id == task_id,
            TaskOwnershipRow.owner_nonce == nonce,
            TaskOwnershipRow.status == ACTIVE,
            TaskOwnershipRow.expires_at > now,
        ]
        if fencing_token is not None:
            filters.append(
                TaskOwnershipRow.fencing_token == int(fencing_token)
            )
        found = (
            self._session.query(TaskOwnershipRow)
            .filter(*filters)
            .update(
                {
                    "heartbeat_at": now,
                    "expires_at": now + timedelta(seconds=ttl),
                    "version": TaskOwnershipRow.version + 1,
                },
                synchronize_session=False,
            )
        )
        return found == 1

    def heartbeat(self, *args: Any, **kwargs: Any) -> bool:
        # A real method, not an alias, providing the common lease vocabulary.
        return self.renew(*args, **kwargs)

    def release(
        self,
        task_id: str,
        nonce: str,
        *,
        fencing_token: Optional[int] = None,
        outcome: Optional[str] = None,
    ) -> bool:
        if not task_id or not nonce:
            return False
        self._ensure_transaction()
        now = self._now()
        current = _fetch_row(self._session, task_id)
        if (
            current is None
            or current.owner_nonce != nonce
            or current.status != ACTIVE
            or _expired(current, now)
        ):
            return False
        if fencing_token is not None and int(current.fencing_token) != int(fencing_token):
            return False

        new_uncertain = _append_uncertain(
            _loads_ids(current.uncertain_action_ids), current.active_action_id
        )
        filters = [
            TaskOwnershipRow.task_id == task_id,
            TaskOwnershipRow.owner_nonce == nonce,
            TaskOwnershipRow.status == ACTIVE,
            TaskOwnershipRow.expires_at > now,
            TaskOwnershipRow.active_action_id == current.active_action_id,
        ]
        if fencing_token is not None:
            filters.append(
                TaskOwnershipRow.fencing_token == int(fencing_token)
            )
        values = {
            "status": RELEASED,
            "released_at": now,
            "release_outcome": outcome,
            "heartbeat_at": now,
            "active_action_id": None,
            "uncertain_action_ids": _dumps_ids(new_uncertain),
            "fencing_token": int(current.fencing_token) + 1,
            "version": int(current.version) + 1,
        }
        found = (
            self._session.query(TaskOwnershipRow)
            .filter(*filters)
            .update(values, synchronize_session=False)
        )
        return found == 1

    def verify(
        self,
        task_id: str,
        nonce: str,
        *,
        fencing_token: Optional[int] = None,
        action_id: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> bool:
        # Read-only: no writes, no transaction start.
        if not task_id or not nonce:
            return False
        effective_now = self._now() if now is None else now
        row = _fetch_row(self._session, task_id)
        if row is None:
            return False
        if row.owner_nonce != nonce or row.status != ACTIVE:
            return False
        if fencing_token is not None and int(row.fencing_token) != int(fencing_token):
            return False
        if _expired(row, effective_now):
            return False
        if action_id is not None:
            if row.active_action_id != action_id or _loads_ids(row.uncertain_action_ids):
                return False
        return True

    def begin_action(
        self,
        task_id: str,
        nonce: str,
        action_id: str,
        *,
        fencing_token: Optional[int] = None,
    ) -> bool:
        if not task_id or not nonce or not action_id:
            return False
        self._ensure_transaction()
        now = self._now()
        current = _fetch_row(self._session, task_id)
        if (
            current is None
            or current.owner_nonce != nonce
            or current.status != ACTIVE
            or _expired(current, now)
        ):
            return False
        if fencing_token is not None and int(current.fencing_token) != int(fencing_token):
            return False

        # A fresh ID is not resolution of an older unknown effect. Malformed
        # legacy uncertainty also remains blocking; only NULL/empty JSON list
        # represent no uncertainty.
        if _loads_ids(current.uncertain_action_ids):
            return False

        if current.active_action_id is not None:
            return False

        filters = [
            TaskOwnershipRow.task_id == task_id,
            TaskOwnershipRow.owner_nonce == nonce,
            TaskOwnershipRow.status == ACTIVE,
            TaskOwnershipRow.expires_at > now,
            TaskOwnershipRow.active_action_id.is_(None),
        ]
        if fencing_token is not None:
            filters.append(
                TaskOwnershipRow.fencing_token == int(fencing_token)
            )
        found = (
            self._session.query(TaskOwnershipRow)
            .filter(*filters)
            .update(
                {
                    "active_action_id": action_id,
                    "heartbeat_at": now,
                    "version": TaskOwnershipRow.version + 1,
                },
                synchronize_session=False,
            )
        )
        return found == 1

    def complete_action(
        self,
        task_id: str,
        nonce: str,
        action_id: str,
        *,
        fencing_token: Optional[int] = None,
    ) -> bool:
        if not task_id or not nonce or not action_id:
            return False
        self._ensure_transaction()
        now = self._now()
        current = _fetch_row(self._session, task_id)
        if (
            current is None
            or current.owner_nonce != nonce
            or current.status != ACTIVE
            or _expired(current, now)
        ):
            return False
        if fencing_token is not None and int(current.fencing_token) != int(fencing_token):
            return False
        if current.active_action_id != action_id:
            return False

        filters = [
            TaskOwnershipRow.task_id == task_id,
            TaskOwnershipRow.owner_nonce == nonce,
            TaskOwnershipRow.status == ACTIVE,
            TaskOwnershipRow.expires_at > now,
            TaskOwnershipRow.active_action_id == action_id,
        ]
        if fencing_token is not None:
            filters.append(
                TaskOwnershipRow.fencing_token == int(fencing_token)
            )
        found = (
            self._session.query(TaskOwnershipRow)
            .filter(*filters)
            .update(
                {
                    "active_action_id": None,
                    "heartbeat_at": now,
                    "version": TaskOwnershipRow.version + 1,
                },
                synchronize_session=False,
            )
        )
        return found == 1

    def recover(
        self,
        task_id: str,
        worker_id: str,
        reason: str,
        *,
        force: bool = False,
        ttl_seconds: Optional[int] = None,
        process_id: Optional[int] = None,
    ) -> AcquisitionResult:
        if not task_id or worker_id is None or not reason:
            return AcquisitionResult(
                acquired=False,
                reason="missing_required_argument",
            )
        worker_id = str(worker_id)
        ttl = self._ttl(ttl_seconds)
        now = self._now()
        pid = process_id

        self._ensure_transaction()
        current = _fetch_row(self._session, task_id)
        if current is None:
            return AcquisitionResult(
                acquired=False, reason="no_existing_ownership"
            )
        if current.status not in (ACTIVE, EXPIRED):
            return AcquisitionResult(
                acquired=False,
                ownership=_row_to_record(current),
                reason="recovery_not_allowed",
            )
        expired = current.status == EXPIRED or _expired(current, now)
        if not expired and current.status == ACTIVE and not force:
            return AcquisitionResult(
                acquired=False,
                ownership=_row_to_record(current),
                reason="owner_active_recovery_requires_force",
            )

        old_uncertain = _loads_ids(current.uncertain_action_ids)
        if current.active_action_id is not None:
            old_uncertain = _append_uncertain(
                old_uncertain, current.active_action_id
            )

        allowed = (
            (TaskOwnershipRow.task_id == task_id)
            & (TaskOwnershipRow.owner_nonce == current.owner_nonce)
            & (
                (TaskOwnershipRow.status == EXPIRED)
                | (
                    (TaskOwnershipRow.status == ACTIVE)
                    & (TaskOwnershipRow.expires_at <= now)
                )
            )
        )
        if force:
            allowed = (TaskOwnershipRow.task_id == task_id) & (
                TaskOwnershipRow.owner_nonce == current.owner_nonce
            )

        values = {
            "owner_nonce": str(uuid.uuid4()),
            "worker_id": worker_id,
            "process_id": pid,
            "status": ACTIVE,
            "acquired_at": now,
            "heartbeat_at": now,
            "expires_at": now + timedelta(seconds=ttl),
            "fencing_token": int(current.fencing_token) + 1,
            "version": int(current.version) + 1,
            "active_action_id": None,
            "uncertain_action_ids": _dumps_ids(old_uncertain),
            "recovered_from_nonce": current.owner_nonce,
            "recovery_reason": reason,
            "recovered_at": now,
        }
        found = (
            self._session.query(TaskOwnershipRow)
            .filter(allowed)
            .update(values, synchronize_session=False)
        )
        if found != 1:
            fresh = _fetch_row(self._session, task_id)
            return AcquisitionResult(
                acquired=False,
                ownership=_row_to_record(fresh) if fresh is not None else None,
                reason="recovery_failed",
            )
        fresh = _fetch_row(self._session, task_id)
        return AcquisitionResult(
            acquired=True,
            ownership=_row_to_record(fresh),
            reason="recovered",
        )

    def list_task_ids_for_worker(self, worker_id: str) -> List[str]:
        if worker_id is None:
            return []
        rows = (
            self._session.query(TaskOwnershipRow)
            .filter_by(worker_id=str(worker_id))
            .populate_existing()
            .all()
        )
        return [row.task_id for row in rows]


__all__ = [
    "ACTIVE",
    "RELEASED",
    "EXPIRED",
    "RECOVERED",
    "DEFAULT_TASK_OWNERSHIP_TTL_SECONDS",
    "TaskOwnership",
    "TaskOwnershipRow",
    "TaskOwnershipManager",
    "AcquisitionResult",
]
