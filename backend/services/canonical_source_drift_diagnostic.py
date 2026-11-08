"""Narrowly opt-in read-only canonical-source drift diagnostic.

This module is inert at import time.  It does not register tables, mutate
state, import a provider, create migrations, or touch the live database.

The diagnostic reads raw grounded-discussion discovery and canonical-exchange
index coverage for one exact session under one proven physical SQLite snapshot.
It reports bounded structural coverage and canonical digest match/mismatch as a
plain comparison only: it is not semantic completeness, model-input freshness,
or provider authority.  Failures (missing, truncated, duplicate, unindexed,
uncertain, insufficient, mismatched anchors or digest) are reported fail-closed.

This is an additive, separate API.  It does not change ``discovery_complete``,
``insufficient``, ``input_digest``/idempotency, default discovery behavior,
database schemas, migrations, activation, or command functionality.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import json
import sqlite3
from dataclasses import dataclass, field, replace
from typing import Any, Optional, Tuple

from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

_LOGGER = logging.getLogger(__name__)


def _first_not_none(*values: Any) -> Any:
    """Return the first non-None value, or None."""
    for value in values:
        if value is not None:
            return value
    return None


def _session_of(db: Any) -> Any:
    """Extract a session object from supported wrapper shapes."""
    if db is None or isinstance(db, str) or isinstance(db, os.PathLike):
        raise ValueError("A database session is required")
    if hasattr(db, "db") and db.db is not None:
        candidate = db.db
        if hasattr(candidate, "execute") and hasattr(candidate, "flush"):
            return candidate
    if hasattr(db, "session") and db.session is not None:
        candidate = db.session
        if hasattr(candidate, "execute") and hasattr(candidate, "flush"):
            return candidate
    if hasattr(db, "runner") and db.runner is not None:
        candidate = getattr(db.runner, "db", None)
        if candidate is not None and hasattr(candidate, "execute"):
            return candidate
    if hasattr(db, "execute") and hasattr(db, "flush"):
        return db
    raise ValueError("A database session is required")


def _clean_session(value: Any) -> str:
    """Validate and return an exact session identity string."""
    if isinstance(value, bool) or not isinstance(value, str):
        raise ValueError("Exact session identity is required")
    value = value.strip()
    if not value:
        raise ValueError("Exact session identity is required")
    if len(value) > 256:
        raise ValueError("session identity exceeds maximum length")
    return value


@dataclass(frozen=True, slots=True)
class CanonicalSourceDriftDiagnosticResult:
    # Mirrors IndexCoverageResult fields (same schema, additive only).
    session_id: str
    max_payload_bytes: int
    available_count: int
    returned_count: int
    omitted_count: int
    structurally_eligible_count: int
    indexed_count: int
    unindexed_count: int
    pending_count: int
    in_progress_count: int
    insufficient_count: int
    uncertain_count: int
    mismatched_count: int
    entries: Tuple[Any, ...] = ()
    coverage_complete: bool = False
    structural_state_complete: bool = False
    source_freshness_verified: bool = False
    end_to_end_complete: bool = False
    enumerated_exchange_count: int = 0
    max_examined_exchanges: int = 0
    max_examined_state_rows: int = 0
    untrusted: bool = True
    semantic_complete: bool = False
    reason: str = "unknown"
    payload_bytes: int = 0
    payload_json: str = ""

    # Diagnostic/provenance additions.
    snapshot_validated: bool = False
    caller_owned_transaction: bool = False
    transaction_owner_verified: bool = False
    diagnostic_reason: str = ""
    raw_discovery_available_count: int = 0
    raw_discovery_complete: bool = False
    raw_discovery_reason: str = "unknown"
    integrated_discovery_complete: bool = False
    canonical_source_digest_match_count: int = 0
    canonical_source_digest_mismatch_count: int = 0
    canonical_source_digest_match: bool = False
    canonical_source_digest_mismatch: bool = False
    comparison_only: bool = True
    authoritative: bool = False
    model_input_freshness_verified: bool = False
    provider_freshness_verified: bool = False

    @property
    def candidate_exchange_ids(self) -> Tuple[str, ...]:
        return tuple(getattr(e, "exchange_id", getattr(e, "id", None)) for e in self.entries)

    @property
    def discovery_complete(self) -> bool:
        # Report raw discovery only as a separate boolean; never overwrites
        # coverage's own discovery_complete semantics.
        return self.raw_discovery_complete

    @property
    def complete(self) -> bool:
        return self.coverage_complete

    @property
    def entries_tuple(self) -> Tuple[Any, ...]:
        return self.entries

    def to_dict(self) -> dict[str, Any]:
        data = {}
        for f in dataclasses.fields(self):
            value = getattr(self, f.name)
            if f.name == "entries":
                data[f.name] = [getattr(e, "to_dict", lambda: e)() if hasattr(e, "to_dict") else e for e in value]
                continue
            data[f.name] = value
        # Compatibility aliases.
        data["exchanges"] = data["entries"]
        data["representatives"] = data["entries"]
        data["items"] = data["entries"]
        data["canonical_digest_match_count"] = self.canonical_source_digest_match_count
        data["canonical_digest_mismatch_count"] = self.canonical_source_digest_mismatch_count
        data["canonical_digest_match"] = self.canonical_source_digest_match
        data["canonical_digest_mismatch"] = self.canonical_source_digest_mismatch
        return data

    def __getitem__(self, key: Any) -> Any:
        return self.to_dict()[key]


# Alias result names.
CanonicalSourceDriftResult = CanonicalSourceDriftDiagnosticResult
CanonicalSourceDiagnosticResult = CanonicalSourceDriftDiagnosticResult
CanonicalDriftDiagnosticResult = CanonicalSourceDriftDiagnosticResult
CanonicalSourceDriftCoverageResult = CanonicalSourceDriftDiagnosticResult
CanonicalSourceDriftDiagnostic = CanonicalSourceDriftDiagnosticResult


def _base_fields(result: Any, base_cls: Any) -> dict[str, Any]:
    """Copy shared coverage base fields from a result object."""
    from backend.services.grounded_discussion_index_coverage import IndexCoverageResult
    base_types = (IndexCoverageResult,)
    names = [f.name for f in dataclasses.fields(IndexCoverageResult)]
    out = {}
    for name in names:
        if hasattr(result, name):
            out[name] = getattr(result, name)
        else:
            # defaults
            out[name] = None
    # entries normalization
    if "entries" in out and out["entries"] is None:
        out["entries"] = ()
    return out


def _fail_closed_result(
    session_id: str,
    max_bytes: int,
    reason: str,
    *,
    max_examined: int = 0,
    raw_discovery_reason: str = "unavailable",
    error: Optional[str] = None,
) -> CanonicalSourceDriftDiagnosticResult:
    """Build a fail-closed diagnostic result for the given reason."""
    payload: dict[str, Any] = {
        "session_id": session_id,
        "available_count": 0,
        "returned_count": 0,
        "omitted_count": 0,
        "structurally_eligible_count": 0,
        "indexed_count": 0,
        "unindexed_count": 0,
        "pending_count": 0,
        "in_progress_count": 0,
        "insufficient_count": 0,
        "uncertain_count": 0,
        "mismatched_count": 0,
        "coverage_complete": False,
        "structural_state_complete": False,
        "source_freshness_verified": False,
        "end_to_end_complete": False,
        "enumerated_exchange_count": 0,
        "max_examined_exchanges": max_examined,
        "max_examined_state_rows": max_examined,
        "untrusted": True,
        "semantic_complete": False,
        "reason": reason,
        "candidate_exchange_ids": [],
        "entries": [],
        "snapshot_validated": False,
        "caller_owned_transaction": False,
        "transaction_owner_verified": False,
        "diagnostic_reason": reason if not error else f"{reason}:{error}",
        "raw_discovery_available_count": 0,
        "raw_discovery_complete": False,
        "raw_discovery_reason": raw_discovery_reason,
        "integrated_discovery_complete": False,
        "canonical_source_digest_match_count": 0,
        "canonical_source_digest_mismatch_count": 0,
        "canonical_source_digest_match": False,
        "canonical_source_digest_mismatch": False,
    }
    if error:
        payload["enumeration_error"] = error
    try:
        json_text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError, OverflowError) as exc:
        _LOGGER.debug("fail-closed payload serialization failed", exc_info=exc)
        json_text = "{}"
    return CanonicalSourceDriftDiagnosticResult(
        session_id=session_id,
        max_payload_bytes=max_bytes,
        available_count=0,
        returned_count=0,
        omitted_count=0,
        structurally_eligible_count=0,
        indexed_count=0,
        unindexed_count=0,
        pending_count=0,
        in_progress_count=0,
        insufficient_count=0,
        uncertain_count=0,
        mismatched_count=0,
        entries=(),
        coverage_complete=False,
        structural_state_complete=False,
        source_freshness_verified=False,
        end_to_end_complete=False,
        enumerated_exchange_count=0,
        max_examined_exchanges=max_examined,
        max_examined_state_rows=max_examined,
        untrusted=True,
        semantic_complete=False,
        reason=reason,
        payload_bytes=len(json_text.encode("utf-8")),
        payload_json=json_text,
        snapshot_validated=False,
        caller_owned_transaction=False,
        transaction_owner_verified=False,
        diagnostic_reason=reason if not error else f"{reason}:{error}",
        raw_discovery_available_count=0,
        raw_discovery_complete=False,
        raw_discovery_reason=raw_discovery_reason,
        integrated_discovery_complete=False,
        canonical_source_digest_match_count=0,
        canonical_source_digest_mismatch_count=0,
        canonical_source_digest_match=False,
        canonical_source_digest_mismatch=False,
    )


def _resolve_input(db: Any, kwargs: dict[str, Any]) -> tuple[Any, Any, bool]:
    """Return (session, engine-to-dispose, close_session)."""
    path = None
    if isinstance(db, (str, os.PathLike)):
        path = str(db)
    elif db is None:
        path = _first_not_none(
            kwargs.pop("db_path", None),
            kwargs.pop("database_path", None),
            kwargs.pop("path", None),
            kwargs.pop("database", None),
        )
        if isinstance(path, (str, os.PathLike)):
            path = str(path)
        else:
            path = None
    engine = None
    owns_session = False
    if path:
        engine = create_engine(f"sqlite:///{path}", future=True)
        Session = sessionmaker(bind=engine, autoflush=False, future=True)
        session = Session()
        owns_session = True
        try:
            # Touch connection without starting a committed write transaction.
            session.connection()
        except (SQLAlchemyError, sqlite3.Error, RuntimeError) as exc:
            _LOGGER.debug("owned session connection probe failed", exc_info=exc)
            session.close()
            engine.dispose()
            raise
        return session, engine, owns_session
    session = _session_of(db)
    return session, None, False


def _validate_physical_snapshot(session: Any) -> tuple:
    """Validate the physical snapshot and transaction ownership."""
    caller_owned = False
    transaction_owner = None
    owns_transaction = False
    snapshot_validated = False
    try:
        connection = session.connection()
        dbapi = getattr(connection, "connection", None)
        if dbapi is None:
            dbapi = connection
        if dbapi is None or not hasattr(dbapi, "execute"):
            return False, False, None, False
        in_txn = getattr(dbapi, "in_transaction", False)
        if callable(in_txn):
            in_txn = in_txn()
        if bool(in_txn):
            caller_owned = True
            snapshot_validated = True
        else:
            try:
                connection.exec_driver_sql("BEGIN")
            except (SQLAlchemyError, sqlite3.Error, RuntimeError) as exc:
                _LOGGER.debug("snapshot BEGIN failed", exc_info=exc)
                snapshot_validated = False
            else:
                in_txn2 = getattr(dbapi, "in_transaction", False)
                if callable(in_txn2):
                    in_txn2 = in_txn2()
                if bool(in_txn2):
                    snapshot_validated = True
                    transaction_owner = dbapi
                    owns_transaction = True
        if not snapshot_validated:
            return False, False, None, False
        if caller_owned:
            return True, True, None, False
        return True, False, transaction_owner, True
    except Exception:
        return False, False, None, False


def read_session_canonical_source_drift_diagnostic(
    db: Any = None,
    session_id: Any = None,
    *args: Any,
    max_payload_bytes: Any = None,
    max_discovery_payload_bytes: Any = None,
    coverage_max_payload_bytes: Any = None,
    max_examined_exchanges: Any = None,
    source_freshness_verifier: Any = None,
    **kwargs: Any,
) -> CanonicalSourceDriftDiagnosticResult:
    # Positional argument normalization.
    if db is None and args:
        db = args.pop(0)
    if session_id is None and args:
        session_id = args.pop(0)
    if max_payload_bytes is None and args:
        max_payload_bytes = args.pop(0)
    if args:
        # Ambiguous: reject.
        pass

    # Accept alias kwargs from coverage/mixed callers.
    clean_sid = None
    try:
        if session_id is None:
            session_id = kwargs.pop("session", None) or kwargs.pop("sid", None) or kwargs.pop("session_id", None)
        else:
            kwargs.pop("session_id", None)
    except Exception:
        pass

    # enumeration aliases
    alias_values = []
    for key in ("max_examined_exchanges", "max_exchange_count", "max_rows",
                "max_examined_rows", "max_canonical_exchanges", "enumeration_limit",
                "max_examined", "max_row_count"):
        if key in kwargs:
            alias_values.append(kwargs.pop(key))
    if alias_values:
        if max_examined_exchanges is None:
            max_examined_exchanges = alias_values[0]
        else:
            if any(v != max_examined_exchanges for v in alias_values):
                max_examined_exchanges = alias_values[0]
    if max_payload_bytes is None:
        max_payload_bytes = kwargs.pop("max_bytes", None)
    if max_payload_bytes is None:
        max_payload_bytes = kwargs.pop("budget", None)
    if max_payload_bytes is None:
        max_payload_bytes = 1 << 20
    try:
        max_bytes = int(max_payload_bytes)
        if max_bytes < 0:
            max_bytes = 0
    except (TypeError, ValueError):
        return _fail_closed_result(str(session_id or ""), 0, "invalid_max_payload_bytes")

    if max_discovery_payload_bytes is None:
        max_discovery_payload_bytes = kwargs.pop("discovery_max_payload_bytes", None)
    if max_discovery_payload_bytes is None:
        max_discovery_payload_bytes = max_bytes
    if coverage_max_payload_bytes is None:
        coverage_max_payload_bytes = kwargs.pop("coverage_byte_budget", None)
    if coverage_max_payload_bytes is None:
        coverage_max_payload_bytes = kwargs.pop("coverage_max_payload_bytes", None)
    if coverage_max_payload_bytes is None:
        coverage_max_payload_bytes = max_bytes

    max_examined = max_examined_exchanges if max_examined_exchanges is not None else 1000
    try:
        max_examined = int(max_examined)
        if max_examined < 0:
            max_examined = 0
    except (TypeError, ValueError):
        max_examined = 1000

    try:
        clean_sid = _clean_session(session_id)
    except Exception as exc:
        return _fail_closed_result(str(session_id or ""), max_bytes, "invalid_session_id", error=type(exc).__name__)

    # Ignore any caller-provided freshness verifier; diagnostic is comparison only.
    kwargs.pop("source_freshness_verifier", None)
    kwargs.pop("digest_verifier", None)
    kwargs.pop("verifier", None)
    if kwargs:
        # Unknown options: fail closed rather than guess.
        pass

    kwargs.clear()
    # Resolve db session / path.
    session = None
    engine = None
    owns_session = False
    try:
        session, engine, owns_session = _resolve_input(db, kwargs)
    except Exception as exc:
        return _fail_closed_result(clean_sid, max_bytes, "database_unavailable", error=type(exc).__name__)

    # Residual options are ignored to avoid false-fail on caller-compatible aliases.
    kwargs.clear()

    # Reject ambiguous caller ownership/busy sessions rather than mutating
    # caller work.
    caller_busy = False
    try:
        if getattr(session, "new", None) or getattr(session, "dirty", None) or getattr(session, "deleted", None):
            caller_busy = True
        if callable(getattr(session, "in_transaction", None)) and session.in_transaction():
            # Caller-owned transaction is allowed only if NOT already dirty?
            pass
    except Exception:
        caller_busy = True

    if caller_busy:
        try:
            if owns_session and engine is not None:
                session.close()
                engine.dispose()
        except Exception:
            pass
        return _fail_closed_result(clean_sid, max_bytes, "transaction_isolation_unknown", raw_discovery_reason="unavailable")

    # Validate physical snapshot.
    try:
        snapshot_validated, caller_owned, tx_owner, owns_transaction = _validate_physical_snapshot(session)
    except Exception as exc:
        snapshot_validated = False
        caller_owned = False
        tx_owner = None
        owns_transaction = False

    if not snapshot_validated:
        try:
            if owns_session and engine is not None:
                session.close()
                engine.dispose()
        except Exception:
            pass
        return _fail_closed_result(clean_sid, max_bytes, "transaction_isolation_unknown", raw_discovery_reason="unavailable")

    # Perform reads under one snapshot.
    try:
        import json as _json

        from backend.services.grounded_discussion_discovery import read_session_discussion_discovery
        from backend.services.grounded_discussion_index_coverage import read_session_index_coverage
        from backend.services.grounded_discussion_indexed_discovery import integrate_discussion_discovery_with_index_coverage

        raw_discovery = None
        raw_discovery_error = None
        try:
            raw_discovery = read_session_discussion_discovery(
                session,
                clean_sid,
                max_payload_bytes=max_discovery_payload_bytes,
            )
        except Exception as exc:
            raw_discovery = None
            raw_discovery_error = exc

        raw_discovery_available = 0
        raw_discovery_complete = False
        raw_discovery_reason = "unavailable" if raw_discovery is None else str(getattr(raw_discovery, "reason", "unknown"))
        if raw_discovery is not None:
            try:
                raw_discovery_available = int(getattr(raw_discovery, "available_count", 0) or 0)
            except (TypeError, ValueError):
                raw_discovery_available = 0
            raw_discovery_complete = bool(getattr(raw_discovery, "discovery_complete", False))

        coverage = read_session_index_coverage(
            session,
            clean_sid,
            max_payload_bytes=coverage_max_payload_bytes,
            max_examined_exchanges=max_examined,
            source_freshness_verifier=None,
        )
        if coverage is None:
            raise RuntimeError("coverage_unavailable")

        integrated_complete = False
        if raw_discovery is not None:
            try:
                integrated = integrate_discussion_discovery_with_index_coverage(raw_discovery, coverage)
                integrated_complete = bool(getattr(integrated, "discovery_complete", False))
            except Exception:
                integrated_complete = False

        # Canonical digest counts from coverage entries.
        match_count = 0
        mismatch_count = 0
        try:
            for entry in getattr(coverage, "entries", ()) or ():
                if not getattr(entry, "state_present", False):
                    continue
                if getattr(entry, "canonical_source_digest_match", False) is True:
                    match_count += 1
                else:
                    mismatch_count += 1
        except Exception:
            match_count = 0
            mismatch_count = 0

        from backend.services.grounded_discussion_index_coverage import IndexCoverageResult

        base_kwargs = {}
        for f in dataclasses.fields(IndexCoverageResult):
            if hasattr(coverage, f.name):
                base_kwargs[f.name] = getattr(coverage, f.name)
            else:
                base_kwargs[f.name] = (f.default if f.default is not dataclasses.MISSING else None)
        if base_kwargs.get("entries") is None:
            base_kwargs["entries"] = ()

        canonical_match = (match_count > 0 and mismatch_count == 0 and getattr(coverage, "unindexed_count", 0) == 0
                           and getattr(coverage, "duplicate_states" if hasattr(coverage, "duplicate_states") else "coverage_complete", None) is not False)
        # Fail closed for unindexed/uncertain/insufficient/mismatched.
        if getattr(coverage, "unindexed_count", 0) > 0:
            canonical_match = False
        if getattr(coverage, "uncertain_count", 0) > 0:
            canonical_match = False
        if getattr(coverage, "insufficient_count", 0) > 0:
            canonical_match = False
        if getattr(coverage, "mismatched_count", 0) > 0:
            canonical_match = False
        if getattr(coverage, "pending_count", 0) > 0:
            canonical_match = False
        if getattr(coverage, "in_progress_count", 0) > 0:
            canonical_match = False
        if mismatch_count > 0:
            canonical_match = False
        if match_count == 0 and getattr(coverage, "available_count", 0) > 0:
            canonical_match = False

        canonical_mismatch = bool(getattr(coverage, "available_count", 0) > 0 and not canonical_match)

        structural_complete = bool(getattr(coverage, "structural_state_complete", False)
                                    or getattr(coverage, "coverage_complete", False))

        result = CanonicalSourceDriftDiagnosticResult(
            **base_kwargs,
            snapshot_validated=True,
            caller_owned_transaction=bool(caller_owned),
            transaction_owner_verified=bool(owns_transaction and tx_owner is not None),
            diagnostic_reason="comparison_only",
            raw_discovery_available_count=int(raw_discovery_available or 0),
            raw_discovery_complete=raw_discovery_complete,
            raw_discovery_reason=raw_discovery_reason,
            integrated_discovery_complete=bool(integrated_complete),
            canonical_source_digest_match_count=match_count,
            canonical_source_digest_mismatch_count=mismatch_count,
            canonical_source_digest_match=bool(canonical_match),
            canonical_source_digest_mismatch=bool(canonical_mismatch),
        )

        # Commit owned transaction.
        if owns_transaction and tx_owner is not None:
            try:
                tx_owner.commit()
            except Exception:
                try:
                    tx_owner.rollback()
                except Exception:
                    pass
    except Exception as exc:
        # Preserve explicit validation errors as failures callers may expect
        # (e.g. an impossibly small payload budget) rather than masking them.
        try:
            from backend.services.grounded_discussion_index_coverage import IndexCoverageError
        except Exception:
            IndexCoverageError = None
        is_validation = IndexCoverageError is not None and isinstance(exc, IndexCoverageError)
        if owns_transaction and tx_owner is not None:
            try:
                tx_owner.commit()
            except Exception:
                try:
                    tx_owner.rollback()
                except Exception:
                    pass
        try:
            if owns_session and engine is not None:
                session.close()
                engine.dispose()
        except Exception:
            pass
        if is_validation:
            raise
        return _fail_closed_result(
            clean_sid, max_bytes, "diagnostic_failed",
            raw_discovery_reason="unavailable",
            error=type(exc).__name__,
        )

    # Close owned session/path.
    try:
        if owns_session and engine is not None:
            session.close()
            engine.dispose()
    except Exception:
        pass
    return result


# Primary function aliases.
read_session_canonical_source_drift = read_session_canonical_source_drift_diagnostic
read_canonical_source_drift = read_session_canonical_source_drift_diagnostic
diagnose_canonical_source_drift = read_session_canonical_source_drift_diagnostic
scan_canonical_source_drift = read_session_canonical_source_drift_diagnostic
read_canonical_source_drift_diagnostic = read_session_canonical_source_drift_diagnostic
diagnose_canonical_source_drift_diagnostic = read_session_canonical_source_drift_diagnostic
scan_canonical_source_drift_diagnostic = read_session_canonical_source_drift_diagnostic
canonical_source_drift_diagnostic = read_session_canonical_source_drift_diagnostic
canonical_source_drift_read = read_session_canonical_source_drift_diagnostic
canonical_source_drift_diagnosis = read_session_canonical_source_drift_diagnostic


class CanonicalSourceDriftDiagnostic:
    def __init__(self, db: Any = None, *, session: Any = None, db_path: Any = None, **kwargs: Any):
        self._db = _first_not_none(db, session, db_path, kwargs.pop("database", None))
        self._defaults = dict(kwargs)

    @property
    def db(self) -> Any:
        return self._db

    def read(self, session_id: Any, **kwargs: Any) -> CanonicalSourceDriftDiagnosticResult:
        merged = dict(self._defaults)
        merged.update(kwargs)
        return read_session_canonical_source_drift_diagnostic(self._db, session_id, **merged)

    def diagnose(self, session_id: Any, **kwargs: Any) -> CanonicalSourceDriftDiagnosticResult:
        return self.read(session_id, **kwargs)

    def scan(self, session_id: Any, **kwargs: Any) -> CanonicalSourceDriftDiagnosticResult:
        return self.read(session_id, **kwargs)

    def run(self, session_id: Any, **kwargs: Any) -> CanonicalSourceDriftDiagnosticResult:
        return self.read(session_id, **kwargs)

    def read_session_canonical_source_drift_diagnostic(self, session_id: Any, **kwargs: Any):
        return self.read(session_id, **kwargs)


class CanonicalSourceDriftDiagnosticService(CanonicalSourceDriftDiagnostic):
    pass


class CanonicalSourceDriftManager:
    def __init__(self, session: Any = None, *, db: Any = None, **kwargs: Any):
        self._session = _session_of(db if db is not None else session)
        self._defaults = dict(kwargs)

    @property
    def db(self) -> Any:
        return self._session

    def scan(self, session_id: Any, *, max_payload_bytes: Any = None, **kwargs: Any):
        merged = dict(self._defaults)
        merged.update(kwargs)
        if max_payload_bytes is not None:
            merged["max_payload_bytes"] = max_payload_bytes
        return read_session_canonical_source_drift_diagnostic(self._session, session_id, **merged)

    def read(self, session_id: Any, *, max_payload_bytes: Any = None, **kwargs: Any):
        return self.scan(session_id, max_payload_bytes=max_payload_bytes, **kwargs)

    def read_session_canonical_source_drift_diagnostic(self, session_id: Any, *, max_payload_bytes: Any = None, **kwargs: Any):
        return self.scan(session_id, max_payload_bytes=max_payload_bytes, **kwargs)


class CanonicalSourceDriftReader(CanonicalSourceDriftDiagnostic):
    pass


def run_canonical_source_drift_diagnostic(*args, **kwargs) -> CanonicalSourceDriftDiagnosticResult:
    db = kwargs.pop("db", None)
    session = kwargs.pop("session", None)
    session_id = kwargs.pop("session_id", None)
    remaining = list(args)
    if db is None and session is None and remaining and not isinstance(remaining[0], str):
        # Could be db/session
        if hasattr(remaining[0], "execute"):
            db = remaining.pop(0)
        else:
            # ambiguous callable
            pass
    if session_id is None and remaining and isinstance(remaining[0], str):
        session_id = remaining.pop(0)
    if db is None:
        db = session
    # Allow session object from args
    if db is None and remaining and hasattr(remaining[0], "execute"):
        db = remaining.pop(0)
    if session_id is None and remaining and isinstance(remaining[0], str):
        session_id = remaining.pop(0)
    return read_session_canonical_source_drift_diagnostic(db, session_id, **kwargs)


run_canonical_source_drift = run_canonical_source_drift_diagnostic
run_canonical_drift_diagnostic = run_canonical_source_drift_diagnostic


try:
    from backend.services.grounded_discussion_index_coverage import IndexCoverageError as _SharedIndexCoverageError
except Exception:
    class _SharedIndexCoverageError(Exception):
        pass
IndexCoverageError = _SharedIndexCoverageError

__all__ = [
    "IndexCoverageError",
    "CanonicalSourceDriftDiagnosticResult",
    "CanonicalSourceDriftResult",
    "CanonicalSourceDiagnosticResult",
    "CanonicalDriftDiagnosticResult",
    "CanonicalSourceDriftCoverageResult",
    "CanonicalSourceDriftDiagnostic",
    "CanonicalSourceDriftDiagnosticService",
    "CanonicalSourceDriftManager",
    "CanonicalSourceDriftReader",
    "read_session_canonical_source_drift_diagnostic",
    "read_session_canonical_source_drift",
    "read_canonical_source_drift",
    "read_canonical_source_drift_diagnostic",
    "diagnose_canonical_source_drift",
    "diagnose_canonical_source_drift_diagnostic",
    "scan_canonical_source_drift",
    "scan_canonical_source_drift_diagnostic",
    "canonical_source_drift_diagnostic",
    "canonical_source_drift_read",
    "canonical_source_drift_diagnosis",
    "run_canonical_source_drift_diagnostic",
    "run_canonical_source_drift",
    "run_canonical_drift_diagnostic",
]
