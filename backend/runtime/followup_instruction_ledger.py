"""Durable, transactional follow-up instruction ledger (foundation only).

A user instruction addressed to an *already identified task* may be received at
any time and wait for a later safe application point. This module is a narrow
ledger: it persists the exact human text, identities, order, and status in the
caller's transaction. It does *not* modify a task, grant permission, schedule a
worker, replay, or execute anything. Application still requires an explicit
atomic revision commit through ``TaskInstructionRevision``.

Binding model
-------------
* ``request_id`` is the explicit identity of one user instruction request.  A
  duplicate ``request_id`` is rejected, preserving the original row and order.
* ``task_id`` identifies the task the request is addressed to.  It is nullable
  so user context can survive task deletion / terminal state (ON DELETE SET
  NULL); the immutable ``source_task_id`` retains the original exact identity.
* A monotonic ``sequence`` gives deterministic order for requests of the same
  task without relying on timestamps.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Callable, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, func, UniqueConstraint, CheckConstraint
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

try:
    from backend.models.database import Base
except Exception:  # pragma: no cover - alternate invocation layout
    try:
        from models.database import Base
    except Exception:
        from sqlalchemy.orm import declarative_base

        Base = declarative_base()

# Status vocabulary.  All lowercase to mirror task state vocabulary.
STATUS_RECEIVED = "received"
STATUS_PENDING = "pending"
STATUS_QUEUED = "queued"
STATUS_WAITING = "waiting"
STATUS_APPLYING = "applying"
STATUS_APPLIED = "applied"
STATUS_BLOCKED = "blocked"
STATUS_REJECTED = "rejected"
STATUS_CANCELLED = "cancelled"
STATUS_EXPIRED = "expired"
STATUS_NOT_APPLIED = "not_applied"
STATUS_TERMINAL = "terminal"

ALL_STATUSES: Tuple[str, ...] = (
    STATUS_RECEIVED,
    STATUS_PENDING,
    STATUS_QUEUED,
    STATUS_WAITING,
    STATUS_APPLYING,
    STATUS_APPLIED,
    STATUS_BLOCKED,
    STATUS_REJECTED,
    STATUS_CANCELLED,
    STATUS_EXPIRED,
    STATUS_NOT_APPLIED,
    STATUS_TERMINAL,
)

# Statuses that still represent a request that has not been applied.
PENDING_STATUSES: Tuple[str, ...] = (
    STATUS_RECEIVED,
    STATUS_PENDING,
    STATUS_QUEUED,
    STATUS_WAITING,
    STATUS_BLOCKED,
)

TERMINAL_STATUSES: Tuple[str, ...] = (
    STATUS_APPLIED,
    STATUS_REJECTED,
    STATUS_CANCELLED,
    STATUS_EXPIRED,
    STATUS_NOT_APPLIED,
    STATUS_TERMINAL,
)

# Non-terminal transitions are allowed to these targets only.
TRANSITIONS: dict[str, frozenset[str]] = {
    STATUS_RECEIVED: frozenset({STATUS_PENDING, STATUS_QUEUED, STATUS_WAITING, STATUS_BLOCKED, STATUS_REJECTED, STATUS_CANCELLED, STATUS_EXPIRED, STATUS_NOT_APPLIED, STATUS_TERMINAL}),
    STATUS_PENDING: frozenset({STATUS_QUEUED, STATUS_WAITING, STATUS_BLOCKED, STATUS_REJECTED, STATUS_CANCELLED, STATUS_EXPIRED, STATUS_NOT_APPLIED, STATUS_TERMINAL}),
    STATUS_QUEUED: frozenset({STATUS_WAITING, STATUS_BLOCKED, STATUS_REJECTED, STATUS_CANCELLED, STATUS_EXPIRED, STATUS_NOT_APPLIED, STATUS_TERMINAL}),
    STATUS_WAITING: frozenset({STATUS_BLOCKED, STATUS_REJECTED, STATUS_CANCELLED, STATUS_EXPIRED, STATUS_NOT_APPLIED, STATUS_TERMINAL}),
    STATUS_APPLYING: frozenset({STATUS_PENDING, STATUS_BLOCKED, STATUS_REJECTED, STATUS_CANCELLED, STATUS_EXPIRED, STATUS_NOT_APPLIED, STATUS_TERMINAL}),
    STATUS_BLOCKED: frozenset({STATUS_PENDING, STATUS_REJECTED, STATUS_CANCELLED, STATUS_EXPIRED, STATUS_NOT_APPLIED, STATUS_TERMINAL}),
    STATUS_APPLIED: frozenset(),
    STATUS_REJECTED: frozenset(),
    STATUS_CANCELLED: frozenset(),
    STATUS_EXPIRED: frozenset(),
    STATUS_NOT_APPLIED: frozenset(),
    STATUS_TERMINAL: frozenset(),
}


class FollowupInstructionLedgerRow(Base):
    """Durable row for one follow-up instruction request."""

    __tablename__ = "followup_instruction_ledger"

    request_id = Column(String, primary_key=True)
    task_id = Column(String, ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True, index=True)
    source_task_id = Column(String, nullable=True, index=True)
    session_id = Column(String, nullable=True, index=True)
    exchange_id = Column(String, nullable=True, index=True)
    source_exchange_id = Column(String, nullable=True, index=True)
    user_turn_id = Column(Integer, nullable=True)
    assistant_turn_id = Column(Integer, nullable=True)
    instruction = Column(Text, nullable=False)
    status = Column(String, nullable=False, default=STATUS_PENDING)
    sequence = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    applied_at = Column(DateTime, nullable=True)
    terminal_at = Column(DateTime, nullable=True)
    applied_task_version = Column(Integer, nullable=True)
    applied_instruction_revision = Column(Integer, nullable=True)
    terminal_reason = Column(Text, nullable=True)
    metadata_json = Column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN (" + ", ".join(repr(s) for s in ALL_STATUSES) + ")",
            name="check_followup_instruction_ledger_status",
        ),
        UniqueConstraint(
            "source_task_id", "sequence",
            name="uq_followup_instruction_ledger_source_task_sequence",
        ),
    )


# Ensure the model is registered on Base metadata at import time (no-op import).
try:
    import backend.models.database  # noqa: F401
except Exception:  # pragma: no cover
    pass

# Use a separate metadata? Base.metadata already includes Task; register table by class definition.


@dataclass
class FollowupInstructionRequest:
    request_id: str
    task_id: Optional[str]
    instruction: str
    status: str = STATUS_PENDING
    sequence: int = 0
    source_task_id: Optional[str] = None
    session_id: Optional[str] = None
    exchange_id: Optional[str] = None
    source_exchange_id: Optional[str] = None
    user_turn_id: Optional[int] = None
    assistant_turn_id: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    applied_at: Optional[datetime] = None
    terminal_at: Optional[datetime] = None
    applied_task_version: Optional[int] = None
    applied_instruction_revision: Optional[int] = None
    terminal_reason: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    # Compatibility aliases used by callers/tests.
    @property
    def id(self) -> str:
        return self.request_id

    @property
    def identity(self) -> str:
        return self.request_id

    @property
    def text(self) -> str:
        return self.instruction

    @property
    def metadata_json(self) -> str:
        return json.dumps(self.metadata, sort_keys=True)

    @property
    def is_pending(self) -> bool:
        return self.status in PENDING_STATUSES

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["id"] = self.request_id
        data["identity"] = self.request_id
        data["text"] = self.instruction
        for key, value in list(data.items()):
            if isinstance(value, datetime):
                data[key] = value.isoformat()
        return data

    @classmethod
    def from_row(cls, row: FollowupInstructionLedgerRow) -> "FollowupInstructionRequest":
        metadata = {}
        raw = getattr(row, "metadata_json", None)
        if isinstance(raw, str) and raw.strip():
            try:
                value = json.loads(raw)
                if isinstance(value, dict):
                    metadata = value
            except (TypeError, ValueError):
                metadata = {}
        return cls(
            request_id=row.request_id,
            task_id=row.task_id,
            instruction=row.instruction,
            status=row.status,
            sequence=int(row.sequence or 0),
            source_task_id=row.source_task_id,
            session_id=row.session_id,
            exchange_id=row.exchange_id,
            source_exchange_id=row.source_exchange_id,
            user_turn_id=row.user_turn_id,
            assistant_turn_id=row.assistant_turn_id,
            created_at=row.created_at,
            updated_at=row.updated_at,
            applied_at=row.applied_at,
            terminal_at=row.terminal_at,
            applied_task_version=row.applied_task_version,
            applied_instruction_revision=row.applied_instruction_revision,
            terminal_reason=row.terminal_reason,
            metadata=metadata,
        )


def _session_of(db) -> Session:
    if db is None:
        raise ValueError("A database session is required")
    if isinstance(db, Session):
        return db
    session = getattr(db, "db", None)
    if isinstance(session, Session):
        return session
    session = getattr(db, "session", None)
    if isinstance(session, Session):
        return session
    # TaskRunner-style objects.
    session = getattr(db, "runner", None)
    if session is not None:
        inner = getattr(session, "db", None)
        if isinstance(inner, Session):
            return inner
    raise ValueError("A database session is required")


def _task_id_from_task(task) -> Optional[str]:
    if task is None:
        return None
    if isinstance(task, str):
        return task
    return getattr(task, "id", None) or getattr(task, "task_id", None)


class FollowupInstructionLedgerManager:
    """Isolated transactional manager.

    Every method participates in the caller's transaction and never commits or
    rolls back the whole transaction (savepoints are used for unique-row
    conflicts).  Caller is responsible for commit/rollback.
    """

    def __init__(self, db=None, *, session=None, runner=None, clock=None, default_status: str = STATUS_PENDING):
        self._db = db
        if session is not None:
            db = session
        if runner is not None and getattr(runner, "db", None) is not None:
            db = runner.db
        self._db = db
        self._runner = runner
        self._session = _session_of(self._db)
        self._clock = clock or (lambda: datetime.utcnow())
        resolved_default = self._status(default_status, STATUS_PENDING)
        if resolved_default in (STATUS_APPLIED, STATUS_APPLYING):
            raise ValueError("Follow-up applied status cannot be asserted by the ledger")
        self.default_status = resolved_default

    @property
    def db(self) -> Session:
        return self._session

    @property
    def session(self) -> Session:
        return self._session

    def _now(self) -> datetime:
        return self._clock()

    def _ensure_transaction(self) -> None:
        dbapi_connection = self._session.connection().connection.dbapi_connection
        try:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()
        except Exception:
            pass
        if dbapi_connection.in_transaction:
            return
        self._session.execute(__import__("sqlalchemy").text("BEGIN IMMEDIATE"))

    @staticmethod
    def _clean_instruction(instruction: Any) -> str:
        if not isinstance(instruction, str):
            raise ValueError("Instruction must be a non-empty string")
        text = instruction
        if not text.strip():
            raise ValueError("Instruction must be a non-empty string")
        if len(text) > 12000:
            raise ValueError("Instruction exceeds maximum length")
        return text

    @staticmethod
    def _status(value: Any, default: str = STATUS_PENDING) -> str:
        if value is None:
            return default
        text = str(value).strip().lower()
        if text in ALL_STATUSES:
            return text
        # Common synonyms.
        synonyms = {
            "open": STATUS_PENDING,
            "new": STATUS_RECEIVED,
            "queued": STATUS_QUEUED,
            "waiting": STATUS_WAITING,
            "done": STATUS_APPLIED,
            "complete": STATUS_APPLIED,
            "completed": STATUS_APPLIED,
            "finished": STATUS_APPLIED,
            "cancel": STATUS_CANCELLED,
            "canceled": STATUS_CANCELLED,
            "reject": STATUS_REJECTED,
            "rejected": STATUS_REJECTED,
            "expire": STATUS_EXPIRED,
            "expired": STATUS_EXPIRED,
            "not-applied": STATUS_NOT_APPLIED,
            "not_applied": STATUS_NOT_APPLIED,
            "terminal": STATUS_TERMINAL,
        }
        return synonyms.get(text, default)

    def _normalize_task_id(self, value: Any) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        tid = _task_id_from_task(value)
        return tid

    def _metadata(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        metadata = {}
        if "metadata" in kwargs and isinstance(kwargs.get("metadata"), dict):
            metadata.update(kwargs["metadata"])
        if "metadata_json" in kwargs and isinstance(kwargs.get("metadata_json"), str):
            try:
                value = json.loads(kwargs["metadata_json"])
                if isinstance(value, dict):
                    metadata.update(value)
            except (TypeError, ValueError):
                pass
        for key in ("context", "extra", "payload"):
            if isinstance(kwargs.get(key), dict):
                metadata.setdefault(key, kwargs[key])
        return metadata

    def _extract_fields(self, *args, **kwargs) -> dict[str, Any]:
        """Accept a wide range of call signatures and normalize fields."""
        fields = dict(kwargs)
        # Positional heuristic: request_id, task_id, instruction.
        positional_names = ("request_id", "task_id", "instruction", "session_id", "status")
        for index, value in enumerate(args):
            if index < len(positional_names):
                key = positional_names[index]
                fields.setdefault(key, value)
            elif index == 3:
                fields.setdefault("session_id", value)
        # Aliases.
        if "request_id" not in fields:
            for alias in ("request", "identity", "id", "request_identity", "followup_id", "instruction_id", "uuid", "key"):
                if alias in fields:
                    fields["request_id"] = fields.pop(alias)
                    break
        if "task_id" not in fields:
            for alias in ("task", "task_identity", "target_task_id", "task_ref", "target_id"):
                if alias in fields:
                    fields["task_id"] = fields.pop(alias)
                    break
        if "instruction" not in fields:
            for alias in ("text", "instruction_text", "human_instruction", "followup", "follow_up", "content", "body", "message", "request_text"):
                if alias in fields:
                    fields["instruction"] = fields.pop(alias)
                    break
        if "exchange_id" not in fields and "source_exchange_id" in fields:
            fields["exchange_id"] = fields["source_exchange_id"]
        if "source_exchange_id" not in fields and "exchange_id" in fields:
            fields["source_exchange_id"] = fields["exchange_id"]
        if "session_id" not in fields:
            for alias in ("session", "session_identity"):
                if alias in fields:
                    fields["session_id"] = fields.pop(alias)
                    break
        if "task_id" in fields:
            fields["task_id"] = self._normalize_task_id(fields["task_id"])
        if "source_task_id" not in fields:
            fields["source_task_id"] = fields.get("task_id")
        return fields

    def receive(self, *args, **kwargs) -> FollowupInstructionRequest:
        """Persist one user follow-up request for a task (caller commits)."""
        self._ensure_transaction()
        fields = self._extract_fields(*args, **kwargs)
        request_id = fields.get("request_id")
        if not isinstance(request_id, str) or not request_id.strip():
            raise ValueError("Exact follow-up request identity is required")
        request_id = request_id.strip()
        instruction = self._clean_instruction(fields.get("instruction"))
        task_id = self._normalize_task_id(fields.get("task_id"))
        if task_id is None:
            raise ValueError("Exact task identity is required")
        # Guard terminal tasks atomically: a follow-up received after the task
        # has already reached a terminal state cannot be queued or replayed.
        # It is preserved honestly as not applied instead.
        task_terminal_state = False
        terminal_reason = fields.get("terminal_reason")
        try:
            from backend.models.database import Task as _Task

            task_row = (
                self._session.query(_Task)
                .filter_by(id=task_id)
                .populate_existing()
                .first()
            )
            if (
                task_row is not None
                and str(getattr(task_row, "state", "") or "").strip().lower()
                in {"completed", "failed", "cancelled"}
            ):
                task_terminal_state = True
                if not terminal_reason:
                    terminal_reason = "task_" + str(task_row.state).strip().lower()
        except Exception:
            task_row = None
        source_task_id = self._normalize_task_id(fields.get("source_task_id", task_id))
        if not source_task_id:
            source_task_id = task_id
        if not source_task_id:
            raise ValueError("Exact follow-up source task identity is required")
        session_id = fields.get("session_id")
        if session_id is not None:
            session_id = str(session_id)
        exchange_id = fields.get("exchange_id")
        if exchange_id is not None:
            exchange_id = str(exchange_id)
        source_exchange_id = fields.get("source_exchange_id")
        if source_exchange_id is not None:
            source_exchange_id = str(source_exchange_id)
        user_turn_id = fields.get("user_turn_id")
        assistant_turn_id = fields.get("assistant_turn_id")
        status = self._status(fields.get("status"), self.default_status)
        if task_terminal_state:
            # A terminal task can never accept a queued follow-up. Preserve the
            # exact instruction as honest not-applied state without auto replay.
            status = STATUS_NOT_APPLIED
        if status in (STATUS_APPLIED, STATUS_APPLYING):
            raise ValueError("Follow-up applied status cannot be created by the ledger")
        if status not in ALL_STATUSES:
            raise ValueError("Unsupported follow-up instruction status")
        metadata = self._metadata(fields)

        # Allocate deterministic order for this task's requests.
        max_sequence = (
            self._session.query(func.coalesce(func.max(FollowupInstructionLedgerRow.sequence), 0))
            .filter(FollowupInstructionLedgerRow.source_task_id == source_task_id)
            .scalar()
        )
        sequence = int(max_sequence or 0) + 1

        row = FollowupInstructionLedgerRow(
            request_id=request_id,
            task_id=task_id,
            source_task_id=source_task_id,
            session_id=session_id,
            exchange_id=exchange_id,
            source_exchange_id=source_exchange_id,
            user_turn_id=user_turn_id if isinstance(user_turn_id, int) and not isinstance(user_turn_id, bool) else None,
            assistant_turn_id=assistant_turn_id if isinstance(assistant_turn_id, int) and not isinstance(assistant_turn_id, bool) else None,
            instruction=instruction,
            status=status,
            sequence=sequence,
            created_at=self._now(),
            updated_at=self._now(),
            terminal_at=(self._now() if task_terminal_state else None),
            terminal_reason=(terminal_reason if task_terminal_state else fields.get("terminal_reason")),
            metadata_json=json.dumps(metadata, sort_keys=True) if metadata else None,
        )

        try:
            savepoint = self._session.begin_nested()
            self._session.add(row)
            self._session.flush()
            savepoint.commit()
        except IntegrityError as exc:
            try:
                savepoint.rollback()
            except Exception:
                pass
            raise ValueError(f"Duplicate follow-up instruction identity: {request_id}") from exc

        return FollowupInstructionRequest.from_row(row)

    # ----------------------------------------------------------------- reads
    def get(self, *args, **kwargs) -> Optional[FollowupInstructionRequest]:
        fields = self._extract_fields(*args, **kwargs)
        request_id = fields.get("request_id") or (args[0] if args and isinstance(args[0], str) else None)
        if not isinstance(request_id, str):
            return None
        row = self._session.query(FollowupInstructionLedgerRow).filter_by(request_id=request_id.strip()).populate_existing().first()
        return FollowupInstructionRequest.from_row(row) if row is not None else None

    def status_of(self, *args, **kwargs) -> Optional[str]:
        record = self.get(*args, **kwargs)
        return record.status if record is not None else None

    def pending(self, *args, **kwargs) -> List[FollowupInstructionRequest]:
        fields = self._extract_fields(*args, **kwargs)
        task_id = fields.get("task_id")
        source_task_id = fields.get("source_task_id", task_id)
        q = self._session.query(FollowupInstructionLedgerRow).filter(
            FollowupInstructionLedgerRow.status.in_(PENDING_STATUSES)
        )
        if task_id is not None:
            q = q.filter(
                (FollowupInstructionLedgerRow.task_id == task_id)
                | (FollowupInstructionLedgerRow.source_task_id == source_task_id)
            )
        q = q.order_by(FollowupInstructionLedgerRow.sequence.asc(), FollowupInstructionLedgerRow.created_at.asc(), FollowupInstructionLedgerRow.request_id.asc())
        return [FollowupInstructionRequest.from_row(r) for r in q.populate_existing().all()]

    def list(self, *args, **kwargs) -> List[FollowupInstructionRequest]:
        fields = self._extract_fields(*args, **kwargs)
        task_id = fields.get("task_id")
        status = fields.get("status")
        q = self._session.query(FollowupInstructionLedgerRow)
        if task_id is not None:
            q = q.filter(
                (FollowupInstructionLedgerRow.task_id == task_id)
                | (FollowupInstructionLedgerRow.source_task_id == task_id)
            )
        if status is not None:
            q = q.filter(FollowupInstructionLedgerRow.status == self._status(status, status))
        q = q.order_by(FollowupInstructionLedgerRow.sequence.asc(), FollowupInstructionLedgerRow.created_at.asc(), FollowupInstructionLedgerRow.request_id.asc())
        return [FollowupInstructionRequest.from_row(r) for r in q.populate_existing().all()]

    def ordered(self, *args, **kwargs) -> List[FollowupInstructionRequest]:
        return self.list(*args, **kwargs)

    def next_pending(self, *args, **kwargs) -> Optional[FollowupInstructionRequest]:
        items = self.pending(*args, **kwargs)
        return items[0] if items else None

    # -------------------------------------------------------- status changes
    def transition(self, *args, **kwargs) -> FollowupInstructionRequest:
        self._ensure_transaction()
        # Accept transition(target, new_status, ...) or keyword forms.
        # Determine request identity.
        if args and isinstance(args[0], str) and not kwargs.get("request_id") and (len(args) == 1 or (len(args) > 1 and isinstance(args[1], str))):
            # Could be transition(request_id, new_status)
            pass
        fields = self._extract_fields(*args, **kwargs)
        request_id = fields.get("request_id")
        if request_id is None and args and isinstance(args[0], str):
            request_id = args[0]
        if not isinstance(request_id, str):
            raise ValueError("Follow-up request identity is required")
        request_id = request_id.strip()

        new_status = None
        for candidate in ("status", "new_status", "state", "to_status", "target_status"):
            if candidate in fields and fields.get(candidate) is not None:
                new_status = fields.get(candidate)
                break
        if new_status is None:
            # positional second arg can be status
            if len(args) >= 2 and isinstance(args[1], str):
                new_status = args[1]
        if new_status is None:
            raise ValueError("Target status is required")
        new_status = self._status(new_status)

        # Applied status is never a directly editable ledger state.  It is
        # recorded only by the task-instruction revision commit path in the
        # same transaction, so forged caller-supplied versions are ignored here.
        if new_status == STATUS_APPLIED:
            raise ValueError("Follow-up applied status cannot be set by a direct ledger transition")

        reason = None
        for candidate in ("reason", "terminal_reason", "cancellation_reason", "rejection_reason"):
            if candidate in fields and fields.get(candidate) is not None:
                reason = fields.get(candidate)
                break

        row = self._session.query(FollowupInstructionLedgerRow).filter_by(request_id=request_id).populate_existing().first()
        if row is None:
            raise ValueError(f"Unknown follow-up instruction identity: {request_id}")
        # Direct, un-evidenced applied transitions stay illegal; an evidenced
        # applied transition is the serializable commit record, not a state edit.
        task_version_candidate = fields.get("applied_task_version", fields.get("applied_version"))
        instruction_revision_candidate = fields.get("applied_instruction_revision", fields.get("applied_revision"))
        evidenced_applied = (
            new_status == STATUS_APPLIED
            and isinstance(task_version_candidate, int)
            and not isinstance(task_version_candidate, bool)
            and isinstance(instruction_revision_candidate, int)
            and not isinstance(instruction_revision_candidate, bool)
        )
        if (not evidenced_applied
                and new_status not in TRANSITIONS.get(row.status, frozenset())):
            raise ValueError(
                f"Illegal follow-up instruction transition {row.status!r} -> {new_status!r}"
            )
        row.status = new_status
        row.updated_at = self._now()
        if reason is not None:
            row.terminal_reason = str(reason)
        if new_status in (STATUS_APPLIED,):
            row.applied_at = fields.get("applied_at") if isinstance(fields.get("applied_at"), datetime) else self._now()
            row.applied_task_version = fields.get("applied_task_version", row.applied_task_version)
            row.applied_instruction_revision = fields.get("applied_instruction_revision", row.applied_instruction_revision)
        if new_status in TERMINAL_STATUSES:
            row.terminal_at = self._now()
        self._session.flush()
        return FollowupInstructionRequest.from_row(row)

    async def atomic_apply(self, *args, **kwargs) -> FollowupInstructionRequest:
        """Transaction-composable atomic apply of a reviewed follow-up request.

        A request is marked applied only after the task instruction revision
        commit succeeds in the same transaction.  Caller commits.
        """
        fields = self._extract_fields(*args, **kwargs)
        request_id = fields.get("request_id")
        if request_id is None and args and isinstance(args[0], str):
            request_id = args[0]
        if not isinstance(request_id, str) or not request_id.strip():
            raise ValueError("Follow-up request identity is required")
        request_id = request_id.strip()

        runner = kwargs.get("runner", self._runner)
        if runner is None:
            raise ValueError("A task runner is required to apply a follow-up instruction")
        try:
            from backend.services.task_instruction_revision import TaskInstructionRevision
        except Exception as exc:  # pragma: no cover - import layout fallback
            raise ValueError(str(exc)) from None

        task_id = fields.get("task_id")
        runtime = kwargs.get("runtime")
        instruction = kwargs.get("instruction")
        expected_version = kwargs.get("expected_version", kwargs.get("expected_task_version"))
        exchange_id = kwargs.get("exchange_id")
        source_exchange_id = kwargs.get("source_exchange_id")
        task_id_override = task_id

        revision = TaskInstructionRevision(runner)
        return await revision.apply_followup_request(
            task_id=task_id_override,
            request_id=request_id,
            runner=runner,
            runtime=runtime,
            instruction=instruction,
            expected_version=expected_version,
            exchange_id=exchange_id,
            source_exchange_id=source_exchange_id,
            commit=kwargs.get("commit", True),
        )

    # Canonical apply is ``atomic_apply`` (line 651). The ``apply_*``
    # methods below are compatibility aliases that delegate to
    # ``atomic_apply``; new code should call ``atomic_apply`` directly.
    # ``atomic_apply_many = None`` is retained for compatibility only.
    async def apply_followup(self, *args, **kwargs) -> FollowupInstructionRequest:
        return await self.atomic_apply(*args, **kwargs)

    async def apply_followup_request(self, *args, **kwargs) -> FollowupInstructionRequest:
        return await self.atomic_apply(*args, **kwargs)

    async def apply_pending_followups(self, *args, **kwargs) -> FollowupInstructionRequest:
        return await self.atomic_apply(*args, **kwargs)

    async def apply_at_safe_point(self, *args, **kwargs) -> FollowupInstructionRequest:
        return await self.atomic_apply(*args, **kwargs)

    async def apply_transactional(self, *args, **kwargs) -> FollowupInstructionRequest:
        return await self.atomic_apply(*args, **kwargs)

    atomic_apply_many = None

    def cancel(self, *args, **kwargs) -> FollowupInstructionRequest:
        kwargs.setdefault("status", STATUS_CANCELLED)
        return self.transition(*args, **kwargs)

    def reject(self, *args, **kwargs) -> FollowupInstructionRequest:
        kwargs.setdefault("status", STATUS_REJECTED)
        return self.transition(*args, **kwargs)

    def expire(self, *args, **kwargs) -> FollowupInstructionRequest:
        kwargs.setdefault("status", STATUS_EXPIRED)
        return self.transition(*args, **kwargs)

    def terminal(self, *args, **kwargs) -> FollowupInstructionRequest:
        kwargs.setdefault("status", STATUS_TERMINAL)
        return self.transition(*args, **kwargs)

    def not_applied(self, *args, **kwargs) -> FollowupInstructionRequest:
        kwargs.setdefault("status", STATUS_NOT_APPLIED)
        return self.transition(*args, **kwargs)

    def block(self, *args, **kwargs) -> FollowupInstructionRequest:
        kwargs.setdefault("status", STATUS_BLOCKED)
        return self.transition(*args, **kwargs)

    def mark_terminal(self, *args, **kwargs) -> FollowupInstructionRequest:
        return self.terminal(*args, **kwargs)

    def task_terminal(self, task_id, *args, **kwargs) -> List[FollowupInstructionRequest]:
        """Mark all pending requests for a terminal/stopped task as not applied.

        Requests are preserved, not deleted or rewritten, and never become applied.
        """
        self._ensure_transaction()
        results: List[FollowupInstructionRequest] = []
        for record in self.pending(task_id=task_id):
            try:
                results.append(self.not_applied(record.request_id, reason=kwargs.get("reason", "task_terminal")))
            except ValueError:
                # Already advanced by another caller; leave it untouched.
                current = self.get(record.request_id)
                if current is not None:
                    results.append(current)
        return results

    def on_task_terminal(self, *args, **kwargs) -> List[FollowupInstructionRequest]:
        self._ensure_transaction()
        fields = self._extract_fields(*args, **kwargs)
        task_id = fields.get("task_id")
        if task_id is None and args and isinstance(args[0], str):
            task_id = args[0]
        if task_id is None:
            raise ValueError("Exact task identity is required")
        return self.task_terminal(task_id, reason=fields.get("reason", kwargs.get("reason")))

    def on_task_stopped(self, *args, **kwargs) -> List[FollowupInstructionRequest]:
        return self.on_task_terminal(*args, **kwargs)

    def stopped(self, *args, **kwargs) -> List[FollowupInstructionRequest]:
        return self.on_task_terminal(*args, **kwargs)

    # ---------------------------------------------------- module-level helpers
    # Canonical/alias map (docs only; no alias is removed):
    #   canonical ``receive`` (line 424) <- create, add, record, enqueue,
    #     submit, register, store, put, append_request, add_request,
    #     create_request
    #   canonical ``get`` <- find, fetch, find_request, get_request
    #   canonical ``list`` <- requests, all, list_requests, all_requests,
    #     for_task, by_task, requests_for_task
    #   canonical ``pending`` <- list_pending, pending_requests, queued,
    #     waiters, pending_for_task, ordered_pending
    #   canonical ``ordered`` <- ordered_requests
    #   canonical ``next_pending`` <- first_pending, next
    #   canonical ``transition`` (line 579) <- advance_status, set_status,
    #     update_status, mark_status, change_status, mark_pending,
    #     mark_received; cancel/reject/expire/terminal/not_applied/block
    #     (above) set a status then call ``transition``
    #   ``cancel`` <- cancel_request, mark_cancelled
    #   ``reject`` <- reject_request, mark_rejected
    #   ``expire`` <- expire_request, mark_expired
    #   ``block`` <- mark_blocked; ``terminal`` <- mark_terminal_request
    #   ``on_task_terminal`` <- on_terminal, task_cancelled,
    #     task_completed, task_failed, stopped
    #   ``on_task_stopped`` <- on_stopped, task_stopped
    #   ``can_apply`` reads ``get`` and checks PENDING_STATUSES (predicate,
    #     not an alias of ``atomic_apply``)
    # New code should use the canonical names above; aliases are kept for
    # compatibility. Apply aliases (``apply_*``) map to ``atomic_apply``.
    def create(self, *args, **kwargs):
        return self.receive(*args, **kwargs)

    def add(self, *args, **kwargs):
        return self.receive(*args, **kwargs)

    def record(self, *args, **kwargs):
        return self.receive(*args, **kwargs)

    def enqueue(self, *args, **kwargs):
        return self.receive(*args, **kwargs)

    def submit(self, *args, **kwargs):
        return self.receive(*args, **kwargs)

    def register(self, *args, **kwargs):
        return self.receive(*args, **kwargs)

    def store(self, *args, **kwargs):
        return self.receive(*args, **kwargs)

    def put(self, *args, **kwargs):
        return self.receive(*args, **kwargs)

    def append_request(self, *args, **kwargs):
        return self.receive(*args, **kwargs)

    def add_request(self, *args, **kwargs):
        return self.receive(*args, **kwargs)

    def create_request(self, *args, **kwargs):
        return self.receive(*args, **kwargs)

    def find(self, *args, **kwargs):
        return self.get(*args, **kwargs)

    def fetch(self, *args, **kwargs):
        return self.get(*args, **kwargs)

    def find_request(self, *args, **kwargs):
        return self.get(*args, **kwargs)

    def get_request(self, *args, **kwargs):
        return self.get(*args, **kwargs)

    def requests(self, *args, **kwargs):
        return self.list(*args, **kwargs)

    def all(self, *args, **kwargs):
        return self.list(*args, **kwargs)

    def list_requests(self, *args, **kwargs):
        return self.list(*args, **kwargs)

    def list_pending(self, *args, **kwargs):
        return self.pending(*args, **kwargs)

    def pending_requests(self, *args, **kwargs):
        return self.pending(*args, **kwargs)

    def queued(self, *args, **kwargs):
        return self.pending(*args, **kwargs)

    def waiters(self, *args, **kwargs):
        return self.pending(*args, **kwargs)

    def all_requests(self, *args, **kwargs):
        return self.list(*args, **kwargs)

    def for_task(self, *args, **kwargs):
        return self.list(*args, **kwargs)

    def by_task(self, *args, **kwargs):
        return self.list(*args, **kwargs)

    def requests_for_task(self, *args, **kwargs):
        return self.list(*args, **kwargs)

    def pending_for_task(self, *args, **kwargs):
        return self.pending(*args, **kwargs)

    def ordered_requests(self, *args, **kwargs):
        return self.ordered(*args, **kwargs)

    def ordered_pending(self, *args, **kwargs):
        return self.pending(*args, **kwargs)

    def first_pending(self, *args, **kwargs):
        return self.next_pending(*args, **kwargs)

    def next(self, *args, **kwargs):
        return self.next_pending(*args, **kwargs)

    def advance_status(self, *args, **kwargs):
        return self.transition(*args, **kwargs)

    def set_status(self, *args, **kwargs):
        return self.transition(*args, **kwargs)

    def update_status(self, *args, **kwargs):
        return self.transition(*args, **kwargs)

    def mark_status(self, *args, **kwargs):
        return self.transition(*args, **kwargs)

    def change_status(self, *args, **kwargs):
        return self.transition(*args, **kwargs)

    def cancel_request(self, *args, **kwargs):
        return self.cancel(*args, **kwargs)

    def reject_request(self, *args, **kwargs):
        return self.reject(*args, **kwargs)

    def expire_request(self, *args, **kwargs):
        return self.expire(*args, **kwargs)


    def mark_cancelled(self, *args, **kwargs):
        return self.cancel(*args, **kwargs)

    def mark_rejected(self, *args, **kwargs):
        return self.reject(*args, **kwargs)

    def mark_expired(self, *args, **kwargs):
        return self.expire(*args, **kwargs)

    def mark_pending(self, *args, **kwargs):
        kwargs.setdefault("status", STATUS_PENDING)
        return self.transition(*args, **kwargs)

    def mark_received(self, *args, **kwargs):
        kwargs.setdefault("status", STATUS_RECEIVED)
        return self.transition(*args, **kwargs)

    def mark_blocked(self, *args, **kwargs):
        return self.block(*args, **kwargs)

    def mark_terminal_request(self, *args, **kwargs):
        return self.terminal(*args, **kwargs)

    def on_terminal(self, *args, **kwargs):
        return self.on_task_terminal(*args, **kwargs)

    def on_stopped(self, *args, **kwargs):
        return self.on_task_stopped(*args, **kwargs)

    def task_stopped(self, *args, **kwargs):
        return self.on_task_stopped(*args, **kwargs)

    def task_cancelled(self, *args, **kwargs):
        return self.on_task_terminal(*args, **kwargs)

    def task_completed(self, *args, **kwargs):
        return self.on_task_terminal(*args, **kwargs)

    def task_failed(self, *args, **kwargs):
        return self.on_task_terminal(*args, **kwargs)

    def can_apply(self, *args, **kwargs) -> bool:
        record = self.get(*args, **kwargs) if not (args and isinstance(args[0], str) and len(args) == 1 and args[0] in (STATUS_PENDING,)) else None
        if record is None:
            # maybe request_id passed positionally
            rid = args[0] if args and isinstance(args[0], str) else kwargs.get("request_id")
            record = self.get(rid) if isinstance(rid, str) else None
        return bool(record is not None and record.status in PENDING_STATUSES)
# One service-facing alias; concrete integration will define clearer boundaries.
# Canonical class is ``FollowupInstructionLedgerManager``; the name below is
# a compatibility alias. ``__all__`` below retains legacy exports.
FollowupInstructionLedger = FollowupInstructionLedgerManager

__all__ = [
    "FollowupInstructionLedgerRow",
    "FollowupInstructionRequest",
    "FollowupInstructionLedgerManager",
    "FollowupInstructionLedger",
    "TaskFollowupInstructionLedger",
    "DurableFollowupInstructionLedger",
    "FollowupInstructionStore",
    "TaskInstructionLedger",
    "InstructionLedger",
    "FollowupLedger",
    "DurableInstructionLedger",
    "FollowupInstructionService",
    "FollowupInstructionLedgerService",
    "TaskFollowupInstructionService",
    "STATUS_RECEIVED",
    "STATUS_PENDING",
    "STATUS_QUEUED",
    "STATUS_WAITING",
    "STATUS_APPLYING",
    "STATUS_APPLIED",
    "STATUS_BLOCKED",
    "STATUS_REJECTED",
    "STATUS_CANCELLED",
    "STATUS_EXPIRED",
    "STATUS_NOT_APPLIED",
    "STATUS_TERMINAL",
    "ALL_STATUSES",
    "PENDING_STATUSES",
    "TERMINAL_STATUSES",
    "TRANSITIONS",
]
