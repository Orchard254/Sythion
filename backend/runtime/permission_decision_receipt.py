"""Transactional durable permission decision receipt (foundation only).

This module defines a narrow primitive: an immutable, durable record of one
explicit permission *decision* bound to an exact permission proposal.  It is
non-executing: it never starts a worker, performs an OS action, mutates a
task, reads model output as consent, or proves a human identity.  It exists to
replace the unsafe "bare pending status flip" with an atomic
compare-and-set receipt.

Binding model
-------------
* ``decision_id`` is the explicit identity of one permission decision
  request (supplied by the trusted UI/composer boundary in the future; not
  derivable from model output).
* ``task_id`` / ``source_task_id`` identify the task the proposed action is
  bound to.
* ``action_id`` and ``tool_name`` identify the proposed action exactly.
* ``task_version`` binds the receipt to the task state version that presented
  the proposal.
* ``schema_version`` binds the receipt to the interpretation of the proposal
  (stale proposal versions cannot be approved).
* ``proposal_digest`` is the canonical immutable digest of the normalized
  proposal.  It is supplied by the caller; this primitive never computes or
  substitutes one, so proposal semantics are not invented here.

CAS / idempotency
-----------------
A receipt is inserted under a unique ``decision_id``.  Replaying the exact
same decision request returns the original receipt.  A conflicting decision
(approve vs. deny), a conflicting binding (task/action/version/digest differs),
or a concurrent insert loses the race and raises ``ValueError``.  Raw
parameters/content are never persisted or returned.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Callable, List, Optional, Tuple

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    CheckConstraint,
    func,
    JSON,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

try:
    from backend.models.database import Base, Task
except Exception:  # pragma: no cover - alternate invocation layout
    try:
        from models.database import Base, Task  # type: ignore
    except Exception:
        from sqlalchemy.orm import declarative_base

        Base = declarative_base()  # type: ignore
        Task = None  # type: ignore

# Status / decision vocabulary.  Pending is allowed only as an un-decided slot
# that must still be advanced by an explicit approve/deny; terminal decisions
# are immutable once recorded.
STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_DENIED = "denied"

ALL_STATUSES: Tuple[str, ...] = (STATUS_PENDING, STATUS_APPROVED, STATUS_DENIED)
TERMINAL_STATUSES: Tuple[str, ...] = (STATUS_APPROVED, STATUS_DENIED)

# Named SQLite busy timeout (ms) used by ``_ensure_transaction``; the literal
# previously embedded in the PRAGMA now reads from here.
BUSY_TIMEOUT = 30000

DIGEST_HEX_CHARS = frozenset("0123456789abcdefABCDEF")

# Nullable, additive provenance fields for decision receipts. These carry trusted
# issuance/provenance metadata opaquely; they never grant authorization, are not
# required for legacy rows, and do not alter the core decision binding. Stored
# as JSON-compatible nullable columns so both scalar identifiers and structured
# provenance objects remain possible without inventing receipt semantics here.
PROVENANCE_FIELDS: Tuple[str, ...] = (
    "provenance",
    "trusted",
    "principal",
    "principal_id",
    "issuer",
    "issuer_id",
    "issued_by",
    "issued_by_principal",
    "trusted_issuer",
    "trust_source",
    "trusted_source",
    "authenticator",
    "authenticator_id",
    "auth_method",
    "auth_assurance",
    "authentication_method",
    "authentication_assurance",
    "authenticator_method",
    "authenticator_reason",
    "assurance",
    "assurance_class",
    "assurance_level",
    "identity_method",
    "identity_assurance",
    "credential_method",
    "approved_by",
    "approved_by_user",
    "actor",
    "actor_id",
    "subject",
    "verified_by",
)

ALL_PROVENANCE_FIELDS: Tuple[str, ...] = PROVENANCE_FIELDS

_DECISION_SYNONYMS = {
    "approve": STATUS_APPROVED,
    "approved": STATUS_APPROVED,
    "allow": STATUS_APPROVED,
    "allowed": STATUS_APPROVED,
    "grant": STATUS_APPROVED,
    "granted": STATUS_APPROVED,
    "yes": STATUS_APPROVED,
    "deny": STATUS_DENIED,
    "denied": STATUS_DENIED,
    "reject": STATUS_DENIED,
    "rejected": STATUS_DENIED,
    "refuse": STATUS_DENIED,
    "refused": STATUS_DENIED,
    "no": STATUS_DENIED,
    "pending": STATUS_PENDING,
    "awaiting": STATUS_PENDING,
    "open": STATUS_PENDING,
}


def _normalize_decision(value: Any, default: Optional[str] = None) -> str:
    if value is None:
        if default is not None:
            value = default
        else:
            raise ValueError("An explicit permission decision is required")
    if isinstance(value, bool):
        raise ValueError("An explicit permission decision is required")
    text = str(value).strip().lower()
    if text in _DECISION_SYNONYMS:
        return _DECISION_SYNONYMS[text]
    if text in ALL_STATUSES:
        return text
    raise ValueError("Unsupported permission decision")


def _is_unsafe_key(key: Any) -> bool:
    if not isinstance(key, str):
        return True
    low = key.strip().lower()
    return low in {
        "params",
        "param",
        "parameters",
        "content",
        "raw_params",
        "raw_content",
        "arguments",
        "args",
        "payload",
    } or low.endswith("_params") or low.endswith("_content")


class PermissionDecisionReceiptRow(Base):  # type: ignore[misc]
    """Durable row for one permission decision receipt."""

    __tablename__ = "permission_decision_receipts"

    decision_id = Column(String, primary_key=True)
    task_id = Column(
        String, ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source_task_id = Column(String, nullable=True, index=True)
    action_id = Column(String, nullable=False, index=True)
    tool_name = Column(String, nullable=False)
    task_version = Column(Integer, nullable=False)
    schema_version = Column(Integer, nullable=False, default=1)
    proposal_digest = Column(String, nullable=False)
    decision = Column(String, nullable=False)
    status = Column(String, nullable=False, default=STATUS_PENDING)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    decided_at = Column(DateTime, nullable=True)
    terminal_reason = Column(String, nullable=True)
    provenance = Column(JSON, nullable=True)
    trusted = Column(JSON, nullable=True)
    principal = Column(JSON, nullable=True)
    principal_id = Column(JSON, nullable=True)
    issuer = Column(JSON, nullable=True)
    issuer_id = Column(JSON, nullable=True)
    issued_by = Column(JSON, nullable=True)
    issued_by_principal = Column(JSON, nullable=True)
    trusted_issuer = Column(JSON, nullable=True)
    trust_source = Column(JSON, nullable=True)
    trusted_source = Column(JSON, nullable=True)
    authenticator = Column(JSON, nullable=True)
    authenticator_id = Column(JSON, nullable=True)
    auth_method = Column(JSON, nullable=True)
    auth_assurance = Column(JSON, nullable=True)
    authentication_method = Column(JSON, nullable=True)
    authentication_assurance = Column(JSON, nullable=True)
    authenticator_method = Column(JSON, nullable=True)
    authenticator_reason = Column(JSON, nullable=True)
    assurance = Column(JSON, nullable=True)
    assurance_class = Column(JSON, nullable=True)
    assurance_level = Column(JSON, nullable=True)
    identity_method = Column(JSON, nullable=True)
    identity_assurance = Column(JSON, nullable=True)
    credential_method = Column(JSON, nullable=True)
    approved_by = Column(JSON, nullable=True)
    approved_by_user = Column(JSON, nullable=True)
    actor = Column(JSON, nullable=True)
    actor_id = Column(JSON, nullable=True)
    subject = Column(JSON, nullable=True)
    verified_by = Column(JSON, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN (" + ", ".join(repr(s) for s in ALL_STATUSES) + ")",
            name="check_permission_decision_receipt_status",
        ),
        CheckConstraint(
            "decision IN (" + ", ".join(repr(s) for s in ALL_STATUSES) + ")",
            name="check_permission_decision_receipt_decision",
        ),
        UniqueConstraint(
            "source_task_id",
            "action_id",
            "proposal_digest",
            name="uq_permission_decision_receipt_binding",
        ),
    )


# Register metadata at import time, matching followup ledger convention.
try:
    import backend.models.database  # noqa: F401
except Exception:  # pragma: no cover
    pass


@dataclass
class PermissionDecisionReceipt:
    """In-memory receipt returned to callers (never contains raw params/content)."""

    decision_id: str
    task_id: Optional[str] = None
    source_task_id: Optional[str] = None
    action_id: str = ""
    tool_name: str = ""
    task_version: int = 1
    schema_version: int = 1
    proposal_digest: str = ""
    decision: str = STATUS_PENDING
    status: str = STATUS_PENDING
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    decided_at: Optional[datetime] = None
    terminal_reason: Optional[str] = None
    # Nullable, additive provenance (non-authorizing). Kept as Any to support
    # opaque composite provenance values supplied by a trusted boundary.
    provenance: Any = None
    trusted: Any = None
    principal: Any = None
    principal_id: Any = None
    issuer: Any = None
    issuer_id: Any = None
    issued_by: Any = None
    issued_by_principal: Any = None
    trusted_issuer: Any = None
    trust_source: Any = None
    trusted_source: Any = None
    authenticator: Any = None
    authenticator_id: Any = None
    auth_method: Any = None
    auth_assurance: Any = None
    authentication_method: Any = None
    authentication_assurance: Any = None
    authenticator_method: Any = None
    authenticator_reason: Any = None
    assurance: Any = None
    assurance_class: Any = None
    assurance_level: Any = None
    identity_method: Any = None
    identity_assurance: Any = None
    credential_method: Any = None
    approved_by: Any = None
    approved_by_user: Any = None
    actor: Any = None
    actor_id: Any = None
    subject: Any = None
    verified_by: Any = None

    @property
    def id(self) -> str:
        return self.decision_id

    @property
    def identity(self) -> str:
        return self.decision_id

    @property
    def receipt_id(self) -> str:
        return self.decision_id

    @property
    def approved(self) -> bool:
        return self.status == STATUS_APPROVED

    @property
    def denied(self) -> bool:
        return self.status == STATUS_DENIED

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["id"] = self.decision_id
        data["identity"] = self.decision_id
        data["receipt_id"] = self.decision_id
        for key, value in list(data.items()):
            if isinstance(value, datetime):
                data[key] = value.isoformat()
        # Defensive: never expose any raw param/content field even if one was
        # accidentally stored on the dataclass.
        for key in list(data.keys()):
            if _is_unsafe_key(key):
                del data[key]
        return data

    @classmethod
    def from_row(cls, row: "PermissionDecisionReceiptRow") -> "PermissionDecisionReceipt":
        receipt = cls(
            decision_id=row.decision_id,
            task_id=row.task_id,
            source_task_id=row.source_task_id,
            action_id=row.action_id,
            tool_name=row.tool_name,
            task_version=int(row.task_version),
            schema_version=int(row.schema_version or 1),
            proposal_digest=row.proposal_digest,
            decision=row.decision,
            status=row.status,
            created_at=row.created_at,
            updated_at=row.updated_at,
            decided_at=row.decided_at,
            terminal_reason=row.terminal_reason,
        )
        # Populate nullable additive provenance fields without breaking older rows.
        for _prov_field in PROVENANCE_FIELDS:
            try:
                setattr(receipt, _prov_field, getattr(row, _prov_field, None))
            except Exception:
                setattr(receipt, _prov_field, None)
        return receipt


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
    # TaskRunner-style objects.
    inner = getattr(db, "db", None)
    if isinstance(inner, Session):
        return inner
    raise ValueError("A database session is required")


def _task_id_from_task(task: Any) -> Optional[str]:
    if task is None:
        return None
    if isinstance(task, str):
        return task
    for attr in ("id", "task_id", "task_identity"):
        value = getattr(task, attr, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


class PermissionDecisionReceiptManager:
    """Isolated transactional manager.

    Every method participates in the caller's transaction and never commits it.
    The caller owns commit/rollback, which preserves CAS atomicity and
    rollback-on-failure semantics.
    """

    def __init__(
        self,
        db: Any = None,
        *,
        session: Any = None,
        runner: Any = None,
        task: Any = None,
        clock: Any = None,
        default_decision: Any = None,
    ) -> None:
        if session is not None:
            db = session
        if runner is not None and getattr(runner, "db", None) is not None:
            db = runner.db
        self._runner = runner
        self._task_hint = task
        self._session = _session_of(db)
        self._clock = clock or (lambda: datetime.utcnow())
        self.default_decision = None
        if default_decision is not None:
            try:
                self.default_decision = _normalize_decision(default_decision, None)
            except ValueError:
                self.default_decision = None

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
                cursor.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT}")
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

    @staticmethod
    def _clean_id(value: Any, field_name: str = "decision identity") -> str:
        if isinstance(value, bool) or not isinstance(value, str):
            raise ValueError(f"Exact {field_name} is required")
        cleaned = value.strip()
        if not cleaned:
            raise ValueError(f"Exact {field_name} is required")
        return cleaned

    @staticmethod
    def _clean_text(value: Any, field_name: str, *, max_len: int = 512) -> str:
        """Validate and return exact text for one field.

        Rejects ``None``/blank and enforces ``max_len``; returns ``str(value)``
        unchanged once valid (no stripping of the stored value).
        """
        if value is None:
            raise ValueError(f"Exact {field_name} is required")
        text = str(value)
        if not text.strip():
            raise ValueError(f"Exact {field_name} is required")
        if len(text) > max_len:
            raise ValueError(f"{field_name} exceeds maximum length")
        return text

    @staticmethod
    def _clean_version(value: Any, field_name: str = "task version") -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            # Allow numeric strings, but reject floats.
            if isinstance(value, str) and value.strip().isdigit():
                value = int(value.strip())
            else:
                raise ValueError(f"Exact {field_name} is required")
        if value < 1:
            raise ValueError(f"Exact {field_name} is required")
        return int(value)

    @staticmethod
    def _clean_schema_version(value: Any) -> int:
        if value is None:
            return 1
        if isinstance(value, bool) or not isinstance(value, int):
            if isinstance(value, str) and value.strip().isdigit():
                value = int(value.strip())
            else:
                raise ValueError("Exact schema version is required")
        if value < 1:
            raise ValueError("Exact schema version is required")
        return int(value)

    @staticmethod
    def _clean_digest(value: Any) -> str:
        if value is None:
            raise ValueError("Exact proposal digest is required")
        text = str(value).strip()
        if not text:
            raise ValueError("Exact proposal digest is required")
        # Keep the digest opaque: any non-empty caller-supplied canonical digest
        # is accepted.  We never derive or repair one.
        if len(text) > 256:
            raise ValueError("Proposal digest exceeds maximum length")
        return text

    def _extract_fields(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        fields = dict(kwargs)

        # Positional heuristic.
        positional_names = (
            "decision_id",
            "task_id",
            "action_id",
            "tool_name",
            "task_version",
            "proposal_digest",
            "decision",
        )
        for index, value in enumerate(args):
            if index < len(positional_names):
                key = positional_names[index]
                fields.setdefault(key, value)

        # Identity aliases.
        if "decision_id" not in fields:
            for alias in (
                "request_id",
                "receipt_id",
                "id",
                "identity",
                "request",
                "permission_id",
                "approval_id",
                "consent_id",
                "permission_receipt_id",
                "key",
                "uuid",
            ):
                if alias in fields:
                    fields["decision_id"] = fields.pop(alias)
                    break

        # Task aliases.
        if "task_id" not in fields:
            for alias in ("task", "task_identity", "target_task_id", "task_ref", "target_id"):
                if alias in fields:
                    fields["task_id"] = fields.pop(alias)
                    break
        # action aliases
        if "action_id" not in fields:
            for alias in ("action", "action_identity", "approved_action_id"):
                if alias in fields:
                    fields["action_id"] = fields.pop(alias)
                    break
        # tool alias
        if "tool_name" not in fields and "tool" in fields:
            fields["tool_name"] = fields.pop("tool")
        # version aliases
        if "task_version" not in fields:
            for alias in (
                "version",
                "expected_version",
                "expected_task_version",
                "task_version_binding",
                "proposal_task_version",
                "task_version_id",
            ):
                if alias in fields:
                    fields["task_version"] = fields.pop(alias)
                    break
        # digest aliases
        if "proposal_digest" not in fields:
            for alias in (
                "digest",
                "canonical_digest",
                "proposal_digest_hex",
                "content_digest",
                "action_digest",
                "immutable_digest",
            ):
                if alias in fields:
                    fields["proposal_digest"] = fields.pop(alias)
                    break
        # decision aliases
        if "decision" not in fields:
            for alias in (
                "status",
                "outcome",
                "consent",
                "resolution",
                "result",
                "verdict",
                "answer",
            ):
                if alias in fields:
                    fields["decision"] = fields.pop(alias)
                    break

        # CAS / binding aliases.
        if "expected_task_version" not in fields and "task_version" in fields:
            fields["expected_task_version"] = fields.get("task_version")
        if "expected_pending_action" not in fields:
            for alias in ("pending_action", "expected_pending", "old_pending_action", "pending_json"):
                if alias in fields:
                    fields["expected_pending_action"] = fields.pop(alias)
                    break
        if "expected_pending_status" not in fields:
            for alias in ("pending_status", "expected_pending_decision"):
                if alias in fields:
                    fields["expected_pending_status"] = fields.pop(alias)
                    break
        if "expected_task_state" not in fields:
            for alias in ("task_state", "expected_state"):
                if alias in fields:
                    fields["expected_task_state"] = fields.pop(alias)
                    break
        if "schema_version" not in fields:
            for alias in ("proposal_schema_version", "action_schema_version"):
                if alias in fields:
                    fields["schema_version"] = fields.pop(alias)
                    break
        if "source_task_id" not in fields:
            fields["source_task_id"] = fields.get("task_id")

        # task hint injection.
        if self._task_hint is not None and "task_id" not in fields:
            tid = _task_id_from_task(self._task_hint)
            if tid is not None:
                fields["task_id"] = tid
        if self._runner is not None and "task_id" not in fields:
            tid = getattr(self._runner, "_task_ownership_task_id", None)
            if isinstance(tid, str):
                fields.setdefault("task_id", tid)

        # Do not leak any unsafe raw input into metadata/storage.
        for bad in (
            "params",
            "param",
            "parameters",
            "content",
            "raw_params",
            "raw_content",
            "arguments",
            "args",
            "payload",
            "metadata",
            "extra",
            "context",
        ):
            fields.pop(bad, None)

        # Normalize types.
        if "task_id" in fields and fields["task_id"] is not None:
            task_id = _task_id_from_task(fields["task_id"])
            if task_id is not None:
                fields["task_id"] = task_id
        if "source_task_id" in fields and fields["source_task_id"] is not None:
            source = _task_id_from_task(fields["source_task_id"])
            if source is not None:
                fields["source_task_id"] = source
        if "action_id" in fields and fields["action_id"] is not None:
            fields["action_id"] = str(fields["action_id"]).strip()
        if "tool_name" in fields and fields["tool_name"] is not None:
            fields["tool_name"] = str(fields["tool_name"]).strip()
        if "decision_id" in fields and fields["decision_id"] is not None:
            fields["decision_id"] = self._clean_id(fields["decision_id"], "permission decision identity")

        return fields

    def _load_task(self, task_id: Optional[str]) -> Optional[Any]:
        task = self._task_hint
        if task is not None and _task_id_from_task(task) == task_id:
            return task
        if task_id is None:
            return None
        if self._runner is not None:
            try:
                task = self._runner.get_task(task_id) if hasattr(self._runner, "get_task") else None
                if task is not None:
                    return task
            except Exception:
                pass
        if Task is None:
            return None
        try:
            return self._session.query(Task).filter_by(id=task_id).populate_existing().first()
        except Exception:
            try:
                return self._session.query(Task).filter_by(id=task_id).first()
            except Exception:
                return None

    def _validate_binding(self, values: dict[str, Any]) -> None:
        # Required validated fields.
        decision_id = self._clean_id(values.get("decision_id"), "permission decision identity")
        values["decision_id"] = decision_id
        source_task_id = values.get("source_task_id", values.get("task_id"))
        if source_task_id is None:
            raise ValueError("Exact task identity is required")
        source_task_id = _task_id_from_task(source_task_id)
        if source_task_id is None:
            raise ValueError("Exact task identity is required")
        values["source_task_id"] = source_task_id
        task_id = values.get("task_id")
        task_id = _task_id_from_task(task_id) if task_id is not None else source_task_id
        values["task_id"] = task_id if task_id is not None else None
        action_id = self._clean_text(values.get("action_id"), "proposed action identity")
        tool_name = self._clean_text(values.get("tool_name"), "proposed tool name")
        task_version = self._clean_version(values.get("task_version"), "task version")
        proposal_digest = self._clean_digest(values.get("proposal_digest"))
        schema_version = self._clean_schema_version(values.get("schema_version"))
        decision = _normalize_decision(
            values.get("decision", self.default_decision), self.default_decision or STATUS_PENDING
        )

        values["action_id"] = action_id
        values["tool_name"] = tool_name
        values["task_version"] = task_version
        values["proposal_digest"] = proposal_digest
        values["schema_version"] = schema_version
        values["decision"] = decision
        values["status"] = decision if decision in ALL_STATUSES else STATUS_PENDING
        if values["status"] not in ALL_STATUSES:
            values["status"] = STATUS_PENDING

        # CAS binding against the live task, if present.
        task_id_for_lookup = values.get("task_id") or source_task_id
        task = self._load_task(task_id_for_lookup)
        if task is not None:
            expected_tv = values.get("expected_task_version", task_version)
            try:
                if expected_tv is not None and int(task.version) != int(expected_tv):
                    raise ValueError("Task version binding mismatch")
            except (TypeError, ValueError):
                raise ValueError("Task version binding mismatch")
            # Task version authoritative check against proposed task version.
            try:
                if int(task.version) != int(task_version):
                    raise ValueError("Task version binding mismatch")
            except (TypeError, ValueError):
                raise ValueError("Task version binding mismatch")

            pending_raw = getattr(task, "pending_action", None)
            expected_pending = values.get("expected_pending_action", None)
            if expected_pending is not None:
                if pending_raw != expected_pending:
                    raise ValueError("Pending action binding mismatch")

            expected_pending_status = values.get("expected_pending_status", None)
            if expected_pending_status is not None:
                try:
                    parsed = json.loads(pending_raw or "null")
                except (TypeError, ValueError):
                    parsed = None
                actual = parsed.get("status") if isinstance(parsed, dict) else None
                if actual != _normalize_decision(expected_pending_status, expected_pending_status):
                    raise ValueError("Pending decision binding mismatch")

            expected_state = values.get("expected_task_state", None)
            if expected_state is not None:
                if str(getattr(task, "state", "")) != str(expected_state):
                    raise ValueError("Task state binding mismatch")

        # Softer digest canonicality check (length/opaque only; no invented semantics).
        if not isinstance(proposal_digest, str) or not proposal_digest.strip():
            raise ValueError("Exact proposal digest is required")

        terminal_reason = values.get("terminal_reason")
        if terminal_reason is not None:
            terminal_reason = str(terminal_reason).strip() or None
        values["terminal_reason"] = terminal_reason

        # Normalize decision_id/source_task_id key for idempotency compare; stable.
        values.setdefault("task_id", task_id if task_id is not None else source_task_id)

    def _row_values(self, values: dict[str, Any]) -> dict[str, Any]:
        insert_values = {
            "decision_id": values["decision_id"],
            "task_id": values.get("task_id"),
            "source_task_id": values.get("source_task_id"),
            "action_id": values["action_id"],
            "tool_name": values["tool_name"],
            "task_version": values["task_version"],
            "schema_version": values["schema_version"],
            "proposal_digest": values["proposal_digest"],
            "decision": values["decision"],
            "status": values["status"],
            "created_at": self._now(),
            "updated_at": self._now(),
            "decided_at": self._now() if values["status"] in TERMINAL_STATUSES else None,
            "terminal_reason": values.get("terminal_reason"),
        }
        for _prov_field in PROVENANCE_FIELDS:
            insert_values[_prov_field] = values.get(_prov_field, None)
        return insert_values

    def _same_binding(self, existing: "PermissionDecisionReceipt", values: dict[str, Any]) -> bool:
        return (
            existing.decision_id == values["decision_id"]
            and (existing.source_task_id or existing.task_id) == values["source_task_id"]
            and existing.action_id == values["action_id"]
            and existing.tool_name == values["tool_name"]
            and int(existing.task_version) == int(values["task_version"])
            and int(existing.schema_version) == int(values["schema_version"])
            and existing.proposal_digest == values["proposal_digest"]
            and existing.decision == values["decision"]
            and existing.status == values["status"]
        )

    def _fetch_existing(
        self, values: dict[str, Any], exc: Optional[BaseException] = None
    ) -> Optional["PermissionDecisionReceiptRow"]:
        # By explicit identity first.
        row = (
            self._session.query(PermissionDecisionReceiptRow)
            .filter_by(decision_id=values["decision_id"])
            .populate_existing()
            .first()
        )
        if row is not None:
            return row
        # By binding identity (handles different decision_id races).
        q = self._session.query(PermissionDecisionReceiptRow).filter_by(
            source_task_id=values["source_task_id"],
            action_id=values["action_id"],
            proposal_digest=values["proposal_digest"],
        )
        rows = q.populate_existing().all()
        if not rows:
            return None
        # Pick the first deterministic row.
        rows_sorted = sorted(rows, key=lambda r: (r.created_at or datetime.min, r.decision_id))
        return rows_sorted[0]

    def decide(self, *args: Any, **kwargs: Any) -> PermissionDecisionReceipt:
        """Atomically create or idempotently return one permission decision receipt."""
        self._ensure_transaction()
        fields = self._extract_fields(*args, **kwargs)
        self._validate_binding(fields)

        values = fields
        insert_values = self._row_values(values)

        try:
            savepoint = self._session.begin_nested()
            row = PermissionDecisionReceiptRow(**insert_values)
            self._session.add(row)
            self._session.flush()
            savepoint.commit()
        except IntegrityError as exc:
            try:
                savepoint.rollback()
            except Exception:
                pass
            existing_row = self._fetch_existing(values, exc)
            if existing_row is None:
                self._session.rollback()
                raise ValueError("Conflicting permission decision receipt") from exc
            existing = PermissionDecisionReceipt.from_row(existing_row)
            if self._same_binding(existing, values):
                return existing
            # Idempotent replay of an identical decision request returns the
            # original receipt; a differing decision is a conflict.
            if (
                existing.decision_id == values["decision_id"]
                and existing.decision == values["decision"]
                and existing.proposal_digest == values["proposal_digest"]
            ):
                # binding already matched above would have returned; here mismatch
                # means conflict.
                pass
            raise ValueError(
                f"Conflicting permission decision for receipt {values['decision_id']!r}"
            )
        except BaseException:
            try:
                self._session.rollback()
            except Exception:
                pass
            raise

        return PermissionDecisionReceipt.from_row(row)

    # Convenience CAS entry points — thin aliases of canonical decide().
    # Alias map (all delegate to decide(), see L854; no duplicated logic):
    #   approve/deny/stage preset decision to approved/denied/pending;
    #   record, create, create_receipt, record_decision, record_receipt,
    #   resolve, resolve_pending_permission, resolve_decision, cas,
    #   compare_and_set, submit, store, add, put, append, register
    #   pass the caller's decision through unchanged.
    def approve(self, *args: Any, **kwargs: Any) -> PermissionDecisionReceipt:
        kwargs = dict(kwargs)
        kwargs.setdefault("decision", STATUS_APPROVED)
        kwargs["decision"] = STATUS_APPROVED
        return self.decide(*args, **kwargs)

    def deny(self, *args: Any, **kwargs: Any) -> PermissionDecisionReceipt:
        kwargs = dict(kwargs)
        kwargs["decision"] = STATUS_DENIED
        return self.decide(*args, **kwargs)

    def record(self, *args: Any, **kwargs: Any) -> PermissionDecisionReceipt:
        return self.decide(*args, **kwargs)

    def create(self, *args: Any, **kwargs: Any) -> PermissionDecisionReceipt:
        return self.decide(*args, **kwargs)

    def create_receipt(self, *args: Any, **kwargs: Any) -> PermissionDecisionReceipt:
        return self.decide(*args, **kwargs)

    def record_decision(self, *args: Any, **kwargs: Any) -> PermissionDecisionReceipt:
        return self.decide(*args, **kwargs)

    def record_receipt(self, *args: Any, **kwargs: Any) -> PermissionDecisionReceipt:
        return self.decide(*args, **kwargs)

    def resolve(self, *args: Any, **kwargs: Any) -> PermissionDecisionReceipt:
        return self.decide(*args, **kwargs)

    def resolve_pending_permission(self, *args: Any, **kwargs: Any) -> PermissionDecisionReceipt:
        return self.decide(*args, **kwargs)

    def resolve_decision(self, *args: Any, **kwargs: Any) -> PermissionDecisionReceipt:
        return self.decide(*args, **kwargs)

    def cas(self, *args: Any, **kwargs: Any) -> PermissionDecisionReceipt:
        return self.decide(*args, **kwargs)

    def compare_and_set(self, *args: Any, **kwargs: Any) -> PermissionDecisionReceipt:
        return self.decide(*args, **kwargs)

    def stage(self, *args: Any, **kwargs: Any) -> PermissionDecisionReceipt:
        kwargs = dict(kwargs)
        kwargs.setdefault("decision", STATUS_PENDING)
        kwargs["decision"] = STATUS_PENDING
        return self.decide(*args, **kwargs)

    def submit(self, *args: Any, **kwargs: Any) -> PermissionDecisionReceipt:
        return self.decide(*args, **kwargs)

    def store(self, *args: Any, **kwargs: Any) -> PermissionDecisionReceipt:
        return self.decide(*args, **kwargs)

    def add(self, *args: Any, **kwargs: Any) -> PermissionDecisionReceipt:
        return self.decide(*args, **kwargs)

    def put(self, *args: Any, **kwargs: Any) -> PermissionDecisionReceipt:
        return self.decide(*args, **kwargs)

    def append(self, *args: Any, **kwargs: Any) -> PermissionDecisionReceipt:
        return self.decide(*args, **kwargs)

    def register(self, *args: Any, **kwargs: Any) -> PermissionDecisionReceipt:
        return self.decide(*args, **kwargs)

    def transition(self, *args: Any, **kwargs: Any) -> PermissionDecisionReceipt:
        # Allow a CAS transition of a previously pending receipt by using its
        # stable decision identity; decision defaults to supplied/new decision.
        fields = self._extract_fields(*args, **kwargs)
        # If a decision is not supplied, treat as no-op passthrough conflict.
        new_decision = fields.get("decision", fields.get("new_decision"))
        if new_decision is None:
            raise ValueError("An explicit permission decision is required")
        fields["decision"] = new_decision
        fields["status"] = new_decision
        return self.decide(*(), **fields)

    def _receipt_to_row(self, receipt: PermissionDecisionReceipt) -> Optional[PermissionDecisionReceiptRow]:
        return (
            self._session.query(PermissionDecisionReceiptRow)
            .filter_by(decision_id=receipt.decision_id)
            .populate_existing()
            .first()
        )

    # Reads
    def get(self, *args: Any, **kwargs: Any) -> Optional[PermissionDecisionReceipt]:
        fields = self._extract_fields(*args, **kwargs)
        decision_id = fields.get("decision_id")
        if not isinstance(decision_id, str):
            # maybe first positional is decision_id
            if args and isinstance(args[0], str):
                decision_id = args[0].strip()
        if not isinstance(decision_id, str) or not decision_id.strip():
            # Fall back to binding lookup.
            source_task_id = fields.get("source_task_id")
            action_id = fields.get("action_id")
            proposal_digest = fields.get("proposal_digest")
            if isinstance(source_task_id, str) and isinstance(action_id, str) and isinstance(proposal_digest, str):
                row = (
                    self._session.query(PermissionDecisionReceiptRow)
                    .filter_by(
                        source_task_id=source_task_id,
                        action_id=action_id,
                        proposal_digest=proposal_digest,
                    )
                    .populate_existing()
                    .first()
                )
                return PermissionDecisionReceipt.from_row(row) if row is not None else None
            return None
        row = (
            self._session.query(PermissionDecisionReceiptRow)
            .filter_by(decision_id=decision_id.strip())
            .populate_existing()
            .first()
        )
        return PermissionDecisionReceipt.from_row(row) if row is not None else None

    def find(self, *args: Any, **kwargs: Any) -> Optional[PermissionDecisionReceipt]:
        return self.get(*args, **kwargs)

    def fetch(self, *args: Any, **kwargs: Any) -> Optional[PermissionDecisionReceipt]:
        return self.get(*args, **kwargs)

    def find_receipt(self, *args: Any, **kwargs: Any) -> Optional[PermissionDecisionReceipt]:
        return self.get(*args, **kwargs)

    def get_receipt(self, *args: Any, **kwargs: Any) -> Optional[PermissionDecisionReceipt]:
        return self.get(*args, **kwargs)

    def by_id(self, *args: Any, **kwargs: Any) -> Optional[PermissionDecisionReceipt]:
        return self.get(*args, **kwargs)

    def status_of(self, *args: Any, **kwargs: Any) -> Optional[str]:
        receipt = self.get(*args, **kwargs)
        return receipt.status if receipt is not None else None

    def decision_of(self, *args: Any, **kwargs: Any) -> Optional[str]:
        receipt = self.get(*args, **kwargs)
        return receipt.decision if receipt is not None else None

    def pending(self, *args: Any, **kwargs: Any) -> List[PermissionDecisionReceipt]:
        fields = self._extract_fields(*args, **kwargs)
        task_id = fields.get("source_task_id") or fields.get("task_id")
        q = self._session.query(PermissionDecisionReceiptRow).filter(
            PermissionDecisionReceiptRow.status == STATUS_PENDING
        )
        if isinstance(task_id, str):
            q = q.filter(
                (PermissionDecisionReceiptRow.task_id == task_id)
                | (PermissionDecisionReceiptRow.source_task_id == task_id)
            )
        q = q.order_by(
            PermissionDecisionReceiptRow.created_at.asc(),
            PermissionDecisionReceiptRow.decision_id.asc(),
        )
        return [PermissionDecisionReceipt.from_row(r) for r in q.populate_existing().all()]

    def list(self, *args: Any, **kwargs: Any) -> List[PermissionDecisionReceipt]:
        fields = self._extract_fields(*args, **kwargs)
        task_id = fields.get("source_task_id") or fields.get("task_id")
        status = fields.get("decision") or fields.get("status")
        q = self._session.query(PermissionDecisionReceiptRow)
        if isinstance(task_id, str):
            q = q.filter(
                (PermissionDecisionReceiptRow.task_id == task_id)
                | (PermissionDecisionReceiptRow.source_task_id == task_id)
            )
        if isinstance(status, str):
            try:
                norm = _normalize_decision(status, status)
            except ValueError:
                norm = status
            q = q.filter(PermissionDecisionReceiptRow.status == norm)
        q = q.order_by(
            PermissionDecisionReceiptRow.created_at.asc(),
            PermissionDecisionReceiptRow.decision_id.asc(),
        )
        return [PermissionDecisionReceipt.from_row(r) for r in q.populate_existing().all()]

    def all(self, *args: Any, **kwargs: Any) -> List[PermissionDecisionReceipt]:
        return self.list(*args, **kwargs)

    def list_receipts(self, *args: Any, **kwargs: Any) -> List[PermissionDecisionReceipt]:
        return self.list(*args, **kwargs)

    def all_receipts(self, *args: Any, **kwargs: Any) -> List[PermissionDecisionReceipt]:
        return self.list(*args, **kwargs)

    def for_task(self, *args: Any, **kwargs: Any) -> List[PermissionDecisionReceipt]:
        return self.list(*args, **kwargs)

    def by_task(self, *args: Any, **kwargs: Any) -> List[PermissionDecisionReceipt]:
        return self.list(*args, **kwargs)

    def receipts_for_task(self, *args: Any, **kwargs: Any) -> List[PermissionDecisionReceipt]:
        return self.list(*args, **kwargs)

    # Predicate helpers
    def exists(self, *args: Any, **kwargs: Any) -> bool:
        return self.get(*args, **kwargs) is not None

    def has_conflict(self, *args: Any, **kwargs: Any) -> bool:
        values = self._extract_fields(*args, **kwargs)
        try:
            self._validate_binding(values)
        except ValueError:
            return True
        existing = self._fetch_existing(values)
        if existing is None:
            return False
        receipt = PermissionDecisionReceipt.from_row(existing)
        return not self._same_binding(receipt, values)

    # Utility
    @staticmethod
    def canonical_binding_digest(
        *,
        task_id: Any,
        action_id: Any,
        tool_name: Any,
        task_version: Any,
        schema_version: Any = 1,
        proposal_digest: Any = "",
    ) -> str:
        """Optional helper for callers; not required by the primitive.

        Computes a SHA-256 over the canonical binding fields.  This is a pure
        utility and does not by itself grant or prove anything.
        """
        payload = {
            "task_id": str(task_id),
            "action_id": str(action_id),
            "tool_name": str(tool_name),
            "task_version": int(task_version),
            "schema_version": int(schema_version),
            "proposal_digest": str(proposal_digest),
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# --- Aliases / naming compatibility layer ---
PermissionDecisionReceiptManager.decide_permission = PermissionDecisionReceiptManager.decide
PermissionDecisionReceiptManager.approve_permission = PermissionDecisionReceiptManager.approve
PermissionDecisionReceiptManager.deny_permission = PermissionDecisionReceiptManager.deny
PermissionDecisionReceiptManager.record_permission_decision = PermissionDecisionReceiptManager.decide
PermissionDecisionReceiptManager.decision = PermissionDecisionReceiptManager.decide

# Deprecated/unsafe status API counterpart stays untouched elsewhere; this manager
# is the durable receipt replacement and does not call it.

# Module-level aliases for alternate import names.
PermissionDecisionReceiptLedger = PermissionDecisionReceiptManager
PermissionDecisionReceiptStore = PermissionDecisionReceiptManager
PermissionDecisionReceiptService = PermissionDecisionReceiptManager
PermissionDecisionReceiptLedgerManager = PermissionDecisionReceiptManager
DurablePermissionDecisionReceipt = PermissionDecisionReceiptManager
PermissionDecision = PermissionDecisionReceiptManager
PermissionReceipt = PermissionDecisionReceiptManager
DecisionReceipt = PermissionDecisionReceiptManager
PermissionDecisionManager = PermissionDecisionReceiptManager

__all__ = [
    "STATUS_PENDING",
    "STATUS_APPROVED",
    "STATUS_DENIED",
    "ALL_STATUSES",
    "TERMINAL_STATUSES",
    "PermissionDecisionReceiptRow",
    "PermissionDecisionReceipt",
    "PermissionDecisionReceiptManager",
    "PermissionDecisionReceiptLedger",
    "PermissionDecisionReceiptStore",
    "PermissionDecisionReceiptService",
    "PermissionDecisionReceiptLedgerManager",
    "DurablePermissionDecisionReceipt",
    "PermissionDecision",
    "PermissionReceipt",
    "DecisionReceipt",
    "PermissionDecisionManager",
    "PROVENANCE_FIELDS",
    "ALL_PROVENANCE_FIELDS",
]
