"""Phase 12/12.5 — PlanningOutcome persistence, metrics, promotion, shadow, counterfactuals."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from backend.learning.planning_taxonomy import (
    PlanningStrategy,
    ReplanTrigger,
    PlanningPolicyStatus,
    PlanningProvenance,
)
from backend.models.database import PlanningOutcome, PlanningPolicy, PlanningShadowDecision, PlanningCounterfactual
from backend.runtime.events import DbEventLedger


def _planning_strength(n: int) -> str:
    from backend.learning.taxonomy import EvidenceStrength, EVIDENCE_THRESHOLDS
    th = EVIDENCE_THRESHOLDS
    if n == 0:
        return EvidenceStrength.UNRATED.value
    if n < th[EvidenceStrength.SUPPORTED.value]:
        return EvidenceStrength.LOW_SAMPLE.value
    if n < th[EvidenceStrength.ESTABLISHED.value]:
        return EvidenceStrength.SUPPORTED.value
    return EvidenceStrength.ESTABLISHED.value


@dataclass
class PlanningStatistics:
    planning_strategy: str
    task_class: str
    sample_count: int
    success_count: int
    verified_success_count: int
    failure_count: int
    replans: int
    retries: int
    success_rate: Optional[float]
    verified_success_rate: Optional[float]
    failure_rate: Optional[float]
    mean_latency_ms: Optional[float]
    mean_cost: Optional[float]
    total_cost: Optional[float]
    evidence_strength: str
    recent_count: int
    decayed_count: int
    retry_success_rate: Optional[float] = None
    replan_success_rate: Optional[float] = None
    # 12.5 real vs synthetic split
    real_sample_count: int = 0
    synthetic_sample_count: int = 0
    real_verified_success_count: int = 0
    real_success_rate: Optional[float] = None


@dataclass
class PlanQualityMetrics:
    task_class: str
    sample_count: int
    verified_goal_success: Optional[float]
    verified_step_success: Optional[float]
    average_steps: Optional[float]
    average_plan_length: Optional[float]
    dependency_depth: Optional[float]
    critical_path: Optional[float]
    replan_count: Optional[float]
    retry_count: Optional[float]
    replan_rate: Optional[float]
    retry_rate: Optional[float]
    specialist_count: Optional[float]
    model_calls: Optional[float]
    runtime_actions: Optional[float]
    average_total_cost: Optional[float]
    average_total_latency: Optional[float]
    latency: Optional[float]
    cost: Optional[float]
    user_correction_rate: Optional[float]
    step_success_rate: Optional[float] = None
    # 12.75 authoritative specialist execution telemetry (§10, §11)
    specialists_created: Optional[float] = None
    specialists_executed: Optional[float] = None
    specialists_completed: Optional[float] = None
    specialists_failed: Optional[float] = None


def compute_delegation_telemetry_for_goal(goal_id: Optional[str], plan_id: Optional[int], db) -> Dict[str, int]:
    """Compute authoritative specialist execution telemetry from Delegation records (§10, §11).

    Distinguishes created, executed, completed, failed.
    Prevents double-counting retries as new specialists and excludes proposed-but-unexecuted.
    """
    if not db or (not goal_id and not plan_id):
        return {"created": 0, "executed": 0, "completed": 0, "failed": 0}
    try:
        from backend.models.database import Delegation
        q = db.query(Delegation)
        if goal_id and plan_id:
            q = q.filter((Delegation.goal_id == goal_id) | (Delegation.plan_id == plan_id))
        elif goal_id:
            q = q.filter(Delegation.goal_id == goal_id)
        else:
            q = q.filter(Delegation.plan_id == plan_id)
        rows = q.all()
        if not rows:
            return {"created": 0, "executed": 0, "completed": 0, "failed": 0}

        by_logical = defaultdict(list)
        for r in rows:
            key = r.fingerprint or r.id
            by_logical[key].append(r)

        created = len(by_logical)
        executed = 0
        completed = 0
        failed = 0

        for key, attempts in by_logical.items():
            ever_executed = any(
                a.status in ("ASSIGNED", "RUNNING", "RESULT_READY", "VERIFIED", "FAILED")
                for a in attempts
            )
            if ever_executed:
                executed += 1

            latest = sorted(attempts, key=lambda a: int(a.attempt or 1))[-1]
            if latest.status in ("VERIFIED", "RESULT_READY"):
                completed += 1
            elif latest.status == "FAILED":
                failed += 1

        return {
            "created": created,
            "executed": executed,
            "completed": completed,
            "failed": failed,
        }
    except Exception:
        return {"created": 0, "executed": 0, "completed": 0, "failed": 0}


def _compute_dependency_metrics_for_plan(plan_id: int, db) -> Dict[str, Any]:
    """Deterministic dependency graph metrics for a single plan (§8).

    Returns step_count, dependency_edge_count, maximum_dependency_depth,
    parallel_ready_width, branch_count, critical_path_length.
    Unknown remains None if plan not found.
    """
    try:
        from backend.models.database import PlanStep
        steps = db.query(PlanStep).filter(PlanStep.plan_id == plan_id).all() if plan_id else []
        if not steps:
            return {"step_count": None, "dependency_edge_count": None, "maximum_dependency_depth": None, "parallel_ready_width": None, "branch_count": None, "critical_path_length": None}
        step_by_key = {s.step_key: s for s in steps}
        # adjacency: key -> list of depends_on keys
        adj = {}
        edge_count = 0
        for s in steps:
            try:
                deps = json.loads(s.depends_on) if s.depends_on else []
            except Exception:
                deps = []
            adj[s.step_key] = [d for d in deps if d in step_by_key]
            edge_count += len(adj[s.step_key])
        # compute depth via topological sort (Kahn). depth = longest path length to node
        indeg = {k: 0 for k in adj}
        children = defaultdict(list)
        for k, deps in adj.items():
            indeg[k] = len(deps)
            for d in deps:
                children[d].append(k)
        # depth: sources have depth 1
        depth = {k: 1 for k in adj}
        q = deque([k for k, deg in indeg.items() if deg == 0])
        visited = 0
        max_depth = 1
        while q:
            node = q.popleft()
            visited += 1
            for child in children[node]:
                # longest path
                if depth[child] < depth[node] + 1:
                    depth[child] = depth[node] + 1
                    max_depth = max(max_depth, depth[child])
                indeg[child] -= 1
                if indeg[child] == 0:
                    q.append(child)
        # if cycle (should not happen after validation), fallback
        if visited != len(adj):
            max_depth = None
        # parallel_ready_width = max number of nodes sharing same depth
        width = None
        if max_depth is not None:
            counts = defaultdict(int)
            for v in depth.values():
                counts[v] += 1
            width = max(counts.values()) if counts else 1
        branch_count = sum(1 for v in children.values() if len(v) > 1)
        critical_path = max_depth
        return {
            "step_count": len(steps),
            "dependency_edge_count": edge_count,
            "maximum_dependency_depth": max_depth,
            "parallel_ready_width": width,
            "branch_count": branch_count,
            "critical_path_length": critical_path,
            "depth_by_key": depth,
        }
    except Exception:
        return {"step_count": None, "dependency_edge_count": None, "maximum_dependency_depth": None, "parallel_ready_width": None, "branch_count": None, "critical_path_length": None}


class PlanningOutcomeStore:
    """Durable store for PlanningOutcome + aggregated metrics (§4, §12)."""

    def __init__(self, session):
        self.db = session
        self.ledger = DbEventLedger(session)

    # -- record -----------------------------------------------------------

    def record(
        self,
        *,
        planning_strategy: str,
        task_class: str = "general",
        goal_id: Optional[str] = None,
        plan_id: Optional[int] = None,
        goal_type: Optional[str] = None,
        initial_plan_steps: Optional[int] = None,
        final_plan_steps: Optional[int] = None,
        retries: int = 0,
        replans: int = 0,
        verified_success: Optional[bool] = None,
        outcome: str = "SUCCESS_PARTIAL",
        total_cost: Optional[Dict[str, Any]] = None,
        total_latency_ms: Optional[int] = None,
        user_feedback: Optional[str] = None,
        evidence_refs: Optional[List[str]] = None,
        evidence_fingerprint: Optional[str] = None,
        original_plan_id: Optional[int] = None,
        new_plan_id: Optional[int] = None,
        failure_reason: Optional[str] = None,
        replan_trigger: Optional[str] = None,
        retry_vs_replan: Optional[str] = None,
        dependency_complexity: Optional[int] = None,
        risk_level: Optional[str] = None,
        provenance: str = "REAL_EXECUTION",
        created_at: Optional[datetime] = None,
        expires_at: Optional[datetime] = None,
        allow_duplicate_fingerprint: bool = False,
    ) -> Dict[str, Any]:
        from backend.core.config import settings
        if not settings.planning_learning_enabled:
            return {"outcome": None, "eligible": False, "reason": "planning_learning_disabled"}
        planning_strategy = str(planning_strategy).strip().upper()
        if planning_strategy not in PlanningStrategy.values():
            return {"outcome": None, "eligible": False, "reason": f"invalid planning_strategy {planning_strategy}"}
        task_class = (task_class or "general").strip().lower()
        outcome = str(outcome).strip().upper()
        if outcome not in ("SUCCESS_VERIFIED", "SUCCESS_PARTIAL", "FAILED", "FAILED_VERIFICATION", "CANCELLED", "TIMED_OUT", "USER_CORRECTED", "USER_REJECTED", "UNAVAILABLE"):
            return {"outcome": None, "eligible": False, "reason": f"invalid outcome {outcome}"}
        if replan_trigger is not None:
            rt = str(replan_trigger).strip().upper()
            if rt not in ReplanTrigger.values():
                return {"outcome": None, "eligible": False, "reason": f"invalid replan_trigger {rt}"}
            replan_trigger = rt
        if retry_vs_replan is not None:
            rvr = str(retry_vs_replan).strip().upper()
            if rvr not in ("RETRY", "REPLAN"):
                return {"outcome": None, "eligible": False, "reason": f"invalid retry_vs_replan {retry_vs_replan}"}
            retry_vs_replan = rvr
        if risk_level is not None:
            risk_level = str(risk_level).strip().upper()
        prov = str(provenance or "REAL_EXECUTION").strip().upper()
        if prov not in PlanningProvenance.values():
            return {"outcome": None, "eligible": False, "reason": f"data_quality_rejected: unknown provenance {provenance}"}

        # Data quality checks (§12)
        if task_class == "goal" or replan_trigger is not None or initial_plan_steps is not None or final_plan_steps is not None:
            if not goal_id and not plan_id:
                return {"outcome": None, "eligible": False, "reason": "data_quality_rejected: missing goal/plan reference"}

        if not evidence_refs and not goal_id and not plan_id:
            return {"outcome": None, "eligible": False, "reason": "data_quality_rejected: missing evidence references"}

        if initial_plan_steps is not None and (int(initial_plan_steps) < 0 or int(initial_plan_steps) > 1000):
            return {"outcome": None, "eligible": False, "reason": f"data_quality_rejected: impossible initial_plan_steps {initial_plan_steps}"}
        if final_plan_steps is not None and (int(final_plan_steps) < 0 or int(final_plan_steps) > 1000):
            return {"outcome": None, "eligible": False, "reason": f"data_quality_rejected: impossible final_plan_steps {final_plan_steps}"}

        if retries is not None and (int(retries) < 0 or int(retries) > 1000):
            return {"outcome": None, "eligible": False, "reason": f"data_quality_rejected: impossible retries count {retries}"}
        if replans is not None and (int(replans) < 0 or int(replans) > 1000):
            return {"outcome": None, "eligible": False, "reason": f"data_quality_rejected: impossible replans count {replans}"}

        if total_latency_ms is not None and int(total_latency_ms) < 0:
            return {"outcome": None, "eligible": False, "reason": f"data_quality_rejected: negative latency {total_latency_ms}"}

        if total_cost is not None:
            if isinstance(total_cost, (int, float)):
                if total_cost < 0:
                    return {"outcome": None, "eligible": False, "reason": f"data_quality_rejected: negative cost {total_cost}"}
            elif isinstance(total_cost, dict):
                for k, v in total_cost.items():
                    if isinstance(v, (int, float)) and v < 0:
                        if k in ("specialist_calls", "specialist_count"):
                            return {"outcome": None, "eligible": False, "reason": f"data_quality_rejected: impossible specialist count {v}"}
                        return {"outcome": None, "eligible": False, "reason": f"data_quality_rejected: negative cost field {k}={v}"}

        if dependency_complexity is not None and (int(dependency_complexity) < 0 or int(dependency_complexity) > 10000):
            return {"outcome": None, "eligible": False, "reason": f"data_quality_rejected: impossible dependency complexity {dependency_complexity}"}

        now = created_at or datetime.utcnow()
        if evidence_fingerprint is None:
            raw = f"planning|{goal_id or ''}|{plan_id or ''}|{planning_strategy}|{outcome}|{retries}|{replans}|{now.isoformat()}|{prov}"
            evidence_fingerprint = hashlib.sha256(raw.encode()).hexdigest()[:16] if (goal_id or plan_id) else None
        if expires_at is None:
            ttl = int(getattr(settings, "planning_evidence_ttl_days", 90))
            expires_at = now + timedelta(days=ttl)
        cost_json = json.dumps(total_cost) if total_cost is not None else None
        refs_json = json.dumps(evidence_refs or [])
        if evidence_fingerprint and not allow_duplicate_fingerprint:
            existing = self.db.query(PlanningOutcome).filter_by(evidence_fingerprint=evidence_fingerprint).first()
            if existing is not None:
                return {"outcome": existing, "eligible": True, "reason": "duplicate_fingerprint_idempotent"}
        row = PlanningOutcome(
            goal_id=goal_id,
            plan_id=plan_id,
            planning_strategy=planning_strategy,
            task_class=task_class,
            goal_type=goal_type,
            initial_plan_steps=initial_plan_steps,
            final_plan_steps=final_plan_steps,
            retries=int(retries or 0),
            replans=int(replans or 0),
            verified_success=verified_success,
            total_cost_json=cost_json,
            total_latency_ms=total_latency_ms,
            user_feedback=user_feedback,
            evidence_refs_json=refs_json,
            evidence_fingerprint=evidence_fingerprint,
            original_plan_id=original_plan_id,
            new_plan_id=new_plan_id,
            failure_reason=(failure_reason or "")[:1000] if failure_reason else None,
            replan_trigger=replan_trigger,
            outcome=outcome,
            retry_vs_replan=retry_vs_replan,
            dependency_complexity=dependency_complexity,
            risk_level=risk_level,
            provenance=prov,
            created_at=now,
            expires_at=expires_at,
        )
        try:
            self.db.add(row)
            self.db.flush()
            self.ledger.append_event("learning", "planning.outcome.recorded", {
                "task_class": task_class,
                "planning_strategy": planning_strategy,
                "outcome": outcome,
                "goal_id": goal_id,
                "retries": retries,
                "replans": replans,
                "replan_trigger": replan_trigger,
                "provenance": prov,
            })
            self.db.commit()
            self.db.refresh(row)
            return {"outcome": row, "eligible": True, "reason": "recorded"}
        except Exception as exc:
            from sqlalchemy.exc import IntegrityError
            self.db.rollback()
            if isinstance(exc, IntegrityError) and "uq_planning_outcomes_fingerprint" in str(exc):
                existing = self.db.query(PlanningOutcome).filter_by(evidence_fingerprint=evidence_fingerprint).first()
                if existing is not None:
                    return {"outcome": existing, "eligible": True, "reason": "duplicate_fingerprint_idempotent"}
            return {"outcome": None, "eligible": False, "reason": str(exc)[:300]}

    def get(self, outcome_id: int) -> Optional[PlanningOutcome]:
        return self.db.query(PlanningOutcome).filter_by(id=outcome_id).first()

    def list_outcomes(
        self,
        *,
        task_class: Optional[str] = None,
        planning_strategy: Optional[str] = None,
        replan_trigger: Optional[str] = None,
        provenance: Optional[str] = None,
        include_expired: bool = False,
        limit: int = 100,
        since: Optional[datetime] = None,
    ) -> List[PlanningOutcome]:
        q = self.db.query(PlanningOutcome)
        if task_class:
            q = q.filter(PlanningOutcome.task_class == task_class.lower())
        if planning_strategy:
            q = q.filter(PlanningOutcome.planning_strategy == planning_strategy.upper())
        if replan_trigger:
            q = q.filter(PlanningOutcome.replan_trigger == replan_trigger.upper())
        if provenance:
            q = q.filter(PlanningOutcome.provenance == provenance.upper())
        if not include_expired:
            now = datetime.utcnow()
            q = q.filter((PlanningOutcome.expires_at.is_(None)) | (PlanningOutcome.expires_at > now))
        if since:
            q = q.filter(PlanningOutcome.created_at >= since)
        return q.order_by(PlanningOutcome.created_at.desc()).limit(limit).all()

    # -- statistics -------------------------------------------------------

    def statistics(
        self,
        *,
        task_class: Optional[str] = None,
        planning_strategy: Optional[str] = None,
        replan_trigger: Optional[str] = None,
        provenance: Optional[str] = None,
        window_days: Optional[int] = None,
        include_expired: bool = False,
        include_synthetic: bool = True,
    ) -> PlanningStatistics | Dict[str, PlanningStatistics]:
        from backend.core.config import settings

        def _stats_for(stype: str) -> PlanningStatistics:
            q = self.db.query(PlanningOutcome).filter(PlanningOutcome.planning_strategy == stype)
            if task_class:
                q = q.filter(PlanningOutcome.task_class == task_class.lower())
            if replan_trigger:
                q = q.filter(PlanningOutcome.replan_trigger == replan_trigger.upper())
            if provenance:
                q = q.filter(PlanningOutcome.provenance == provenance.upper())
            if not include_synthetic:
                q = q.filter(PlanningOutcome.provenance == PlanningProvenance.REAL_EXECUTION.value)
            if not include_expired:
                now = datetime.utcnow()
                q = q.filter((PlanningOutcome.expires_at.is_(None)) | (PlanningOutcome.expires_at > now))
            if window_days is not None:
                cutoff = datetime.utcnow() - timedelta(days=window_days)
                q = q.filter(PlanningOutcome.created_at >= cutoff)
            rows = q.all()
            n = len(rows)
            success = sum(1 for r in rows if r.outcome in ("SUCCESS_VERIFIED", "SUCCESS_PARTIAL"))
            verified = sum(1 for r in rows if r.outcome == "SUCCESS_VERIFIED" or r.verified_success is True)
            failed = sum(1 for r in rows if r.outcome in ("FAILED", "FAILED_VERIFICATION", "TIMED_OUT", "UNAVAILABLE"))
            replans_sum = sum(int(r.replans or 0) for r in rows)
            retries_sum = sum(int(r.retries or 0) for r in rows)
            latencies = [r.total_latency_ms for r in rows if r.total_latency_ms is not None]
            costs: List[float] = []
            total = 0.0
            has_cost = False
            for r in rows:
                try:
                    c = json.loads(r.total_cost_json) if r.total_cost_json else None
                    if isinstance(c, dict):
                        v = c.get("total") if c.get("total") is not None else c.get("cost")
                        if isinstance(v, (int, float)):
                            costs.append(float(v))
                            total += float(v)
                            has_cost = True
                        # also count detailed cost keys without total
                        elif any(k in c for k in ("model_calls", "tokens", "runtime_actions", "specialist_calls")):
                            # synthetic cost from actual counts
                            cc = 0.0
                            cc += float(c.get("model_calls", 0)) * 0.2
                            cc += float(c.get("runtime_actions", 0)) * 0.05
                            cc += float(c.get("specialist_calls", 0)) * 0.3
                            cc += float(c.get("tokens", 0)) * 0.0001
                            if cc > 0:
                                costs.append(cc)
                                total += cc
                                has_cost = True
                    elif isinstance(c, (int, float)):
                        costs.append(float(c))
                        total += float(c)
                        has_cost = True
                except Exception:
                    pass
            recent_window = int(getattr(settings, "planning_recent_window_days", 30))
            recent_cutoff = datetime.utcnow() - timedelta(days=recent_window)
            recent = sum(1 for r in rows if r.created_at and r.created_at >= recent_cutoff)
            ttl = int(getattr(settings, "planning_evidence_ttl_days", 90))
            decay_cutoff = datetime.utcnow() - timedelta(days=ttl)
            decayed = sum(1 for r in rows if r.created_at and r.created_at < decay_cutoff)
            retry_rows = [r for r in rows if r.retry_vs_replan == "RETRY"]
            replan_rows = [r for r in rows if r.retry_vs_replan == "REPLAN"]
            def _sr(sub):
                if not sub:
                    return None
                s = sum(1 for r in sub if r.outcome in ("SUCCESS_VERIFIED", "SUCCESS_PARTIAL"))
                return round(s / len(sub), 4)
            # real vs synthetic split
            real_rows = [r for r in rows if (r.provenance or "REAL_EXECUTION") == PlanningProvenance.REAL_EXECUTION.value]
            synth_rows = [r for r in rows if (r.provenance or "") != PlanningProvenance.REAL_EXECUTION.value]
            real_verified = sum(1 for r in real_rows if r.outcome == "SUCCESS_VERIFIED" or r.verified_success is True)
            real_sr = round(sum(1 for r in real_rows if r.outcome in ("SUCCESS_VERIFIED","SUCCESS_PARTIAL")) / len(real_rows), 4) if real_rows else None
            return PlanningStatistics(
                planning_strategy=stype,
                task_class=task_class or "all",
                sample_count=n,
                success_count=success,
                verified_success_count=verified,
                failure_count=failed,
                replans=replans_sum,
                retries=retries_sum,
                success_rate=round(success / n, 4) if n else None,
                verified_success_rate=round(verified / n, 4) if n else None,
                failure_rate=round(failed / n, 4) if n else None,
                mean_latency_ms=round(sum(latencies) / len(latencies), 2) if latencies else None,
                mean_cost=round(sum(costs) / len(costs), 4) if costs else None,
                total_cost=round(total, 4) if has_cost else None,
                evidence_strength=_planning_strength(n),
                recent_count=recent,
                decayed_count=decayed,
                retry_success_rate=_sr(retry_rows),
                replan_success_rate=_sr(replan_rows),
                real_sample_count=len(real_rows),
                synthetic_sample_count=len(synth_rows),
                real_verified_success_count=real_verified,
                real_success_rate=real_sr,
            )

        if planning_strategy:
            return _stats_for(planning_strategy.upper())
        return {st: _stats_for(st) for st in PlanningStrategy.values()}

    def quality_metrics(self, task_class: Optional[str] = None, include_expired: bool = False) -> PlanQualityMetrics:
        q = self.db.query(PlanningOutcome)
        if task_class:
            q = q.filter(PlanningOutcome.task_class == task_class.lower())
        if not include_expired:
            now = datetime.utcnow()
            q = q.filter((PlanningOutcome.expires_at.is_(None)) | (PlanningOutcome.expires_at > now))
        rows = q.all()
        n = len(rows)
        if n == 0:
            return PlanQualityMetrics(
                task_class=task_class or "all",
                sample_count=0,
                verified_goal_success=None,
                verified_step_success=None,
                average_steps=None,
                average_plan_length=None,
                dependency_depth=None,
                critical_path=None,
                replan_count=None,
                retry_count=None,
                replan_rate=None,
                retry_rate=None,
                specialist_count=None,
                model_calls=None,
                runtime_actions=None,
                average_total_cost=None,
                average_total_latency=None,
                latency=None,
                cost=None,
                user_correction_rate=None,
            )
        verified = sum(1 for r in rows if r.verified_success is True or r.outcome == "SUCCESS_VERIFIED")
        lengths = [r.final_plan_steps for r in rows if r.final_plan_steps is not None]
        avg_steps = round(sum(lengths) / len(lengths), 2) if lengths else None
        replans_vals = [int(r.replans or 0) for r in rows]
        retries_vals = [int(r.retries or 0) for r in rows]
        replans_any = sum(1 for r in rows if (r.replans or 0) > 0)
        retries_any = sum(1 for r in rows if (r.retries or 0) > 0)
        costs = []
        model_calls_vals = []
        runtime_vals = []
        specialist_vals = []
        latencies = [r.total_latency_ms for r in rows if r.total_latency_ms is not None]
        for r in rows:
            try:
                c = json.loads(r.total_cost_json) if r.total_cost_json else None
                if isinstance(c, dict):
                    if isinstance(c.get("total"), (int, float)):
                        costs.append(float(c["total"]))
                    if isinstance(c.get("model_calls"), (int, float)):
                        model_calls_vals.append(float(c["model_calls"]))
                    if isinstance(c.get("runtime_actions"), (int, float)):
                        runtime_vals.append(float(c["runtime_actions"]))
                    if isinstance(c.get("specialist_calls"), (int, float)):
                        specialist_vals.append(float(c["specialist_calls"]))
            except Exception:
                pass
        corrections = sum(1 for r in rows if r.user_feedback is not None)
        # deterministic dependency metrics aggregation from persisted plans
        depths = []
        crits = []
        for r in rows:
            if r.plan_id:
                dm = _compute_dependency_metrics_for_plan(r.plan_id, self.db)
                if dm.get("maximum_dependency_depth") is not None:
                    depths.append(dm["maximum_dependency_depth"])
                if dm.get("critical_path_length") is not None:
                    crits.append(dm["critical_path_length"])

        # Phase 12.75 authoritative specialist telemetry from Delegation records (§10, §11)
        spec_created_list = []
        spec_executed_list = []
        spec_completed_list = []
        spec_failed_list = []
        for r in rows:
            if r.goal_id or r.plan_id:
                telemetry = compute_delegation_telemetry_for_goal(r.goal_id, r.plan_id, self.db)
                if telemetry["created"] > 0:
                    spec_created_list.append(telemetry["created"])
                    spec_executed_list.append(telemetry["executed"])
                    spec_completed_list.append(telemetry["completed"])
                    spec_failed_list.append(telemetry["failed"])

        if spec_created_list:
            avg_specialist_count = round(sum(spec_executed_list) / len(spec_executed_list), 2)
            avg_spec_created = round(sum(spec_created_list) / len(spec_created_list), 2)
            avg_spec_executed = round(sum(spec_executed_list) / len(spec_executed_list), 2)
            avg_spec_completed = round(sum(spec_completed_list) / len(spec_completed_list), 2)
            avg_spec_failed = round(sum(spec_failed_list) / len(spec_failed_list), 2)
        elif specialist_vals:
            avg_specialist_count = round(sum(specialist_vals) / len(specialist_vals), 2)
            avg_spec_created = None
            avg_spec_executed = avg_specialist_count
            avg_spec_completed = None
            avg_spec_failed = None
        else:
            avg_specialist_count = None
            avg_spec_created = None
            avg_spec_executed = None
            avg_spec_completed = None
            avg_spec_failed = None

        return PlanQualityMetrics(
            task_class=task_class or "all",
            sample_count=n,
            verified_goal_success=round(verified / n, 4),
            verified_step_success=round(verified / n, 4) if n else None,
            average_steps=avg_steps,
            average_plan_length=avg_steps,
            dependency_depth=round(sum(depths) / len(depths), 2) if depths else None,
            critical_path=round(sum(crits) / len(crits), 2) if crits else None,
            replan_count=round(sum(replans_vals) / n, 2) if n else None,
            retry_count=round(sum(retries_vals) / n, 2) if n else None,
            replan_rate=round(replans_any / n, 4),
            retry_rate=round(retries_any / n, 4),
            specialist_count=avg_specialist_count,
            model_calls=round(sum(model_calls_vals) / len(model_calls_vals), 2) if model_calls_vals else None,
            runtime_actions=round(sum(runtime_vals) / len(runtime_vals), 2) if runtime_vals else None,
            average_total_cost=round(sum(costs) / len(costs), 4) if costs else None,
            average_total_latency=round(sum(latencies) / len(latencies), 2) if latencies else None,
            latency=round(sum(latencies) / len(latencies), 2) if latencies else None,
            cost=round(sum(costs) / len(costs), 4) if costs else None,
            user_correction_rate=round(corrections / n, 4),
            specialists_created=avg_spec_created,
            specialists_executed=avg_spec_executed,
            specialists_completed=avg_spec_completed,
            specialists_failed=avg_spec_failed,
        )

    def coverage_report(self, include_expired: bool = False) -> Dict[str, Any]:
        """Deterministic coverage dashboard (§3)."""
        from backend.learning.planning_taxonomy import PlanningStrategy
        # aggregate per task_class / strategy
        all_rows = self.db.query(PlanningOutcome)
        if not include_expired:
            now = datetime.utcnow()
            all_rows = all_rows.filter((PlanningOutcome.expires_at.is_(None)) | (PlanningOutcome.expires_at > now))
        rows = all_rows.all()
        # shadow divergence
        shadow_rows = self.db.query(PlanningShadowDecision).all()
        total_shadow = len(shadow_rows)
        divergent = sum(1 for r in shadow_rows if r.would_diverge)
        divergence_rate = round(divergent / total_shadow, 4) if total_shadow else None
        # per task_class coverage
        task_classes = sorted(set(r.task_class for r in rows)) if rows else []
        strategies = PlanningStrategy.values()
        per_class = []
        for tc in task_classes:
            tc_rows = [r for r in rows if r.task_class == tc]
            real_ct = sum(1 for r in tc_rows if (r.provenance or "REAL_EXECUTION") == PlanningProvenance.REAL_EXECUTION.value)
            synth_ct = len(tc_rows) - real_ct
            per_class.append({
                "task_class": tc,
                "sample_count": len(tc_rows),
                "real_sample_count": real_ct,
                "synthetic_sample_count": synth_ct,
                "verified_success_rate": round(sum(1 for r in tc_rows if r.outcome == "SUCCESS_VERIFIED" or r.verified_success) / len(tc_rows), 4) if tc_rows else None,
                "replan_rate": round(sum(1 for r in tc_rows if (r.replans or 0) > 0) / len(tc_rows), 4) if tc_rows else None,
                "retry_rate": round(sum(1 for r in tc_rows if (r.retries or 0) > 0) / len(tc_rows), 4) if tc_rows else None,
                "mean_cost": None,  # computed per strategy below
                "mean_latency": round(sum(r.total_latency_ms for r in tc_rows if r.total_latency_ms) / len([r for r in tc_rows if r.total_latency_ms]) ,2) if any(r.total_latency_ms for r in tc_rows) else None,
                "evidence_strength": _planning_strength(len(tc_rows)),
            })
        per_strategy = []
        for strat in strategies:
            s_rows = [r for r in rows if r.planning_strategy == strat]
            if not s_rows:
                continue
            real_ct = sum(1 for r in s_rows if (r.provenance or "REAL_EXECUTION") == PlanningProvenance.REAL_EXECUTION.value)
            synth_ct = len(s_rows) - real_ct
            stats = self.statistics(planning_strategy=strat)
            per_strategy.append({
                "planning_strategy": strat,
                "sample_count": stats.sample_count,
                "real_sample_count": stats.real_sample_count,
                "synthetic_sample_count": stats.synthetic_sample_count,
                "verified_success_rate": stats.verified_success_rate,
                "replan_rate": None,
                "retry_rate": None,
                "mean_cost": stats.mean_cost,
                "mean_latency": stats.mean_latency_ms,
                "evidence_strength": stats.evidence_strength,
                "shadow_divergence_rate": divergence_rate,
                "retry_success_rate": stats.retry_success_rate,
                "replan_success_rate": stats.replan_success_rate,
            })
        return {
            "total_outcomes": len(rows),
            "real_sample_count": sum(1 for r in rows if (r.provenance or "REAL_EXECUTION") == PlanningProvenance.REAL_EXECUTION.value),
            "synthetic_sample_count": sum(1 for r in rows if (r.provenance or "") != PlanningProvenance.REAL_EXECUTION.value),
            "task_classes": per_class,
            "strategies": per_strategy,
            "shadow_divergence_rate": divergence_rate,
            "shadow_total": total_shadow,
            "shadow_divergent": divergent,
        }

    def shadow_quality_report(self) -> Dict[str, Any]:
        """Shadow agreement/divergence/expected advantage for real tasks (§6-8)."""
        shadows = self.db.query(PlanningShadowDecision).all()
        if not shadows:
            return {"total": 0, "agreement": None, "divergence": None, "expected_advantage": None, "details": []}
        agree = sum(1 for r in shadows if not r.would_diverge)
        diverg = len(shadows) - agree
        # expected advantage: where shadow differs, do we have supporting REAL evidence that shadow would be better?
        # A shadow policy must not be described as superior unless independent historical REAL evidence supports the claim (§8).
        details = []
        advantage_count = 0
        known_advantage_diverg_count = 0
        for r in shadows:
            if r.would_diverge:
                actual_stats = self.statistics(planning_strategy=r.actual_policy, include_synthetic=False) if r.actual_policy in PlanningStrategy.values() else None
                shadow_stats = self.statistics(planning_strategy=r.shadow_policy, include_synthetic=False) if r.shadow_policy in PlanningStrategy.values() else None

                has_advantage = False
                expected_adv = "UNKNOWN"
                if isinstance(shadow_stats, PlanningStatistics) and isinstance(actual_stats, PlanningStatistics):
                    if shadow_stats.evidence_strength in ("SUPPORTED", "ESTABLISHED") and shadow_stats.real_sample_count >= 3:
                        known_advantage_diverg_count += 1
                        if (shadow_stats.real_success_rate or 0) > (actual_stats.real_success_rate or 0) + 0.05:
                            has_advantage = True
                            expected_adv = "TRUE"
                            advantage_count += 1
                        else:
                            expected_adv = "FALSE"
                    else:
                        expected_adv = "UNKNOWN"
                else:
                    expected_adv = "UNKNOWN"

                details.append({
                    "id": r.id,
                    "goal_id": r.goal_id,
                    "task_class": r.task_class,
                    "actual_policy": r.actual_policy,
                    "shadow_policy": r.shadow_policy,
                    "would_diverge": bool(r.would_diverge),
                    "actual_outcome": r.actual_outcome,
                    "has_expected_advantage": has_advantage,
                    "expected_advantage": expected_adv,
                    "historical_support_for_shadow": shadow_stats.evidence_strength if isinstance(shadow_stats, PlanningStatistics) else "UNRATED",
                    "sample_count": shadow_stats.real_sample_count if isinstance(shadow_stats, PlanningStatistics) else 0,
                    "verified_success_rate": shadow_stats.real_success_rate if isinstance(shadow_stats, PlanningStatistics) else None,
                    "evidence_strength": shadow_stats.evidence_strength if isinstance(shadow_stats, PlanningStatistics) else "UNRATED",
                    "actual_real_success_rate": actual_stats.real_success_rate if isinstance(actual_stats, PlanningStatistics) else None,
                    "shadow_real_success_rate": shadow_stats.real_success_rate if isinstance(shadow_stats, PlanningStatistics) else None,
                })
        return {
            "total": len(shadows),
            "agreement": round(agree / len(shadows), 4) if shadows else None,
            "divergence": round(diverg / len(shadows), 4) if shadows else None,
            "agreement_count": agree,
            "divergence_count": diverg,
            "expected_advantage_count": advantage_count,
            "expected_advantage": round(advantage_count / known_advantage_diverg_count, 4) if known_advantage_diverg_count else None,
            "details": details,
        }

    def retry_replan_calibration(self, task_class: Optional[str] = None) -> Dict[str, Any]:
        """Real evidence calibration RETRY vs REPLAN for comparable failure classes (§10, §16)."""
        q = self.db.query(PlanningOutcome)
        if task_class:
            q = q.filter(PlanningOutcome.task_class == task_class.lower())
        rows = q.all()
        # only real execution for calibration (§16)
        real = [r for r in rows if (r.provenance or "REAL_EXECUTION") == PlanningProvenance.REAL_EXECUTION.value]
        retry_rows = [r for r in real if r.retry_vs_replan == "RETRY"]
        replan_rows = [r for r in real if r.retry_vs_replan == "REPLAN"]
        def _metrics(sub):
            if not sub:
                return {"sample_count": 0, "verified_recovery_rate": None, "mean_cost": None, "mean_latency": None, "mean_attempts": None, "user_correction_rate": None}
            verified = sum(1 for r in sub if r.verified_success or r.outcome == "SUCCESS_VERIFIED")
            costs = []
            for r in sub:
                try:
                    c = json.loads(r.total_cost_json) if r.total_cost_json else None
                    if isinstance(c, dict) and isinstance(c.get("total"), (int,float)):
                        costs.append(float(c["total"]))
                except: pass
            lats = [r.total_latency_ms for r in sub if r.total_latency_ms is not None]
            atts = [(r.retries or 0) + (r.replans or 0) for r in sub]
            return {
                "sample_count": len(sub),
                "verified_recovery_rate": round(verified / len(sub), 4),
                "mean_cost": round(sum(costs)/len(costs),4) if costs else None,
                "mean_latency": round(sum(lats)/len(lats),2) if lats else None,
                "mean_attempts": round(sum(atts)/len(atts),2) if atts else None,
                "user_correction_rate": round(sum(1 for r in sub if r.user_feedback)/len(sub),4) if sub else None,
            }
        # per trigger
        triggers = ReplanTrigger.values()
        per_trigger = {}
        for trig in triggers:
            trig_real = [r for r in real if r.replan_trigger == trig]
            trig_retry = [r for r in trig_real if r.retry_vs_replan == "RETRY"]
            trig_replan = [r for r in trig_real if r.retry_vs_replan == "REPLAN"]
            per_trigger[trig] = {
                "retry": _metrics(trig_retry),
                "replan": _metrics(trig_replan),
            }
        # per strategy
        strategies = PlanningStrategy.values()
        per_strategy = {}
        for strat in strategies:
            strat_real = [r for r in real if r.planning_strategy == strat]
            strat_retry = [r for r in strat_real if r.retry_vs_replan == "RETRY"]
            strat_replan = [r for r in strat_real if r.retry_vs_replan == "REPLAN"]
            per_strategy[strat] = {
                "retry": _metrics(strat_retry),
                "replan": _metrics(strat_replan),
            }
        # per task_class
        task_classes = sorted(set(r.task_class for r in real)) if real else []
        per_task_class = {}
        for tc in task_classes:
            tc_real = [r for r in real if r.task_class == tc]
            tc_retry = [r for r in tc_real if r.retry_vs_replan == "RETRY"]
            tc_replan = [r for r in tc_real if r.retry_vs_replan == "REPLAN"]
            per_task_class[tc] = {
                "retry": _metrics(tc_retry),
                "replan": _metrics(tc_replan),
            }
        return {
            "overall_retry": _metrics(retry_rows),
            "overall_replan": _metrics(replan_rows),
            "per_trigger": per_trigger,
            "per_strategy": per_strategy,
            "per_task_class": per_task_class,
            "real_sample_count": len(real),
        }

    def replan_trigger_calibration(self) -> Dict[str, Any]:
        """For each real trigger, measure retry vs replan recovery (§11)."""
        return self.retry_replan_calibration()

    def live_readiness(self) -> Dict[str, Any]:
        """Deterministic live evidence readiness evaluation (§3, §4, §9, §14, §15, §19, §22)."""
        from backend.core.config import settings
        from backend.learning.planning_taxonomy import PlanningStrategy, PlanningProvenance

        min_real = int(getattr(settings, "planning_min_real_samples", 5))
        min_verified = int(getattr(settings, "planning_min_real_verified_successes", 3))

        now = datetime.utcnow()
        real_q = self.db.query(PlanningOutcome).filter(
            PlanningOutcome.provenance == PlanningProvenance.REAL_EXECUTION.value,
            (PlanningOutcome.expires_at.is_(None)) | (PlanningOutcome.expires_at > now),
        )
        real_rows = real_q.all()
        real_count = len(real_rows)
        real_verified = sum(1 for r in real_rows if r.outcome == "SUCCESS_VERIFIED" or r.verified_success is True)

        shadow_rows = self.db.query(PlanningShadowDecision).all()
        shadow_total = len(shadow_rows)
        shadow_divergent = sum(1 for r in shadow_rows if r.would_diverge)

        # Shadow validation on REAL workload: requires real evidence threshold met + evaluated shadows
        shadow_validated = bool(real_count >= min_real and real_verified >= min_verified and shadow_total >= 1)

        # Deterministic policy readiness level (§4, §22)
        if real_count == 0:
            readiness_level = "NOT_READY"
        elif real_count < min_real or real_verified < min_verified:
            readiness_level = "LOW_REAL_EVIDENCE"
        elif not shadow_validated:
            readiness_level = "REAL_SUPPORTED"
        else:
            readiness_level = "SHADOW_VALIDATED"

        promotion_eligible = bool(readiness_level in ("SHADOW_VALIDATED", "READY_FOR_OWNER_REVIEW"))

        # Task classes breakdown (§14)
        task_classes = sorted(set(r.task_class for r in real_rows)) if real_rows else []
        task_class_list = []
        for tc in task_classes:
            tc_rows = [r for r in real_rows if r.task_class == tc]
            tc_verified = sum(1 for r in tc_rows if r.outcome == "SUCCESS_VERIFIED" or r.verified_success is True)
            tc_sufficient = (len(tc_rows) >= min_real and tc_verified >= min_verified)
            if len(tc_rows) == 0:
                tc_readiness = "UNRATED"
            elif len(tc_rows) < min_real:
                tc_readiness = "LOW_SAMPLE"
            elif not shadow_validated:
                tc_readiness = "REAL_SUPPORTED"
            else:
                tc_readiness = "SHADOW_VALIDATED"
            task_class_list.append({
                "task_class": tc,
                "real_sample_count": len(tc_rows),
                "real_verified_successes": tc_verified,
                "readiness": tc_readiness,
                "sufficient_real_evidence": tc_sufficient,
            })

        # Strategy breakdown (§15)
        strategy_list = []
        for strat in PlanningStrategy.values():
            s_rows = [r for r in real_rows if r.planning_strategy == strat]
            s_verified = sum(1 for r in s_rows if r.outcome == "SUCCESS_VERIFIED" or r.verified_success is True)
            s_sr = round(s_verified / len(s_rows), 4) if s_rows else None
            strategy_list.append({
                "planning_strategy": strat,
                "real_sample_count": len(s_rows),
                "real_verified_successes": s_verified,
                "real_success_rate": s_sr,
                "sufficient_real_evidence": (len(s_rows) >= min_real and s_verified >= min_verified),
            })

        # Expiration metrics (§19)
        ttl = int(getattr(settings, "planning_evidence_ttl_days", 90))
        decay_cutoff = now - timedelta(days=ttl)
        all_real_q = self.db.query(PlanningOutcome).filter(
            PlanningOutcome.provenance == PlanningProvenance.REAL_EXECUTION.value
        )
        all_real_rows = all_real_q.all()
        expired_count = sum(1 for r in all_real_rows if r.expires_at and r.expires_at <= now)
        decayed_count = sum(1 for r in all_real_rows if r.created_at and r.created_at < decay_cutoff)

        return {
            "real_sample_count": real_count,
            "real_verified_successes": real_verified,
            "eligible_real_samples": real_count,
            "shadow_sample_count": shadow_total,
            "shadow_divergence_count": shadow_divergent,
            "shadow_validated": shadow_validated,
            "promotion_eligible": promotion_eligible,
            "policy_readiness": readiness_level,
            "task_classes": task_class_list,
            "strategies": strategy_list,
            "expiration": {
                "active_real_evidence": real_count,
                "expired_real_evidence": expired_count,
                "decayed_real_evidence": decayed_count,
            },
            "phase13_ready": False,  # Manual owner approval + genuine real evidence gate required
        }

    def promotion_readiness(self, policy: PlanningPolicy) -> Dict[str, Any]:
        """Deterministic readiness evaluation (§18, §12.5 audit).

        5 real samples + 3 verified successes means minimum evidence for review,
        NOT proven superior. Levels REAL_SUPPORTED / SHADOW_VALIDATED indicate
        eligibility only; superiority requires established evidence and owner review.
        """
        try:
            ev = json.loads(policy.evidence_summary_json or "{}")
        except: ev = {}
        # Need real evidence stats for this policy's task_class/strategy
        stats = self.statistics(task_class=policy.task_class, planning_strategy=policy.planning_strategy)
        # real counts
        real_n = stats.real_sample_count if isinstance(stats, PlanningStatistics) else 0
        real_verified = stats.real_verified_success_count if isinstance(stats, PlanningStatistics) else 0
        from backend.core.config import settings
        min_real = int(getattr(settings, "planning_min_real_samples", 5))
        min_verified = int(getattr(settings, "planning_min_real_verified_successes", 3))
        has_shadow = bool(ev.get("shadow_validated"))
        status = policy.status
        if status == "ROLLED_BACK":
            level = "ROLLED_BACK"
        elif status == "ACTIVE":
            level = "ACTIVE"
        elif status == "OWNER_APPROVED":
            level = "READY_FOR_OWNER_REVIEW"
        elif has_shadow and real_n >= min_real and real_verified >= min_verified:
            level = "SHADOW_VALIDATED"
        elif real_n >= min_real and real_verified >= min_verified:
            level = "REAL_SUPPORTED"
        elif stats.sample_count >= 1 and stats.sample_count < 5:
            level = "LOW_SAMPLE"
        elif stats.sample_count == 0:
            level = "UNRATED"
        else:
            level = "LOW_SAMPLE"
        # eligibility for next promotion
        from backend.learning.planning_taxonomy import PlanningPolicyStatus
        eligible_next = None
        if level in ("UNRATED","LOW_SAMPLE"):
            eligible_next = None
        elif level == "REAL_SUPPORTED" and not has_shadow:
            eligible_next = "SHADOW_VALIDATED requires shadow validation"
        elif level == "SHADOW_VALIDATED":
            eligible_next = "READY_FOR_OWNER_REVIEW (owner approval required for ACTIVE)"
        elif status == "ACTIVE":
            eligible_next = None
        return {
            "policy_id": policy.id,
            "policy_key": policy.policy_key,
            "status": policy.status,
            "readiness_level": level,
            "real_samples": real_n,
            "real_verified_successes": real_verified,
            "min_real_samples_required": min_real,
            "min_real_verified_required": min_verified,
            "shadow_validated": has_shadow,
            "evidence_strength": stats.evidence_strength if isinstance(stats, PlanningStatistics) else None,
            "eligible_next": eligible_next,
            "promotion_eligibility": level in ("SHADOW_VALIDATED","READY_FOR_OWNER_REVIEW") or status in ("ACTIVE","OWNER_APPROVED"),
        }


class PlanningPolicyStore:
    """Promotion / rollback lifecycle (§17-18)."""

    def __init__(self, session):
        self.db = session
        self.ledger = DbEventLedger(session)

    def get(self, policy_id: str) -> Optional[PlanningPolicy]:
        return self.db.query(PlanningPolicy).filter_by(id=policy_id).first()

    def get_by_key(self, policy_key: str) -> Optional[PlanningPolicy]:
        return self.db.query(PlanningPolicy).filter_by(policy_key=policy_key).order_by(PlanningPolicy.policy_version.desc()).first()

    def create_observed(self, task_class: str, planning_strategy: str, evidence_summary: Optional[Dict[str, Any]] = None) -> PlanningPolicy:
        policy_key = f"{task_class.lower()}:{planning_strategy.upper()}"
        existing = self.get_by_key(policy_key)
        if existing and existing.status in ("OBSERVED", "SUPPORTED", "SHADOW_VALIDATED", "OWNER_APPROVED", "ACTIVE"):
            return existing
        pol = PlanningPolicy(
            id=str(uuid.uuid4()),
            policy_key=policy_key,
            planning_strategy=planning_strategy.upper(),
            task_class=task_class.lower(),
            status=PlanningPolicyStatus.OBSERVED.value,
            evidence_summary_json=json.dumps(evidence_summary or {}),
            policy_version=1,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        self.db.add(pol)
        self.db.commit()
        self.db.refresh(pol)
        return pol

    def promote(self, policy_id: str, target_status: str, require_owner_approval: bool = True, owner_approved: bool = False) -> Dict[str, Any]:
        pol = self.get(policy_id)
        if pol is None:
            return {"ok": False, "reason": "policy_not_found"}
        order = ["OBSERVED", "SUPPORTED", "SHADOW_VALIDATED", "OWNER_APPROVED", "ACTIVE"]
        if target_status not in order:
            return {"ok": False, "reason": f"invalid target_status {target_status}"}
        try:
            cur_idx = order.index(pol.status)
            tgt_idx = order.index(target_status)
        except ValueError:
            return {"ok": False, "reason": "current status not in promotion order"}
        if tgt_idx <= cur_idx:
            return {"ok": False, "reason": f"cannot promote {pol.status} -> {target_status}"}
        try:
            ev = json.loads(pol.evidence_summary_json or "{}")
            sample_count = ev.get("sample_count") or ev.get("n") or 0
        except Exception:
            sample_count = 0
        # 12.5 hardening: synthetic alone cannot promote — check real evidence
        from backend.core.config import settings
        from backend.learning.planning_taxonomy import PlanningProvenance
        # Count real evidence for this policy's key
        try:
            # query real outcomes for this task_class/strategy
            real_q = self.db.query(PlanningOutcome).filter(
                PlanningOutcome.task_class == pol.task_class.lower(),
                PlanningOutcome.planning_strategy == pol.planning_strategy.upper(),
                PlanningOutcome.provenance == PlanningProvenance.REAL_EXECUTION.value,
            )
            # respect TTL
            now = datetime.utcnow()
            real_q = real_q.filter((PlanningOutcome.expires_at.is_(None)) | (PlanningOutcome.expires_at > now))
            real_count = real_q.count()
            real_verified = real_q.filter((PlanningOutcome.outcome == "SUCCESS_VERIFIED") | (PlanningOutcome.verified_success == True)).count()
        except Exception:
            real_count = 0
            real_verified = 0
        min_real = int(getattr(settings, "planning_min_real_samples", 5))
        min_verified = int(getattr(settings, "planning_min_real_verified_successes", 3))
        if tgt_idx >= 1 and sample_count < 5:
            return {"ok": False, "reason": "insufficient_evidence_for_supported"}
        # For SUPPORTED and beyond, require minimum real samples (synthetic-only blocked)
        if tgt_idx >= 1 and real_count < min_real:
            return {"ok": False, "reason": f"insufficient_real_samples: have {real_count}, need {min_real} (synthetic alone cannot promote)"}
        if tgt_idx >= 1 and real_verified < min_verified:
            return {"ok": False, "reason": f"insufficient_real_verified_successes: have {real_verified}, need {min_verified}"}
        if tgt_idx >= 2 and not ev.get("shadow_validated"):
            return {"ok": False, "reason": "shadow_validation_required"}
        if target_status == "ACTIVE":
            if require_owner_approval and not owner_approved:
                safe_auto = bool(getattr(settings, "planning_safe_auto_promotion", False))
                if not safe_auto:
                    return {"ok": False, "reason": "owner_approval_required"}
                if not ev.get("safe_for_auto_promotion"):
                    return {"ok": False, "reason": "safe_auto_promotion_not_eligible"}
        prev_status = pol.status
        pol.status = target_status
        pol.promoted_at = datetime.utcnow()
        pol.updated_at = datetime.utcnow()
        if target_status == "ACTIVE":
            pol.policy_version += 1
        self.ledger.append_event("learning", "planning.policy.promoted", {
            "policy_id": pol.id,
            "policy_key": pol.policy_key,
            "from": prev_status,
            "to": target_status,
            "owner_approved": owner_approved,
            "real_count": real_count,
            "real_verified": real_verified,
        })
        self.db.commit()
        self.db.refresh(pol)
        return {"ok": True, "policy": pol}

    def rollback(self, policy_id: str, reason: str) -> Dict[str, Any]:
        pol = self.get(policy_id)
        if pol is None:
            return {"ok": False, "reason": "policy_not_found"}
        prev = pol.status
        pol.previous_policy_json = json.dumps({"status": prev, "policy_version": pol.policy_version, "evidence": pol.evidence_summary_json})
        pol.status = PlanningPolicyStatus.ROLLED_BACK.value
        pol.rollback_reason = (reason or "")[:500]
        pol.updated_at = datetime.utcnow()
        self.ledger.append_event("learning", "planning.policy.rolled_back", {
            "policy_id": pol.id,
            "previous_status": prev,
            "reason": reason,
        })
        self.db.commit()
        self.db.refresh(pol)
        return {"ok": True, "policy": pol}

    def list_policies(self, task_class: Optional[str] = None, status: Optional[str] = None) -> List[PlanningPolicy]:
        q = self.db.query(PlanningPolicy)
        if task_class:
            q = q.filter(PlanningPolicy.task_class == task_class.lower())
        if status:
            q = q.filter(PlanningPolicy.status == status.upper())
        return q.order_by(PlanningPolicy.created_at.desc()).all()


class PlanningShadowStore:
    """Shadow decisions (§15)."""

    def __init__(self, session):
        self.db = session

    def record_shadow(
        self,
        *,
        task_class: str,
        actual_policy: str,
        shadow_policy: str,
        goal_id: Optional[str] = None,
        failure_context: Optional[str] = None,
        actual_outcome: Optional[str] = None,
        evidence_refs: Optional[List[str]] = None,
    ) -> PlanningShadowDecision:
        row = PlanningShadowDecision(
            goal_id=goal_id,
            task_class=task_class.lower(),
            failure_context=(failure_context or "")[:1000] if failure_context else None,
            actual_policy=str(actual_policy).upper(),
            shadow_policy=str(shadow_policy).upper(),
            would_diverge=str(actual_policy).upper() != str(shadow_policy).upper(),
            actual_outcome=actual_outcome,
            evidence_refs_json=json.dumps(evidence_refs or []),
            created_at=datetime.utcnow(),
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def list(self, task_class: Optional[str] = None, limit: int = 100) -> List[PlanningShadowDecision]:
        q = self.db.query(PlanningShadowDecision)
        if task_class:
            q = q.filter(PlanningShadowDecision.task_class == task_class.lower())
        return q.order_by(PlanningShadowDecision.created_at.desc()).limit(limit).all()


class PlanningCounterfactualStore:
    """Counterfactual hypotheses (§16) — never fabricates outcomes."""

    def __init__(self, session):
        self.db = session

    def record(
        self,
        *,
        actual_action: str,
        counterfactual_candidate: str,
        goal_id: Optional[str] = None,
        evidence_refs: Optional[List[str]] = None,
        hypothesis: Optional[str] = None,
    ) -> PlanningCounterfactual:
        row = PlanningCounterfactual(
            goal_id=goal_id,
            actual_action=str(actual_action),
            counterfactual_candidate=str(counterfactual_candidate),
            evidence_refs_json=json.dumps(evidence_refs or []),
            claimed_outcome=None,
            hypothesis=(hypothesis or "")[:1000] if hypothesis else None,
            created_at=datetime.utcnow(),
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def list(self, goal_id: Optional[str] = None, limit: int = 100) -> List[PlanningCounterfactual]:
        q = self.db.query(PlanningCounterfactual)
        if goal_id:
            q = q.filter(PlanningCounterfactual.goal_id == goal_id)
        return q.order_by(PlanningCounterfactual.created_at.desc()).limit(limit).all()
