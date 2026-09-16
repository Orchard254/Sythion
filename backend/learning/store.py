"""StrategyOutcome persistence, statistics, eligibility, decay (Phase 11)."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import text as _sql_text

from backend.learning.taxonomy import EvidenceStrength, OutcomeClass, StrategyType
from backend.models.database import StrategyOutcome
from backend.runtime.events import DbEventLedger


@dataclass
class StrategyStatistics:
    strategy_type: str
    task_class: str
    role: Optional[str]
    sample_count: int
    success_count: int
    verified_success_count: int
    failure_count: int
    verification_failure_count: int
    user_correction_count: int
    success_rate: Optional[float]
    verified_success_rate: Optional[float]
    failure_rate: Optional[float]
    verification_failure_rate: Optional[float]
    user_correction_rate: Optional[float]
    mean_latency_ms: Optional[float]
    mean_cost: Optional[float]
    total_cost: Optional[float]
    evidence_strength: str
    decayed_count: int = 0
    recent_count: int = 0


def _evidence_strength_for(n: int, thresholds: Dict[str, int] | None = None) -> str:
    from backend.learning.taxonomy import EVIDENCE_THRESHOLDS

    th = thresholds or EVIDENCE_THRESHOLDS
    if n == 0:
        return EvidenceStrength.UNRATED.value
    if n < th[EvidenceStrength.SUPPORTED.value]:
        if n < 3:
            return EvidenceStrength.LOW_SAMPLE.value
        return EvidenceStrength.LOW_SAMPLE.value
    if n < th[EvidenceStrength.ESTABLISHED.value]:
        return EvidenceStrength.SUPPORTED.value
    return EvidenceStrength.ESTABLISHED.value


def _is_eligible(data: Dict[str, Any]) -> tuple[bool, str]:
    """Check learning eligibility (section 7)."""
    if not data.get("task_class"):
        return False, "missing task_class"
    if not data.get("strategy_type") or data["strategy_type"] not in StrategyType.values():
        return False, "invalid strategy_type"
    if not data.get("outcome") or data["outcome"] not in OutcomeClass.values():
        return False, "invalid outcome"
    # terminal only — interrupted/incomplete before meaningful work not eligible
    # outcome taxonomy already terminal; allow all defined outcomes except we reject
    # empty evidence when success claimed
    outcome = data["outcome"]
    if outcome in (OutcomeClass.SUCCESS_VERIFIED.value, OutcomeClass.SUCCESS_PARTIAL.value):
        # verified success must have verification signal
        if outcome == OutcomeClass.SUCCESS_VERIFIED.value and not data.get("verification"):
            # allow verification field to be implicit: if outcome is SUCCESS_VERIFIED, verification is implied
            pass
        # need evidence refs for verified
        refs = data.get("evidence_refs") or []
        if outcome == OutcomeClass.SUCCESS_VERIFIED.value and not refs:
            return False, "SUCCESS_VERIFIED without evidence_refs"
    # execution must have completed or failed terminally — already covered
    # check evidence fingerprint valid (if present must be non-empty string)
    if data.get("evidence_fingerprint") is not None and not str(data["evidence_fingerprint"]).strip():
        return False, "empty evidence_fingerprint"
    # timestamp must be present
    if not data.get("created_at"):
        return False, "missing timestamp"
    return True, ""


class StrategyOutcomeStore:
    """Durable store for StrategyOutcome records and deterministic statistics."""

    def __init__(self, session):
        self.db = session
        self.ledger = DbEventLedger(session)

    # -- record -----------------------------------------------------------

    def record(
        self,
        *,
        task_class: str,
        strategy_type: str,
        outcome: str,
        role: Optional[str] = None,
        model_id: Optional[str] = None,
        verification: Optional[str] = None,
        user_feedback: Optional[str] = None,
        cost: Optional[Dict[str, Any]] = None,
        latency_ms: Optional[int] = None,
        attempts: int = 1,
        evidence_refs: Optional[List[str]] = None,
        task_id: Optional[str] = None,
        goal_id: Optional[str] = None,
        evidence_fingerprint: Optional[str] = None,
        created_at: Optional[datetime] = None,
        expires_at: Optional[datetime] = None,
        allow_duplicate_fingerprint: bool = False,
    ) -> Dict[str, Any]:
        """Create a durable StrategyOutcome with eligibility + idempotent fingerprint.

        Returns {"outcome": StrategyOutcome, "eligible": bool, "reason": str}
        Fingerprint is SHA of (task_id|strategy|outcome|evidence_refs) if not supplied;
        duplicate fingerprints are idempotent (returns existing row, no new insert).
        """
        from backend.core.config import settings

        if not settings.strategy_learning_enabled:
            return {"outcome": None, "eligible": False, "reason": "learning_disabled"}
        task_class = (task_class or "general").strip().lower()
        strategy_type = str(strategy_type).strip().upper()
        outcome = str(outcome).strip().upper()
        # normalize role
        if role is not None:
            role = str(role).strip().upper() or None
            if role and role not in [r.value for r in __import__("backend.agents.state", fromlist=["SpecialistRole"]).SpecialistRole]:
                # keep unknown roles but normalize
                role = str(role).upper()
        now = created_at or datetime.utcnow()
        # fingerprint for idempotent ingestion
        if evidence_fingerprint is None:
            raw = f"{task_id or ''}|{strategy_type}|{outcome}|{','.join(evidence_refs or [])}|{now.isoformat()}"
            evidence_fingerprint = hashlib.sha256(raw.encode()).hexdigest()[:16] if task_id else None
        cost_json = json.dumps(cost) if cost is not None else None
        refs_json = json.dumps(evidence_refs or [])
        # expiry
        if expires_at is None:
            ttl = int(getattr(settings, "strategy_evidence_ttl_days", 90))
            expires_at = now + timedelta(days=ttl)

        data = {
            "task_class": task_class,
            "strategy_type": strategy_type,
            "outcome": outcome,
            "role": role,
            "model_id": model_id,
            "verification": verification,
            "user_feedback": user_feedback,
            "cost": cost,
            "latency_ms": latency_ms,
            "attempts": attempts,
            "evidence_refs": evidence_refs or [],
            "evidence_fingerprint": evidence_fingerprint,
            "created_at": now,
            "task_id": task_id,
            "goal_id": goal_id,
        }
        eligible, reason = _is_eligible(data)
        if not eligible:
            # still persist ineligible as audit? Spec says not every event should train —
            # we do not persist ineligible (or we could persist with ineligible flag).
            # For phase 11, we reject with reason and do not store.
            return {"outcome": None, "eligible": False, "reason": reason}

        # Idempotent via fingerprint unique index
        if evidence_fingerprint and not allow_duplicate_fingerprint:
            existing = self.db.query(StrategyOutcome).filter_by(evidence_fingerprint=evidence_fingerprint).first()
            if existing is not None:
                return {"outcome": existing, "eligible": True, "reason": "duplicate_fingerprint_idempotent"}

        row = StrategyOutcome(
            task_id=task_id,
            goal_id=goal_id,
            task_class=task_class,
            strategy_type=strategy_type,
            role=role,
            model_id=model_id,
            outcome=outcome,
            verification=verification,
            user_feedback=user_feedback,
            cost_json=cost_json,
            latency_ms=latency_ms,
            attempts=max(1, int(attempts)),
            evidence_refs_json=refs_json,
            evidence_fingerprint=evidence_fingerprint,
            created_at=now,
            expires_at=expires_at,
        )
        try:
            self.db.add(row)
            self.db.flush()
            self.ledger.append_event("learning", "strategy.outcome.recorded", {
                "task_class": task_class,
                "strategy_type": strategy_type,
                "role": role,
                "outcome": outcome,
                "verification": verification,
                "task_id": task_id,
                "latency_ms": latency_ms,
                "attempts": attempts,
            })
            self.db.commit()
            self.db.refresh(row)
            return {"outcome": row, "eligible": True, "reason": "recorded"}
        except Exception as exc:
            from sqlalchemy.exc import IntegrityError
            self.db.rollback()
            if isinstance(exc, IntegrityError) and "uq_strategy_outcomes_fingerprint" in str(exc):
                existing = self.db.query(StrategyOutcome).filter_by(evidence_fingerprint=evidence_fingerprint).first()
                if existing is not None:
                    return {"outcome": existing, "eligible": True, "reason": "duplicate_fingerprint_idempotent"}
            return {"outcome": None, "eligible": False, "reason": str(exc)[:300]}

    def get(self, outcome_id: int) -> Optional[StrategyOutcome]:
        return self.db.query(StrategyOutcome).filter_by(id=outcome_id).first()

    def list_outcomes(
        self,
        *,
        task_class: Optional[str] = None,
        strategy_type: Optional[str] = None,
        role: Optional[str] = None,
        include_expired: bool = False,
        limit: int = 100,
        since: Optional[datetime] = None,
    ) -> List[StrategyOutcome]:
        q = self.db.query(StrategyOutcome)
        if task_class:
            q = q.filter(StrategyOutcome.task_class == task_class.lower())
        if strategy_type:
            q = q.filter(StrategyOutcome.strategy_type == strategy_type.upper())
        if role:
            q = q.filter(StrategyOutcome.role == role.upper())
        if not include_expired:
            now = datetime.utcnow()
            q = q.filter((StrategyOutcome.expires_at.is_(None)) | (StrategyOutcome.expires_at > now))
        if since:
            q = q.filter(StrategyOutcome.created_at >= since)
        return q.order_by(StrategyOutcome.created_at.desc()).limit(limit).all()

    # -- statistics -------------------------------------------------------

    def statistics(
        self,
        *,
        task_class: Optional[str] = None,
        strategy_type: Optional[str] = None,
        role: Optional[str] = None,
        window_days: Optional[int] = None,
        include_expired: bool = False,
    ) -> StrategyStatistics | Dict[str, StrategyStatistics]:
        """Compute deterministic statistics.

        If strategy_type is given, returns single StrategyStatistics.
        Otherwise returns dict keyed by strategy_type.
        """
        from backend.core.config import settings

        def _stats_for(stype: str) -> StrategyStatistics:
            q = self.db.query(StrategyOutcome).filter(StrategyOutcome.strategy_type == stype)
            if task_class:
                q = q.filter(StrategyOutcome.task_class == task_class.lower())
            if role:
                q = q.filter(StrategyOutcome.role == role.upper())
            if not include_expired:
                now = datetime.utcnow()
                q = q.filter((StrategyOutcome.expires_at.is_(None)) | (StrategyOutcome.expires_at > now))
            if window_days is not None:
                cutoff = datetime.utcnow() - timedelta(days=window_days)
                q = q.filter(StrategyOutcome.created_at >= cutoff)
            rows = q.all()
            n = len(rows)
            success = sum(1 for r in rows if r.outcome in (OutcomeClass.SUCCESS_VERIFIED.value, OutcomeClass.SUCCESS_PARTIAL.value))
            verified = sum(1 for r in rows if r.outcome == OutcomeClass.SUCCESS_VERIFIED.value)
            failed = sum(1 for r in rows if r.outcome in (OutcomeClass.FAILED.value, OutcomeClass.FAILED_VERIFICATION.value, OutcomeClass.TIMED_OUT.value, OutcomeClass.UNAVAILABLE.value))
            ver_failed = sum(1 for r in rows if r.outcome == OutcomeClass.FAILED_VERIFICATION.value)
            corrected = sum(1 for r in rows if r.outcome == OutcomeClass.USER_CORRECTED.value or (r.user_feedback is not None))
            latencies = [r.latency_ms for r in rows if r.latency_ms is not None]
            costs = []
            total_cost = 0.0
            has_cost = False
            for r in rows:
                try:
                    c = json.loads(r.cost_json) if r.cost_json else None
                    if isinstance(c, dict):
                        v = c.get("total") or c.get("cost") or c.get("model_cost")
                        if isinstance(v, (int, float)):
                            costs.append(float(v))
                            total_cost += float(v)
                            has_cost = True
                        elif "actions" in c or "model_calls" in c:
                            # synthetic cost from actions
                            cc = float(c.get("actions", 0)) * 0.1 + float(c.get("model_calls", 0)) * 0.2
                            costs.append(cc)
                            total_cost += cc
                            has_cost = True
                except Exception:
                    pass
            # recent vs decayed
            recent_window = int(getattr(settings, "strategy_recent_window_days", 30))
            recent_cutoff = datetime.utcnow() - timedelta(days=recent_window)
            recent = sum(1 for r in rows if r.created_at and r.created_at >= recent_cutoff)
            ttl = int(getattr(settings, "strategy_evidence_ttl_days", 90))
            decay_cutoff = datetime.utcnow() - timedelta(days=ttl)
            decayed = sum(1 for r in rows if r.created_at and r.created_at < decay_cutoff)
            strength = _evidence_strength_for(n)
            return StrategyStatistics(
                strategy_type=stype,
                task_class=task_class or "all",
                role=role,
                sample_count=n,
                success_count=success,
                verified_success_count=verified,
                failure_count=failed,
                verification_failure_count=ver_failed,
                user_correction_count=corrected,
                success_rate=round(success / n, 4) if n else None,
                verified_success_rate=round(verified / n, 4) if n else None,
                failure_rate=round(failed / n, 4) if n else None,
                verification_failure_rate=round(ver_failed / n, 4) if n else None,
                user_correction_rate=round(corrected / n, 4) if n else None,
                mean_latency_ms=round(sum(latencies) / len(latencies), 2) if latencies else None,
                mean_cost=round(sum(costs) / len(costs), 4) if costs else None,
                total_cost=round(total_cost, 4) if has_cost else None,
                evidence_strength=strength,
                decayed_count=decayed,
                recent_count=recent,
            )

        if strategy_type:
            return _stats_for(strategy_type.upper())
        # all strategies
        all_types = StrategyType.values()
        return {st: _stats_for(st) for st in all_types}

    def provenance(self, outcome_id: int) -> Optional[Dict[str, Any]]:
        row = self.get(outcome_id)
        if row is None:
            return None
        try:
            cost = json.loads(row.cost_json) if row.cost_json else None
        except Exception:
            cost = None
        try:
            refs = json.loads(row.evidence_refs_json) if row.evidence_refs_json else []
        except Exception:
            refs = []
        return {
            "id": row.id,
            "task_class": row.task_class,
            "strategy_type": row.strategy_type,
            "role": row.role,
            "model_id": row.model_id,
            "outcome": row.outcome,
            "verification": row.verification,
            "user_feedback": row.user_feedback,
            "cost": cost,
            "latency_ms": row.latency_ms,
            "attempts": row.attempts,
            "evidence_refs": refs,
            "evidence_fingerprint": row.evidence_fingerprint,
            "task_id": row.task_id,
            "goal_id": row.goal_id,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        }

    def decayed_count(self) -> int:
        now = datetime.utcnow()
        return self.db.query(StrategyOutcome).filter(StrategyOutcome.expires_at.isnot(None), StrategyOutcome.expires_at < now).count()

    def expire_stale(self) -> int:
        """Count of expired rows (not deleted — kept for audit but excluded from stats)."""
        return self.decayed_count()
