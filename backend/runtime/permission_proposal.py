"""Small isolated non-executing system-authored permission proposal primitive."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

try:
    from backend.models.database import Base, Task
except Exception:
    try:
        from models.database import Base, Task
    except Exception:
        from sqlalchemy.orm import declarative_base

        Base = declarative_base()
        Task = None

# TARGET IDENTITY LIMIT (intentional blocker-level caveat): target_canonical is
# only OS-normpath syntax plus absolute/traversal/wildcard/reserved screening.
# Approved-root containment, reparse-point checks, and durable target identity
# are NOT established here because the existing safe resolver requires an
# initialized application settings database and is not safely reusable by this
# isolated primitive.  Consequently this primitive MUST NOT be described as
# having stable filesystem identity; execution must re-run the full safe
# resolver and fail closed on any mismatch.

# PRIVACY LIMIT: params_digest/proposal_digest/content_sha256 are one-way, but
# known inputs may be brute-forced by parties who know the candidate target and
# content semantics.  Treat these digests as sensitive and do not expose them
# to untrusted callers.

SUPPORTED_ACTION = "file_create"
SUPPORTED_TOOL_NAME = SUPPORTED_ACTION
SCHEMA_VERSION = 1
MAX_WRITE_BYTES = 2 * 1024 * 1024
TERMINAL_TASK_STATES = frozenset(
    {"completed", "failed", "cancelled", "interrupted", "paused"}
)


@dataclass
class PermissionProposal:
    proposal_id: str
    task_id: str
    action_id: str
    tool_name: str
    task_version: int
    schema_version: int
    params_digest: str
    proposal_digest: str
    target_canonical: str
    content_sha256: str
    content_bytes: int
    target_must_not_exist: bool = True
    status: str = "awaiting"
    created_at: Optional[datetime] = None

    @property
    def id(self) -> str:
        return self.proposal_id

    @property
    def identity(self) -> str:
        return self.proposal_id

    @property
    def action_identity(self) -> str:
        return self.action_id

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        for key, value in list(data.items()):
            if isinstance(value, datetime):
                data[key] = value.isoformat()
        for key in list(data.keys()):
            if _is_unsafe_key(key):
                del data[key]
        data["proposal_id"] = self.proposal_id
        data["identity"] = self.proposal_id
        return data


class PermissionProposalRow(Base):
    __tablename__ = "permission_proposals"

    proposal_id = Column(String, primary_key=True)
    task_id = Column(String, nullable=False, index=True)
    action_id = Column(String, nullable=False, index=True)
    tool_name = Column(String, nullable=False)
    task_version = Column(Integer, nullable=False)
    schema_version = Column(Integer, nullable=False, default=SCHEMA_VERSION)
    params_digest = Column(String, nullable=False)
    proposal_digest = Column(String, nullable=False)
    target_canonical = Column(String, nullable=False)
    content_sha256 = Column(String, nullable=False)
    content_bytes = Column(Integer, nullable=False, default=0)
    target_must_not_exist = Column(Boolean, nullable=False, default=True)
    status = Column(String, nullable=False, default="awaiting")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    __table_args__ = (
        UniqueConstraint(
            "task_id",
            "action_id",
            name="uq_permission_proposal_task_action",
        ),
    )


class PermissionProposalManager:
    def __init__(self, session: Any, *, clock: Any = None, task: Any = None) -> None:
        # Task hints are intentionally refused: proposal identity must be
        # established from the actual durable database row on every stage.
        if task is not None:
            raise ValueError("Task hints are not allowed; use the live database row")
        if session is None:
            raise ValueError("A database session is required")
        if isinstance(session, str):
            raise ValueError("A database session is required")
        if not hasattr(session, "execute") and hasattr(session, "db"):
            session = session.db
        self._session = session
        self._clock = clock or (lambda: datetime.utcnow())

    @property
    def db(self):
        return self._session

    @property
    def session(self):
        return self._session

    def _now(self) -> datetime:
        return self._clock()

    def _ensure_transaction(self) -> bool:
        dbapi_connection = None
        try:
            dbapi_connection = self._session.connection().connection.dbapi_connection
        except Exception:
            dbapi_connection = None
        try:
            cursor = dbapi_connection.cursor() if dbapi_connection is not None else None
            if cursor is not None:
                cursor.execute("PRAGMA busy_timeout=30000")
                cursor.close()
        except Exception:
            pass
        if dbapi_connection is not None and getattr(dbapi_connection, "in_transaction", False):
            return False
        self._session.execute(text("BEGIN IMMEDIATE"))
        return True

    @staticmethod
    def _clean_id(value: Any, field_name: str) -> str:
        if isinstance(value, bool) or not isinstance(value, str):
            raise ValueError(f"Exact {field_name} is required")
        cleaned = value.strip()
        if not cleaned:
            raise ValueError(f"Exact {field_name} is required")
        if len(cleaned) > 256:
            raise ValueError(f"{field_name} exceeds maximum length")
        return cleaned

    @staticmethod
    def _clean_version(value: Any) -> int:
        if isinstance(value, bool):
            raise ValueError("Exact task version is required")
        if isinstance(value, int):
            parsed = value
        elif isinstance(value, str) and value.strip().isdigit():
            parsed = int(value.strip())
        else:
            raise ValueError("Exact task version is required")
        if parsed < 1:
            raise ValueError("Exact task version is required")
        return int(parsed)

    @staticmethod
    def _clean_schema_version(value: Any) -> int:
        if value is None:
            return SCHEMA_VERSION
        parsed = None
        if isinstance(value, bool):
            raise ValueError("Exact schema version is required")
        if isinstance(value, int):
            parsed = value
        elif isinstance(value, str) and value.strip().isdigit():
            parsed = int(value.strip())
        else:
            raise ValueError("Exact schema version is required")
        # Only the single supported interpretation is admissible.
        if parsed != SCHEMA_VERSION:
            raise ValueError("Unsupported schema version")
        return int(parsed)

    def _load_task(self, task_id: str):
        # Fail closed if the Task model/schema is unavailable: no fake hints.
        if Task is None:
            return None
        try:
            return self._session.query(Task).filter_by(id=task_id).populate_existing().first()
        except Exception:
            try:
                return self._session.query(Task).filter_by(id=task_id).first()
            except Exception:
                return None

    @staticmethod
    def _normalize_params(params: Any):
        if not isinstance(params, Mapping):
            raise ValueError("Malformed permission proposal parameters")
        unknown = set(params.keys()) - {"path", "content", "overwrite"}
        if unknown:
            raise ValueError("Malformed permission proposal parameters")
        if "path" not in params or "content" not in params:
            raise ValueError("Malformed permission proposal parameters")
        path_raw = params.get("path")
        if not isinstance(path_raw, str) or not path_raw.strip():
            raise ValueError("Malformed permission proposal parameters")
        if path_raw != path_raw.strip():
            raise ValueError("Malformed permission proposal parameters")
        raw_text = path_raw
        # Fail closed on alias/traversal/relative/unsafe names. Do not trim or
        # normalize aliases away. Exact approved-root/reparse identity is still
        # not established (see module-level blocker).
        if "\x00" in raw_text or not os.path.isabs(raw_text):
            raise ValueError("Malformed permission proposal parameters")
        if raw_text.startswith("\\\\") or raw_text.startswith("//"):
            raise ValueError("Malformed permission proposal parameters")
        # Require a fully-qualified drive/root path.  Drive-relative Windows
        # aliases such as "\\server\\share\\x.txt" have no drive and must not be
        # accepted as canonical absolute targets.
        if not os.path.splitdrive(raw_text)[0]:
            raise ValueError("Malformed permission proposal parameters")
        if raw_text.count(":") > 1:
            raise ValueError("Malformed permission proposal parameters")
        path_parts = Path(raw_text).parts
        if len(path_parts) < 2:
            raise ValueError("Malformed permission proposal parameters")
        if any(part == ".." for part in path_parts):
            raise ValueError("Malformed permission proposal parameters")
        if any(_reserved_name(part) for part in path_parts):
            raise ValueError("Malformed permission proposal parameters")
        if any(part != part.strip() or part in {"", ".", ".."} for part in path_parts):
            raise ValueError("Malformed permission proposal parameters")
        content = params.get("content")
        if not isinstance(content, str):
            raise ValueError("Malformed permission proposal parameters")
        content_bytes = len(content.encode("utf-8"))
        if content_bytes > MAX_WRITE_BYTES:
            raise ValueError("Malformed permission proposal parameters")
        if "overwrite" in params and params.get("overwrite") is not False:
            raise ValueError("Malformed permission proposal parameters")

        target_canonical = os.path.normpath(raw_text)
        if (not target_canonical or not os.path.isabs(target_canonical)
                or os.path.normpath(target_canonical) != target_canonical):
            raise ValueError("Malformed permission proposal parameters")
        if any(ch in target_canonical for ch in set("*?[")):
            raise ValueError("Malformed permission proposal parameters")
        if _reserved_name(target_canonical):
            raise ValueError("Malformed permission proposal parameters")

        normalized = {"path": target_canonical, "content": content, "overwrite": False}
        content_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
        params_digest = _digest_value(normalized)
        return normalized, target_canonical, content_sha256, content_bytes

    def stage(self, *, task_id, action_id, tool_name=SUPPORTED_TOOL_NAME, task_version, params, schema_version=None):
        clean_task_id = self._clean_id(task_id, "task identity")
        clean_action_id = self._clean_id(action_id, "action identity")
        if not isinstance(tool_name, str) or tool_name.strip() != SUPPORTED_TOOL_NAME:
            raise ValueError("Unsupported permission action")
        clean_task_version = self._clean_version(task_version)
        clean_schema_version = self._clean_schema_version(schema_version)

        # Begin the physical transaction before any task reads so task/version/
        # pending-action/cancellation guards participate in the same transaction
        # and concurrent mutation races fail closed.
        self._ensure_transaction()

        if isinstance(params, Mapping) and any(_looks_like_digest_key(k) for k in params.keys()):
            raise ValueError("Caller-supplied digest is not allowed")

        task = self._load_task(clean_task_id)
        if task is None:
            raise ValueError("Exact task identity is required")
        if task is not None:
            state = getattr(task, "state", None)
            if state in TERMINAL_TASK_STATES:
                raise ValueError("Task is cancelled, interrupted, or terminal")
            stored_version = getattr(task, "version", None)
            if stored_version is not None and int(stored_version) != clean_task_version:
                raise ValueError("Task version mismatch")
            if getattr(task, "active_action_id", None):
                raise ValueError("Task has an active action")
            pending_raw = getattr(task, "pending_action", None)
            if pending_raw not in (None, "", b""):
                raise ValueError("Task has a pending action")

        normalized, target_canonical, content_sha256, content_bytes = self._normalize_params(params)

        self._ensure_transaction()
        existing = self._fetch_row(clean_task_id, clean_action_id)
        if existing is not None:
            if (
                existing.task_version == clean_task_version
                and existing.schema_version == clean_schema_version
                and existing.target_canonical == target_canonical
                and existing.content_sha256 == content_sha256
            ):
                return _row_to_proposal(existing)
            raise ValueError("Permission proposal already exists or is stale")

        params_digest = _digest_value(normalized)
        proposal_binding = {
            "task_id": clean_task_id,
            "action_id": clean_action_id,
            "tool_name": SUPPORTED_TOOL_NAME,
            "task_version": clean_task_version,
            "schema_version": clean_schema_version,
            "params_digest": params_digest,
            "target_canonical": target_canonical,
            "content_sha256": content_sha256,
            "content_bytes": content_bytes,
            "target_must_not_exist": True,
        }
        proposal_digest = _digest_value(proposal_binding)

        savepoint = None
        try:
            savepoint = self._session.begin_nested()
            row = PermissionProposalRow(
                proposal_id=str(uuid.uuid4()),
                task_id=clean_task_id,
                action_id=clean_action_id,
                tool_name=SUPPORTED_TOOL_NAME,
                task_version=clean_task_version,
                schema_version=clean_schema_version,
                params_digest=params_digest,
                proposal_digest=proposal_digest,
                target_canonical=target_canonical,
                content_sha256=content_sha256,
                content_bytes=content_bytes,
                target_must_not_exist=True,
                status="awaiting",
                created_at=self._now(),
            )
            self._session.add(row)
            self._session.flush()
            if savepoint is not None:
                savepoint.commit()
        except IntegrityError:
            if savepoint is not None:
                try:
                    savepoint.rollback()
                except Exception:
                    pass
            try:
                self._session.expunge(row)
            except Exception:
                pass
            current = self._fetch_row(clean_task_id, clean_action_id)
            if (
                current is not None
                and current.target_canonical == target_canonical
                and current.content_sha256 == content_sha256
                and current.task_version == clean_task_version
            ):
                return _row_to_proposal(current)
            raise ValueError("Permission proposal already exists or is stale")

        return _row_to_proposal(row)

    def get(self, proposal_id=None, *, task_id=None, action_id=None):
        if proposal_id is not None:
            row = self._session.query(PermissionProposalRow).filter_by(proposal_id=proposal_id).populate_existing().first()
            return _row_to_proposal(row) if row is not None else None
        if task_id is not None and action_id is not None:
            return _row_to_proposal(self._fetch_row(task_id, action_id))
        return None

    def _fetch_row(self, task_id, action_id):
        return (
            self._session.query(PermissionProposalRow)
            .filter_by(task_id=task_id, action_id=action_id)
            .populate_existing()
            .first()
        )


def _canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest_value(value):
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _is_unsafe_key(key):
    if not isinstance(key, str):
        return True
    low = key.strip().lower()
    return low in {
        "params", "param", "parameters", "content", "raw_params",
        "raw_content", "arguments", "args", "payload", "params_raw",
        "params_normalized", "normalized_params", "normalized", "secret",
        "token", "password",
    } or low.endswith("_params") or low.endswith("_content") or low.endswith("_secret")


def _looks_like_digest_key(key):
    return isinstance(key, str) and "digest" in key.lower()


def _reserved_name(path):
    try:
        # A bare or full path may be passed.  Windows treats trailing
        # spaces/dots as ambiguous aliases (the OS strips them) and strips the
        # extension before applying reserved-device-name rules, so screen both.
        name = Path(path).name if str(path) not in ("", ".", "..") else str(path)
    except Exception:
        name = str(path)
    if not name:
        return False
    if name.endswith(" ") or name.endswith("."):
        return True
    stem = name.split(".", 1)[0].strip().upper()
    return stem in {
        "CON", "PRN", "AUX", "NUL",
        "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
        "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
    }


def _row_to_proposal(row):
    if row is None:
        return None
    return PermissionProposal(
        proposal_id=row.proposal_id,
        task_id=row.task_id,
        action_id=row.action_id,
        tool_name=row.tool_name,
        task_version=int(row.task_version),
        schema_version=int(row.schema_version or SCHEMA_VERSION),
        params_digest=row.params_digest,
        proposal_digest=row.proposal_digest,
        target_canonical=row.target_canonical,
        content_sha256=row.content_sha256,
        content_bytes=int(row.content_bytes or 0),
        target_must_not_exist=bool(row.target_must_not_exist),
        status=row.status,
        created_at=row.created_at,
    )
