"""Opt-in partial (read-only limited attributed) discussion recall linkage.

The service resolves raw partial recall only when the server-derived snapshot
says it is eligible; otherwise it stays on the strict indexed discovery path.
The route keeps *every* request read-only when this service is used.

Raw vs. strict:
    * ``raw_discovery`` from the partial snapshot is a limited, freshness
      unverified projection.
    * Eligibility is server-derived from physical snapshot validation,
      untruncated discovery, complete index coverage, and canonical source
      digest agreement.
    * Ineligible cases use strict indexed discovery; completeness is never
      fabricated.
"""

from __future__ import annotations

from typing import Any

from backend.services.grounded_discussion_indexed_linkage import (
    GroundedDiscussionIndexedLinkageService,
)
from backend.services.grounded_discussion_linkage import _first_not_none
from backend.services.partial_discussion_recall_snapshot import (
    read_session_partial_discussion_recall_snapshot,
)


class PartialDiscussionRecallLinkageService(GroundedDiscussionIndexedLinkageService):
    """Linkage adapter that may return raw partial recall when eligible."""

    def make_evidence_reader(self, db: Any = None, session_id: Any = None, **kwargs: Any):
        session = self._session_for(db)
        sid = session_id if session_id is not None else kwargs.get("session_id")
        if sid is None:
            raise ValueError("Exact session identity is required")

        def read(selected_ids: Any) -> Any:
            from backend.services.grounded_discussion_evidence import read_selected_discussion_evidence

            return read_selected_discussion_evidence(
                session, sid, selected_ids, source_pair_readback=True
            )

        return read

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
            max_examined_exchanges=max_examined_exchanges,
            source_freshness_verifier=None,
            **kwargs,
        )

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

        snapshot = None
        try:
            snapshot = read_session_partial_discussion_recall_snapshot(
                session,
                sid,
                max_payload_bytes=budget,
                max_discovery_payload_bytes=budget,
                coverage_max_payload_bytes=coverage_budget,
                max_examined_exchanges=examined,
            )
        except Exception:
            snapshot = None

        eligible = bool(
            snapshot is not None
            and getattr(snapshot, "recall_eligible", False) is True
            and getattr(snapshot, "raw_discovery", None) is not None
        )
        if eligible:
            return snapshot.raw_discovery

        return super().discover_session_candidates(
            db,
            session_id,
            max_payload_bytes=budget,
            source_freshness_verifier=None,
            max_examined_exchanges=examined,
            **kwargs,
        )
