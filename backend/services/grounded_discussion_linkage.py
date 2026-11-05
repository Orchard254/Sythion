"""Small, optional, server-controlled linkage collaborator.

This module is intentionally inert at import time.  It does not register grounded
tables, import grounded runtime modules eagerly, or provide any client-facing
enabling field.  Contract callers (usually tests) explicitly inject a service
instance or factory.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Mapping, Optional

from sqlalchemy.orm import Session


_DEFAULT_DISCOVERY_MAX_PAYLOAD_BYTES = 1 << 20


@dataclass
class ProvisionalDiscussionLinkage:
    """Explicit caller-owned linkage proposal container (Mapping-compatible)."""

    proposal: Mapping[str, Any]

    def get(self, key: str, default: Any = None) -> Any:
        return self.proposal.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self.proposal[key]

    def keys(self):
        return self.proposal.keys()

    def __contains__(self, key: str) -> bool:
        return key in self.proposal

    def to_mapping(self) -> Mapping[str, Any]:
        return self.proposal


def _session_of(db: Any) -> Session:
    if db is None:
        raise ValueError("A database session is required")
    if isinstance(db, Session):
        return db
    if isinstance(db, str):
        raise ValueError("A database session is required")
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
    if hasattr(db, "execute") and hasattr(db, "flush"):
        return db  # duck-typed
    raise ValueError("A database session is required")


def _resolve_db(value: Any, *, fallback: Any = None) -> Session:
    if value is not None:
        try:
            return _session_of(value)
        except Exception:
            pass
    if fallback is not None:
        return _session_of(fallback)
    raise ValueError("A database session is required")


def _first_not_none(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


class GroundedDiscussionLinkageService:
    """Tiny collaborator used for opt-in, server-owned linkage work."""

    def __init__(
        self,
        db: Any = None,
        *,
        session: Any = None,
        clock: Callable[[], datetime] | None = None,
        discussion_manager: Any = None,
        discovery_max_payload_bytes: int | None = None,
        max_payload_bytes: int | None = None,
        **kwargs: Any,
    ) -> None:
        self._db = _first_not_none(db, session, kwargs.get("database"))
        self._clock = clock or (lambda: __import__("datetime").datetime.utcnow())
        self._manager = discussion_manager
        self.discovery_max_payload_bytes = _first_not_none(
            discovery_max_payload_bytes,
            max_payload_bytes,
            kwargs.get("max_payload_bytes"),
            _DEFAULT_DISCOVERY_MAX_PAYLOAD_BYTES,
        )

    @property
    def db(self) -> Session:
        return _session_of(self._db) if self._db is not None else None

    def _manager_for(self, db: Any) -> Any:
        if self._manager is not None:
            # Allow a caller to supply a ready manager.
            manager = self._manager
            if hasattr(manager, "db"):
                try:
                    manager.db  # ensure accessible
                except Exception:
                    pass
            # If the manager exposes a bindable transactional db, keep caller db.
            return manager
        from backend.services.grounded_discussions import GroundedDiscussionManager

        return GroundedDiscussionManager(db, clock=self._clock)

    def _session_for(self, db: Any = None, session_id: Any = None, *, fallback: Any = None) -> Session:
        if db is None and fallback is not None:
            db = fallback
        session = db
        if session is None:
            session = self._db
        try:
            return _session_of(session)
        except Exception:
            return _resolve_db(session, fallback=self._db)

    # ---------------------------------------------------------------- discovery
    def discover_session_candidates(self, db: Any = None, session_id: Any = None, *, max_payload_bytes: Any = None, **kwargs: Any) -> Any:
        session = self._session_for(db)
        sid = session_id if session_id is not None else kwargs.get("session_id")
        budget = _first_not_none(max_payload_bytes, kwargs.get("max_payload_bytes"), self.discovery_max_payload_bytes)
        from backend.services.grounded_discussion_discovery import read_session_discussion_discovery

        return read_session_discussion_discovery(session, sid, max_payload_bytes=budget)

    # aliases
    def discover(self, *args, **kwargs):
        # Accept (db, session_id, ...) or (session_id, ...) with db=self._db
        if len(args) >= 2 and not isinstance(args[0], str):
            return self.discover_session_candidates(args[0], args[1], **kwargs)
        if len(args) == 1 and isinstance(args[0], str):
            return self.discover_session_candidates(self._db, args[0], **kwargs)
        return self.discover_session_candidates(**kwargs)

    def make_evidence_reader(self, db: Any = None, session_id: Any = None, **kwargs: Any) -> Callable[[Any], Any]:
        session = self._session_for(db)
        sid = session_id if session_id is not None else kwargs.get("session_id")
        if sid is None:
            raise ValueError("Exact session identity is required")

        def _reader(selected_ids: Any) -> Any:
            from backend.services.grounded_discussion_evidence import read_selected_discussion_evidence

            return read_selected_discussion_evidence(session, sid, selected_ids)

        return _reader

    def read_discussion_evidence(self, db: Any = None, session_id: Any = None, selected_ids: Any = None, **kwargs: Any) -> Any:
        reader = self.make_evidence_reader(db, session_id, **kwargs)
        return reader(selected_ids if selected_ids is not None else kwargs.get("discussion_ids"))

    # ---------------------------------------------------------------- linkage
    def _extract_persisted_identity(self, persisted: Any, kwargs: dict) -> dict:
        exchange_id = _first_not_none(
            getattr(persisted, "exchange_id", None),
            getattr(persisted, "conversation_id", None),
            kwargs.get("exchange_id"),
            kwargs.get("conversation_id"),
            (persisted.get("exchange_id") if isinstance(persisted, Mapping) else None),
        )
        user_turn_id = _first_not_none(
            getattr(persisted, "user_turn_id", None),
            (persisted.get("user_turn_id") if isinstance(persisted, Mapping) else None),
            kwargs.get("user_turn_id"),
        )
        assistant_turn_id = _first_not_none(
            getattr(persisted, "assistant_turn_id", None),
            (persisted.get("assistant_turn_id") if isinstance(persisted, Mapping) else None),
            kwargs.get("assistant_turn_id"),
        )
        # turn_ids tuple fallback
        turn_ids = None
        if isinstance(persisted, Mapping):
            turn_ids = persisted.get("turn_ids")
        else:
            turn_ids = getattr(persisted, "turn_ids", None)
        if (user_turn_id is None or assistant_turn_id is None) and turn_ids:
            try:
                normalized = [int(x) for x in turn_ids]
                if len(normalized) >= 2:
                    if user_turn_id is None:
                        user_turn_id = normalized[0]
                    if assistant_turn_id is None:
                        assistant_turn_id = normalized[1]
            except (TypeError, ValueError):
                pass
        session_id = _first_not_none(
            getattr(persisted, "session_id", None),
            (persisted.get("session_id") if isinstance(persisted, Mapping) else None),
            kwargs.get("session_id"),
        )
        task_id = _first_not_none(
            getattr(persisted, "task_id", None),
            (persisted.get("task_id") if isinstance(persisted, Mapping) else None),
            kwargs.get("task_id"),
        )
        if isinstance(exchange_id, str):
            exchange_id = exchange_id.strip() or None
        if isinstance(session_id, str):
            session_id = session_id.strip() or None
        if isinstance(task_id, str):
            task_id = task_id.strip() or None
        try:
            if user_turn_id is not None:
                user_turn_id = int(user_turn_id)
            if assistant_turn_id is not None:
                assistant_turn_id = int(assistant_turn_id)
        except (TypeError, ValueError):
            raise ValueError("Exact exchange turn identities are required")
        if isinstance(exchange_id, bool) or not isinstance(exchange_id, str) or not exchange_id:
            raise ValueError("Canonical exchange identity is required")
        if isinstance(session_id, bool) or not isinstance(session_id, str) or not session_id:
            raise ValueError("Canonical session identity is required")
        if isinstance(user_turn_id, bool) or not isinstance(user_turn_id, int) or user_turn_id < 1:
            raise ValueError("Canonical user turn identity is required")
        if isinstance(assistant_turn_id, bool) or not isinstance(assistant_turn_id, int) or assistant_turn_id < 1:
            raise ValueError("Canonical assistant turn identity is required")
        return {
            "session_id": session_id,
            "exchange_id": exchange_id,
            "user_turn_id": user_turn_id,
            "assistant_turn_id": assistant_turn_id,
            "task_id": task_id,
        }

    def _normalize_linkage_input(self, linkage: Any) -> Optional[Mapping[str, Any]]:
        if linkage is None:
            return None
        if isinstance(linkage, ProvisionalDiscussionLinkage):
            return linkage.to_mapping()
        if isinstance(linkage, Mapping):
            # Accept {"validated_proposal": {...}} or {"proposal": {...}}.
            for wrapper in ("validated_proposal", "proposal", "validated_discussion_linkage", "discussion_linkage"):
                inner = linkage.get(wrapper)
                if isinstance(inner, Mapping):
                    merged = dict(linkage)
                    merged.pop(wrapper, None)
                    merged.update(inner)
                    linkage = merged
                    break
            # If an object with first non-mapping value still mapping, return.
            return linkage
        # Allow object exposing .proposal
        proposal = getattr(linkage, "proposal", None)
        if isinstance(proposal, Mapping):
            return proposal
        validated = getattr(linkage, "validated_proposal", None)
        if isinstance(validated, Mapping):
            return validated
        mapping = getattr(linkage, "to_mapping", None)
        if callable(mapping):
            result = mapping()
            if isinstance(result, Mapping):
                return result
        raise ValueError("Explicit linkage input must be a mapping or validated proposal")

    def _apply_correction_events(self, manager: Any, discussion_id: Any, session_id: Any, ids: dict, proposal: Mapping[str, Any]) -> None:
        lp = proposal.get("provenance") or proposal.get("link_provenance") or proposal.get("label_provenance")
        for kind, method_name, text_key in (
            ("correction", "add_correction", "correction_text"),
            ("correction", "add_event", None),
            ("conflict", "add_conflict", "conflict_text"),
            ("link_repair", "add_link_repair", "repair_text"),
        ):
            text_value = proposal.get(text_key) if text_key else None
            if not isinstance(text_value, str) or not text_value.strip():
                continue
            method = getattr(manager, method_name, None)
            if not callable(method):
                continue
            try:
                method(
                    discussion_id,
                    session_id,
                    target_exchange_id=ids["exchange_id"],
                    target_user_turn_id=ids["user_turn_id"],
                    target_assistant_turn_id=ids["assistant_turn_id"],
                    text=text_value if text_key == "correction_text" else text_value,
                    correction_text=text_value if text_key else None,
                    conflict_text=text_value if text_key == "conflict_text" else None,
                    repair_text=text_value if text_key == "repair_text" else None,
                    provenance=lp,
                )
                return
            except TypeError:
                # Be tolerant of slightly different method signatures.
                try:
                    method(
                        discussion_id=discussion_id,
                        session_id=session_id,
                        target_exchange_id=ids["exchange_id"],
                        target_user_turn_id=ids["user_turn_id"],
                        target_assistant_turn_id=ids["assistant_turn_id"],
                        correction_text=text_value if text_key == "correction_text" else None,
                        conflict_text=text_value if text_key == "conflict_text" else None,
                        repair_text=text_value if text_key == "repair_text" else None,
                        provenance=lp,
                    )
                except Exception:
                    raise
            except Exception:
                raise

    def link_persisted_exchange(self, *args: Any, db: Any = None, session_id: Any = None, exchange_id: Any = None, persisted: Any = None, linkage: Any = None, validated_proposal: Any = None, proposal: Any = None, discussion_linkage: Any = None, validated_discussion_linkage: Any = None, **kwargs: Any) -> Any:
        # Allow first positional to be db or persisted identity.
        if args:
            if len(args) == 1:
                if isinstance(args[0], str):
                    if session_id is None:
                        session_id = args[0]
                else:
                    if persisted is None and not isinstance(args[0], str):
                        persisted = args[0]
            elif len(args) >= 2:
                if persisted is None:
                    persisted = args[0]
                if session_id is None and isinstance(args[1], str):
                    session_id = args[1]
        session = self._session_for(db if db is not None else kwargs.get("db"), fallback=self._db)
        ids = self._extract_persisted_identity(persisted, {
            "exchange_id": exchange_id,
            "session_id": session_id,
            "user_turn_id": kwargs.get("user_turn_id"),
            "assistant_turn_id": kwargs.get("assistant_turn_id"),
            "task_id": kwargs.get("task_id"),
        })
        if session_id is None:
            session_id = ids["session_id"]
        sid = _clean_id(session_id, "session identity")
        ids["session_id"] = sid

        explicit = _first_not_none(
            linkage,
            validated_proposal,
            proposal,
            discussion_linkage,
            validated_discussion_linkage,
            kwargs.get("grounded_discussion_linkage"),
            kwargs.get("validated_discussion_linkage"),
            kwargs.get("provisional_linkage"),
        )
        explicit = self._normalize_linkage_input(explicit)
        if explicit is None:
            return None

        # Strict server-owned proposal contract: reject unknown fields instead of
        # silently stripping them.
        _proposal_keys = {
            "discussion_id", "session_id", "target_exchange_id", "target_user_turn_id",
            "target_assistant_turn_id", "correction_text", "conflict_text", "repair_text",
            "label", "provenance", "session",
        }
        _service_config_keys = {
            "label_provenance", "link_provenance", "label_text", "members",
            "member_exchanges", "validated_proposal", "proposal",
        }
        unknown = set(explicit.keys()) - (_proposal_keys | _service_config_keys)
        if unknown:
            raise ValueError("Unknown field in validated discussion proposal")

        ids["session_id"] = sid
        tid = ids["task_id"]
        if tid is not None:
            # Keep exact string.
            tid = str(tid)

        manager = self._manager_for(session)

        # Provenance validations are delegated to the grounded manager's
        # validated_proposal handling.
        lp = _clean_provenance(explicit.get("link_provenance", explicit.get("provenance", None)))
        label_prov = _clean_provenance(explicit.get("label_provenance", explicit.get("provenance", None)))
        allowed_proposal_keys = {
            "discussion_id", "session_id", "target_exchange_id", "target_user_turn_id",
            "target_assistant_turn_id", "correction_text", "conflict_text", "repair_text",
            "label", "provenance", "session",
        }
        sanitized_proposal = {k: v for k, v in explicit.items() if k in allowed_proposal_keys}
        # Canonical identities from the flushed exchange take precedence over any
        # stale or client-supplied proposal values.
        sanitized_proposal["session_id"] = sid
        sanitized_proposal["target_exchange_id"] = ids["exchange_id"]
        sanitized_proposal["target_user_turn_id"] = ids["user_turn_id"]
        sanitized_proposal["target_assistant_turn_id"] = ids["assistant_turn_id"]
        discussion_id = explicit.get("discussion_id")
        label = explicit.get("label", explicit.get("label_text"))
        if label is not None:
            sanitized_proposal.setdefault("label", label)
        if discussion_id is not None:
            sanitized_proposal.setdefault("discussion_id", discussion_id)

        try:
            if discussion_id is not None:
                clean_discussion = _clean_id(discussion_id, "discussion identity")
                if explicit.get("validated_proposal") is not None and not isinstance(explicit.get("validated_proposal"), Mapping):
                    raise ValueError("Validated discussion proposal must be a mapping")
                result = manager.link_exchange(
                    clean_discussion,
                    sid,
                    ids["exchange_id"],
                    ids["user_turn_id"],
                    ids["assistant_turn_id"],
                    link_provenance=lp,
                    validated_proposal=sanitized_proposal,
                )
                if explicit.get("correction_text") or explicit.get("conflict_text") or explicit.get("repair_text"):
                    self._apply_correction_events(manager, clean_discussion, sid, ids, explicit)
                return result

            if label is not None:
                clean_label = _clean_label(label)
                members = explicit.get("member_exchanges")
                if members is None:
                    members = explicit.get("members")
                if members is None:
                    members = [{
                        "exchange_id": ids["exchange_id"],
                        "user_turn_id": ids["user_turn_id"],
                        "assistant_turn_id": ids["assistant_turn_id"],
                    }]
                result = manager.create_discussion(
                    session_id=sid,
                    label=clean_label,
                    label_provenance=label_prov,
                    task_id=tid,
                    member_exchanges=members,
                    link_provenance=lp,
                    validated_proposal=sanitized_proposal,
                )
                if explicit.get("correction_text") or explicit.get("conflict_text") or explicit.get("repair_text"):
                    from backend.services.grounded_discussions import _row_to_discussion
                    try:
                        did = getattr(result, "discussion_id", None)
                    except Exception:
                        did = None
                    if did is not None:
                        self._apply_correction_events(manager, did, sid, ids, explicit)
                return result

            raise ValueError("Explicit linkage proposal must contain discussion_id or label")
        except Exception:
            raise

    # aliases
    def link(self, *args, **kwargs):
        return self.link_persisted_exchange(*args, **kwargs)

def _clean_id(value: Any, field_name: str) -> str:
    if isinstance(value, bool) or not isinstance(value, str):
        raise ValueError(f"Exact {field_name} is required")
    candidate = value.strip()
    if not candidate:
        raise ValueError(f"Exact {field_name} is required")
    if len(candidate) > 256:
        raise ValueError(f"{field_name} exceeds maximum length")
    return candidate


def _clean_label(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("A non-empty discussion label is required")
    return value.strip()


def _clean_provenance(value: Any) -> str:
    if value is None:
        return "model_inferred_unverified"
    if not isinstance(value, str):
        raise ValueError("Discussion provenance must be a string")
    candidate = value.strip()
    allowed = {
        "model_inferred_unverified",
        "explicit_validated_proposal",
        "human_stated",
        "legacy_unverified",
    }
    if candidate not in allowed:
        raise ValueError("Unsupported discussion provenance")
    return candidate


# Avoid accidental star-import leak; module exposes explicit classes above.


__all__ = [
    "GroundedDiscussionLinkageService",
]
