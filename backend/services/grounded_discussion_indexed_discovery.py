"""Default-inert opt-in integration of discussion discovery and index coverage.

This module wires the already-approved discussion-discovery and
index-coverage seams together without activating routes, imports at default
startup, database tables, migrations, schedulers, or providers.  Callers must
explicitly instantiate or call a function here; ordinary discussion discovery
remains unchanged when this module is not used.

The integration uses ``end_to_end_complete`` from index coverage, not
``structural_state_complete``.  Since no source-freshness verifier is injected
by default, a nonempty but indexed session still reports coverage incomplete.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Optional

from sqlalchemy.orm import Session


_DEFAULT_DISCOVERY_MAX_PAYLOAD_BYTES = 1 << 20


def _session_of(value: Any) -> Session:
    if value is None or isinstance(value, str):
        raise ValueError("A database session is required")
    if isinstance(value, Session):
        return value
    session = getattr(value, "db", None)
    if isinstance(session, Session):
        return session
    session = getattr(value, "session", None)
    if isinstance(session, Session):
        return session
    runner = getattr(value, "runner", None)
    if runner is not None:
        inner = getattr(runner, "db", None)
        if isinstance(inner, Session):
            return inner
    if hasattr(value, "execute") and hasattr(value, "flush"):
        return value
    raise ValueError("A database session is required")


def _first_not_none(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def integrate_discussion_discovery_with_index_coverage(
    discovery_result: Any,
    coverage_result: Any,
) -> Any:
    """Return a gated discovery result using coverage's end-to-end verdict.

    This does not mutate the inputs.  If either availability or freshness is
    unknown, incomplete, or errored, discovery completeness is fail-closed.
    """
    if discovery_result is None:
        return discovery_result

    try:
        discovery_complete = bool(getattr(discovery_result, "discovery_complete", False))
    except Exception:
        discovery_complete = False

    discovery_session = getattr(discovery_result, "session_id", None)
    coverage_complete = False
    # Missing, invalid, foreign, truthy-only, or unauthenticated coverage is
    # fail-closed.  Only an exact end_to_end_complete True value with the same
    # session identity counts.
    if coverage_result is not None and not isinstance(
        coverage_result, (bool, str, bytes, bytearray, int, float)
    ):
        try:
            is_true = getattr(coverage_result, "end_to_end_complete", None) is True
            covariance_sid = getattr(coverage_result, "session_id", None)
            same_session = (
                discovery_session is None
                or covariance_sid is None
                or covariance_sid == discovery_session
            )
            available = getattr(coverage_result, "available_count", 0)
            valid_shape = (
                isinstance(available, int)
                and not isinstance(available, bool)
                and available >= 0
            )
            if valid_shape and same_session and int(available or 0) == 0:
                coverage_complete = True
            elif is_true and same_session and valid_shape:
                coverage_complete = True
        except Exception:
            coverage_complete = False

    complete = bool(discovery_complete and coverage_complete)

    reason = getattr(discovery_result, "reason", "unknown")
    if discovery_complete and not coverage_complete:
        reason = "unresolved_canonical_source"
    elif not discovery_complete:
        # Preserve discovery's original insufficiency reason.
        reason = getattr(discovery_result, "reason", reason)

    payload_text = getattr(discovery_result, "payload_json", None)
    payload_bytes = getattr(discovery_result, "payload_bytes", 0)
    if isinstance(payload_text, str):
        try:
            import json

            payload = json.loads(payload_text)
            if isinstance(payload, dict):
                payload["discovery_complete"] = complete
                payload["reason"] = reason
                serialized = json.dumps(
                    payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
                )
                payload_text = serialized
                payload_bytes = len(serialized.encode("utf-8"))
        except (TypeError, ValueError):
            pass

    try:
        return replace(
            discovery_result,
            discovery_complete=complete,
            reason=reason,
            payload_json=payload_text,
            payload_bytes=payload_bytes,
        )
    except Exception:
        # Fall back to a shallow attribute copy when replace is unsupported.
        try:
            cloned = object.__new__(type(discovery_result))
            cloned.__dict__.update(getattr(discovery_result, "__dict__", {}))
            cloned.discovery_complete = complete
            cloned.reason = reason
            cloned.payload_json = payload_text
            cloned.payload_bytes = payload_bytes
            return cloned
        except Exception:
            return discovery_result


def gate_discovery_with_index_coverage(
    discovery_result: Any,
    coverage_result: Any,
) -> Any:
    return integrate_discussion_discovery_with_index_coverage(discovery_result, coverage_result)


def read_session_indexed_discussion_discovery(
    db: Any,
    session_id: Any,
    *,
    discovery_max_payload_bytes: Any = None,
    coverage_max_payload_bytes: Any = None,
    max_examined_exchanges: Any = None,
    source_freshness_verifier: Any = None,
    max_payload_bytes: Any = None,
    **kwargs: Any,
) -> Any:
    """Explicitly opt-in session discovery gated by index-coverage end-to-end."""
    session = _session_of(db)
    sid = session_id

    snapshot_validated = False
    transaction_owner = None
    owns_transaction = False
    caller_owned = False
    try:
        connection = session.connection()
        dbapi = getattr(connection, "connection", None)
        if dbapi is not None and getattr(dbapi, "in_transaction", False):
            # Caller already has a real DBAPI transaction.  We trust that
            # transaction boundary, never commit/rollback it, and use it as
            # the read snapshot.
            caller_owned = True
            snapshot_validated = True
        elif dbapi is not None:
            # Start a real low-level transaction.  SQLAlchemy's boolean
            # transaction state is not sufficient evidence on SQLite.
            try:
                connection.exec_driver_sql("BEGIN")
            except Exception:
                snapshot_validated = False
            else:
                snapshot_validated = bool(getattr(dbapi, "in_transaction", False))
                transaction_owner = dbapi
                owns_transaction = True
        else:
            snapshot_validated = False
            owns_transaction = False
    except Exception:
        snapshot_validated = False
        transaction_owner = None
        caller_owned = False
    if not snapshot_validated:
        fallback_payload = '{"session_id":"","discovery_complete":false,"reason":"transaction_isolation_unknown","representatives":[]}'
        try:
            from backend.services.grounded_discussion_discovery import DiscussionDiscoveryResult

            result = DiscussionDiscoveryResult(
                session_id=str(sid),
                max_payload_bytes=int(discovery_max_payload_bytes or _DEFAULT_DISCOVERY_MAX_PAYLOAD_BYTES),
                available_count=0,
                returned_count=0,
                omitted_count=0,
                unresolved_discussion_count=0,
                unresolved_task_proof_count=0,
                candidate_discussion_ids=(),
                representatives=(),
                discovery_complete=False,
                reason="transaction_isolation_unknown",
                payload_bytes=len(fallback_payload.encode("utf-8")),
                payload_json=fallback_payload,
            )
        except Exception:
            result = None
        return result

    try:
        if discovery_max_payload_bytes is None:
            discovery_max_payload_bytes = kwargs.pop("max_discovery_payload_bytes", None)
        if discovery_max_payload_bytes is None:
            discovery_max_payload_bytes = max_payload_bytes
        if discovery_max_payload_bytes is None:
            discovery_max_payload_bytes = _DEFAULT_DISCOVERY_MAX_PAYLOAD_BYTES
        if coverage_max_payload_bytes is None:
            coverage_max_payload_bytes = kwargs.pop("coverage_byte_budget", None)
        if coverage_max_payload_bytes is None:
            coverage_max_payload_bytes = discovery_max_payload_bytes
        if max_examined_exchanges is None:
            max_examined_exchanges = kwargs.pop("max_examined_rows", None)
        if max_examined_exchanges is None:
            max_examined_exchanges = 1000

        from backend.services.grounded_discussion_discovery import read_session_discussion_discovery
        from backend.services.grounded_discussion_index_coverage import read_session_index_coverage

        try:
            discovery_result = read_session_discussion_discovery(
                session, sid, max_payload_bytes=discovery_max_payload_bytes
            )
            coverage_result = read_session_index_coverage(
                session,
                sid,
                max_payload_bytes=coverage_max_payload_bytes,
                max_examined_exchanges=max_examined_exchanges,
                source_freshness_verifier=source_freshness_verifier,
            )
            return integrate_discussion_discovery_with_index_coverage(discovery_result, coverage_result)
        finally:
            if owns_transaction and transaction_owner is not None:
                try:
                    transaction_owner.commit()
                except Exception:
                    try:
                        transaction_owner.rollback()
                    except Exception:
                        pass
    except Exception:
        if owns_transaction and transaction_owner is not None:
            try:
                transaction_owner.commit()
            except Exception:
                try:
                    transaction_owner.rollback()
                except Exception:
                    pass
        raise



class IndexedDiscussionDiscoveryService:
    """Default-inert local collaborator for opt-in discovery plus coverage."""

    def __init__(
        self,
        db: Any = None,
        *,
        session: Any = None,
        discovery_max_payload_bytes: Any = None,
        max_examined_exchanges: Any = None,
        source_freshness_verifier: Any = None,
        **kwargs: Any,
    ) -> None:
        self._db = _first_not_none(db, session, kwargs.get("database"))
        self.discovery_max_payload_bytes = _first_not_none(
            discovery_max_payload_bytes, kwargs.get("max_discovery_payload_bytes"),
            _DEFAULT_DISCOVERY_MAX_PAYLOAD_BYTES,
        )
        self.max_examined_exchanges = _first_not_none(max_examined_exchanges, 1000)
        self.source_freshness_verifier = source_freshness_verifier

    @property
    def db(self) -> Session:
        return _session_of(self._db)

    def discover_session_candidates(
        self,
        db: Any = None,
        session_id: Any = None,
        *,
        max_payload_bytes: Any = None,
        source_freshness_verifier: Any = None,
        **kwargs: Any,
    ) -> Any:
        session = db if db is not None else self._db
        effective_verifier = _first_not_none(source_freshness_verifier, self.source_freshness_verifier)
        return read_session_indexed_discussion_discovery(
            session,
            session_id,
            discovery_max_payload_bytes=max_payload_bytes or self.discovery_max_payload_bytes,
            max_examined_exchanges=self.max_examined_exchanges,
            source_freshness_verifier=effective_verifier,
            **kwargs,
        )

    def discover(self, *args: Any, **kwargs: Any) -> Any:
        if len(args) >= 2 and not isinstance(args[0], str):
            return self.discover_session_candidates(args[0], args[1], **kwargs)
        if len(args) == 1 and isinstance(args[0], str):
            return self.discover_session_candidates(self._db, args[0], **kwargs)
        return self.discover_session_candidates(**kwargs)


# Compatibility aliases.
GatedDiscussionDiscoveryService = IndexedDiscussionDiscoveryService
IntegratedDiscussionDiscoveryService = IndexedDiscussionDiscoveryService
read_session_index_gated_discussion_discovery = read_session_indexed_discussion_discovery
read_session_gated_discussion_discovery = read_session_indexed_discussion_discovery
gate_discussion_discovery = integrate_discussion_discovery_with_index_coverage

__all__ = [
    "integrate_discussion_discovery_with_index_coverage",
    "gate_discovery_with_index_coverage",
    "read_session_indexed_discussion_discovery",
    "IndexedDiscussionDiscoveryService",
    "GatedDiscussionDiscoveryService",
    "IntegratedDiscussionDiscoveryService",
    "read_session_index_gated_discussion_discovery",
    "read_session_gated_discussion_discovery",
    "gate_discussion_discovery",
]
