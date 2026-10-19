"""Controlled experiment infrastructure (Phase 11, section 27)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from backend.models.database import StrategyExperiment


class ExperimentService:
    """Minimal controlled experiment support — owner-approved or low-risk auto only."""

    def __init__(self, session):
        self.db = session

    def create(
        self,
        *,
        strategy_a: str,
        strategy_b: str,
        task_class: str,
        constraints: Optional[Dict[str, Any]] = None,
        sample_limit: int = 10,
        risk_limit: str = "LOW",
    ) -> StrategyExperiment:
        exp = StrategyExperiment(
            id=str(uuid.uuid4()),
            strategy_a=strategy_a,
            strategy_b=str(strategy_b),
            task_class=str(task_class),
            constraints_json=json.dumps(constraints or {}),
            sample_limit=max(1, int(sample_limit)),
            risk_limit=str(risk_limit).upper(),
            status="DRAFT",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        self.db.add(exp)
        self.db.commit()
        self.db.refresh(exp)
        return exp

    def activate(self, exp_id: str) -> Optional[StrategyExperiment]:
        exp = self.db.query(StrategyExperiment).filter_by(id=exp_id).first()
        if exp is None or exp.status != "DRAFT":
            return None
        # only low-risk auto experiments allowed without owner approval
        if exp.risk_limit not in ("LOW", "NONE"):
            return None
        exp.status = "ACTIVE"
        exp.updated_at = datetime.utcnow()
        self.db.commit()
        return exp

    def complete(self, exp_id: str, outcome: Dict[str, Any]) -> Optional[StrategyExperiment]:
        exp = self.db.query(StrategyExperiment).filter_by(id=exp_id).first()
        if exp is None or exp.status != "ACTIVE":
            return None
        exp.outcome_json = json.dumps(outcome)
        exp.status = "COMPLETED"
        exp.updated_at = datetime.utcnow()
        self.db.commit()
        return exp

    def get(self, exp_id: str) -> Optional[StrategyExperiment]:
        return self.db.query(StrategyExperiment).filter_by(id=exp_id).first()
