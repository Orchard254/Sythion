"""Default-inactive, read-only limited attributed recall candidate snapshot.

The reader reuses the existing canonical-source drift diagnostic's physical SQLite
snapshot validation, discovery/coverage reads, and digest comparison.  It exposes
one raw discovery object and one structural recall-eligibility flag.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any

try:
    from backend.services.canonical_source_drift_diagnostic import (
        CanonicalSourceDriftDiagnosticResult,
        _fail_closed_result as _canonical_fail_closed,
        _resolve_input,
        _validate_physical_snapshot,
        read_session_canonical_source_drift_diagnostic,
    )
except Exception:  # pragma: no cover - alternate invocation layout
    from services.canonical_source_drift_diagnostic import (  # type: ignore
        CanonicalSourceDriftDiagnosticResult,
        _fail_closed_result as _canonical_fail_closed,
        _resolve_input,
        _validate_physical_snapshot,
        read_session_canonical_source_drift_diagnostic,
    )

try:
    from backend.services.grounded_discussion_discovery import read_session_discussion_discovery
except Exception:  # pragma: no cover - alternate invocation layout
    from services.grounded_discussion_discovery import read_session_discussion_discovery  # type: ignore

try:
    from backend.services.grounded_discussion_index_coverage import IndexCoverageError
except Exception:  # pragma: no cover - alternate invocation layout
    try:
        from services.grounded_discussion_index_coverage import IndexCoverageError  # type: ignore
    except Exception:  # pragma: no cover
        class IndexCoverageError(Exception):
            pass


@dataclass(frozen=True)
class PartialDiscussionRecallSnapshotResult(CanonicalSourceDriftDiagnosticResult):
    raw_discovery: Any = None
    recall_eligible: bool = False


def _wrap(raw_discovery: Any, eligible: bool, delegated: Any) -> PartialDiscussionRecallSnapshotResult:
    kwargs = {f.name: getattr(delegated, f.name) for f in dataclasses.fields(CanonicalSourceDriftDiagnosticResult)}
    kwargs["raw_discovery"] = raw_discovery
    kwargs["recall_eligible"] = bool(eligible)
    return PartialDiscussionRecallSnapshotResult(**kwargs)


def read_session_partial_discussion_recall_snapshot(
    db: Any,
    session_id: Any,
    *,
    max_payload_bytes: Any = 1 << 20,
    max_discovery_payload_bytes: Any = None,
    coverage_max_payload_bytes: Any = None,
    max_examined_exchanges: Any = 1000,
) -> PartialDiscussionRecallSnapshotResult:
    try:
        from backend.services.canonical_source_drift_diagnostic import _clean_session
    except Exception:  # pragma: no cover - alternate invocation layout
        from services.canonical_source_drift_diagnostic import _clean_session  # type: ignore

    try:
        max_bytes = int(max_payload_bytes)
        if max_bytes < 0:
            max_bytes = 0
    except (TypeError, ValueError) as exc:
        return _wrap(None, False, _canonical_fail_closed(str(session_id or ""), 0, "invalid_max_payload_bytes", error=type(exc).__name__))

    try:
        clean_sid = _clean_session(session_id)
    except Exception as exc:
        return _wrap(None, False, _canonical_fail_closed(str(session_id or ""), max_bytes, "invalid_session_id", error=type(exc).__name__))

    if max_discovery_payload_bytes is None:
        max_discovery_payload_bytes = max_bytes
    if coverage_max_payload_bytes is None:
        coverage_max_payload_bytes = max_bytes
    try:
        max_examined = int(max_examined_exchanges)
        if max_examined < 0:
            max_examined = 0
    except (TypeError, ValueError):
        max_examined = 1000

    try:
        session, engine, owns_session = _resolve_input(db, {})
    except Exception as exc:
        return _wrap(None, False, _canonical_fail_closed(clean_sid, max_bytes, "database_unavailable", error=type(exc).__name__))

    caller_busy = False
    try:
        if getattr(session, "new", None) or getattr(session, "dirty", None) or getattr(session, "deleted", None):
            caller_busy = True
    except Exception:
        caller_busy = True
    if caller_busy:
        if owns_session and engine is not None:
            try:
                session.close()
                engine.dispose()
            except Exception:
                pass
        return _wrap(None, False, _canonical_fail_closed(clean_sid, max_bytes, "transaction_isolation_unknown", raw_discovery_reason="unavailable"))

    try:
        snapshot_validated, caller_owned, tx_owner, owns_transaction = _validate_physical_snapshot(session)
    except Exception:
        snapshot_validated = False
        caller_owned = False
        tx_owner = None
        owns_transaction = False
    if not snapshot_validated:
        if owns_session and engine is not None:
            try:
                session.close()
                engine.dispose()
            except Exception:
                pass
        return _wrap(None, False, _canonical_fail_closed(clean_sid, max_bytes, "transaction_isolation_unknown", raw_discovery_reason="unavailable"))

    raw_discovery = None
    try:
        raw_discovery = read_session_discussion_discovery(
            session,
            clean_sid,
            max_payload_bytes=max_discovery_payload_bytes,
        )
    except Exception:
        raw_discovery = None

    try:
        delegated = read_session_canonical_source_drift_diagnostic(
            session,
            clean_sid,
            max_payload_bytes=max_bytes,
            max_discovery_payload_bytes=max_discovery_payload_bytes,
            coverage_max_payload_bytes=coverage_max_payload_bytes,
            max_examined_exchanges=max_examined,
            source_freshness_verifier=None,
        )
    except Exception as exc:
        if owns_transaction and tx_owner is not None:
            try:
                tx_owner.rollback()
            except Exception:
                pass
        if owns_session and engine is not None:
            try:
                session.close()
                engine.dispose()
            except Exception:
                pass
        if isinstance(exc, IndexCoverageError):
            raise
        return _wrap(raw_discovery, False, _canonical_fail_closed(clean_sid, max_bytes, "diagnostic_failed", raw_discovery_reason="unavailable", error=type(exc).__name__))

    raw_complete = bool(getattr(raw_discovery, "discovery_complete", False)) if raw_discovery is not None else False
    raw_available = int(getattr(raw_discovery, "available_count", 0) or 0) if raw_discovery is not None else 0
    raw_returned = int(getattr(raw_discovery, "returned_count", 0) or 0) if raw_discovery is not None else 0
    raw_omitted = int(getattr(raw_discovery, "omitted_count", 0) or 0) if raw_discovery is not None else 0
    raw_unresolved_discussions = int(getattr(raw_discovery, "unresolved_discussion_count", 0) or 0) if raw_discovery is not None else 0
    raw_unresolved_task_proofs = int(getattr(raw_discovery, "unresolved_task_proof_count", 0) or 0) if raw_discovery is not None else 0
    untruncated = bool(
        raw_discovery is not None
        and raw_complete
        and raw_returned == raw_available
        and raw_omitted == 0
        and raw_unresolved_discussions == 0
        and raw_unresolved_task_proofs == 0
    )

    index_ok = bool(
        getattr(delegated, "coverage_complete", False)
        and getattr(delegated, "structural_state_complete", False)
        and int(getattr(delegated, "unindexed_count", 0) or 0) == 0
        and int(getattr(delegated, "pending_count", 0) or 0) == 0
        and int(getattr(delegated, "in_progress_count", 0) or 0) == 0
        and int(getattr(delegated, "insufficient_count", 0) or 0) == 0
        and int(getattr(delegated, "uncertain_count", 0) or 0) == 0
        and int(getattr(delegated, "mismatched_count", 0) or 0) == 0
    )

    digest_ok = bool(getattr(delegated, "canonical_source_digest_match", False))
    if raw_available == 0 and int(getattr(delegated, "available_count", 0) or 0) == 0:
        digest_ok = digest_ok or (not bool(getattr(delegated, "canonical_source_digest_mismatch", False)))

    eligible = bool(
        getattr(delegated, "snapshot_validated", False)
        and untruncated
        and index_ok
        and digest_ok
    )

    result = _wrap(raw_discovery, eligible, delegated)

    if owns_transaction and tx_owner is not None:
        try:
            tx_owner.commit()
        except Exception:
            try:
                tx_owner.rollback()
            except Exception:
                pass
    if owns_session and engine is not None:
        try:
            session.close()
            engine.dispose()
        except Exception:
            pass
    return result


class PartialDiscussionRecallSnapshot:
    def __init__(self, db: Any = None, *, session: Any = None, db_path: Any = None, **kwargs: Any):
        self._db = db if db is not None else (session if session is not None else db_path)
        self._defaults = dict(kwargs)

    @property
    def db(self) -> Any:
        return self._db

    def read(self, session_id: Any, **kwargs: Any) -> PartialDiscussionRecallSnapshotResult:
        return read_session_partial_discussion_recall_snapshot(self._db, session_id, **kwargs)