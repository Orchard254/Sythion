"""Default-inactive grounded discussion service adapter for indexed discovery.

This module is intentionally inert at import time.  It reuses the existing
``GroundedDiscussionLinkageService`` for evidence reads and persisted-exchange
linkage, while overriding discovery to use the strict indexed discovery path.
No caller-provided freshness verifier is honored, so unknown source freshness
remains fail-closed (incomplete).
"""

from __future__ import annotations

from typing import Any

from backend.services.grounded_discussion_linkage import (
    GroundedDiscussionLinkageService,
    _first_not_none,
)


class GroundedDiscussionIndexedLinkageService(GroundedDiscussionLinkageService):
    """Adapter: linkage service whose discovery uses strict indexed discovery.

    Discovery never accepts a caller-provided freshness verifier; unknown
    freshness therefore stays fail-closed (incomplete) unless coverage is
    genuinely end-to-end complete without one (e.g. an empty session).
    """

    def __init__(
        self,
        db: Any = None,
        *,
        session: Any = None,
        clock: Any = None,
        discussion_manager: Any = None,
        discovery_max_payload_bytes: Any = None,
        max_payload_bytes: Any = None,
        max_examined_exchanges: Any = None,
        source_freshness_verifier: Any = None,
        **kwargs: Any,
    ) -> None:
        kwargs.pop("source_freshness_verifier", None)
        kwargs.pop("freshness_verifier", None)
        kwargs.pop("verifier", None)
        super().__init__(
            db=db,
            session=session,
            clock=clock,
            discussion_manager=discussion_manager,
            discovery_max_payload_bytes=discovery_max_payload_bytes,
            max_payload_bytes=max_payload_bytes,
            **kwargs,
        )
        self.max_examined_exchanges = _first_not_none(
            max_examined_exchanges,
            kwargs.get("max_examined_exchanges"),
            kwargs.get("max_examined_rows"),
            1000,
        )
        self.source_freshness_verifier = None

    def discover_session_candidates(
        self,
        db: Any = None,
        session_id: Any = None,
        *,
        max_payload_bytes: Any = None,
        source_freshness_verifier: Any = None,
        max_examined_exchanges: Any = None,
        **kwargs: Any,
    ) -> Any:
        kwargs.pop("source_freshness_verifier", None)
        kwargs.pop("freshness_verifier", None)
        kwargs.pop("verifier", None)

        session = self._session_for(db)
        sid = session_id if session_id is not None else kwargs.get("session_id")

        budget = _first_not_none(
            max_payload_bytes,
            kwargs.get("max_payload_bytes"),
            kwargs.get("discovery_max_payload_bytes"),
            self.discovery_max_payload_bytes,
        )
        coverage_budget = _first_not_none(
            kwargs.get("coverage_max_payload_bytes"),
            kwargs.get("coverage_byte_budget"),
            budget,
        )
        examined = _first_not_none(
            max_examined_exchanges,
            kwargs.get("max_examined_exchanges"),
            kwargs.get("max_examined_rows"),
            self.max_examined_exchanges,
        )

        from backend.services.grounded_discussion_indexed_discovery import (
            read_session_indexed_discussion_discovery,
        )

        return read_session_indexed_discussion_discovery(
            session,
            sid,
            discovery_max_payload_bytes=budget,
            coverage_max_payload_bytes=coverage_budget,
            max_examined_exchanges=examined,
        )

__all__ = ["GroundedDiscussionIndexedLinkageService"]
