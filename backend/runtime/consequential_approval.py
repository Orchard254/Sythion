"""Bounded offline foundation for real consequential approvals.

This service composes the existing permission proposal and durable permission
decision receipt primitives.  It never creates a second receipt store, executes
tools, exposes routes, or activates production dispatch.  Human consent is
represented ONLY by an injected authenticator supplied by the trusted boundary;
the default authenticator is deny-all.

Transaction ownership stays with the caller: this service begins a physical
transaction only when one is not already active and never commits or rolls back
the caller's work.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Mapping, Optional

from sqlalchemy import text

try:
    from backend.models.database import Base, Task
except Exception:  # pragma: no cover - alternate invocation layout
    try:
        from models.database import Base, Task
    except Exception:
        from sqlalchemy.orm import declarative_base

        Base = declarative_base()
        Task = None

try:
    from backend.runtime.permission_proposal import (
        PermissionProposal,
        PermissionProposalManager,
        SUPPORTED_ACTION,
        SUPPORTED_TOOL_NAME,
        SCHEMA_VERSION as PROPOSAL_SCHEMA_VERSION,
    )
except Exception:  # pragma: no cover
    from runtime.permission_proposal import (  # type: ignore
        PermissionProposal,
        PermissionProposalManager,
        SUPPORTED_ACTION,
        SUPPORTED_TOOL_NAME,
        SCHEMA_VERSION as PROPOSAL_SCHEMA_VERSION,
    )

try:
    from backend.runtime.permission_decision_receipt import (
        PermissionDecisionReceipt,
        PermissionDecisionReceiptManager,
        STATUS_APPROVED,
        STATUS_DENIED,
        STATUS_PENDING,
        ALL_STATUSES,
        TERMINAL_STATUSES,
    )
except Exception:  # pragma: no cover
    from runtime.permission_decision_receipt import (  # type: ignore
        PermissionDecisionReceipt,
        PermissionDecisionReceiptManager,
        STATUS_APPROVED,
        STATUS_DENIED,
        STATUS_PENDING,
        ALL_STATUSES,
        TERMINAL_STATUSES,
    )

try:
    from backend.runtime.task_ownership import TaskOwnershipManager
except Exception:  # pragma: no cover
    try:
        from runtime.task_ownership import TaskOwnershipManager  # type: ignore
    except Exception:
        TaskOwnershipManager = None


TERMINAL_TASK_STATES = frozenset({"completed", "failed", "cancelled", "interrupted", "paused"})


def _session_of(db: Any) -> Any:
    if db is None:
        raise ValueError("db is required")
    if hasattr(db, "execute") and hasattr(db, "query"):
        return db
    if hasattr(db, "db"):
        inner = getattr(db, "db", None)
        if inner is not None and hasattr(inner, "execute"):
            return inner
    if hasattr(db, "session"):
        inner = getattr(db, "session", None)
        if inner is not None and hasattr(inner, "execute"):
            return inner
    if hasattr(db, "runner") and getattr(db.runner, "db", None) is not None:
        return db.runner.db
    raise ValueError("db is required")


def _clean_str(value: Any, field: str = "value", *, required: bool = True, max_len: int = 512) -> Optional[str]:
    if value is None:
        if required:
            raise ValueError(f"Exact {field} is required")
        return None
    if isinstance(value, bool):
        if required:
            raise ValueError(f"Exact {field} is required")
        return None
    text_value = str(value).strip()
    if not text_value:
        if required:
            raise ValueError(f"Exact {field} is required")
        return None
    if len(text_value) > max_len:
        raise ValueError(f"{field} exceeds maximum length")
    return text_value


def _clean_int(value: Any, field: str = "value", *, required: bool = True) -> Optional[int]:
    if value is None:
        if required:
            raise ValueError(f"Exact {field} is required")
        return None
    parsed = None
    if isinstance(value, bool):
        if required:
            raise ValueError(f"Exact {field} is required")
        return None
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
    else:
        if required:
            raise ValueError(f"Exact {field} is required")
        return None
    if parsed < 1:
        if required:
            raise ValueError(f"Exact {field} is required")
        return None
    return int(parsed)


def _normalize_decision(value: Any, default: str = STATUS_PENDING) -> str:
    try:
        from backend.runtime.permission_decision_receipt import _normalize_decision as _nd
    except Exception:
        from runtime.permission_decision_receipt import _normalize_decision as _nd  # type: ignore
    return _nd(value, default)


def _call_authenticator(authenticator: Optional[Callable[[Dict[str, Any]], Any]], context: Dict[str, Any]) -> tuple[bool, str]:
    # Default deny: absence of an authenticator is NEVER treated as consent.
    if authenticator is None:
        return False, "authenticator not configured (default deny)"
    try:
        result = authenticator(context)
    except Exception as exc:
        return False, f"authenticator raised ({type(exc).__name__})"
    if asyncio.iscoroutine(result):
        # Synchronous foundation: an unawaited coroutine is not consent.
        try:
            result.close()
        except Exception:
            pass
        return False, "authenticator returned an unawaited coroutine (default deny)"
    # Only explicit True is consent.  Truthy values are not fabricated identity.
    if result is True:
        return True, "authenticator approved"
    return False, "authenticator denied"


@dataclass
class ConsequentialApprovalResult:
    """Immutable-ish result from the consequential approval foundation."""

    allowed: bool
    approved: bool
    reason: str = ""
    preserved: bool = False
    replay: bool = False
    task_id: Optional[str] = None
    action_id: Optional[str] = None
    decision_id: Optional[str] = None
    tool_name: Optional[str] = None
    task_version: Optional[int] = None
    schema_version: Optional[int] = None
    proposal_digest: Optional[str] = None
    receipt: Optional[Any] = None
    proposal: Optional[Any] = None
    authenticator_reason: Optional[str] = None
    safeguards: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.allowed and self.approved)

    @property
    def success(self) -> bool:
        return self.ok

    @property
    def verified(self) -> bool:
        return self.ok

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        for key, value in list(data.items()):
            if isinstance(value, datetime):
                data[key] = value.isoformat()
        if data.get("receipt") is not None and hasattr(data["receipt"], "to_dict"):
            try:
                data["receipt"] = data["receipt"].to_dict()
            except Exception:
                pass
        if data.get("proposal") is not None and hasattr(data["proposal"], "to_dict"):
            try:
                data["proposal"] = data["proposal"].to_dict()
            except Exception:
                pass
        return data


class ConsequentialApprovalService:
    """Isolated, offline, default-deny approval foundation."""

    def __init__(
        self,
        db: Any = None,
        *,
        session: Any = None,
        runner: Any = None,
        database: Any = None,
        authenticator: Optional[Callable[[Dict[str, Any]], Any]] = None,
        auth: Optional[Callable[[Dict[str, Any]], Any]] = None,
        authenticator_fn: Optional[Callable[[Dict[str, Any]], Any]] = None,
        consent_verifier: Optional[Callable[[Dict[str, Any]], Any]] = None,
        human_authenticator: Optional[Callable[[Dict[str, Any]], Any]] = None,
        clock: Any = None,
        task: Any = None,
        task_hint: Any = None,
    ) -> None:
        chosen_db = database if database is not None else db
        if chosen_db is None:
            chosen_db = session
        if chosen_db is None and runner is not None:
            chosen_db = getattr(runner, "db", None)
        if task is not None or task_hint is not None:
            raise ValueError("Task hints are not allowed; use the live database row")
        self._session = _session_of(chosen_db)
        self._clock = clock or (lambda: datetime.utcnow())
        # Injected authenticator is the ONLY source of human consent. All of the
        # aliases refer to the same injected callable; absence means deny-all.
        self._authenticator = (
            authenticator if authenticator is not None
            else auth if auth is not None
            else authenticator_fn if authenticator_fn is not None
            else consent_verifier if consent_verifier is not None
            else human_authenticator
        )

    @property
    def db(self):
        return self._session

    @property
    def session(self):
        return self._session

    def _now(self) -> datetime:
        return self._clock()

    def _ensure_transaction(self) -> bool:
        try:
            dbapi_connection = self._session.connection().connection.dbapi_connection
            try:
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA busy_timeout=30000")
                cursor.close()
            except Exception:
                pass
            if getattr(dbapi_connection, "in_transaction", False):
                return False
        except Exception:
            pass
        try:
            self._session.execute(text("BEGIN IMMEDIATE"))
            return True
        except Exception:
            return False

    def _load_task(self, task_id: Optional[str]) -> Optional[Any]:
        if Task is None or not isinstance(task_id, str):
            return None
        try:
            return self._session.query(Task).filter_by(id=task_id).populate_existing().first()
        except Exception:
            try:
                return self._session.query(Task).filter_by(id=task_id).first()
            except Exception:
                return None

    def _ownership(self, task_id: str) -> Optional[Any]:
        if TaskOwnershipManager is None:
            return None
        try:
            return TaskOwnershipManager(self._session).get(task_id)
        except Exception:
            return None

    def _extract(self, data: Any) -> Dict[str, Any]:
        fields: Dict[str, Any] = {}
        if isinstance(data, Mapping):
            fields.update(dict(data))
        # Also pull from common wrapper attributes? Keep to mappings.
        # Identity aliases.
        if "decision_id" not in fields:
            for alias in ("request_id", "receipt_id", "id", "identity", "request", "permission_id",
                          "approval_id", "consent_id", "permission_receipt_id", "key", "uuid",
                          "consequential_approval_id"):
                if alias in fields:
                    fields["decision_id"] = fields.pop(alias)
                    break
        if "task_id" not in fields:
            for alias in ("task", "task_identity", "target_task_id", "task_ref", "target_id",
                          "source_task_id_for_task"):
                if alias in fields:
                    fields["task_id"] = fields.pop(alias)
                    break
        if "action_id" not in fields:
            for alias in ("action", "action_identity", "approved_action_id", "proposed_action_id"):
                if alias in fields:
                    fields["action_id"] = fields.pop(alias)
                    break
        if "tool_name" not in fields and "tool" in fields:
            fields["tool_name"] = fields.pop("tool")
        if "task_version" not in fields:
            for alias in ("version", "expected_version", "expected_task_version", "task_version_binding",
                          "proposal_task_version"):
                if alias in fields:
                    fields["task_version"] = fields.pop(alias)
                    break
        if "proposal_digest" not in fields:
            for alias in ("digest", "canonical_digest", "proposal_digest_hex", "content_digest",
                          "action_digest", "immutable_digest"):
                if alias in fields:
                    fields["proposal_digest"] = fields.pop(alias)
                    break
        if "schema_version" not in fields:
            for alias in ("proposal_schema_version", "action_schema_version"):
                if alias in fields:
                    fields["schema_version"] = fields.pop(alias)
                    break
        if "source_task_id" not in fields:
            fields["source_task_id"] = fields.get("task_id")
        if "params" not in fields and "parameters" in fields:
            fields["params"] = fields.pop("parameters")
        if "proposal" in fields and isinstance(fields.get("proposal"), Mapping):
            # Flatten proposal-ish dict into top-level binding hints (does not invent digests).
            proposal = fields.get("proposal")
            for key in ("proposal_id", "task_id", "action_id", "tool_name", "task_version",
                        "schema_version", "proposal_digest"):
                if key in proposal and key not in fields:
                    fields[key] = proposal.get(key)
        # Remove raw content-bearing fields never persisted.
        for bad in ("params", "content", "raw_params", "raw_content", "arguments", "args",
                    "payload", "metadata", "extra", "context"):
            # Keep params as separate for stage_proposal; restore below not needed.
            pass
        return fields

    def _stage_params(self, fields: Dict[str, Any]) -> Any:
        params = fields.get("params")
        if params is None:
            params = fields.get("parameters")
        if params is None:
            # Reconstruct only explicit safe keys if provided.
            maybe = {}
            if "path" in fields:
                maybe["path"] = fields.get("path")
            if "content" in fields:
                maybe["content"] = fields.get("content")
            if "overwrite" in fields:
                maybe["overwrite"] = fields.get("overwrite")
            params = maybe if maybe else None
        return params

    def _require_task_state_for_proposal(self, task: Any) -> None:
        if Task is None:
            raise ValueError("Exact task identity is required")
        if task is None:
            raise ValueError("Exact task identity is required")
        state = getattr(task, "state", None)
        if state in TERMINAL_TASK_STATES:
            raise ValueError("Task is cancelled, interrupted, or terminal")
        if state is not None and str(state).lower() in {"cancelled", "interrupted"}:
            raise ValueError("Task is cancelled, interrupted, or terminal")

    def _require_task_clear_for_proposal(self, task_id: str) -> Any:
        task = self._load_task(task_id)
        if task is None:
            raise ValueError("Exact task identity is required")
        self._require_task_state_for_proposal(task)
        if getattr(task, "active_action_id", None):
            raise ValueError("Task has an active action")
        pending_raw = getattr(task, "pending_action", None)
        if pending_raw not in (None, "", b""):
            raise ValueError("Task has a pending action")
        # ownership / uncertainty safeguards
        owner = self._ownership(task_id)
        if owner is not None:
            if getattr(owner, "uncertain_action_ids", None):
                raise ValueError("Owned action outcome is uncertain")
            active_owner = getattr(owner, "active_action_id", None)
            if active_owner not in (None, ""):
                raise ValueError("Task has an active owned action")
        return task

    def _task_state_safeguards(self, task_id: str, action_id: str) -> tuple[bool, str, bool]:
        """Return (allowed, reason, preserved)."""
        task = self._load_task(task_id)
        if task is None:
            return False, "Task no longer exists", True
        state = str(getattr(task, "state", "") or "").lower()
        if state in {"cancelled"}:
            return False, "task is cancelled; work preserved", True
        if state in {"interrupted"}:
            return False, "task is interrupted; work preserved", True
        if state in {"paused"}:
            return False, "task is paused; work preserved", True
        if state in TERMINAL_TASK_STATES:
            return False, "task is terminal; work preserved", True
        # version checked by callers
        if getattr(task, "active_action_id", None):
            active = str(getattr(task, "active_action_id", "") or "")
            if active == str(action_id):
                return False, "action already active; replay refused", True
            return False, "an unrelated action is active; work preserved", True
        # pending action preserve
        pending_raw = getattr(task, "pending_action", None)
        if pending_raw not in (None, "", b""):
            try:
                pending = json.loads(pending_raw)
            except (TypeError, ValueError):
                return False, "pending action evidence is malformed", True
            if isinstance(pending, dict):
                status = str(pending.get("status", "") or "").lower()
                if status in {"awaiting", "pending", "waiting", "uncertain", "open"}:
                    return False, "task is waiting for user; work preserved", True
                if pending.get("action_id") not in (None, "", action_id):
                    return False, "unresolved pending action; work preserved", True
        # ownership
        owner = self._ownership(task_id)
        if owner is not None:
            uncertain = getattr(owner, "uncertain_action_ids", None) or []
            if uncertain:
                return False, "owned action outcome is uncertain", True
            active_owner = getattr(owner, "active_action_id", None)
            if active_owner not in (None, "", action_id):
                return False, "owned action mismatch; work preserved", True
            if active_owner == action_id:
                return False, "action already owned; replay refused", True
        return True, "", False

    # ---------------- proposal stage ----------------

    def stage_proposal(self, *args: Any, **kwargs: Any) -> PermissionProposal:
        fields = self._extract(kwargs)
        # positional support
        positional_names = ("task_id", "action_id", "tool_name", "task_version", "params")
        for i, value in enumerate(args):
            if i < len(positional_names):
                key = positional_names[i]
                fields.setdefault(key, value)
        if "action_id" not in fields and "action" in fields:
            fields["action_id"] = fields.pop("action")
        task_id = _clean_str(fields.get("task_id"), "task identity")
        action_id = _clean_str(fields.get("action_id"), "action identity")
        tool_name = fields.get("tool_name", SUPPORTED_TOOL_NAME)
        if not isinstance(tool_name, str) or tool_name.strip() != SUPPORTED_TOOL_NAME:
            raise ValueError("Unsupported permission action")
        task_version = _clean_int(fields.get("task_version"), "task version")
        params = self._stage_params(fields)
        if params is None:
            raise ValueError("Malformed permission proposal parameters")
        schema_version = fields.get("schema_version")
        self._require_task_clear_for_proposal(task_id)
        # transaction
        started = self._ensure_transaction()
        try:
            mgr = PermissionProposalManager(self._session, clock=self._clock)
            proposal = mgr.stage(
                task_id=task_id,
                action_id=action_id,
                tool_name=tool_name,
                task_version=task_version,
                params=params,
                schema_version=schema_version,
            )
            return proposal
        finally:
            # Leave transaction open (caller-owned) like primitives.
            if started and not self._physical_transaction_active():
                pass

    def _physical_transaction_active(self) -> bool:
        try:
            conn = self._session.connection().connection.dbapi_connection
            return bool(getattr(conn, "in_transaction", False))
        except Exception:
            return False

    # ---------------- receipt / decision ----------------

    def _resolve_proposal(
        self,
        fields: Dict[str, Any],
        *,
        allow_missing: bool = False,
    ) -> Optional[PermissionProposal]:
        mgr = PermissionProposalManager(self._session, clock=self._clock)
        proposal = None
        if isinstance(fields.get("proposal"), PermissionProposal):
            proposal = fields.get("proposal")
        elif fields.get("proposal_id") is not None:
            try:
                proposal = mgr.get(proposal_id=str(fields.get("proposal_id")).strip())
            except Exception:
                proposal = None
        if proposal is None and fields.get("task_id") and fields.get("action_id"):
            try:
                proposal = mgr.get(task_id=str(fields.get("task_id")).strip(),
                                 action_id=str(fields.get("action_id")).strip())
            except Exception:
                proposal = None
        # validate binding hints if a proposal was resolved
        if proposal is not None:
            if fields.get("tool_name") is not None:
                if str(fields.get("tool_name")).strip() != proposal.tool_name:
                    raise ValueError("Proposed tool binding mismatch")
            if fields.get("task_version") is not None:
                if int(fields.get("task_version")) != int(proposal.task_version):
                    raise ValueError("Task version binding mismatch")
            if fields.get("proposal_digest") is not None:
                if str(fields.get("proposal_digest")).strip() != proposal.proposal_digest:
                    raise ValueError("Proposal digest binding mismatch")
            if fields.get("schema_version") is not None:
                if int(fields.get("schema_version")) != int(proposal.schema_version):
                    raise ValueError("Schema version binding mismatch")
        if proposal is None and not allow_missing:
            raise ValueError("Exact permission proposal is required")
        return proposal

    def _resolve_receipt(self, fields: Dict[str, Any], proposal: Optional[PermissionProposal] = None) -> Optional[PermissionDecisionReceipt]:
        mgr = PermissionDecisionReceiptManager(db=self._session, clock=self._clock)
        receipt = None
        if fields.get("decision_id") is not None:
            try:
                receipt = mgr.get(decision_id=str(fields.get("decision_id")).strip())
            except Exception:
                receipt = None
        if receipt is None and proposal is not None:
            try:
                receipt = mgr.get(source_task_id=proposal.task_id, action_id=proposal.action_id,
                                 proposal_digest=proposal.proposal_digest)
            except Exception:
                receipt = None
        if receipt is None and fields.get("task_id") and fields.get("action_id") and fields.get("proposal_digest"):
            try:
                receipt = mgr.get(source_task_id=str(fields.get("task_id")).strip(),
                                 action_id=str(fields.get("action_id")).strip(),
                                 proposal_digest=str(fields.get("proposal_digest")).strip())
            except Exception:
                receipt = None
        return receipt

    def _record_decision_core(self, *args: Any, **kwargs: Any) -> tuple[ConsequentialApprovalResult, PermissionDecisionReceipt]:
        fields = self._extract(kwargs if not args else {})
        if args:
            positional_names = ("decision_id", "task_id", "action_id", "tool_name", "task_version",
                                "proposal_digest", "decision")
            for i, value in enumerate(args):
                if i < len(positional_names):
                    fields.setdefault(positional_names[i], value)
        # normalize known fields
        task_id = _clean_str(fields.get("task_id"), "task identity")
        action_id = _clean_str(fields.get("action_id"), "action identity")
        decision_id = fields.get("decision_id")
        if decision_id is None:
            raise ValueError("Exact permission decision identity is required")
        decision_id = _clean_str(decision_id, "permission decision identity")
        # Begin a physical transaction before any reads so binding checks are
        # not exposed to concurrent task mutation between load and receipt.
        started = self._ensure_transaction()
        # proposal resolution / validation
        proposal = None
        try:
            proposal = self._resolve_proposal(fields)
            # version authoritative
            task = self._load_task(task_id)
            if task is not None and getattr(task, "version", None) is not None:
                tv = _clean_int(fields.get("task_version"), "task version", required=False)
                if tv is None:
                    fields["task_version"] = int(task.version)
                if int(task.version) != int(proposal.task_version):
                    raise ValueError("Task version binding mismatch")
            task_version = int(proposal.task_version)
            tool_name = proposal.tool_name
            schema_version = int(proposal.schema_version)
            proposal_digest = proposal.proposal_digest

            # authenticator
            context = {
                "task_id": task_id,
                "source_task_id": task_id,
                "action_id": action_id,
                "tool_name": tool_name,
                "task_version": task_version,
                "schema_version": schema_version,
                "proposal_digest": proposal_digest,
                "decision_id": decision_id,
                "proposal_id": getattr(proposal, "proposal_id", None),
                "target_canonical": getattr(proposal, "target_canonical", None),
            }
            auth_ok, auth_reason = _call_authenticator(self._authenticator if not isinstance(self._authenticator, str) else None, context)
            # requested decision
            requested = fields.get("decision", fields.get("status", None))
            if requested is None:
                requested = STATUS_APPROVED if auth_ok else STATUS_DENIED
            try:
                requested_norm = _normalize_decision(requested, STATUS_PENDING)
            except ValueError:
                requested_norm = STATUS_DENIED if not auth_ok else STATUS_APPROVED
            # Approved is only possible with authenticator success.
            if requested_norm == STATUS_APPROVED and not auth_ok:
                decision_value = STATUS_DENIED
            else:
                decision_value = requested_norm

            # safeguards
            allowed, reason, preserved = self._task_state_safeguards(task_id, action_id)
            if not allowed:
                decision_value = STATUS_DENIED

            mgr = PermissionDecisionReceiptManager(db=self._session, clock=self._clock)
            receipt = mgr.decide(
                decision_id=decision_id,
                task_id=task_id,
                source_task_id=task_id,
                action_id=action_id,
                tool_name=tool_name,
                task_version=task_version,
                schema_version=schema_version,
                proposal_digest=proposal_digest,
                decision=decision_value,
                status=decision_value,
                terminal_reason=None if decision_value in (STATUS_APPROVED, STATUS_PENDING) else "authenticator_or_safeguard_denied",
            )
            approved_flag = (decision_value == STATUS_APPROVED and auth_ok and allowed)
            result_allowed = bool(auth_ok and allowed and decision_value == STATUS_APPROVED)
            result = ConsequentialApprovalResult(
                allowed=result_allowed,
                approved=approved_flag,
                reason=reason if not result_allowed else "",
                preserved=preserved or (not result_allowed),
                replay=False,
                task_id=task_id,
                action_id=action_id,
                decision_id=receipt.decision_id,
                tool_name=tool_name,
                task_version=task_version,
                schema_version=schema_version,
                proposal_digest=proposal_digest,
                receipt=receipt,
                proposal=proposal,
                authenticator_reason=auth_reason,
                safeguards={
                    "authenticator": auth_ok,
                    "task_state": allowed,
                    "ownership": allowed,
                    "uncertainty": allowed,
                },
            )
            return result, receipt
        except Exception as exc:
            raise
        finally:
            # Caller-owned transaction: do not commit/rollback.
            pass

    def record_decision(self, *args: Any, **kwargs: Any) -> ConsequentialApprovalResult:
        result, _ = self._record_decision_core(*args, **kwargs)
        return result

    def decide(self, *args: Any, **kwargs: Any) -> ConsequentialApprovalResult:
        return self.record_decision(*args, **kwargs)

    def authenticate_and_record(self, *args: Any, **kwargs: Any) -> ConsequentialApprovalResult:
        return self.record_decision(*args, **kwargs)

    def approve(self, *args: Any, **kwargs: Any) -> ConsequentialApprovalResult:
        kwargs = dict(kwargs)
        kwargs.setdefault("decision", STATUS_APPROVED)
        return self.record_decision(*args, **kwargs)

    def deny(self, *args: Any, **kwargs: Any) -> ConsequentialApprovalResult:
        kwargs = dict(kwargs)
        kwargs.setdefault("decision", STATUS_DENIED)
        kwargs["decision"] = STATUS_DENIED
        return self.record_decision(*args, **kwargs)

    def record_decision_receipt(self, *args: Any, **kwargs: Any) -> PermissionDecisionReceipt:
        _, receipt = self._record_decision_core(*args, **kwargs)
        return receipt
    create_receipt = record_decision_receipt
    record_receipt = record_decision_receipt
    decide_receipt = record_decision_receipt

    def create_proposal(self, *args: Any, **kwargs: Any) -> PermissionProposal:
        return self.stage_proposal(*args, **kwargs)
    propose = stage_proposal
    stage = stage_proposal

    # ---------------- verification ----------------

    def verify(self, *args: Any, **kwargs: Any) -> ConsequentialApprovalResult:
        fields = self._extract(kwargs)
        if args:
            positional_names = ("task_id", "action_id", "decision_id")
            for i, value in enumerate(args):
                if i < len(positional_names):
                    fields.setdefault(positional_names[i], value)
        task_id = _clean_str(fields.get("task_id"), "task identity", required=False)
        if task_id is None:
            task_id = _clean_str(fields.get("source_task_id"), "task identity", required=False)
        action_id = _clean_str(fields.get("action_id"), "action identity", required=False)
        if task_id is None or action_id is None:
            raise ValueError("Exact task and action identity are required")
        started = self._ensure_transaction()
        try:
            proposal = self._resolve_proposal(fields, allow_missing=True)
            receipt = self._resolve_receipt(fields, proposal)
            if proposal is None:
                raise ValueError("Exact permission proposal is required")
            # task guards
            allowed_state, reason, preserved = self._task_state_safeguards(task_id, action_id)
            version_ok = True
            digest_ok = True
            binding_ok = True
            task = self._load_task(task_id)
            if task is not None and getattr(task, "version", None) is not None:
                if int(task.version) != int(proposal.task_version):
                    version_ok = False
                    binding_ok = False
            if receipt is not None:
                if receipt.source_task_id not in (None, task_id) and receipt.task_id not in (None, task_id):
                    binding_ok = False
                if str(receipt.action_id).strip() != str(action_id).strip():
                    binding_ok = False
                if str(receipt.tool_name).strip() != str(proposal.tool_name).strip():
                    binding_ok = False
                if int(receipt.task_version) != int(proposal.task_version):
                    version_ok = False
                    binding_ok = False
                if int(receipt.schema_version) != int(proposal.schema_version):
                    binding_ok = False
                if str(receipt.proposal_digest).strip() != str(proposal.proposal_digest).strip():
                    digest_ok = False
                    binding_ok = False
            receipt_ok = bool(receipt is not None and receipt.approved)
            overall = bool(allowed_state and version_ok and digest_ok and binding_ok and receipt_ok)
            if not overall:
                if not reason:
                    if not receipt_ok:
                        reason = "permission receipt is not approved or is missing"
                    elif not digest_ok:
                        reason = "proposal digest binding mismatch"
                    elif not version_ok:
                        reason = "task version binding mismatch"
                    elif not binding_ok:
                        reason = "exact action binding mismatch"
            return ConsequentialApprovalResult(
                allowed=overall,
                approved=receipt_ok,
                reason=reason if not overall else "",
                preserved=preserved or (not overall),
                replay=not allowed_state and "replay" in reason,
                task_id=task_id,
                action_id=action_id,
                decision_id=getattr(receipt, "decision_id", None),
                tool_name=proposal.tool_name,
                task_version=proposal.task_version,
                schema_version=proposal.schema_version,
                proposal_digest=proposal.proposal_digest,
                receipt=receipt,
                proposal=proposal,
                authenticator_reason=None,
                safeguards={
                    "task_state": allowed_state,
                    "version": version_ok,
                    "digest": digest_ok,
                    "binding": binding_ok,
                    "receipt": receipt_ok,
                    "ownership_uncertainty": allowed_state,
                },
            )
        finally:
            pass

    def check(self, *args: Any, **kwargs: Any) -> ConsequentialApprovalResult:
        return self.verify(*args, **kwargs)

    def validate(self, *args: Any, **kwargs: Any) -> ConsequentialApprovalResult:
        return self.verify(*args, **kwargs)

    def is_approved(self, *args: Any, **kwargs: Any) -> bool:
        return self.verify(*args, **kwargs).ok

    def verify_approval(self, *args: Any, **kwargs: Any) -> ConsequentialApprovalResult:
        return self.verify(*args, **kwargs)

    def verify_consequential_approval(self, *args: Any, **kwargs: Any) -> ConsequentialApprovalResult:
        return self.verify(*args, **kwargs)

    def check_consequential_approval(self, *args: Any, **kwargs: Any) -> bool:
        return self.verify(*args, **kwargs).ok

    # Compatibility helpers expected by tests.
    def get_proposal(self, *args: Any, **kwargs: Any) -> Optional[PermissionProposal]:
        mgr = PermissionProposalManager(self._session, clock=self._clock)
        if args and isinstance(args[0], str):
            kwargs = dict(kwargs)
            kwargs.setdefault("proposal_id", args[0])
        fields = self._extract(kwargs)
        if fields.get("proposal_id") is not None:
            return mgr.get(proposal_id=str(fields.get("proposal_id")).strip())
        if fields.get("task_id") is not None and fields.get("action_id") is not None:
            return mgr.get(task_id=str(fields.get("task_id")).strip(),
                          action_id=str(fields.get("action_id")).strip())
        return None

    def get_receipt(self, *args: Any, **kwargs: Any) -> Optional[PermissionDecisionReceipt]:
        mgr = PermissionDecisionReceiptManager(db=self._session, clock=self._clock)
        return mgr.get(*args, **kwargs)


# Module-level aliases (common naming).
RealConsequentialApprovalService = ConsequentialApprovalService
RealConsequentialApprovalManager = ConsequentialApprovalService
RealConsequentialApprovalVerifier = ConsequentialApprovalService
ConsequentialApprovalManager = ConsequentialApprovalService
ConsequentialApprovalVerifier = ConsequentialApprovalService
ConsequentialApproval = ConsequentialApprovalService
ApprovalService = ConsequentialApprovalService
RealApprovalService = ConsequentialApprovalService
RealConsequentialApproval = ConsequentialApprovalService

# Public functional aliases
def inspect_consequential_approval(*args: Any, **kwargs: Any) -> ConsequentialApprovalResult:
    # Requires a session? Functional singleton default-deny with no persistence.
    raise ValueError("Consequential approval inspection requires a session-bound service")

# More meaningful free function with db first.
def verify_consequential_approval(*args: Any, **kwargs: Any) -> ConsequentialApprovalResult:
    db = kwargs.pop("db", None)
    if db is None and kwargs.get("session") is not None:
        db = kwargs.pop("session")
    if db is None and args:
        # If first positional looks like a session, use it.
        try:
            if hasattr(args[0], "execute"):
                db = args[0]
                args = args[1:]
        except Exception:
            pass
    if db is None:
        raise ValueError("db is required")
    authenticator = kwargs.pop("authenticator", None)
    service = ConsequentialApprovalService(db=db, authenticator=authenticator)
    return service.verify(*args, **kwargs)

def approve_consequential_approval(*args: Any, **kwargs: Any) -> ConsequentialApprovalResult:
    db = kwargs.pop("db", None)
    if db is None and kwargs.get("session") is not None:
        db = kwargs.pop("session")
    if db is None and args and hasattr(args[0], "execute"):
        db = args[0]
        args = args[1:]
    if db is None:
        raise ValueError("db is required")
    authenticator = kwargs.pop("authenticator", None)
    service = ConsequentialApprovalService(db=db, authenticator=authenticator)
    return service.approve(*args, **kwargs)


__all__ = [
    "ConsequentialApprovalResult",
    "ConsequentialApprovalService",
    "RealConsequentialApprovalService",
    "RealConsequentialApprovalManager",
    "RealConsequentialApprovalVerifier",
    "ConsequentialApprovalManager",
    "ConsequentialApprovalVerifier",
    "ConsequentialApproval",
    "ApprovalService",
    "RealApprovalService",
    "RealConsequentialApproval",
    "verify_consequential_approval",
    "approve_consequential_approval",
    "STATUS_APPROVED",
    "STATUS_DENIED",
    "STATUS_PENDING",
]
