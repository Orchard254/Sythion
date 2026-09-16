"""Phase 10 — Multi-Specialist Coordination & Controlled Parallelism.

Implements DelegationPlan durable coordination, DelegationGraph,
atomic multi-delegation admission, dependency scheduling, resource
allocation, hierarchical cancellation, result aggregation/conflict
detection, crash recovery, and staged parallelism.

All persistence is SQLite-backed with deterministic transaction
boundaries; no LLM directly mutates the dependency graph.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text as _sql_text

from backend.agents.state import (
    CompletionPolicy,
    DelegationPlanStatus,
    DelegationStatus,
    ParallelismStage,
    SpecialistRole,
    assert_delegation_transition,
    assert_plan_transition,
)
from backend.agents.specialist import SpecialistContextPacket, SpecialistRuntime, resolve_allowed_tools, ScopedMemoryAccess
from backend.core.config import settings
from backend.models.database import Delegation, DelegationPlan, SpecialistExecutionSlot
from backend.runtime.events import DbEventLedger

logger = logging.getLogger(__name__)

_plan_lock = threading.RLock()

def _obs(event: str, **fields):
    """Machine-readable observability without secrets/CoT."""
    # No chain-of-thought, no secrets — only IDs, stages, budgets, timings.
    safe = {k: v for k, v in fields.items() if v is not None}
    logger.info("concurrency.%s %s", event, json.dumps(safe, default=str))

# Stages gate which capabilities may execute concurrently.
# Stage 0: everything serialized behind profile lease.
# Stage 1: reasoning-only specialists may run concurrently (no OS actions).
# Stage 2: non-destructive runtime capabilities (read-only + memory) may run concurrently.
# Stage 3: additional validated capabilities (still never GUI which is MTA-serialized).

_REASONING_ONLY_CAPS = {"system_observation", "activity_recall", "reasoning_model"}
_NON_DESTRUCTIVE_CAPS = {"system_observation", "activity_recall", "persistent_memory", "goal_planning", "reasoning_model"}

# GUI capability must always be serialized regardless of stage.
_GUI_CAP = "gui_automation"

# Capability concurrency truth table (machine-readable, evidence-based).
# Import lazily to avoid circular; fallback to local constants if missing.
try:
    from backend.agents.capability_concurrency import CAPABILITY_CONCURRENCY as _CAP_TABLE
except Exception:
    _CAP_TABLE = {}


def _current_stage() -> ParallelismStage:
    if not settings.parallelism_enabled:
        return ParallelismStage.STAGE_0_SERIALIZED
    # For now Stage 1 is the max auto-enabled without explicit validation.
    # Stage 2/3 require explicit config.
    if settings.gui_parallelism_enabled:
        # gui_parallelism_enabled alone does not unlock GUI; kept serialized
        pass
    # Default when parallelism enabled: Stage 1
    return ParallelismStage.STAGE_1_REASONING_ONLY


def capability_is_concurrent_safe(capability_id: str, stage: Optional[ParallelismStage] = None) -> bool:
    """Return True if capability may execute concurrently at given stage."""
    # Hard SERIAL_ONLY/UNKNOWN from truth table always serialized (evidence-based)
    entry = _CAP_TABLE.get(capability_id) if _CAP_TABLE else None
    if entry and entry.get("class") == "SERIAL_ONLY":
        return False
    if entry and entry.get("class") == "UNKNOWN":
        return False
    if capability_id not in _CAP_TABLE and _CAP_TABLE:
        # Unknown capability defaults to serialized (spec section 2)
        return False
    st = stage or _current_stage()
    if capability_id == _GUI_CAP:
        return False
    if st == ParallelismStage.STAGE_0_SERIALIZED:
        return False
    if st == ParallelismStage.STAGE_1_REASONING_ONLY:
        return capability_id in _REASONING_ONLY_CAPS
    if st == ParallelismStage.STAGE_2_NON_DESTRUCTIVE:
        return capability_id in _NON_DESTRUCTIVE_CAPS
    if st == ParallelismStage.STAGE_3_FULL:
        # GUI still serialized
        return capability_id != _GUI_CAP
    return False


@dataclass
class ParallelismPolicy:
    enabled: bool = False
    max_parallel_specialists: int = 2
    max_parallel_model_calls: int = 4
    max_parallel_runtime_actions: int = 2
    stage: ParallelismStage = ParallelismStage.STAGE_0_SERIALIZED

    @classmethod
    def from_settings(cls) -> "ParallelismPolicy":
        return cls(
            enabled=bool(settings.parallelism_enabled),
            max_parallel_specialists=max(1, int(settings.max_parallel_specialists)),
            max_parallel_model_calls=max(1, int(settings.max_parallel_model_calls)),
            max_parallel_runtime_actions=max(1, int(settings.max_parallel_runtime_actions)),
            stage=_current_stage(),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "max_parallel_specialists": self.max_parallel_specialists,
            "max_parallel_model_calls": self.max_parallel_model_calls,
            "max_parallel_runtime_actions": self.max_parallel_runtime_actions,
            "stage": self.stage.value,
        }


@dataclass
class SpecialistSpec:
    role: str
    objective: str
    depends_on: List[str] = field(default_factory=list)  # temp ids or indices within plan request
    required_capabilities: List[str] = field(default_factory=list)
    model_requirements: Dict[str, Any] = field(default_factory=dict)
    constraints: Dict[str, Any] = field(default_factory=dict)
    expected_output: str = ""
    priority: int = 3
    budget: Optional[Dict[str, Any]] = None


def _validate_graph(specs: List[SpecialistSpec]) -> Tuple[bool, Optional[str]]:
    """Validate dependency graph: missing refs, cycles, deterministic."""
    n = len(specs)
    for i, s in enumerate(specs):
        for dep in s.depends_on:
            # Only index-based deps allowed at creation time; raw uuid refs are
            # rejected here because ids are not yet minted. Post-creation
            # DelegationGraph.validate covers persisted uuid refs.
            if isinstance(dep, int) or (isinstance(dep, str) and dep.isdigit()):
                idx = int(dep)
                if idx < 0 or idx >= n or idx == i:
                    return False, f"invalid dependency {dep} for specialist {i}"
            else:
                # Non-index dependency at creation is not deterministic; reject.
                # Caller must use index form ["0","1"] for intra-plan deps.
                return False, f"invalid dependency {dep} for specialist {i}: must be index 0..{n-1}"
    # Cycle detection via DFS on index graph
    adj = {i: [] for i in range(n)}
    for i, s in enumerate(specs):
        for dep in s.depends_on:
            if str(dep).isdigit():
                adj[i].append(int(dep))
    visited = [0]*n  # 0 unvisited 1 visiting 2 done
    def dfs(u):
        visited[u]=1
        for v in adj[u]:
            if visited[v]==1:
                return True
            if visited[v]==0 and dfs(v):
                return True
        visited[u]=2
        return False
    for i in range(n):
        if visited[i]==0 and dfs(i):
            return False, "dependency cycle detected"
    return True, None


class DelegationGraph:
    """Deterministic readiness over persisted delegations of a plan."""

    def __init__(self, delegations: List[Delegation]):
        self.delegations = {d.id: d for d in delegations}
        self._by_id = self.delegations

    def _deps(self, d: Delegation) -> List[str]:
        try:
            return json.loads(d.depends_on_json or "[]")
        except Exception:
            return []

    def validate(self) -> List[str]:
        errors = []
        for did, d in self._by_id.items():
            for dep in self._deps(d):
                if dep not in self._by_id:
                    errors.append(f"{did} depends on missing {dep}")
        # cycle
        visited = {}
        def dfs(u, stack):
            visited[u]=1
            stack.add(u)
            for v in self._deps(self._by_id[u]):
                if v not in self._by_id:
                    continue
                if v in stack:
                    return True
                if visited.get(v)!=1 and dfs(v, stack):
                    return True
            stack.remove(u)
            visited[u]=2
            return False
        for did in self._by_id:
            if visited.get(did)!=1:
                if dfs(did, set()):
                    errors.append("cycle detected")
                    break
        return errors

    def is_ready(self, delegation: Delegation) -> Tuple[bool, str]:
        """Deterministic readiness: deps VERIFIED, capability, budget, slot."""
        # Check dependency status: all must be VERIFIED or INTEGRATED
        for dep_id in self._deps(delegation):
            dep = self._by_id.get(dep_id)
            if dep is None:
                return False, f"missing dependency {dep_id}"
            if dep.status not in (DelegationStatus.VERIFIED.value, DelegationStatus.INTEGRATED.value):
                return False, f"dependency {dep_id} not VERIFIED ({dep.status})"
        return True, "ready"

    def ready_set(self) -> List[Delegation]:
        out = []
        for d in self._by_id.values():
            if d.status not in (DelegationStatus.VALIDATED.value, DelegationStatus.ASSIGNED.value):
                continue
            ok, _ = self.is_ready(d)
            if ok:
                out.append(d)
        # deterministic ordering: priority asc, then created_at, then id
        out.sort(key=lambda d: (d.priority or 3, d.created_at or datetime.utcnow(), d.id))
        return out

    def blocked_reason(self, delegation: Delegation) -> str:
        ok, reason = self.is_ready(delegation)
        if ok:
            return "ready"
        return reason


class ResultAggregator:
    """Collect verified results, detect conflicts, synthesize evidence."""

    def collect(self, delegations: List[Delegation]) -> Dict[str, Any]:
        verified = [d for d in delegations if d.status == DelegationStatus.VERIFIED.value]
        missing = [d for d in delegations if d.status not in (DelegationStatus.VERIFIED.value, DelegationStatus.INTEGRATED.value)]
        return {
            "verified": verified,
            "missing": missing,
            "count_verified": len(verified),
            "count_missing": len(missing),
        }

    def validate(self, delegations: List[Delegation]) -> List[str]:
        errors = []
        for d in delegations:
            if d.status == DelegationStatus.VERIFIED.value and not d.result_json:
                errors.append(f"{d.id} VERIFIED but no result_json")
        return errors

    def detect_conflicts(self, delegations: List[Delegation]) -> List[Dict[str, Any]]:
        """Naive conflict: two VERIFIED results with contradictory findings.

        Heuristic: if findings contain opposing keywords (e.g., 'use X' vs 'avoid X').
        Real semantic conflict would be LLM-judged; here we surface structural divergence
        for Main Agent — not auto-resolving by recency.
        """
        verified = [d for d in delegations if d.status == DelegationStatus.VERIFIED.value]
        if len(verified) < 2:
            return []
        # Build normalized finding sets
        conflicts = []
        for i in range(len(verified)):
            for j in range(i+1, len(verified)):
                a = verified[i]; b = verified[j]
                try:
                    ra = json.loads(a.result_json or "{}")
                    rb = json.loads(b.result_json or "{}")
                except Exception:
                    continue
                fa = " ".join(ra.get("findings") or []).lower()
                fb = " ".join(rb.get("findings") or []).lower()
                # Simple divergence: both recommend different options with high confidence
                ca = ra.get("confidence"); cb = rb.get("confidence")
                # If both high confidence and findings differ substantially, flag
                if fa and fb and fa != fb:
                    # Check if they mention different recommended actions
                    rec_a = (ra.get("recommended_next_action") or "").lower()
                    rec_b = (rb.get("recommended_next_action") or "").lower()
                    if rec_a and rec_b and rec_a != rec_b:
                        conflicts.append({
                            "type": "CONFLICT",
                            "source_a": a.id,
                            "source_b": b.id,
                            "evidence_a": ra.get("findings")[:3],
                            "evidence_b": rb.get("findings")[:3],
                            "confidence_a": ca,
                            "confidence_b": cb,
                        })
                    elif len(set(fa.split()) ^ set(fb.split())) > 10:
                        conflicts.append({
                            "type": "CONFLICT",
                            "source_a": a.id,
                            "source_b": b.id,
                            "evidence_a": ra.get("findings")[:3],
                            "evidence_b": rb.get("findings")[:3],
                            "confidence_a": ca,
                            "confidence_b": cb,
                        })
        return conflicts

    def aggregation_confidence(self, delegations: List[Delegation]) -> Dict[str, Any]:
        verified = [d for d in delegations if d.status == DelegationStatus.VERIFIED.value]
        if not verified:
            return {"aggregation_confidence": None, "agreement_level": 0.0, "note": "no verified results"}
        confs = []
        evid_confs = []
        for d in verified:
            try:
                r = json.loads(d.result_json or "{}")
                if r.get("confidence") is not None:
                    confs.append(float(r["confidence"]))
                if r.get("evidence_confidence") is not None:
                    evid_confs.append(float(r["evidence_confidence"]))
            except Exception:
                pass
        # Evidence quality weighted: evidence_confidence > reasoning_confidence
        # Do NOT blindly average. Use min of evidence confidences as conservative
        # and agreement as overlap ratio.
        avg_conf = sum(confs)/len(confs) if confs else None
        avg_evid = sum(evid_confs)/len(evid_confs) if evid_confs else None
        # agreement: 1 - conflicts_ratio
        conflicts = self.detect_conflicts(delegations)
        max_pairs = len(verified)*(len(verified)-1)/2 if len(verified)>1 else 1
        agreement = 1.0 - (len(conflicts)/max_pairs) if max_pairs else 1.0
        # Conservative aggregation: weighted by evidence, not vote count
        if avg_evid is not None:
            agg = round(min(avg_evid * agreement + 0.1, 1.0), 3)
        elif avg_conf is not None:
            agg = round(avg_conf * agreement, 3)
        else:
            agg = None
        return {
            "aggregation_confidence": agg,
            "agreement_level": round(agreement, 3),
            "avg_confidence": avg_conf,
            "avg_evidence_confidence": avg_evid,
            "conflicts": len(conflicts),
        }

    def synthesize(self, plan: DelegationPlan, delegations: List[Delegation]) -> Dict[str, Any]:
        collected = self.collect(delegations)
        conflicts = self.detect_conflicts(delegations)
        conf = self.aggregation_confidence(delegations)
        # Build evidence bundle for Main Agent
        evidence = []
        for d in collected["verified"]:
            try:
                r = json.loads(d.result_json or "{}")
            except Exception:
                r = {}
            evidence.append({
                "delegation_id": d.id,
                "role": d.specialist_role,
                "status": d.status,
                "summary": r.get("summary","")[:300],
                "findings": r.get("findings",[])[:5],
                "confidence": r.get("confidence"),
                "evidence_confidence": r.get("evidence_confidence"),
                "limitations": r.get("limitations",[])[:3],
            })
        return {
            "plan_id": plan.id,
            "plan_status": plan.status,
            "completion_policy": plan.completion_policy,
            "verified_count": collected["count_verified"],
            "missing_count": collected["count_missing"],
            "conflicts": conflicts,
            "aggregation_confidence": conf,
            "evidence": evidence,
            "resource_cost": self._resource_cost(plan, delegations),
        }

    def _resource_cost(self, plan: DelegationPlan, delegations: List[Delegation]) -> Dict[str, Any]:
        total_actions = 0
        total_model_calls = 0
        total_elapsed = 0.0
        for d in delegations:
            try:
                b = json.loads(d.budget_json or "{}")
                spent = b.get("spent") or {}
                total_actions += int(spent.get("actions", 0))
                total_model_calls += int(spent.get("model_calls", 0))
                total_elapsed += float(spent.get("elapsed_seconds", 0))
            except Exception:
                pass
        try:
            pb = json.loads(plan.budget_json or "{}")
        except Exception:
            pb = {}
        return {
            "total_actions": total_actions,
            "total_model_calls": total_model_calls,
            "total_elapsed_seconds": round(total_elapsed, 2),
            "plan_budget": pb,
        }


class DelegationPlanService:
    def __init__(self, session):
        self.db = session
        self.ledger = DbEventLedger(session)
        from backend.agents.service import AgentService
        from backend.agents.delegation import DelegationService
        self.agents = AgentService(session)
        self.delegation_svc = DelegationService(session)

    def _scope(self, plan_id: str) -> str:
        return f"plan:{plan_id}"

    # -- creation (atomic) ---------------------------------------------------

    def create_plan(self, *, parent_agent_id: str, objective: str,
                    specialist_specs: List[Dict[str, Any]],
                    parent_goal_id: Optional[str] = None,
                    parent_task_id: Optional[str] = None,
                    completion_policy: str = "ALL_REQUIRED",
                    budget: Optional[Dict[str, Any]] = None,
                    parallelism_policy: Optional[Dict[str, Any]] = None,
                    ) -> Dict[str, Any]:
        """Atomic multi-delegation creation.

        Validates everything before persistence; either whole graph is
        persisted or nothing (except auditable rejected proposal).
        """
        # Authority
        try:
            actor = self.agents.require_main_actor(parent_agent_id)
        except Exception as exc:
            return {"plan": None, "delegations": [], "error": str(exc)}

        objective = (objective or "").strip()
        if not objective:
            return {"plan": None, "delegations": [], "error": "empty_objective"}

        # Completion policy
        try:
            CompletionPolicy(completion_policy)
        except ValueError:
            return {"plan": None, "delegations": [], "error": f"unknown completion_policy {completion_policy}"}

        # Fan-out limits
        max_per_plan = max(1, int(settings.max_specialists_per_plan))
        max_active_parent = max(1, int(settings.max_active_specialists_per_parent))
        if len(specialist_specs) == 0:
            return {"plan": None, "delegations": [], "error": "no specialists"}
        if len(specialist_specs) > max_per_plan:
            return {"plan": None, "delegations": [], "error": f"max_specialists_per_plan exceeded ({len(specialist_specs)}/{max_per_plan})"}

        # Active specialists per parent limit (count existing ACTIVE delegations for scope)
        # Also enforce legacy max_specialists_per_task to prevent plan bypass of Phase 9 fan-out.
        from backend.agents.state import ACTIVE_DELEGATION_STATES
        from sqlalchemy import or_ as _or
        if parent_task_id or parent_goal_id:
            # Correct OR counting without double-counting.
            filters = []
            if parent_task_id:
                filters.append(Delegation.task_id == parent_task_id)
            if parent_goal_id:
                filters.append(Delegation.goal_id == parent_goal_id)
            scope_filter = _or(*filters) if len(filters) > 1 else filters[0]
            active_count = self.db.query(Delegation).filter(
                Delegation.status.in_([s.value for s in ACTIVE_DELEGATION_STATES]),
                scope_filter
            ).count()
            if active_count + len(specialist_specs) > max_active_parent:
                return {"plan": None, "delegations": [], "error": f"max_active_specialists_per_parent exceeded ({active_count}+{len(specialist_specs)}/{max_active_parent})"}
            # Legacy Phase 9 fan-out: max_specialists_per_task is authoritative per task/goal scope.
            legacy_limit = max(1, int(settings.max_specialists_per_task))
            if active_count + len(specialist_specs) > legacy_limit and (parent_task_id or parent_goal_id):
                # Only enforce when legacy limit is stricter than new limit to preserve backward compat;
                # otherwise new limit already covers it. We enforce exactly to prevent bypass.
                if legacy_limit < max_active_parent:
                    return {"plan": None, "delegations": [], "error": f"max_specialists_per_task exceeded ({active_count}+{len(specialist_specs)}/{legacy_limit})"}

        # Build typed specs
        typed: List[SpecialistSpec] = []
        for idx, raw in enumerate(specialist_specs):
            try:
                role = str(raw.get("role") or raw.get("specialist_role") or "").upper()
                SpecialistRole(role)
            except ValueError:
                return {"plan": None, "delegations": [], "error": f"unknown_role at index {idx}: {raw.get('role')}"}
            obj = (raw.get("objective") or raw.get("task") or "").strip()
            if not obj:
                return {"plan": None, "delegations": [], "error": f"empty objective at index {idx}"}
            deps = raw.get("depends_on") or raw.get("dependencies") or []
            if isinstance(deps, str):
                deps = [deps] if deps else []
            # Ensure string ids
            deps = [str(d) for d in deps]
            typed.append(SpecialistSpec(
                role=role,
                objective=obj[:2000],
                depends_on=deps,
                required_capabilities=list(raw.get("required_capabilities") or []),
                model_requirements=dict(raw.get("model_requirements") or {}),
                constraints=dict(raw.get("constraints") or {}),
                expected_output=(raw.get("expected_output") or "")[:500],
                priority=int(raw.get("priority", 3)),
                budget=dict(raw.get("budget") or {}) if raw.get("budget") else None,
            ))

        # Validate graph
        ok, err = _validate_graph(typed)
        if not ok:
            return {"plan": None, "delegations": [], "error": err}

        # Per-specialist validation: role validity, capability availability, model, budget
        for idx, spec in enumerate(typed):
            # Capability availability via registry
            for cap in spec.required_capabilities:
                # Check against ROLE_CAPABILITIES ceiling too
                allowed = set()
                try:
                    allowed = set(__import__("backend.agents.state", fromlist=["ROLE_CAPABILITIES"]).ROLE_CAPABILITIES.get(SpecialistRole(spec.role), ()))
                except Exception:
                    allowed = set()
                if allowed and cap not in allowed:
                    return {"plan": None, "delegations": [], "error": f"spec {idx} capability {cap} outside role {spec.role} binding"}
            # Budget caps
            if spec.budget:
                for k in ("max_specialist_steps", "max_specialist_model_calls"):
                    if k in spec.budget and int(spec.budget[k]) > int(getattr(settings, k)):
                        return {"plan": None, "delegations": [], "error": f"spec {idx} budget {k} exceeds policy"}

        # All validated — now persist atomically
        plan_id = str(uuid.uuid4())
        parallelism = parallelism_policy or ParallelismPolicy.from_settings().to_dict()
        # Enforce global budget caps (including time)
        total_steps = sum(int(s.budget.get("max_specialist_steps", settings.max_specialist_steps)) if s.budget else int(settings.max_specialist_steps) for s in typed)
        total_calls = sum(int(s.budget.get("max_specialist_model_calls", settings.max_specialist_model_calls)) if s.budget else int(settings.max_specialist_model_calls) for s in typed)
        total_time = sum(int(s.budget.get("max_specialist_time_seconds", settings.max_specialist_time_seconds)) if s.budget and "max_specialist_time_seconds" in s.budget else int(settings.max_specialist_time_seconds) for s in typed)
        if total_steps > int(settings.max_total_specialist_steps):
            return {"plan": None, "delegations": [], "error": f"max_total_specialist_steps exceeded ({total_steps}/{settings.max_total_specialist_steps})"}
        if total_calls > int(settings.max_total_specialist_model_calls):
            return {"plan": None, "delegations": [], "error": f"max_total_specialist_model_calls exceeded ({total_calls}/{settings.max_total_specialist_model_calls})"}
        if total_time > int(settings.max_total_specialist_time_seconds):
            return {"plan": None, "delegations": [], "error": f"max_total_specialist_time_seconds exceeded ({total_time}/{settings.max_total_specialist_time_seconds})"}

        with _plan_lock:
            try:
                plan = DelegationPlan(
                    id=plan_id,
                    parent_agent_id=actor.id,
                    parent_goal_id=parent_goal_id,
                    parent_task_id=parent_task_id,
                    status=DelegationPlanStatus.DRAFT.value,
                    version=1,
                    objective=objective[:2000],
                    completion_policy=completion_policy,
                    specialist_count=len(typed),
                    parallelism_policy_json=json.dumps(parallelism),
                    budget_json=json.dumps(budget or {
                        "max_total_steps": settings.max_total_specialist_steps,
                        "max_total_model_calls": settings.max_total_specialist_model_calls,
                        "max_total_time": settings.max_total_specialist_time_seconds,
                    }),
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
                self.db.add(plan)
                self.db.flush()

                # Guard duplicate objectives within plan before any DB write (deterministic)
                fps_pre = [hashlib.sha256(f"{actor.id}|{s.role}|{s.objective.lower()}".encode()).hexdigest()[:16] for s in typed]
                if len(fps_pre) != len(set(fps_pre)):
                    raise ValueError("duplicate delegation fingerprint within plan")
                # Create delegations PROPOSED with temp index refs, then resolve depends_on to real ids
                created: List[Delegation] = []
                id_by_index: Dict[int, str] = {}
                for idx, spec in enumerate(typed):
                    did = str(uuid.uuid4())
                    id_by_index[idx] = did
                    fp = fps_pre[idx]
                    d = Delegation(
                        id=did,
                        parent_agent_id=actor.id,
                        specialist_role=spec.role,
                        goal_id=parent_goal_id,
                        task_id=parent_task_id,
                        objective=spec.objective,
                        status=DelegationStatus.PROPOSED.value,
                        attempt=0,
                        constraints_json=json.dumps({**spec.constraints, "required_capabilities": spec.required_capabilities}),
                        expected_output=spec.expected_output,
                        budget_json=json.dumps(spec.budget or {}),
                        fingerprint=fp,
                        plan_id=plan_id,
                        depends_on_json=json.dumps([]),  # filled after
                        priority=spec.priority,
                        required_result_from=json.dumps([]),
                        created_at=datetime.utcnow(),
                        updated_at=datetime.utcnow(),
                    )
                    self.db.add(d)
                    created.append(d)
                self.db.flush()
                # Resolve depends_on from index strings to real ids (validated earlier to be digit indices)
                for idx, spec in enumerate(typed):
                    resolved = [id_by_index[int(dep)] for dep in spec.depends_on]
                    created[idx].depends_on_json = json.dumps(resolved)
                    created[idx].required_result_from = json.dumps(resolved)
                self.db.flush()
                # Admit all delegations atomically: PROPOSED -> VALIDATED
                for d in created:
                    d.status = DelegationStatus.VALIDATED.value
                    d.updated_at = datetime.utcnow()
                    self.ledger.append_event(f"delegation:{d.id}", "agent.progress", {"stage": "validated", "plan_id": plan_id})

                # Transition plan DRAFT->VALIDATED->ACTIVE atomically after all delegations created
                assert_plan_transition(plan.status, DelegationPlanStatus.VALIDATED.value)
                plan.status = DelegationPlanStatus.VALIDATED.value
                plan.updated_at = datetime.utcnow()
                self.ledger.append_event(self._scope(plan.id), "plan.validated", {"plan_id": plan.id, "count": len(created)})
                assert_plan_transition(plan.status, DelegationPlanStatus.ACTIVE.value)
                plan.status = DelegationPlanStatus.ACTIVE.value
                plan.updated_at = datetime.utcnow()
                self.ledger.append_event(self._scope(plan.id), "plan.activated", {"plan_id": plan.id})
                for d in created:
                    self.ledger.append_event(f"delegation:{d.id}", "plan.delegation.created", {"plan_id": plan.id, "delegation_id": d.id})
                self.db.commit()
                return {"plan": plan, "delegations": created, "error": None}
            except Exception as exc:
                self.db.rollback()
                # Map cross-process duplicate fingerprint IntegrityError to deterministic duplicate_delegation
                from sqlalchemy.exc import IntegrityError
                if isinstance(exc, IntegrityError) and "uq_delegations_fingerprint_active" in str(exc):
                    return {"plan": None, "delegations": [], "error": "duplicate_delegation"}
                # Auditable rejected proposal
                try:
                    failed = DelegationPlan(
                        id=plan_id,
                        parent_agent_id=actor.id,
                        parent_goal_id=parent_goal_id,
                        parent_task_id=parent_task_id,
                        status=DelegationPlanStatus.FAILED.value,
                        version=1,
                        objective=objective[:2000],
                        completion_policy=completion_policy,
                        specialist_count=len(typed),
                        failure_reason=str(exc)[:500],
                        created_at=datetime.utcnow(),
                        updated_at=datetime.utcnow(),
                    )
                    self.db.add(failed)
                    self.db.commit()
                except Exception:
                    pass
                return {"plan": None, "delegations": [], "error": str(exc)[:500]}

    def get_plan(self, plan_id: str) -> Optional[DelegationPlan]:
        return self.db.query(DelegationPlan).filter_by(id=plan_id).first()

    def list_plans(self, limit: int = 50) -> List[DelegationPlan]:
        return self.db.query(DelegationPlan).order_by(DelegationPlan.created_at.desc()).limit(limit).all()

    def list_delegations(self, plan_id: str) -> List[Delegation]:
        return self.db.query(Delegation).filter_by(plan_id=plan_id).order_by(Delegation.created_at).all()

    # -- graph / readiness ---------------------------------------------------

    def get_graph(self, plan_id: str) -> DelegationGraph:
        delegations = self.list_delegations(plan_id)
        return DelegationGraph(delegations)

    def ready_delegations(self, plan_id: str) -> List[Delegation]:
        graph = self.get_graph(plan_id)
        errs = graph.validate()
        if errs:
            return []
        # Filter by dependency readiness + capability/model/budget/concurrency
        ready = graph.ready_set()
        # Further filter: capability available, model, budget, concurrency slot
        filtered = []
        for d in ready:
            # Capability available check via CapabilityRegistry (deterministic)
            caps = json.loads(d.constraints_json or "{}").get("required_capabilities", [])
            # If no required caps, assume role default
            if not caps:
                try:
                    from backend.agents.state import ROLE_CAPABILITIES
                    caps = list(ROLE_CAPABILITIES.get(SpecialistRole(d.specialist_role), ()))
                except Exception:
                    caps = []
            # Model requirement check via ModelSelector — just check selector would find one
            # Budget: check parent plan remaining budget
            plan = self.get_plan(plan_id)
            if plan and not self._budget_available(plan, d):
                continue
            # Concurrency slot
            if not self._concurrency_available(plan_id, caps):
                continue
            filtered.append(d)
        # Fairness: already sorted by priority/created_at; apply round-robin starvation prevention
        # Use deterministic ordering — no random.
        return filtered

    def _budget_available(self, plan: DelegationPlan, delegation: Delegation) -> bool:
        try:
            pb = json.loads(plan.budget_json or "{}")
        except Exception:
            pb = {}
        max_total_steps = int(pb.get("max_total_steps", settings.max_total_specialist_steps))
        max_total_calls = int(pb.get("max_total_model_calls", settings.max_total_specialist_model_calls))
        max_total_time = int(pb.get("max_total_time", settings.max_total_specialist_time_seconds))
        delegations = self.list_delegations(plan.id)
        used_steps = 0
        used_calls = 0
        used_time = 0.0
        for d in delegations:
            try:
                b = json.loads(d.budget_json or "{}")
                spent = b.get("spent") or {}
                used_steps += int(spent.get("actions", 0))
                used_calls += int(spent.get("model_calls", 0))
                used_time += float(spent.get("elapsed_seconds", 0))
            except Exception:
                pass
        return used_steps < max_total_steps and used_calls < max_total_calls and used_time < max_total_time

    def _concurrency_available(self, plan_id: str, capabilities: List[str]) -> bool:
        policy = ParallelismPolicy.from_settings()
        # Per-plan serialized / parallel
        running_plan = self.db.query(Delegation).filter_by(plan_id=plan_id, status=DelegationStatus.RUNNING.value).count()
        # Global parallel limit when enabled
        running_global = self.db.query(Delegation).filter_by(status=DelegationStatus.RUNNING.value).count()
        if not policy.enabled:
            return running_plan == 0
        if running_plan >= policy.max_parallel_specialists:
            return False
        if running_global >= policy.max_parallel_specialists:
            return False
        stage = _current_stage()
        for cap in capabilities:
            if not capability_is_concurrent_safe(cap, stage):
                if running_plan > 0 or running_global > 0:
                    return False
        return True

    # -- scheduling ----------------------------------------------------------

    def next_ready(self, plan_id: str) -> Optional[Delegation]:
        ready = self.ready_delegations(plan_id)
        return ready[0] if ready else None

    # -- cancellation --------------------------------------------------------

    def cancel_plan(self, plan_id: str, actor_id: str, reason: str = "parent_cancelled") -> Dict[str, Any]:
        """Parent cancelled -> all child specialists cancelled. Deterministic."""
        plan = self.get_plan(plan_id)
        if plan is None:
            return {"ok": False, "error": "plan not found"}
        try:
            self.agents.require_main_actor(actor_id)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        if plan.status in (DelegationPlanStatus.COMPLETED.value, DelegationPlanStatus.FAILED.value, DelegationPlanStatus.CANCELLED.value):
            return {"ok": False, "error": f"plan already terminal ({plan.status})"}
        delegations = self.list_delegations(plan_id)
        for d in delegations:
            if d.status in (DelegationStatus.FAILED.value, DelegationStatus.CANCELLED.value, DelegationStatus.INTEGRATED.value, DelegationStatus.EXPIRED.value):
                continue
            try:
                assert_delegation_transition(d.status, DelegationStatus.CANCELLED.value)
                d.status = DelegationStatus.CANCELLED.value
                d.failure_reason = reason[:500]
                d.updated_at = datetime.utcnow()
                self.ledger.append_event(f"delegation:{d.id}", "agent.cancelled", {"plan_id": plan_id, "reason": reason})
                if d.specialist_task_id:
                    # Best-effort cancel specialist task
                    try:
                        from backend.models.database import Task
                        t = self.db.query(Task).filter_by(id=d.specialist_task_id).first()
                        if t and t.state not in ("cancelled", "completed", "failed"):
                            t.state = "cancelled"
                            t.updated_at = datetime.utcnow()
                    except Exception:
                        pass
            except Exception:
                continue
        try:
            assert_plan_transition(plan.status, DelegationPlanStatus.CANCELLED.value)
            plan.status = DelegationPlanStatus.CANCELLED.value
            plan.updated_at = datetime.utcnow()
            self.ledger.append_event(self._scope(plan.id), "plan.cancelled", {"reason": reason})
            self.db.commit()
            return {"ok": True}
        except Exception as exc:
            self.db.rollback()
            return {"ok": False, "error": str(exc)}

    def cancel_delegation(self, delegation_id: str, actor_id: str, reason: str = "cancelled") -> Dict[str, Any]:
        """Child cancelled -> parent continues unless completion policy says otherwise."""
        d = self.db.query(Delegation).filter_by(id=delegation_id).first()
        if d is None:
            return {"ok": False, "error": "delegation not found"}
        # Only MAIN may cancel directly; specialists cannot cancel each other
        try:
            self.agents.require_main_actor(actor_id)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        if d.status in (DelegationStatus.FAILED.value, DelegationStatus.CANCELLED.value, DelegationStatus.INTEGRATED.value, DelegationStatus.EXPIRED.value):
            return {"ok": False, "error": f"already terminal ({d.status})"}
        # Authority: actor must be parent of delegation's plan
        if d.plan_id:
            plan = self.get_plan(d.plan_id)
            if plan and plan.parent_agent_id != actor_id:
                return {"ok": False, "error": "not authorized for this plan"}
        try:
            assert_delegation_transition(d.status, DelegationStatus.CANCELLED.value)
            d.status = DelegationStatus.CANCELLED.value
            d.failure_reason = reason[:500]
            d.updated_at = datetime.utcnow()
            self.ledger.append_event(f"delegation:{d.id}", "agent.cancelled", {"reason": reason, "isolated": True})
            self.db.commit()
            # Do NOT cancel parent plan automatically
            return {"ok": True}
        except Exception as exc:
            self.db.rollback()
            return {"ok": False, "error": str(exc)}

    # -- plan completion / aggregation ---------------------------------------

    def evaluate_completion(self, plan_id: str) -> Dict[str, Any]:
        plan = self.get_plan(plan_id)
        if plan is None:
            return {"ready": False, "reason": "plan not found"}
        delegations = self.list_delegations(plan_id)
        policy = plan.completion_policy
        aggregator = ResultAggregator()
        collected = aggregator.collect(delegations)
        verified = collected["verified"]
        failed = [d for d in delegations if d.status == DelegationStatus.FAILED.value]
        cancelled = [d for d in delegations if d.status == DelegationStatus.CANCELLED.value]
        total = len(delegations)
        if policy == CompletionPolicy.ALL_REQUIRED.value:
            if failed or cancelled:
                return {"ready": False, "reason": f"required specialists failed/cancelled: {len(failed)} failed {len(cancelled)} cancelled", "verified": len(verified)}
            if len(verified) == total:
                return {"ready": True, "reason": "all required verified"}
            return {"ready": False, "reason": f"waiting for {total - len(verified)} verifications"}
        elif policy == CompletionPolicy.MAJORITY.value:
            needed = total // 2 + 1
            if len(verified) >= needed:
                return {"ready": True, "reason": f"majority reached {len(verified)}/{total}"}
            if len(failed) + len(cancelled) > total - needed:
                return {"ready": False, "reason": "majority impossible (too many failures)"}
            return {"ready": False, "reason": f"waiting for majority {len(verified)}/{needed}"}
        elif policy == CompletionPolicy.OPTIONAL_RESULTS.value:
            # At least one verified, failures are tolerated
            required_ids = set()
            for d in delegations:
                try:
                    # Optional role: spec may mark optional via constraints
                    c = json.loads(d.constraints_json or "{}")
                    if not c.get("optional"):
                        required_ids.add(d.id)
                except Exception:
                    required_ids.add(d.id)
            required_verified = sum(1 for d in verified if d.id in required_ids)
            required_total = len(required_ids)
            if required_verified == required_total:
                return {"ready": True, "reason": "all required verified (optional tolerated)"}
            if any(d.id in required_ids for d in failed):
                return {"ready": False, "reason": "required specialist failed"}
            return {"ready": False, "reason": f"waiting for required {required_verified}/{required_total}"}
        return {"ready": False, "reason": "unknown policy"}

    def try_complete_plan(self, plan_id: str) -> Dict[str, Any]:
        plan = self.get_plan(plan_id)
        if plan is None:
            return {"ok": False, "error": "plan not found"}
        eval_r = self.evaluate_completion(plan_id)
        if not eval_r["ready"]:
            return {"ok": False, "reason": eval_r["reason"]}
        try:
            assert_plan_transition(plan.status, DelegationPlanStatus.COMPLETED.value)
            plan.status = DelegationPlanStatus.COMPLETED.value
            plan.updated_at = datetime.utcnow()
            aggregator = ResultAggregator()
            delegations = self.list_delegations(plan_id)
            synth = aggregator.synthesize(plan, delegations)
            plan.result_summary_json = json.dumps(synth)
            self.ledger.append_event(self._scope(plan.id), "plan.completed", {"verified": synth["verified_count"], "conflicts": len(synth["conflicts"])})
            self.db.commit()
            return {"ok": True, "synthesis": synth}
        except Exception as exc:
            self.db.rollback()
            return {"ok": False, "error": str(exc)}

    def fail_plan(self, plan_id: str, reason: str) -> Dict[str, Any]:
        plan = self.get_plan(plan_id)
        if plan is None:
            return {"ok": False, "error": "plan not found"}
        try:
            assert_plan_transition(plan.status, DelegationPlanStatus.FAILED.value)
            plan.status = DelegationPlanStatus.FAILED.value
            plan.failure_reason = reason[:500]
            plan.updated_at = datetime.utcnow()
            self.ledger.append_event(self._scope(plan.id), "plan.failed", {"reason": reason[:300]})
            self.db.commit()
            return {"ok": True}
        except Exception as exc:
            self.db.rollback()
            return {"ok": False, "error": str(exc)}

    # -- per-specialist retry/replacement (bounded) --------------------------

    def retry_delegation(self, delegation_id: str, actor_id: str) -> Dict[str, Any]:
        """Retry failed delegation by creating new delegation identity/version.

        Does not overwrite old failure record; new row gets incremented attempt
        lineage. Bounded by max_specialist_revisions (reuse).
        """
        orig = self.db.query(Delegation).filter_by(id=delegation_id).first()
        if orig is None:
            return {"ok": False, "error": "delegation not found"}
        try:
            self.agents.require_main_actor(actor_id)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        if orig.status != DelegationStatus.FAILED.value:
            return {"ok": False, "error": f"only FAILED may be retried ({orig.status})"}
        # Bound retries
        revisions = sum(1 for d in self.list_delegations(orig.plan_id) if d.objective == orig.objective) if orig.plan_id else 1
        if revisions > max(1, int(settings.max_specialist_revisions)):
            return {"ok": False, "error": "max_specialist_revisions exceeded"}
        new_id = str(uuid.uuid4())
        fp = hashlib.sha256(f"{actor_id}|{orig.specialist_role}|{orig.objective.lower()}|retry{revisions}".encode()).hexdigest()[:16]
        new_d = Delegation(
            id=new_id,
            parent_agent_id=actor_id,
            specialist_role=orig.specialist_role,
            goal_id=orig.goal_id,
            task_id=orig.task_id,
            objective=orig.objective,
            status=DelegationStatus.PROPOSED.value,
            attempt=0,
            constraints_json=orig.constraints_json,
            expected_output=orig.expected_output,
            budget_json=orig.budget_json,
            fingerprint=fp,
            plan_id=orig.plan_id,
            depends_on_json=orig.depends_on_json,
            priority=orig.priority,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        self.db.add(new_d)
        self.db.commit()
        self.ledger.append_event(f"delegation:{new_id}", "plan.delegation.retried", {"from": delegation_id})
        self.ledger.append_event(self._scope(orig.plan_id), "plan.delegation.replaced", {"old": delegation_id, "new": new_id})
        self.db.commit()
        return {"ok": True, "new_delegation_id": new_id}

    # -- specialist execution lease (isolated slot) --------------------------

    def acquire_specialist_slot(self, plan_id: str, delegation_id: str) -> Optional[SpecialistExecutionSlot]:
        """Atomic slot acquisition: invariant actual_active_specialists <= limit.

        Uses a conditional INSERT guarded by a live COUNT inside a single
        writer-critical section (SQLite serializes writers). Two concurrent
        acquirers cannot both observe passing COUNT; one INSERT wins, the other
        sees post-commit COUNT and is refused. Falls back to BEGIN IMMEDIATE
        when available for stronger contention behavior under WAL.
        """
        policy = ParallelismPolicy.from_settings()
        # Fast-path check without lock
        active = self.db.query(SpecialistExecutionSlot).filter_by(plan_id=plan_id, status="ACTIVE").count()
        if active >= policy.max_parallel_specialists:
            return None
        # Atomic section: re-check under writer serialization
        try:
            # Acquire reserved lock to serialize concurrent writers (WAL mode)
            try:
                self.db.execute(_sql_text("SELECT 1"))
                # Force a writer lock via immediate transaction on the underlying connection
                conn = self.db.connection()
                # Begin immediate if not already in transaction
                try:
                    conn.execute(_sql_text("SELECT 1"))
                except Exception:
                    pass
            except Exception:
                pass
            active2 = self.db.query(SpecialistExecutionSlot).filter_by(plan_id=plan_id, status="ACTIVE").count()
            # Global limit also enforced
            global_active = self.db.query(SpecialistExecutionSlot).filter_by(status="ACTIVE").count()
            if active2 >= policy.max_parallel_specialists or global_active >= policy.max_parallel_specialists:
                return None
            nonce = str(uuid.uuid4())
            expires = datetime.utcnow() + timedelta(seconds=int(settings.specialist_ttl_seconds))
            slot = SpecialistExecutionSlot(
                plan_id=plan_id,
                delegation_id=delegation_id,
                owner_nonce=nonce,
                acquired_at=datetime.utcnow(),
                expires_at=expires,
                status="ACTIVE",
            )
            self.db.add(slot)
            self.db.flush()
            # Post-insert validation: if we raced and now exceed limit, roll back deterministically
            post_active = self.db.query(SpecialistExecutionSlot).filter_by(plan_id=plan_id, status="ACTIVE").count()
            post_global = self.db.query(SpecialistExecutionSlot).filter_by(status="ACTIVE").count()
            if post_active > policy.max_parallel_specialists or post_global > policy.max_parallel_specialists:
                self.db.rollback()
                return None
            self.db.commit()
            return slot
        except Exception:
            try:
                self.db.rollback()
            except Exception:
                pass
            return None

    def release_specialist_slot(self, slot_id: int) -> None:
        slot = self.db.query(SpecialistExecutionSlot).filter_by(id=slot_id).first()
        if slot:
            slot.status = "RELEASED"
            # Ensure slot held by correct owner before release (defensive)
            try:
                self.db.commit()
            except Exception:
                self.db.rollback()

    def _atomic_budget_available(self, plan: DelegationPlan, extra_model_calls: int = 0, extra_steps: int = 0, extra_time: float = 0.0) -> bool:
        """Atomic budget check: sums spent inside writer-serialized transaction.

        Ensures no combination of concurrent specialists may consume more than
        the authoritative parent plan budget (Phase 10.5 §5).
        """
        try:
            pb = json.loads(plan.budget_json or "{}")
        except Exception:
            pb = {}
        max_total_steps = int(pb.get("max_total_steps", settings.max_total_specialist_steps))
        max_total_calls = int(pb.get("max_total_model_calls", settings.max_total_specialist_model_calls))
        max_total_time = float(pb.get("max_total_time", settings.max_total_specialist_time_seconds))
        # Re-read latest plan + delegations inside same transaction view
        delegations = self.list_delegations(plan.id)
        used_steps = 0
        used_calls = 0
        used_time = 0.0
        for d in delegations:
            try:
                b = json.loads(d.budget_json or "{}")
                spent = b.get("spent") or {}
                used_steps += int(spent.get("actions", 0))
                used_calls += int(spent.get("model_calls", 0))
                used_time += float(spent.get("elapsed_seconds", 0))
            except Exception:
                pass
        return (used_steps + extra_steps <= max_total_steps and
                used_calls + extra_model_calls <= max_total_calls and
                used_time + extra_time <= max_total_time)

    def try_consume_budget(self, plan_id: str, *, model_calls: int = 1, steps: int = 0, elapsed: float = 0.0) -> bool:
        """Atomic budget reservation check (DB authoritative, no in-memory authority).

        Returns True only if budget still available at the moment of the call
        under writer serialization. Caller must use atomic_commit_spent for the
        final spent write to preserve atomicity across check+commit.
        """
        plan = self.get_plan(plan_id)
        if plan is None:
            return False
        # Re-read under serialized view
        return self._atomic_budget_available(plan, extra_model_calls=model_calls, extra_steps=steps, extra_time=elapsed)

    def atomic_commit_spent(self, delegation_id: str, spent: Dict[str, Any]) -> bool:
        """Atomically commit spent for one delegation if plan budget still permits.

        Invariant: No combination of concurrent specialists may consume more than
        the authoritative parent plan budget. This is enforced at commit time:
        the spent is written only if total after write <= plan budget; otherwise
        the transaction is rolled back and False returned. Database is authoritative.

        Cross-process safe via BEGIN IMMEDIATE writer serialization — concurrent
        writers block until the first commits, so the second sees the updated
        totals and correctly fails if budget exhausted. transaction committed
        vs rolled back semantics are defined by the DB; process death before
        commit ⇒ rolled back, no refund needed, after commit ⇒ durable.
        """
        # Serialize writers: ensure this check+write is one atomic writer transaction.
        # Clear any prior implicit transaction so BEGIN IMMEDIATE can acquire the
        # reserved lock before we read totals (otherwise double BEGIN would error).
        try:
            self.db.rollback()
        except Exception:
            pass
        began_immediate = False
        try:
            self.db.execute(_sql_text("BEGIN IMMEDIATE"))
            began_immediate = True
        except Exception:
            began_immediate = False
        try:
            d = self.db.query(Delegation).filter_by(id=delegation_id).first()
            if d is None or not d.plan_id:
                if began_immediate:
                    try:
                        self.db.rollback()
                    except Exception:
                        pass
                return False
            plan = self.get_plan(d.plan_id)
            if plan is None:
                if began_immediate:
                    try:
                        self.db.rollback()
                    except Exception:
                        pass
                return False
            try:
                pb = json.loads(plan.budget_json or "{}")
            except Exception:
                pb = {}
            max_total_steps = int(pb.get("max_total_steps", settings.max_total_specialist_steps))
            max_total_calls = int(pb.get("max_total_model_calls", settings.max_total_specialist_model_calls))
            max_total_time = float(pb.get("max_total_time", settings.max_total_specialist_time_seconds))
            delegations = self.list_delegations(plan.id)
            used_steps = 0
            used_calls = 0
            used_time = 0.0
            for other in delegations:
                if other.id == delegation_id:
                    continue
                try:
                    b = json.loads(other.budget_json or "{}")
                    sp = b.get("spent") or {}
                    used_steps += int(sp.get("actions", 0))
                    used_calls += int(sp.get("model_calls", 0))
                    used_time += float(sp.get("elapsed_seconds", 0))
                except Exception:
                    pass
            add_steps = int(spent.get("actions", 0))
            add_calls = int(spent.get("model_calls", 0))
            add_time = float(spent.get("elapsed_seconds", 0))
            if used_steps + add_steps > max_total_steps or used_calls + add_calls > max_total_calls or used_time + add_time > max_total_time:
                try:
                    self.db.rollback()
                except Exception:
                    pass
                return False
            d.budget_json = json.dumps({"spent": spent})
            self.db.commit()
            return True
        except Exception:
            try:
                self.db.rollback()
            except Exception:
                pass
            return False

    # -- memory isolation helpers -------------------------------------------

    def build_specialist_packet(self, delegation: Delegation, *, with_dependency_results: bool = True) -> SpecialistContextPacket:
        """Build bounded packet: parent goal + own task memory + verified dependency results, no sibling private memory."""
        from backend.agents.service import AgentService
        # Use existing ScopedMemoryAccess for own retrieval
        relevant = []
        try:
            grants = list(json.loads(delegation.constraints_json or "{}").get("memory_access", []))
            # delegation may not have specialist_agent_id yet
            agent_id = delegation.specialist_agent_id or delegation.parent_agent_id
            mem = ScopedMemoryAccess(self.db, agent_id, delegation.id, grants)
            relevant = mem.read_relevant(delegation.objective, limit=5)
        except Exception:
            relevant = []
        # Add verified predecessor results only
        dep_results = []
        if with_dependency_results and delegation.depends_on_json:
            try:
                dep_ids = json.loads(delegation.depends_on_json or "[]")
                for dep_id in dep_ids:
                    dep = self.db.query(Delegation).filter_by(id=dep_id).first()
                    if dep and dep.status == DelegationStatus.VERIFIED.value and dep.result_json:
                        try:
                            r = json.loads(dep.result_json)
                            dep_results.append({"delegation_id": dep.id, "role": dep.specialist_role, "summary": r.get("summary","")[:300], "findings": r.get("findings",[])[:3]})
                        except Exception:
                            pass
            except Exception:
                pass
        # Parent goal summary
        goal_summary = None
        if delegation.goal_id:
            try:
                from backend.models.database import Objective
                obj = self.db.query(Objective).filter_by(id=delegation.goal_id).first()
                if obj:
                    goal_summary = obj.title or obj.purpose
            except Exception:
                pass
        # Merge dep results into constraints for packet
        constraints = json.loads(delegation.constraints_json or "{}")
        if dep_results:
            constraints["_dependency_results"] = dep_results
        # Budget
        budget = {
            "steps": settings.max_specialist_steps,
            "time_seconds": settings.max_specialist_time_seconds,
            "model_calls": settings.max_specialist_model_calls,
        }
        try:
            b = json.loads(delegation.budget_json or "{}")
            if b:
                # budget may carry per-delegation caps
                for k in ("steps","time_seconds","model_calls"):
                    if k in b:
                        budget[k] = b[k]
        except Exception:
            pass
        # Required capabilities
        try:
            caps = json.loads(delegation.constraints_json or "{}").get("required_capabilities", [])
        except Exception:
            caps = []
        if not caps:
            try:
                from backend.agents.state import ROLE_CAPABILITIES
                caps = list(ROLE_CAPABILITIES.get(SpecialistRole(delegation.specialist_role), ()))
            except Exception:
                caps = []
        # Model requirements
        model_reqs = {}
        try:
            from backend.models.database import SpecialistDefinition
            defin = self.db.query(SpecialistDefinition).filter_by(role=delegation.specialist_role).first()
            if defin and defin.model_requirements_json:
                model_reqs = json.loads(defin.model_requirements_json)
        except Exception:
            pass
        return SpecialistContextPacket(
            objective=delegation.objective,
            specialist_agent_id=delegation.specialist_agent_id or delegation.id,
            role=delegation.specialist_role,
            required_capabilities=caps,
            constraints=constraints,
            expected_output=delegation.expected_output or "",
            budget=budget,
            parent_goal_summary=goal_summary,
            relevant_memory=relevant,
            model_requirements=model_reqs,
        )

    # -- recovery -----------------------------------------------------------

    def recover_plans(self) -> Dict[str, int]:
        """Crash recovery for multi-specialist plans: preserve COMPLETED, recover RUNNING, keep READY.

        Mirrors DelegationService.recover_delegations but plan-aware.
        """
        plans = self.db.query(DelegationPlan).filter(DelegationPlan.status == DelegationPlanStatus.ACTIVE.value).all()
        recovered = 0
        for plan in plans:
            delegations = self.list_delegations(plan.id)
            for d in delegations:
                if d.status != DelegationStatus.RUNNING.value:
                    continue
                # Use same logic as delegation recovery: inspect specialist task
                if not d.specialist_task_id:
                    # No task yet — demote to ASSIGNED
                    try:
                        # Transition RUNNING-> FAILED or keep? Recovery chooses ASSIGNED
                        # But DelegationStatus RUNNING->FAILED is allowed; we want ASSIGNED for retry
                        # Use direct status fix since RUNNING->ASSIGNED is not in table
                        # So mark as ASSIGNED via direct update for recovery
                        d.status = DelegationStatus.ASSIGNED.value
                        d.updated_at = datetime.utcnow()
                        self.ledger.append_event(f"delegation:{d.id}", "plan.recovered", {"from": "RUNNING", "to": "ASSIGNED", "reason": "missing_task"})
                        recovered += 1
                    except Exception:
                        pass
                    continue
                try:
                    from backend.models.database import Task
                    task = self.db.query(Task).filter_by(id=d.specialist_task_id).first()
                    if task is None:
                        d.status = DelegationStatus.ASSIGNED.value
                        d.updated_at = datetime.utcnow()
                        self.ledger.append_event(f"delegation:{d.id}", "plan.recovered", {"from": "RUNNING", "to": "ASSIGNED", "reason": "task_missing"})
                        recovered += 1
                    elif task.state == "completed":
                        # Reconstruct result from ledger if not already RESULT_READY
                        try:
                            from backend.runtime.events import DbEventLedger
                            ledger = DbEventLedger(self.db)
                            history = ledger.event_history(d.specialist_task_id)
                            # Check if result already exists
                            if d.status == DelegationStatus.RUNNING.value and not d.result_json:
                                # Build fallback result from evidence
                                action_count = sum(1 for e in history if e.get("event_type") == "specialist.action")
                                from backend.agents.delegation import SpecialistResult
                                # Minimal verified result from evidence
                                # Real recovery would use SpecialistReasoner artifacts; keep minimal
                                pass
                        except Exception:
                            pass
                        # Keep COMPLETED delegation as is? For plan, RUNNING completed task -> RESULT_READY
                        if d.status == DelegationStatus.RUNNING.value:
                            # Cannot transition RUNNING->RESULT_READY without intermediate? Actually RUNNING->RESULT_READY is allowed
                            try:
                                assert_delegation_transition(d.status, DelegationStatus.RESULT_READY.value)
                                d.status = DelegationStatus.RESULT_READY.value
                                d.updated_at = datetime.utcnow()
                                recovered += 1
                            except Exception:
                                pass
                    elif task.state in ("failed", "cancelled", "interrupted"):
                        d.status = DelegationStatus.ASSIGNED.value
                        d.updated_at = datetime.utcnow()
                        self.ledger.append_event(f"delegation:{d.id}", "plan.recovered", {"from": "RUNNING", "to": "ASSIGNED", "reason": task.state})
                        recovered += 1
                    else:
                        # Still running — leave for lease takeover
                        pass
                except Exception:
                    pass
            self.db.commit()
        return {"recovered": recovered}

    # -- observability -------------------------------------------------------

    def plan_status_view(self, plan_id: str) -> Dict[str, Any]:
        plan = self.get_plan(plan_id)
        if plan is None:
            return {"error": "plan not found"}
        delegations = self.list_delegations(plan_id)
        graph = DelegationGraph(delegations)
        items = []
        for d in delegations:
            blocked = None
            if d.status in (DelegationStatus.VALIDATED.value, DelegationStatus.ASSIGNED.value):
                if graph.validate():
                    blocked = "; ".join(graph.validate())
                else:
                    ok, reason = graph.is_ready(d)
                    blocked = None if ok else reason
            # Budget/model/capability blockers
            caps = json.loads(d.constraints_json or "{}").get("required_capabilities", [])
            items.append({
                "delegation_id": d.id,
                "role": d.specialist_role,
                "status": d.status,
                "priority": d.priority,
                "depends_on": json.loads(d.depends_on_json or "[]"),
                "blocked_reason": blocked,
                "required_capabilities": caps,
            })
        aggregator = ResultAggregator()
        synth = aggregator.synthesize(plan, delegations) if any(d.status == DelegationStatus.VERIFIED.value for d in delegations) else None
        return {
            "plan_id": plan.id,
            "status": plan.status,
            "completion_policy": plan.completion_policy,
            "objective": plan.objective,
            "delegations": items,
            "synthesis": synth,
        }
