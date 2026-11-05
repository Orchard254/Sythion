"""Default-inert server-owned orchestration for semantic discussion indexing.

No factory is treated as exclusively owned merely because it was passed.  An
explicit ownership assertion is required before a supplied factory is used; a
private session created from caller context is owned by this module.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Optional, Tuple

from sqlalchemy.orm import sessionmaker

from backend.services.grounded_discussion_semantic_index import (
    SemanticIndexSnapshot,
    SemanticDiscussionIndexer,
)


def _first_not_none(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "owned", "exclusive", "isolated", "provided", "clean-owned"}
    return bool(value)


def _callers_busy(session: Any) -> bool:
    try:
        if session is None:
            return True
        if session.new or session.dirty or session.deleted:
            return True
        return bool(session.in_transaction())
    except Exception:
        return True


class _OwnershipNotVerified(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ServerOwnedSemanticInput:
    session_id: str
    exchange_id: str
    user_turn_id: int
    assistant_turn_id: int
    task_id: Optional[str]
    source_user_text: Optional[str]
    source_assistant_text: Optional[str]
    candidate_discussion_ids: Tuple[str, ...]
    candidate_labels: Mapping[str, str]
    eligible_correction_target_ids: Tuple[str, ...]
    max_candidate_count: int
    max_source_text: int
    max_source_bytes: int
    max_payload_bytes: int
    max_proposals: int
    canonical_source_digest: str
    model_input_digest: str
    semantic_snapshot: SemanticIndexSnapshot
    candidate_memberships: Optional[Mapping[str, Any]] = None
    untrusted: bool = True


@dataclass(frozen=True, slots=True)
class ServerOwnedSemanticIndexOutcome:
    session_id: str
    exchange_id: str
    user_turn_id: int
    assistant_turn_id: int
    semantic_result: Any = None
    state_idempotency_key: Optional[str] = None
    state_status: Optional[str] = None
    applied: bool = False
    applied_count: int = 0
    rejected_reason: Optional[str] = None
    model_input_binding_verified: bool = False  # server-side trusted snapshot/canonical digest binding, not model receipt or semantic freshness
    canonical_source_mutation_detected: bool = False
    provider_call_count: int = 0
    model_input_digest: Optional[str] = None
    session_ownership_explicitly_verified: bool = False  # private-session derivation only; caller/session ownership is not proven
    untrusted: bool = True
    overall_end_to_end_complete_possible: bool = False


class CallerSessionBusyError(ValueError):
    """Caller session already has pending/uncommitted rows or an active transaction."""


class SessionOwnershipNotVerifiedError(ValueError):
    """Factory/session ownership was not explicitly asserted."""


class ServerOwnedSemanticIndexOrchestrator:
    def __init__(self, session_or_context: Any = None, *, model_client: Any, budget: Any = None,
                 max_candidate_count: int = 100, max_source_text: int = 1000,
                 max_source_bytes: int = 4096, max_payload_bytes: int = 16384,
                 max_proposals: int = 10, **ownership_options) -> None:
        self._session_or_context = session_or_context
        self._model_client = model_client
        self._protected_sessions = []
        self._supplied_sessions = []
        self._budget = budget
        self._max_candidate_count = max_candidate_count
        self._max_source_text = max_source_text
        self._max_source_bytes = max_source_bytes
        self._max_payload_bytes = max_payload_bytes
        self._max_proposals = max_proposals
        self._ownership_options = dict(ownership_options)
        raw_context = session_or_context
        caller_candidate = getattr(raw_context, "_caller_session", None)
        if caller_candidate is None and self._looks_like_session(raw_context):
            caller_candidate = raw_context
        if caller_candidate is not None and self._looks_like_session(caller_candidate):
            self._register_protected(caller_candidate)
        # Treat direct (or non-callable wrapper) session binds as caller context only.
        # Callable factories are never auto-trusted for private session derivation.
        unwrap = self._unwrap_session(raw_context)
        if unwrap is not None and not callable(raw_context):
            bind = getattr(unwrap, "bind", None)
        else:
            bind = None
        self._caller_binding = bind
        self._supplied_factory = ownership_options.get("supplied_factory") or _first_not_none(
            ownership_options.get("session_factory"),
            ownership_options.get("db_session_factory"),
            ownership_options.get("session_maker"),
            ownership_options.get("factory"),
        )
        flags = [
            ownership_options.get("owns_session"),
            ownership_options.get("owned_session"),
            ownership_options.get("owns_sessions"),
            ownership_options.get("exclusive_session"),
            ownership_options.get("exclusive"),
            ownership_options.get("owned"),
        ]
        owner_text = ownership_options.get("session_owner")
        owner_text = None if owner_text is None else str(owner_text).strip().lower()
        self._explicitly_owned = (
            any(_truthy(x) for x in flags)
            or owner_text in {"owned", "exclusive", "isolated", "provided", "clean-owned"}
        )

    async def run(self, session_id: str, exchange_id: str, user_turn_id: int,
                  assistant_turn_id: int, *, task_id: Any = None, model_client: Any = None,
                  **options) -> ServerOwnedSemanticIndexOutcome:
        client = model_client if model_client is not None else self._model_client
        if client is None:
            raise ValueError("A model client is required")

        from backend.services.grounded_discussion_index import (
            canonical_exchange_input_digest,
            _validate_exact_member,
            apply_semantic_index_proposals,
        )
        from backend.services.grounded_discussions import GroundedDiscussionManager

        sid = session_id
        eid = exchange_id
        ut = user_turn_id
        at = assistant_turn_id

        # Caller state is inspected without alteration.  Pending/uncommitted
        # work never follows past this fail-closed rejection.
        try:
            raw_context = self._session_or_context
            caller_session = self._unwrap_session(raw_context)
            if caller_session is not None:
                self._register_protected(caller_session)
            caller_busy = _callers_busy(caller_session) if caller_session is not None else False
        except Exception:
            caller_session = None
            caller_busy = False

        supplied_factory = _first_not_none(
            options.pop("session_factory", None),
            options.pop("db_session_factory", None),
            options.pop("session_maker", None),
            options.pop("factory", None),
            self._supplied_factory,
        )
        owns_explicit = self._explicitly_owned or _truthy(options.get("owns_session")) or \
            _truthy(options.get("owned_session")) or _truthy(options.get("owns_sessions")) or \
            _truthy(options.get("exclusive_session")) or _truthy(options.get("exclusive")) or \
            _truthy(options.get("owned")) or str(options.get("session_owner") or "").strip().lower() in {
                "owned", "exclusive", "isolated", "provided", "clean-owned"}

        bind = getattr(caller_session, "bind", None) if caller_session is not None else self._caller_binding
        if caller_session is None:
            bind = self._caller_binding

        # Reject busy callers before any read or provider invocation.
        if caller_busy:
            return ServerOwnedSemanticIndexOutcome(
                session_id=sid, exchange_id=eid, user_turn_id=ut, assistant_turn_id=at,
                applied=False, rejected_reason="caller_session_busy",
                provider_call_count=0,
                session_ownership_explicitly_verified=False,
            )

        # Resolve a real read/write session.  If a factory was supplied without a
        # plain ownership assertion, use a transient private connection from caller
        # context where available; otherwise fail closed.
        supplied_factory_untrusted = supplied_factory is not None and not owns_explicit
        # Never invoke or trust a supplied factory, even when ownership is
        # asserted.  Use only a privately derived session from a known, safe bind.
        using_supplied_factory = False
        supplied_factory = None
        if (
            bind is not None
            and caller_session is not None
            and self._safe_private_bind(bind)
        ):
            factory = sessionmaker(bind=bind, autoflush=False, future=True)
            factory_owned = True
        else:
            return ServerOwnedSemanticIndexOutcome(
                session_id=sid, exchange_id=eid, user_turn_id=ut, assistant_turn_id=at,
                applied=False, rejected_reason="session_ownership_not_verified",
                provider_call_count=0,
                session_ownership_explicitly_verified=False,
            )

        # Capture is always isolated in an operation-owned session.
        capture_session = None
        trusted = None
        capture_failed = False
        try:
            # Only privately derived capture sessions are used.
            capture_session = factory()
            if capture_session is caller_session or capture_session is self._session_or_context:
                self._register_protected(capture_session)
            if _callers_busy(capture_session):
                raise CallerSessionBusyError("clean session required")
            trusted = self._capture(
                capture_session, sid, eid, ut, at, task_id,
                GroundedDiscussionManager, canonical_exchange_input_digest,
                _validate_exact_member,
            )
            if trusted is None:
                capture_failed = True
        except Exception as capture_error:
            self._rollback_and_close(capture_session, factory_owned)
            # Specifically supplied, explicitly owned factories *may* be closed;
            # supplied/unverified factories never have their sessions touched.
            return ServerOwnedSemanticIndexOutcome(
                session_id=sid, exchange_id=eid, user_turn_id=ut, assistant_turn_id=at,
                applied=False,
                rejected_reason=capture_error.__class__.__name__ if capture_failed is False else "capture_failed",
                provider_call_count=0,
                session_ownership_explicitly_verified=False,
            )
        finally:
            if capture_session is not None and not capture_failed and trusted is not None:
                # Safely release only a clean capture transaction.  A non-clean
                # session cannot be released without potentially destroying caller
                # or shared state, so fail closed instead of holding it.
                try:
                    if getattr(capture_session, "new", False) or getattr(capture_session, "dirty", False) or getattr(capture_session, "deleted", False):
                        capture_failed = True
                        trusted = None
                    else:
                        if callable(getattr(capture_session, "in_transaction", None)) and capture_session.in_transaction():
                            capture_session.rollback()
                        if callable(getattr(capture_session, "in_transaction", None)) and capture_session.in_transaction():
                            capture_failed = True
                            trusted = None
                except Exception:
                    capture_failed = True
                    trusted = None
                if trusted is not None and (factory_owned or self._may_close(capture_session, caller_session)):
                    # Private sessions, including those created from caller context when
                    # an unused supplied factory was passed, must close before provider.
                    if factory_owned:
                        try:
                            capture_session.close()
                        except Exception:
                            pass
                # Explicitly owned supplied sessions are kept open only after their
                # capture transaction has been safely released.
        # For un-owned or reused supplies, ensure pending remains if no trusted digest.
        if trusted is None:
            if self._may_close(capture_session, caller_session):
                try:
                    capture_session.close()
                except Exception:
                    pass
            return ServerOwnedSemanticIndexOutcome(
                session_id=sid, exchange_id=eid, user_turn_id=ut, assistant_turn_id=at,
                applied=False, rejected_reason="capture_failed", provider_call_count=0,
                session_ownership_explicitly_verified=False,
            )

        trusted_digest = trusted.canonical_source_digest

        # No operation-owned DB transaction is active while awaiting.
        semantic_result = None
        provider_count = 0
        indexer = SemanticDiscussionIndexer(client, budget=self._budget)
        try:
            semantic_result = await indexer.index(trusted.semantic_snapshot)
            provider_count = indexer.call_count
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            provider_count = indexer.call_count
            return ServerOwnedSemanticIndexOutcome(
                session_id=sid, exchange_id=eid, user_turn_id=ut, assistant_turn_id=at,
                applied=False, rejected_reason=exc.__class__.__name__,
                provider_call_count=provider_count,
                session_ownership_explicitly_verified=False,
            )

        if semantic_result is None:
            return ServerOwnedSemanticIndexOutcome(
                session_id=sid, exchange_id=eid, user_turn_id=ut, assistant_turn_id=at,
                applied=False, rejected_reason="no_semantic_result",
                provider_call_count=provider_count,
                session_ownership_explicitly_verified=False,
            )

        apply_failed = False
        apply_mutation = False
        apply_session = None
        try:
            # Apply state with a privately owned session; never invoke supplied factories.
            bind = getattr(caller_session, "bind", None) if caller_session is not None else self._caller_binding
            if bind is None:
                bind = self._caller_binding
            # Re-validate the caller and its bind; an unsafe pool/connection or a now-busy
            # caller cannot prove isolation for an apply session.
            if caller_session is not None and _callers_busy(caller_session):
                return ServerOwnedSemanticIndexOutcome(
                    session_id=sid, exchange_id=eid, user_turn_id=ut, assistant_turn_id=at,
                    semantic_result=semantic_result,
                    applied=False, rejected_reason="caller_session_busy",
                    model_input_digest=trusted.model_input_digest,
                    provider_call_count=provider_count,
                    session_ownership_explicitly_verified=False,
                )
            if not self._safe_private_bind(bind):
                return ServerOwnedSemanticIndexOutcome(
                    session_id=sid, exchange_id=eid, user_turn_id=ut, assistant_turn_id=at,
                    semantic_result=semantic_result,
                    applied=False, rejected_reason="session_ownership_not_verified",
                    model_input_digest=trusted.model_input_digest,
                    provider_call_count=provider_count,
                    session_ownership_explicitly_verified=False,
                )
            apply_session = sessionmaker(bind=bind, autoflush=False, future=True)()
            if apply_session is caller_session or apply_session is self._session_or_context:
                self._register_protected(apply_session)
            if _callers_busy(apply_session):
                raise CallerSessionBusyError("apply session is not clean")
            # Acquire a writer lock *before* any candidate/model-input comparison.
            # A private apply session must be clean; reusing an ambiguous in-flight
            # transaction would silently skip locking, so fail closed instead.
            # NOTE: never roll back caller/protected state here.
            if not hasattr(apply_session, "in_transaction") or apply_session.in_transaction():
                raise ValueError("SQLite writer lock not acquired: apply session not clean")
            try:
                apply_session.execute(
                    __import__("sqlalchemy", fromlist=["text"]).text("BEGIN IMMEDIATE")
                )
            except Exception as lock_error:
                raise ValueError("SQLite writer lock not acquired") from lock_error
            try:
                dbapi = apply_session.connection().connection.dbapi_connection
            except Exception:
                dbapi = None
            if dbapi is None or not getattr(dbapi, "in_transaction", False):
                raise ValueError("SQLite writer lock not acquired: DBAPI not in transaction")
            terminal_existing = False
            try:
                from backend.services.grounded_discussion_index import (
                    DiscussionIndexStateRow, INDEX_STATUS_COMPLETE, INDEX_STATUS_INSUFFICIENT,
                )
                rows = apply_session.query(DiscussionIndexStateRow).filter_by(
                    session_id=sid, exchange_id=eid
                ).all()
                if len(rows) > 1:
                    # Ambiguous historical rows are fail-closed before any terminal
                    # shortcut or proposal write.
                    raise ValueError("Conflicting indexing request binding")
                if rows:
                    row = rows[0]
                    if (
                        int(row.user_turn_id) != ut
                        or int(row.assistant_turn_id) != at
                        or row.input_digest != trusted_digest
                    ):
                        raise ValueError("Historical indexing state mismatch")
                    if row.status in (
                        INDEX_STATUS_COMPLETE, INDEX_STATUS_INSUFFICIENT
                    ):
                        terminal_existing = True
            except Exception:
                terminal_existing = False
                raise
            if not terminal_existing:
                current_input = self._capture(
                    apply_session, sid, eid, ut, at, trusted.task_id,
                    GroundedDiscussionManager, canonical_exchange_input_digest,
                    _validate_exact_member,
                )
                if (
                    current_input is None
                    or current_input.canonical_source_digest != trusted.canonical_source_digest
                    or current_input.model_input_digest != trusted.model_input_digest
                ):
                    apply_mutation = True
                    raise ValueError("Canonical model input changed during semantic indexing")
                current_digest = current_input.canonical_source_digest
            else:
                current_digest = trusted_digest
            outcome = apply_semantic_index_proposals(
                apply_session,
                session_id=sid,
                exchange_id=eid,
                user_turn_id=ut,
                assistant_turn_id=at,
                task_id=trusted.task_id,
                semantic_result=semantic_result,
                input_digest=None,
                expected_canonical_input_digest=trusted_digest,
                commit=True,
            )
        except asyncio.CancelledError:
            apply_failed = True
            try:
                if apply_session is not None and trusted is not None:
                    current = self._capture(
                        apply_session, sid, eid, ut, at, trusted.task_id,
                        GroundedDiscussionManager, canonical_exchange_input_digest,
                        _validate_exact_member,
                    )
                    if (
                        current is None
                        or current.canonical_source_digest != trusted.canonical_source_digest
                        or current.model_input_digest != trusted.model_input_digest
                    ):
                        apply_mutation = True
            except Exception:
                try:
                    if apply_session is not None and trusted is not None and canonical_exchange_input_digest(
                        apply_session, sid, eid, ut, at
                    ) != trusted_digest:
                        apply_mutation = True
                except Exception:
                    pass
            self._release_transaction(apply_session)
            if self._may_close(apply_session, caller_session):
                try:
                    apply_session.close()
                except Exception:
                    pass
            raise
        except Exception as apply_error:
            apply_failed = True
            try:
                if apply_session is not None and trusted is not None:
                    current = self._capture(
                        apply_session, sid, eid, ut, at, trusted.task_id,
                        GroundedDiscussionManager, canonical_exchange_input_digest,
                        _validate_exact_member,
                    )
                    if (
                        current is None
                        or current.canonical_source_digest != trusted.canonical_source_digest
                        or current.model_input_digest != trusted.model_input_digest
                    ):
                        apply_mutation = True
            except Exception:
                try:
                    if apply_session is not None and trusted is not None and canonical_exchange_input_digest(
                        apply_session, sid, eid, ut, at
                    ) != trusted_digest:
                        apply_mutation = True
                except Exception:
                    pass
            try:
                self._release_transaction(apply_session)
            except Exception:
                pass
            if self._may_close(apply_session, caller_session):
                try:
                    apply_session.close()
                except Exception:
                    pass
            return ServerOwnedSemanticIndexOutcome(
                session_id=sid, exchange_id=eid, user_turn_id=ut, assistant_turn_id=at,
                semantic_result=semantic_result,
                applied=False,
                rejected_reason=apply_error.__class__.__name__,
                model_input_digest=trusted.model_input_digest,
                canonical_source_mutation_detected=apply_mutation,
                provider_call_count=provider_count,
                session_ownership_explicitly_verified=False,
            )
        try:
            self._release_transaction(apply_session)
        except Exception:
            pass
        if self._may_close(apply_session, caller_session):
            try:
                apply_session.close()
            except Exception:
                pass

        return ServerOwnedSemanticIndexOutcome(
            session_id=sid, exchange_id=eid, user_turn_id=ut, assistant_turn_id=at,
            semantic_result=semantic_result,
            state_idempotency_key=outcome.get("idempotency_key"),
            state_status=outcome.get("status"),
            applied=bool(outcome.get("applied_count", 0)),
            applied_count=int(outcome.get("applied_count", 0) or 0),
            model_input_digest=trusted.model_input_digest,
            model_input_binding_verified=False,  # fail closed: server capture/revalidation is not full provider/persistence binding
            canonical_source_mutation_detected=False,
            provider_call_count=provider_count,
            session_ownership_explicitly_verified=False,
            rejected_reason=None,
        )

    def _capture(self, session, sid, eid, ut, at, task_id, manager_cls,
                 canonical_digest_fn, validate_member_fn):
        # Establish one physical SQLite read (snapshot) transaction for a PRIVATE
        # session only.  Apply-time capture runs under the caller-apply writer
        # transaction and must not begin a nested transaction.
        read_transaction_owned = False
        try:
            from sqlalchemy import text as _text
            if not (hasattr(session, "in_transaction") and session.in_transaction()):
                session.execute(_text("BEGIN"))
                read_transaction_owned = True
                dbapi = session.connection().connection.dbapi_connection
                if dbapi is None or not getattr(dbapi, "in_transaction", False):
                    raise RuntimeError("SQLite read snapshot was not acquired")
            elif read_transaction_owned is False:
                # Already in apply writer transaction; verify the writer lock is live.
                dbapi = session.connection().connection.dbapi_connection
                if dbapi is None or not getattr(dbapi, "in_transaction", False):
                    raise RuntimeError("SQLite writer transaction is not live")
        except Exception:
            return None
        try:
            validate_member_fn(session, sid, eid, ut, at)
            canonical_digest = canonical_digest_fn(session, sid, eid, ut, at)
        except Exception:
            # Read transaction remains tracked by the session and is either
            # rolled back by orchestrator cleanup or remains with apply_session's
            # caller-controlled IMMEDIATE transaction.
            return None
        from backend.models.database import ConversationExchange, ConversationTurn
        exchange = session.query(ConversationExchange).filter_by(id=eid, session_id=sid).populate_existing().first()
        user_turn = session.query(ConversationTurn).filter_by(id=ut, session_id=sid, role="user").populate_existing().first()
        assistant_turn = session.query(ConversationTurn).filter_by(id=at, session_id=sid, role="assistant").populate_existing().first()
        if exchange is None or user_turn is None or assistant_turn is None:
            return None
        manager = manager_cls(session)
        summaries, coverage = manager.read_session_discussions(session, sid, limit=self._max_candidate_count)
        candidate_ids = tuple(summary.discussion_id for summary in summaries)
        candidate_labels = {summary.discussion_id: summary.label for summary in summaries}
        from backend.services.grounded_discussion_index import build_eligible_correction_targets
        eligible = build_eligible_correction_targets(session, sid, candidate_ids)
        try:
            if getattr(coverage, "omitted_count", 0) not in (0, None):
                return None
        except Exception:
            pass
        source_user_text = exchange.user_text
        source_assistant_text = exchange.assistant_text
        if not isinstance(source_user_text, str) or not source_user_text.strip():
            return None
        if len(source_user_text) > self._max_source_text or len(source_user_text.encode("utf-8")) > self._max_source_bytes:
            return None
        if not isinstance(source_assistant_text, str) or not source_assistant_text.strip():
            return None
        if len(source_assistant_text) > self._max_source_text or len(source_assistant_text.encode("utf-8")) > self._max_source_bytes:
            return None
        if len(candidate_ids) > self._max_candidate_count:
            return None
        clean_task_id = None
        if task_id is not None:
            if not isinstance(task_id, str) or not task_id.strip():
                return None
            clean_task_id = task_id.strip()
        labels = {discussion_id: candidate_labels[discussion_id] for discussion_id in candidate_ids}
        try:
            from backend.services.grounded_discussions import DiscussionExchangeLinkRow
        except Exception:
            try:
                from services.grounded_discussions import DiscussionExchangeLinkRow
            except Exception:
                DiscussionExchangeLinkRow = None
        memberships = {}
        if DiscussionExchangeLinkRow is not None:
            for discussion_id in candidate_ids:
                try:
                    links = session.query(DiscussionExchangeLinkRow).filter_by(
                        discussion_id=discussion_id, session_id=sid
                    ).all()
                    memberships[discussion_id] = sorted(
                        (str(link.exchange_id), int(link.user_turn_id), int(link.assistant_turn_id))
                        for link in links
                    )
                except Exception:
                    memberships[discussion_id] = []
        try:
            import backend.services.grounded_discussion_semantic_index as semantic_mod
            raw_snapshot = {
                "session_id": sid,
                "exchange_id": eid,
                "user_turn_id": ut,
                "assistant_turn_id": at,
                "task_id": clean_task_id,
                "source_user_text": source_user_text,
                "source_assistant_text": source_assistant_text,
                "candidate_discussion_ids": tuple(candidate_ids),
                "candidate_labels": labels,
                "max_candidate_count": self._max_candidate_count,
                "max_source_text": self._max_source_text,
                "max_source_bytes": self._max_source_bytes,
                "max_payload_bytes": self._max_payload_bytes,
                "max_proposals": self._max_proposals,
                "eligible_correction_target_ids": tuple(eligible),
            }
            snapshot = semantic_mod.SemanticIndexSnapshot(**raw_snapshot)
        except Exception:
            return None
        model_input_digest = hashlib.sha256(
            json.dumps(
                {
                    "schema_version": "sythion.server_owned_semantic_model_input.v1",
                    "session_id": sid,
                    "exchange_id": eid,
                    "user_turn_id": ut,
                    "assistant_turn_id": at,
                    "task_id": clean_task_id,
                    "canonical_source_digest": canonical_digest,
                    "candidate_memberships": memberships,
                    "model_input": asdict(snapshot),
                },
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        return ServerOwnedSemanticInput(
            session_id=sid,
            exchange_id=eid,
            user_turn_id=ut,
            assistant_turn_id=at,
            task_id=clean_task_id,
            source_user_text=source_user_text,
            source_assistant_text=source_assistant_text,
            candidate_discussion_ids=tuple(candidate_ids),
            candidate_labels=labels,
            eligible_correction_target_ids=tuple(eligible),
            max_candidate_count=self._max_candidate_count,
            max_source_text=self._max_source_text,
            max_source_bytes=self._max_source_bytes,
            max_payload_bytes=self._max_payload_bytes,
            max_proposals=self._max_proposals,
            canonical_source_digest=canonical_digest,
            model_input_digest=model_input_digest,
            semantic_snapshot=snapshot,
            candidate_memberships=memberships,
        )

    @staticmethod
    def _base_session_like(value: Any) -> bool:
        if value is None:
            return False
        return bool(
            hasattr(value, "execute")
            and (hasattr(value, "in_transaction") or hasattr(value, "new") or hasattr(value, "bind") or hasattr(value, "close"))
        )

    @staticmethod
    def _safe_private_bind(bind: Any) -> bool:
        # Only proveably private, multi-connection pools are used.  A Connection or
        # shared/single-thread pool may reuse state with the caller, so fail closed.
        try:
            from sqlalchemy.engine import Engine
            from sqlalchemy.pool import StaticPool, SingletonThreadPool
        except Exception:
            return False
        if not isinstance(bind, Engine):
            return False
        pool = getattr(bind, "pool", None)
        if pool is None:
            return False
        if isinstance(pool, (StaticPool, SingletonThreadPool)):
            return False
        # Refuse engines that have already been disposed/closed.
        if getattr(pool, "_disposed", False) or getattr(pool, "_closed", False):
            return False
        if getattr(bind, "_disposed", False) or getattr(bind, "_closed", False):
            return False
        return True

    @staticmethod
    def _unwrap_session(value: Any, seen: Any = None) -> Any:
        # Inspect structure only. Never invoke arbitrary callables/factories: doing so
        # can create untracked sessions or trigger caller side effects.
        if seen is None:
            seen = set()
        if value is None or id(value) in seen:
            return None
        seen.add(id(value))
        if ServerOwnedSemanticIndexOrchestrator._base_session_like(value):
            return value
        for attr in ("_caller_session", "session", "_session", "db_session", "_db_session", "current_session", "orm_session"):
            try:
                candidate = getattr(value, attr, None)
            except Exception:
                candidate = None
            resolved = ServerOwnedSemanticIndexOrchestrator._unwrap_session(candidate, seen)
            if resolved is not None:
                return resolved
        return None

    @staticmethod
    def _looks_like_session(value: Any) -> bool:
        return ServerOwnedSemanticIndexOrchestrator._unwrap_session(value) is not None

    def _register_supplied(self, session: Any) -> None:
        if session is not None and not any(existing is session for existing in self._supplied_sessions):
            self._supplied_sessions.append(session)

    def _register_protected(self, session: Any) -> None:
        if session is not None and not any(existing is session for existing in self._protected_sessions):
            self._protected_sessions.append(session)

    def _may_close(self, session: Any, caller_session: Any = None) -> bool:
        if session is None:
            return False
        if caller_session is not None and session is caller_session:
            return False
        if any(existing is session for existing in self._protected_sessions) or any(existing is session for existing in self._supplied_sessions):
            return False
        return True

    def _release_transaction(self, session: Any) -> None:
        if session is None:
            return
        if any(existing is session for existing in self._protected_sessions) or any(existing is session for existing in self._supplied_sessions):
            return
        try:
            if not session.in_transaction():
                return
            session.rollback()
        except Exception:
            pass

    def _rollback_and_close(self, session: Any, close: bool = False) -> None:
        if session is None:
            return
        if any(existing is session for existing in self._protected_sessions) or any(existing is session for existing in self._supplied_sessions):
            return
        try:
            if session.in_transaction():
                session.rollback()
        except Exception:
            pass
        if close:
            try:
                session.close()
            except Exception:
                pass


async def run_server_owned_semantic_index(*args, **kwargs):
    session_or_context = None
    model_client = kwargs.pop("model_client", None)
    budget = kwargs.pop("budget", None)
    task_id = kwargs.pop("task_id", None)
    session_id = kwargs.pop("session_id", None)
    exchange_id = kwargs.pop("exchange_id", None)
    user_turn_id = kwargs.pop("user_turn_id", None)
    assistant_turn_id = kwargs.pop("assistant_turn_id", None)
    owns_session = kwargs.pop("owns_session", None)
    session_owner = kwargs.pop("session_owner", None)
    owned_session = kwargs.pop("owned_session", None)
    owns_sessions = kwargs.pop("owns_sessions", None)
    exclusive_session = kwargs.pop("exclusive_session", None)
    exclusive = kwargs.pop("exclusive", None)
    owned = kwargs.pop("owned", None)
    session_factory = kwargs.pop("session_factory", None)
    db_session_factory = kwargs.pop("db_session_factory", None)
    session_maker = kwargs.pop("session_maker", None)
    factory = kwargs.pop("factory", None)
    max_candidate_count = kwargs.pop("max_candidate_count", 100)
    max_source_text = kwargs.pop("max_source_text", 1000)
    max_source_bytes = kwargs.pop("max_source_bytes", 4096)
    max_payload_bytes = kwargs.pop("max_payload_bytes", 16384)
    max_proposals = kwargs.pop("max_proposals", 10)

    remaining = list(args)
    if remaining and hasattr(remaining[0], "chat") and not _session_like_compat(remaining[0]):
        if model_client is None:
            model_client = remaining.pop(0)
    elif len(remaining) >= 2 and hasattr(remaining[1], "chat"):
        if session_or_context is None:
            session_or_context = remaining.pop(0)
        if model_client is None:
            model_client = remaining.pop(0)
    if remaining and session_or_context is None and _session_like_compat(remaining[0]):
        session_or_context = remaining.pop(0)
    elif remaining and session_or_context is None and callable(remaining[0]) and session_factory is None and factory is None and db_session_factory is None and session_maker is None:
        # Ambiguous callable shorthand. Treat as a factory only; ownership is still
        # caller-asserted separately and never guessed from callability.
        session_factory = remaining.pop(0)

    if remaining and session_id is None:
        session_id = remaining.pop(0)
    if remaining and exchange_id is None:
        exchange_id = remaining.pop(0)
    if remaining and user_turn_id is None:
        user_turn_id = remaining.pop(0)
    if remaining and assistant_turn_id is None:
        assistant_turn_id = remaining.pop(0)

    orchestrator = ServerOwnedSemanticIndexOrchestrator(
        session_or_context,
        model_client=model_client,
        budget=budget,
        session_factory=session_factory,
        db_session_factory=db_session_factory,
        session_maker=session_maker,
        factory=factory,
        owns_session=owns_session,
        session_owner=session_owner,
        owned_session=owned_session,
        owns_sessions=owns_sessions,
        exclusive_session=exclusive_session,
        exclusive=exclusive,
        owned=owned,
        max_candidate_count=max_candidate_count,
        max_source_text=max_source_text,
        max_source_bytes=max_source_bytes,
        max_payload_bytes=max_payload_bytes,
        max_proposals=max_proposals,
    )
    return await orchestrator.run(
        session_id=session_id,
        exchange_id=exchange_id,
        user_turn_id=user_turn_id,
        assistant_turn_id=assistant_turn_id,
        task_id=task_id,
        model_client=model_client,
        **{k: v for k, v in (
            ("session_factory", session_factory),
            ("db_session_factory", db_session_factory),
            ("session_maker", session_maker),
            ("factory", factory),
            ("owns_session", owns_session),
            ("session_owner", session_owner),
            ("owned_session", owned_session),
            ("owns_sessions", owns_sessions),
            ("exclusive_session", exclusive_session),
            ("exclusive", exclusive),
            ("owned", owned),
        ) if v is not None},
    )

def _session_like_compat(value):
    return bool(
        value is not None
        and hasattr(value, "execute")
        and (hasattr(value, "in_transaction") or hasattr(value, "new") or hasattr(value, "bind") or hasattr(value, "close"))
    )

__all__ = [
    "CallerSessionBusyError",
    "SessionOwnershipNotVerifiedError",
    "ServerOwnedSemanticInput",
    "ServerOwnedSemanticIndexOutcome",
    "ServerOwnedSemanticIndexOrchestrator",
    "run_server_owned_semantic_index",
]
