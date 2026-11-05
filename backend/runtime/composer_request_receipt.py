"""Durable, transactional composer inbound request receipt (foundation only).

This module is a narrow, non-executing primitive: it persists an immutable
receipt for one inbound composer request, scoped to a trusted session.  It
does not classify the request, select or launch a task, ask for or infer
consent, dispatch a worker, replay, or execute anything.  Replaying the
exact same request identity within the same trusted session returns the
original receipt; a changed exact text or payload identity conflicts rather
than overwriting either user's data.

Binding model
-------------
* ``request_id`` is the explicit client/user supplied identity of one inbound
  request.
* ``session_id`` must be a trusted (system-generated) session.  The receipt
  identity is scoped to ``(request_id, session_id)``; requests in different
  trusted sessions are never merged.
* ``text`` is the exact human request text, preserved without normalization.
* ``payload_identity`` is an opaque canonical digest supplied or derived from
  the exact request payload.  The primitive never interprets it.

Caller owns the database transaction.  This module never commits or rolls
back the whole transaction (savepoints are used only for row conflicts).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Iterable, List, Optional, Tuple

from sqlalchemy import Column, DateTime, Integer, String, Text, UniqueConstraint, CheckConstraint, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

try:
    from backend.models.database import Base
except Exception:  # pragma: no cover - alternate invocation layout
    try:
        from models.database import Base  # type: ignore
    except Exception:
        from sqlalchemy.orm import declarative_base

        Base = declarative_base()

# Status vocabulary for inbound receipts.  The receipt records only that a
# request was received (together with its exact text/payload identity).  It
# does not imply task selection, resolution, dispatch, or consent.
STATUS_RECEIVED = "received"
STATUS_RESOLVING = "resolving"
STATUS_NEEDS_CLARIFICATION = "needs_clarification"
STATUS_RESOLVED = "resolved"
STATUS_FAILED = "failed"

ALL_STATUSES: Tuple[str, ...] = (
    STATUS_RECEIVED,
    STATUS_RESOLVING,
    STATUS_NEEDS_CLARIFICATION,
    STATUS_RESOLVED,
    STATUS_FAILED,
)

DIGEST_VERSION = 1

COMPOSER_REQUEST_RECEIPT_TRANSITIONS: dict[str, frozenset[str]] = {
    STATUS_RECEIVED: frozenset({STATUS_RESOLVING, STATUS_NEEDS_CLARIFICATION, STATUS_RESOLVED, STATUS_FAILED}),
    STATUS_RESOLVING: frozenset({STATUS_NEEDS_CLARIFICATION, STATUS_RESOLVED, STATUS_FAILED}),
    STATUS_NEEDS_CLARIFICATION: frozenset({STATUS_RESOLVED, STATUS_FAILED}),
    STATUS_RESOLVED: frozenset(),
    STATUS_FAILED: frozenset(),
}

TRANSITIONS = COMPOSER_REQUEST_RECEIPT_TRANSITIONS

TERMINAL_STATUSES: Tuple[str, ...] = (STATUS_RESOLVED, STATUS_FAILED)

_STATUS_SYNONYMS = {
    "received": STATUS_RECEIVED,
    "new": STATUS_RECEIVED,
    "open": STATUS_RECEIVED,
    "pending": STATUS_RECEIVED,
    "resolving": STATUS_RESOLVING,
    "needs-clarification": STATUS_NEEDS_CLARIFICATION,
    "needs_clarification": STATUS_NEEDS_CLARIFICATION,
    "clarification": STATUS_NEEDS_CLARIFICATION,
    "resolved": STATUS_RESOLVED,
    "done": STATUS_RESOLVED,
    "complete": STATUS_RESOLVED,
    "completed": STATUS_RESOLVED,
    "failed": STATUS_FAILED,
    "error": STATUS_FAILED,
}

# Aliases kept for compatibility with receipt/ledger style callers.
STATUS_PENDING = STATUS_RECEIVED
STATUS_OPEN = STATUS_RECEIVED
STATUS_DONE = STATUS_RESOLVED
STATUS_COMPLETE = STATUS_RESOLVED
STATUS_COMPLETED = STATUS_RESOLVED
STATUS_ERROR = STATUS_FAILED


def _normalize_status(value: Any, default: str = STATUS_RECEIVED) -> str:
    if value is None:
        return default
    if isinstance(value, bool):
        raise ValueError("Composer request receipt status is required")
    text = str(value).strip().lower()
    if text in ALL_STATUSES:
        return text
    if text in _STATUS_SYNONYMS:
        return _STATUS_SYNONYMS[text]
    # Allow common hyphen/underscore spelling differences.
    alt = text.replace("-", "_")
    if alt in ALL_STATUSES:
        return alt
    if alt in _STATUS_SYNONYMS:
        return _STATUS_SYNONYMS[alt]
    raise ValueError("Unsupported composer request receipt status")


class ComposerRequestReceiptRow(Base):  # type: ignore[misc]
    """Durable row for one composer inbound request receipt."""

    __tablename__ = "composer_request_receipts"

    request_id = Column(String, primary_key=True, nullable=False)
    session_id = Column(String, primary_key=True, nullable=False, index=True)
    text = Column(Text, nullable=False)
    status = Column(String, nullable=False, default=STATUS_RECEIVED)
    text_digest = Column(String, nullable=False)
    payload_digest = Column(String, nullable=False)
    request_digest = Column(String, nullable=False, index=True)
    digest_version = Column(Integer, nullable=False, default=1)
    row_version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        CheckConstraint(
            "status IN (" + ", ".join(repr(s) for s in ALL_STATUSES) + ")",
            name="check_composer_request_receipt_status",
        ),
    )


# Register model metadata at import, matching followup/permission primitives.
try:  # pragma: no cover - import layout
    import backend.models.database  # noqa: F401
except Exception:
    pass


@dataclass
class ComposerRequestReceipt:
    """In-memory receipt returned to callers. Never contains raw params."""

    request_id: str
    session_id: str
    text: str
    status: str = STATUS_RECEIVED
    text_digest: str = ""
    payload_digest: str = ""
    request_digest: str = ""
    digest_version: int = 1
    row_version: int = 1
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @property
    def id(self) -> str:
        return self.request_id

    @property
    def identity(self) -> str:
        return self.request_id

    @property
    def receipt_id(self) -> str:
        return self.request_id

    @property
    def session(self) -> str:
        return self.session_id

    @property
    def trusted_session_id(self) -> str:
        return self.session_id

    @property
    def received(self) -> bool:
        return self.status == STATUS_RECEIVED

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["id"] = self.request_id
        data["identity"] = self.request_id
        data["receipt_id"] = self.request_id
        for key, value in list(data.items()):
            if isinstance(value, datetime):
                data[key] = value.isoformat()
        return data

    @classmethod
    def from_row(cls, row: "ComposerRequestReceiptRow") -> "ComposerRequestReceipt":
        return cls(
            request_id=row.request_id,
            session_id=row.session_id,
            text=row.text,
            status=row.status,
            text_digest=row.text_digest,
            payload_digest=row.payload_digest,
            request_digest=row.request_digest,
            digest_version=int(getattr(row, "digest_version", 1) or 1),
            row_version=int(getattr(row, "row_version", 1) or 1),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


def _session_of(db: Any) -> Session:
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
    runner = getattr(db, "runner", None)
    if runner is not None:
        inner = getattr(runner, "db", None)
        if isinstance(inner, Session):
            return inner
    inner = getattr(db, "db", None)
    if isinstance(inner, Session):
        return inner
    raise ValueError("A database session is required")


def _digest_text(value: Any) -> Tuple[str, str]:
    """Return (stored_text, digest) for exact human text."""
    if not isinstance(value, str):
        raise ValueError("Exact composer request text is required")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("Composer request text contains invalid Unicode") from exc
    text = value
    if not text.strip():
        raise ValueError("Exact composer request text is required")
    raw = text.encode("utf-8")
    return text, hashlib.sha256(raw).hexdigest()


def _digest_payload(value: Any) -> str:
    if value is None:
        # Hash an explicit null sentinel so "no payload" is a stable identity.
        return hashlib.sha256(b"\x00null").hexdigest()
    if isinstance(value, bool):
        canonical = "true" if value else "false"
    elif isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("Composer request payload contains invalid Unicode") from exc
        canonical = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    elif isinstance(value, (int, float)):
        try:
            canonical = json.dumps(value, allow_nan=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise ValueError("Composer request payload is not strict JSON data") from exc
    elif isinstance(value, (dict, list, tuple)):
        try:
            canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError, UnicodeEncodeError) as exc:
            raise ValueError("Composer request payload is not strict JSON data") from exc
    else:
        raise ValueError("Unsupported composer request payload type")
    try:
        encoded = canonical.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("Composer request payload contains invalid Unicode") from exc
    try:
        json.loads(encoded.decode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise ValueError("Composer request payload is not strict JSON data") from exc
    return hashlib.sha256(encoded).hexdigest()


def _trusted_session(session_id: str, db: Session) -> bool:
    try:
        from backend.core.session import is_trusted_session_id

        return bool(is_trusted_session_id(session_id, db=db))
    except Exception:
        try:
            from backend.models.database import SessionLineage
        except Exception:
            return False
        try:
            rows = db.query(SessionLineage).filter_by(session_id=session_id).all()
            return any(bool(getattr(r, "system_generated", False)) and getattr(r, "provenance", "") == "system_uuid4" for r in rows)
        except Exception:
            return False


class ComposerRequestReceiptManager:
    """Isolated transactional manager.

    Every method participates in the caller's transaction and never commits or
    rolls back it.  Caller is responsible for commit/rollback.
    """

    def __init__(self, db: Any = None, *, session: Any = None, runner: Any = None, clock: Any = None, default_status: str = STATUS_RECEIVED):
        if session is not None:
            db = session
        if runner is not None and getattr(runner, "db", None) is not None:
            db = runner.db
        self._runner = runner
        self._session = _session_of(db)
        self._clock = clock or (lambda: datetime.utcnow())
        self.default_status = _normalize_status(default_status, STATUS_RECEIVED)
        # Receipts are received-work evidence only; an invalid default degrades
        # to received rather than allowing an inferred terminal state.
        if self.default_status not in ALL_STATUSES:
            self.default_status = STATUS_RECEIVED

    @property
    def db(self) -> Session:
        return self._session

    @property
    def session(self) -> Session:
        return self._session

    def _now(self) -> datetime:
        return self._clock()

    def _ensure_transaction(self) -> None:
        try:
            dbapi_connection = self._session.connection().connection.dbapi_connection
            try:
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA busy_timeout=30000")
                cursor.close()
            except Exception:
                pass
            if getattr(dbapi_connection, "in_transaction", False):
                return
        except Exception:
            pass
        try:
            import sqlalchemy

            self._session.execute(sqlalchemy.text("BEGIN IMMEDIATE"))
        except Exception:
            pass

    def _extract_fields(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        fields = dict(kwargs)

        positional_names = ("request_id", "session_id", "text", "status", "payload")
        for index, value in enumerate(args):
            if index < len(positional_names):
                key = positional_names[index]
                fields.setdefault(key, value)

        # Identity aliases.
        if "request_id" not in fields:
            for alias in ("request", "id", "identity", "receipt_id", "request_identity", "key", "uuid", "client_request_id", "client_request"):
                if alias in fields:
                    fields["request_id"] = fields.pop(alias)
                    break
        # Trusted session aliases.
        if "session_id" not in fields:
            for alias in ("session", "trusted_session_id", "trusted_session", "session_identity", "context_session"):
                if alias in fields:
                    fields["session_id"] = fields.pop(alias)
                    break
        # Text aliases.
        if "text" not in fields:
            for alias in ("instruction", "human_text", "request_text", "message", "body", "content", "raw_text", "exact_text", "text_bytes", "raw_bytes", "body_bytes"):
                if alias in fields:
                    fields["text"] = fields.pop(alias)
                    break
        # Payload aliases.  The payload is required to be supplied structurally;
        # any digest-like parameter names are deliberately not carried forward,
        # so callers cannot assert payload identity by digest alone.
        if "payload" not in fields:
            for alias in ("data", "request_payload", "raw_payload", "payload_json", "body_payload"):
                if alias in fields:
                    fields["payload"] = fields.pop(alias)
                    break
        # Status is lifecycle state: it is deliberately not used to form receipt
        # identity.  Receive always creates a received receipt; later
        # transactional transitions mutate status separately.

        return fields

    def _validate(self, values: dict[str, Any]) -> dict[str, Any]:
        request_id = values.get("request_id")
        if isinstance(request_id, bool) or not isinstance(request_id, str) or not request_id.strip():
            raise ValueError("Exact composer request identity is required")
        request_id = request_id.strip()
        values["request_id"] = request_id

        session_id = values.get("session_id")
        if isinstance(session_id, bool) or not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("Exact session identity is required")
        session_id = session_id.strip()
        values["session_id"] = session_id

        text, text_digest = _digest_text(values.get("text"))
        values["text"] = text
        values["text_digest"] = text_digest

        payload = values.get("payload")
        if payload is None and "data" in values:
            payload = values.get("data")
        payload_digest = _digest_payload(payload)
        values["payload_digest"] = payload_digest

        # Initial receipt status is deliberately received.  Lifecycle status is
        # mutable only through transactional transitions and is excluded from
        # immutable request binding.
        values["status"] = STATUS_RECEIVED

        canonical_binding = json.dumps(
            {
                "digest_version": 1,
                "request_id": request_id,
                "session_id": session_id,
                "text_digest": text_digest,
                "payload_digest": payload_digest,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        combined = hashlib.sha256(canonical_binding.encode("utf-8")).hexdigest()
        values["request_digest"] = combined
        values["digest_version"] = 1

        values["created_at"] = self._now()
        values["updated_at"] = self._now()
        return values

    def _row_values(self, values: dict[str, Any]) -> dict[str, Any]:
        return {
            "request_id": values["request_id"],
            "session_id": values["session_id"],
            "text": values["text"],
            "status": values["status"],
            "text_digest": values["text_digest"],
            "payload_digest": values["payload_digest"],
            "request_digest": values["request_digest"],
            "digest_version": values.get("digest_version", 1),
            "row_version": 1,
            "created_at": values["created_at"],
            "updated_at": values["updated_at"],
        }

    @staticmethod
    def _valid_immutable_fields(data: Any) -> bool:
        request_id = getattr(data, "request_id", None)
        session_id = getattr(data, "session_id", None)
        text = getattr(data, "text", None)
        text_digest = getattr(data, "text_digest", None)
        payload_digest = getattr(data, "payload_digest", None)
        if not (isinstance(request_id, str) and request_id and "\x00" not in request_id):
            return False
        if not (isinstance(session_id, str) and session_id and "\x00" not in session_id):
            return False
        if not (isinstance(text, str) and text and "\x00" not in text and text.encode("utf-8", errors="replace") is not None):
            return False
        expected_text_digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if text_digest != expected_text_digest:
            return False
        if not (isinstance(payload_digest, str) and len(payload_digest) == 64):
            return False
        if any(ch not in "0123456789abcdef" and ch not in "ABCDEF" for ch in payload_digest):
            return False
        return True

    def _same_binding(self, existing: "ComposerRequestReceipt", values: dict[str, Any]) -> bool:
        # Binding authority is exact immutable components, not the legacy or
        # current digest metadata or mutable lifecycle state.
        if not self._valid_immutable_fields(existing):
            return False
        return (
            existing.request_id == values["request_id"]
            and existing.session_id == values["session_id"]
            and existing.text == values["text"]
            and existing.text_digest == values["text_digest"]
            and existing.payload_digest == values["payload_digest"]
            and "\x00" not in str(values["request_id"])
            and "\x00" not in str(values["session_id"])
        )

    def _fetch_existing(self, values: dict[str, Any]) -> Optional["ComposerRequestReceiptRow"]:
        row = (
            self._session.query(ComposerRequestReceiptRow)
            .filter_by(request_id=values["request_id"], session_id=values["session_id"])
            .populate_existing()
            .first()
        )
        if row is not None:
            return row
        return None

    def receive(self, *args: Any, **kwargs: Any) -> ComposerRequestReceipt:
        """Persist one inbound request receipt, caller commits."""
        self._ensure_transaction()
        fields = self._extract_fields(*args, **kwargs)
        values = self._validate(fields)
        insert_values = self._row_values(values)

        try:
            savepoint = self._session.begin_nested()
            row = ComposerRequestReceiptRow(**insert_values)
            self._session.add(row)
            self._session.flush()
            savepoint.commit()
        except IntegrityError:
            try:
                savepoint.rollback()
            except Exception:
                pass
            existing_row = self._fetch_existing(values)
            if existing_row is None:
                raise ValueError("Conflicting composer request receipt")
            # Version-aware replay: only upconvert rows whose immutable fields
            # now recompute to an unambiguous canonical binding. Legacy ambiguous
            # or malformed rows stay fail-closed and never authorize a retry.
            existing = ComposerRequestReceipt.from_row(existing_row)
            if (
                self._same_binding(existing, values)
                and self._valid_immutable_fields(existing_row)
            ):
                if int(getattr(existing_row, "digest_version", 0) or 0) != DIGEST_VERSION:
                    existing_row.request_digest = values["request_digest"]
                    existing_row.digest_version = DIGEST_VERSION
                    if int(getattr(existing_row, "row_version", 0) or 0) < 1:
                        existing_row.row_version = 1
                    self._session.flush()
                    existing = ComposerRequestReceipt.from_row(existing_row)
                return existing
            raise ValueError(
                f"Conflicting composer request payload for receipt {values['request_id']!r}"
            )
        except BaseException:
            try:
                savepoint.rollback()
            except Exception:
                pass
            raise
        else:
            return ComposerRequestReceipt.from_row(row)

    def receive_request(self, *args: Any, **kwargs: Any) -> ComposerRequestReceipt:
        return self.receive(*args, **kwargs)

    def transition(self, *args: Any, **kwargs: Any) -> ComposerRequestReceipt:
        self._ensure_transaction()
        fields = self._extract_fields(*args, **kwargs)
        request_id = fields.get("request_id")
        if request_id is None and args and isinstance(args[0], str):
            request_id = args[0]
        if not isinstance(request_id, str) or not request_id.strip():
            raise ValueError("Exact composer request identity is required")
        request_id = request_id.strip()
        session_id = fields.get("session_id")
        if not (isinstance(session_id, str) and session_id.strip()):
            raise ValueError("Exact explicit session identity is required")
        session_id = session_id.strip()

        new_status = None
        for candidate in ("status", "new_status", "state", "to_status", "target_status"):
            if candidate in fields and fields.get(candidate) is not None:
                new_status = fields.get(candidate)
                break
        if new_status is None and len(args) >= 2 and isinstance(args[1], str):
            new_status = args[1]
        if new_status is None:
            raise ValueError("Target status is required")
        new_status = _normalize_status(new_status, new_status)
        # _normalize_status no longer accepts unknown? It raises? It calls ALL? Current normalize raises ValueError if not. Good.

        row = (
            self._session.query(ComposerRequestReceiptRow)
            .filter_by(request_id=request_id, session_id=session_id)
            .populate_existing()
            .first()
        )
        if row is None:
            raise ValueError("Unknown composer request receipt")
        if new_status == row.status:
            return ComposerRequestReceipt.from_row(row)
        allowed = TRANSITIONS.get(row.status, frozenset())
        if new_status not in allowed:
            raise ValueError(
                f"Illegal composer request receipt transition {row.status!r} -> {new_status!r}"
            )
        now = self._now()
        expected_version = int(getattr(row, "row_version", 1) or 1)
        updated_count = (
            self._session.query(ComposerRequestReceiptRow)
            .filter_by(
                request_id=request_id,
                session_id=session_id,
                status=row.status,
                row_version=expected_version,
            )
            .update(
                {
                    "status": new_status,
                    "updated_at": now,
                    "row_version": expected_version + 1,
                },
                synchronize_session=False,
            )
        )
        if updated_count != 1:
            latest = (
                self._session.query(ComposerRequestReceiptRow)
                .filter_by(request_id=request_id, session_id=session_id)
                .populate_existing()
                .first()
            )
            if latest is not None and latest.status == new_status:
                return ComposerRequestReceipt.from_row(latest)
            raise ValueError("Conflicting composer request receipt transition")
        latest = (
            self._session.query(ComposerRequestReceiptRow)
            .filter_by(request_id=request_id, session_id=session_id)
            .populate_existing()
            .first()
        )
        self._session.flush()
        return ComposerRequestReceipt.from_row(latest if latest is not None else row)

    def set_status(self, *args: Any, **kwargs: Any) -> ComposerRequestReceipt:
        return self.transition(*args, **kwargs)

    def update_status(self, *args: Any, **kwargs: Any) -> ComposerRequestReceipt:
        return self.transition(*args, **kwargs)

    def advance_status(self, *args: Any, **kwargs: Any) -> ComposerRequestReceipt:
        return self.transition(*args, **kwargs)

    def record(self, *args: Any, **kwargs: Any) -> ComposerRequestReceipt:
        return self.receive(*args, **kwargs)

    def store(self, *args: Any, **kwargs: Any) -> ComposerRequestReceipt:
        return self.receive(*args, **kwargs)

    def create(self, *args: Any, **kwargs: Any) -> ComposerRequestReceipt:
        return self.receive(*args, **kwargs)

    def capture(self, *args: Any, **kwargs: Any) -> ComposerRequestReceipt:
        return self.receive(*args, **kwargs)

    def ingest(self, *args: Any, **kwargs: Any) -> ComposerRequestReceipt:
        return self.receive(*args, **kwargs)

    def submit(self, *args: Any, **kwargs: Any) -> ComposerRequestReceipt:
        return self.receive(*args, **kwargs)

    def add(self, *args: Any, **kwargs: Any) -> ComposerRequestReceipt:
        return self.receive(*args, **kwargs)

    def put(self, *args: Any, **kwargs: Any) -> ComposerRequestReceipt:
        return self.receive(*args, **kwargs)

    def record_model_rejection_marker(self, request_id, session_id, text_digest, payload_digest):
        return record_composer_model_rejection_marker(
            self.db, request_id, session_id, text_digest, payload_digest
        )

    def get_model_rejection_marker(self, request_id, session_id):
        return get_composer_model_rejection_marker(self.db, request_id, session_id)

    # Reads.
    def get(self, *args: Any, **kwargs: Any) -> Optional[ComposerRequestReceipt]:
        fields = self._extract_fields(*args, **kwargs)
        request_id = fields.get("request_id")
        if request_id is None and args and isinstance(args[0], str):
            request_id = args[0]
        session_id = fields.get("session_id")
        if not (isinstance(session_id, str) and session_id.strip()):
            raise ValueError("Exact explicit session identity is required")
        session_id = session_id.strip()
        if isinstance(request_id, str):
            request_id = request_id.strip()
            q = self._session.query(ComposerRequestReceiptRow).filter_by(request_id=request_id, session_id=session_id)
            rows = q.populate_existing().all()
            if not rows:
                return None
            # If multiple sessions match and no explicit session, do not leak one.
            if not (isinstance(session_id, str) and session_id.strip()) and len(rows) > 1:
                return None
            return ComposerRequestReceipt.from_row(rows[0])
        return None

    def find(self, *args: Any, **kwargs: Any) -> Optional[ComposerRequestReceipt]:
        return self.get(*args, **kwargs)

    def fetch(self, *args: Any, **kwargs: Any) -> Optional[ComposerRequestReceipt]:
        return self.get(*args, **kwargs)

    def get_receipt(self, *args: Any, **kwargs: Any) -> Optional[ComposerRequestReceipt]:
        return self.get(*args, **kwargs)

    def find_receipt(self, *args: Any, **kwargs: Any) -> Optional[ComposerRequestReceipt]:
        return self.get(*args, **kwargs)

    def by_id(self, *args: Any, **kwargs: Any) -> Optional[ComposerRequestReceipt]:
        return self.get(*args, **kwargs)

    def status_of(self, *args: Any, **kwargs: Any) -> Optional[str]:
        receipt = self.get(*args, **kwargs)
        return receipt.status if receipt is not None else None

    def list(self, *args: Any, **kwargs: Any) -> List[ComposerRequestReceipt]:
        fields = self._extract_fields(*args, **kwargs)
        session_id = fields.get("session_id")
        if not (isinstance(session_id, str) and session_id.strip()):
            raise ValueError("Exact explicit session identity is required")
        session_id = session_id.strip()
        status = fields.get("status")
        q = self._session.query(ComposerRequestReceiptRow).filter_by(session_id=session_id)
        if isinstance(status, str):
            try:
                norm = _normalize_status(status, status)
            except ValueError:
                norm = status
            q = q.filter(ComposerRequestReceiptRow.status == norm)
        q = q.order_by(ComposerRequestReceiptRow.created_at.asc(), ComposerRequestReceiptRow.request_id.asc())
        return [ComposerRequestReceipt.from_row(r) for r in q.populate_existing().all()]

    def all(self, *args: Any, **kwargs: Any) -> List[ComposerRequestReceipt]:
        return self.list(*args, **kwargs)

    def list_receipts(self, *args: Any, **kwargs: Any) -> List[ComposerRequestReceipt]:
        return self.list(*args, **kwargs)

    def for_session(self, *args: Any, **kwargs: Any) -> List[ComposerRequestReceipt]:
        return self.list(*args, **kwargs)

    def by_session(self, *args: Any, **kwargs: Any) -> List[ComposerRequestReceipt]:
        return self.list(*args, **kwargs)

    def exists(self, *args: Any, **kwargs: Any) -> bool:
        return self.get(*args, **kwargs) is not None

    def has_conflict(self, *args: Any, **kwargs: Any) -> bool:
        fields = self._extract_fields(*args, **kwargs)
        try:
            values = self._validate(fields)
        except ValueError:
            return True
        existing = self._fetch_existing(values)
        if existing is None:
            return False
        return not self._same_binding(ComposerRequestReceipt.from_row(existing), values)


def migrate_composer_request_receipt_digests(engine: Any) -> int:
    """Version-aware, non-destructive replay migration for legacy digest rows.

    Only rows whose immutable identities recompute cleanly and contain no
    ambiguous NUL identity are upgraded.  They retain their request text and
    payload digest; malformed rows are skipped rather than being trusted.
    """
    try:
        from sqlalchemy.orm import sessionmaker
    except Exception:
        return 0

    migrated = 0
    db = sessionmaker(bind=engine)()
    try:
        rows = db.query(ComposerRequestReceiptRow).all()
        for row in rows:
            try:
                if not ComposerRequestReceiptManager._valid_immutable_fields(row):
                    continue
                if int(getattr(row, "digest_version", 0) or 0) >= DIGEST_VERSION:
                    continue
                session = db
                canonical = json.dumps(
                    {
                        "digest_version": DIGEST_VERSION,
                        "request_id": row.request_id,
                        "session_id": row.session_id,
                        "text_digest": row.text_digest,
                        "payload_digest": row.payload_digest,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                )
                row.request_digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
                row.digest_version = DIGEST_VERSION
                if int(getattr(row, "row_version", 0) or 0) < 1:
                    row.row_version = 1
                migrated += 1
            except Exception:
                continue
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        migrated = 0
    finally:
        db.close()
    return migrated


# Module-level aliases for alternate import/name expectations.
ComposerInboundRequestReceiptManager = ComposerRequestReceiptManager
ComposerInboundRequestReceipt = ComposerRequestReceipt
InboundRequestReceiptManager = ComposerRequestReceiptManager
ComposerReceiptManager = ComposerRequestReceiptManager
RequestReceiptManager = ComposerRequestReceiptManager
ComposerRequestReceiptLedger = ComposerRequestReceiptManager
ComposerRequestReceiptStore = ComposerRequestReceiptManager
ComposerRequestReceiptService = ComposerRequestReceiptManager
ComposerRequestLedger = ComposerRequestReceiptManager
ComposerInboundReceiptManager = ComposerRequestReceiptManager
ComposerRequestManager = ComposerRequestReceiptManager
DurableComposerRequestReceipt = ComposerRequestReceiptManager
DurableComposerInboundRequestReceipt = ComposerRequestReceiptManager

# Alias the row class as well, under likely spelling variants.
ComposerInboundRequestReceiptRow = ComposerRequestReceiptRow
InboundRequestReceiptRow = ComposerRequestReceiptRow
ComposerReceiptRow = ComposerRequestReceiptRow
RequestReceiptRow = ComposerRequestReceiptRow

__all__ = [
    "STATUS_RECEIVED",
    "STATUS_RESOLVING",
    "STATUS_NEEDS_CLARIFICATION",
    "STATUS_RESOLVED",
    "STATUS_FAILED",
    "ComposerModelRejectionMarkerRow",
    "ComposerModelRejectionMarker",
    "MODEL_REJECTION_CLASS",
    "record_composer_model_rejection_marker",
    "get_composer_model_rejection_marker",
    "is_composer_model_rejection_marker_exact",
    "ALL_STATUSES",
    "TERMINAL_STATUSES",
    "STATUS_PENDING",
    "STATUS_OPEN",
    "STATUS_DONE",
    "STATUS_COMPLETE",
    "STATUS_COMPLETED",
    "STATUS_ERROR",
    "ComposerRequestReceiptRow",
    "ComposerRequestReceipt",
    "ComposerRequestReceiptManager",
    "ComposerInboundRequestReceiptManager",
    "ComposerInboundRequestReceipt",
    "InboundRequestReceiptManager",
    "ComposerReceiptManager",
    "RequestReceiptManager",
    "ComposerRequestReceiptLedger",
    "ComposerRequestReceiptStore",
    "ComposerRequestReceiptService",
    "ComposerRequestLedger",
    "ComposerInboundReceiptManager",
    "ComposerRequestManager",
    "DurableComposerRequestReceipt",
    "DurableComposerInboundRequestReceipt",
    "TRANSITIONS",
    "COMPOSER_REQUEST_RECEIPT_TRANSITIONS",
    "DIGEST_VERSION",
    "migrate_composer_request_receipt_digests",
    "ComposerInboundRequestReceiptRow",
    "InboundRequestReceiptRow",
    "ComposerReceiptRow",
    "RequestReceiptRow",
]

# ---------------------------------------------------------------------------
# Additive future known model-output rejection marker.
#
# This is intentionally additive: the historical composer receipt table and its
# CHECK vocabulary remain untouched, including old ``failed`` rows that predate
# the marker.  Table registration happens at import, so Base.metadata.create_all
# at startup creates it on old populated databases without rebuilding historical
# rows or altering their CHECK constraints.
MODEL_REJECTION_CLASS = "model_output"
KNOWN_MODEL_REJECTION_CLASS = MODEL_REJECTION_CLASS


class ComposerModelRejectionMarkerRow(Base):  # type: ignore[misc]
    """Durable marker for a freshly owned known model-output rejection."""

    __tablename__ = "composer_model_rejection_markers"

    request_id = Column(String, primary_key=True, nullable=False)
    session_id = Column(String, primary_key=True, nullable=False, index=True)
    text_digest = Column(String, nullable=False)
    payload_digest = Column(String, nullable=False)
    rejection_class = Column(String, nullable=False, default=MODEL_REJECTION_CLASS, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    __table_args__ = (
        CheckConstraint(
            "rejection_class IN ('" + MODEL_REJECTION_CLASS + "')",
            name="check_composer_model_rejection_class",
        ),
    )


def _marker_row_values(request_id, session_id, text_digest, payload_digest):
    return {
        "request_id": request_id,
        "session_id": session_id,
        "text_digest": text_digest,
        "payload_digest": payload_digest,
        "rejection_class": MODEL_REJECTION_CLASS,
        "created_at": datetime.utcnow(),
    }


def record_composer_model_rejection_marker(session, request_id, session_id, text_digest, payload_digest):
    """Insert one marker, participating in the caller transaction."""
    row = ComposerModelRejectionMarkerRow(**_marker_row_values(request_id, session_id, text_digest, payload_digest))
    session.add(row)
    session.flush()
    return row


def get_composer_model_rejection_marker(session, request_id, session_id):
    return (
        session.query(ComposerModelRejectionMarkerRow)
        .filter_by(request_id=request_id, session_id=session_id)
        .populate_existing()
        .first()
    )


def is_composer_model_rejection_marker_exact(marker, receipt):
    if marker is None or receipt is None:
        return False
    if getattr(marker, "rejection_class", None) != MODEL_REJECTION_CLASS:
        return False
    if getattr(marker, "request_id", None) != getattr(receipt, "request_id", None):
        return False
    if getattr(marker, "session_id", None) != getattr(receipt, "session_id", None):
        return False
    if getattr(marker, "text_digest", None) != getattr(receipt, "text_digest", None):
        return False
    if getattr(marker, "payload_digest", None) != getattr(receipt, "payload_digest", None):
        return False
    return True


# Backward/alternate naming.
ComposerModelRejectionMarker = ComposerModelRejectionMarkerRow
