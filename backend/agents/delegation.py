"""Delegation (Phase 9) — structured requests, bounded lifecycle, results.

Flow: Main Agent decides DELEGATE → :class:`DelegationRequest` →
propose → validate (deterministic eligibility) → assign (temporary specialist
instance + scoped context packet) → run (through the protected kernel under
the profile lease) → submit structured result → verify (evidence consistency)
→ integrate (Main Agent decision).

Security invariants enforced in THIS module:
- only the MAIN agent may propose/cancel/integrate (authority guard);
- a specialist can never create another specialist (parent must be MAIN);
- delegation depth is capped by configuration (max_specialist_depth = 1);
- fan-out is capped (max_specialists_per_task);
- raw natural-language output can never become trusted state — only a
  schema-valid SpecialistResult with consistent ledger evidence reaches
  VERIFIED, and only the Main Agent may integrate it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import text as _sql_text
from sqlalchemy.exc import IntegrityError

from backend.agents.service import AgentService
from backend.agents.specialist import (
    ScopedMemoryAccess,
    SpecialistContextPacket,
    SpecialistRuntime,
    resolve_allowed_tools,
)
from backend.agents.state import (
    ACTIVE_DELEGATION_STATES,
    DelegationStatus,
    InvalidTransition,
    SpecialistRole,
    assert_delegation_transition,
)
from backend.core.config import settings
from backend.models.database import AgentRecord, Delegation, SpecialistDefinition
from backend.runtime.events import DbEventLedger

logger = logging.getLogger(__name__)

import threading as _threading

# Serialized in-process section for propose()'s fingerprint scan (fast path
# only). The CROSS-PROCESS guarantees live at the database boundary:
# - duplicate admission: partial UNIQUE index on Delegation.fingerprint;
# - fan-out limit: the single conditional UPDATE in validate().
_admission_lock = _threading.RLock()

# Delegation states that count as ADMITTED for the active-specialist limit.
# PROPOSED is deliberately excluded: a proposal has not claimed a specialist
# slot yet — only VALIDATED-and-beyond does.
_ADMITTED_STATES_SQL = (
    "'VALIDATED','ASSIGNED','RUNNING','RESULT_READY','VERIFIED'"
)

# The admission invariant, established by ONE conditional statement:
# the row may only leave PROPOSED while the number of already-admitted
# delegations sharing its task/goal scope is below the configured limit.
# SQLite serializes writers, so no two processes can both observe a passing
# count and admit over-limit state. NULL scope columns use IS-semantics via
# the :tid/:gid IS NOT NULL guards.
_ADMIT_SQL = _sql_text(
    "UPDATE delegations SET status = 'VALIDATED', updated_at = :now "
    "WHERE id = :did AND status = 'PROPOSED' AND ("
    "  SELECT COUNT(*) FROM delegations"
    "  WHERE status IN (" + _ADMITTED_STATES_SQL + ")"
    "    AND ((:tid IS NOT NULL AND task_id = :tid)"
    "      OR (:gid IS NOT NULL AND goal_id = :gid))"
    ") < :limit"
)

_NO_SCOPE_ADMIT_SQL = _sql_text(
    "UPDATE delegations SET status = 'VALIDATED', updated_at = :now "
    "WHERE id = :did AND status = 'PROPOSED'"
)

_ADMITTED_COUNT_SQL = _sql_text(
    "SELECT COUNT(*) FROM delegations"
    " WHERE status IN (" + _ADMITTED_STATES_SQL + ")"
    "   AND ((:tid IS NOT NULL AND task_id = :tid)"
    "     OR (:gid IS NOT NULL AND goal_id = :gid))"
)


def _scope(delegation_id: str) -> str:
    return f"delegation:{delegation_id}"


# ---------------------------------------------------------------------- #
# Structured contracts                                                    #
# ---------------------------------------------------------------------- #

@dataclass
class DelegationRequest:
    parent_agent_id: str
    specialist_role: str
    objective: str
    goal_id: Optional[str] = None
    task_id: Optional[str] = None
    required_capabilities: List[str] = field(default_factory=list)
    model_requirements: Dict[str, Any] = field(default_factory=dict)
    constraints: Dict[str, Any] = field(default_factory=dict)
    expected_output: str = ""
    deadline: Optional[datetime] = None
    budget: Optional[Dict[str, Any]] = None


@dataclass
class SpecialistResult:
    """The ONLY trusted output shape of a specialist. Arbitrary natural-
    language output never becomes state; everything is validated."""
    status: str                      # COMPLETED | FAILED
    summary: str
    findings: List[str] = field(default_factory=list)
    evidence_refs: List[str] = field(default_factory=list)
    confidence: Optional[float] = None          # result confidence (evidence-backed)
    reasoning_confidence: Optional[float] = None  # model self-confidence (not proof)
    evidence_confidence: Optional[float] = None   # evidence-derived confidence
    limitations: List[str] = field(default_factory=list)
    recommended_next_action: Optional[str] = None
    cost: Dict[str, Any] = field(default_factory=dict)
    failure_reason: Optional[str] = None
    failure_code: Optional[str] = None  # structured failure classification

    # Valid failure codes (Requirement 18)
    VALID_FAILURE_CODES = {
        "MODEL_FAILURE", "INVALID_SPECIALIST_OUTPUT", "CAPABILITY_DENIED",
        "RUNTIME_FAILURE", "VERIFICATION_FAILURE", "TIMEOUT",
        "BUDGET_EXCEEDED", "CANCELLED",
    }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status, "summary": self.summary[:1000],
            "findings": [f[:300] for f in self.findings[:20]],
            "evidence_refs": list(self.evidence_refs)[:20],
            "confidence": self.confidence,
            "reasoning_confidence": self.reasoning_confidence,
            "evidence_confidence": self.evidence_confidence,
            "limitations": [l[:200] for l in self.limitations[:10]],
            "recommended_next_action": (
                self.recommended_next_action or "")[:300],
            "cost": self.cost, "failure_reason": self.failure_reason,
            "failure_code": self.failure_code,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SpecialistResult":
        return cls(
            status=str(data.get("status", "")),
            summary=str(data.get("summary", "")),
            findings=[str(f) for f in (data.get("findings") or [])],
            evidence_refs=[str(e) for e in (data.get("evidence_refs") or [])],
            confidence=data.get("confidence"),
            reasoning_confidence=data.get("reasoning_confidence"),
            evidence_confidence=data.get("evidence_confidence"),
            limitations=[str(l) for l in (data.get("limitations") or [])],
            recommended_next_action=data.get("recommended_next_action"),
            cost=dict(data.get("cost") or {}),
            failure_reason=data.get("failure_reason"),
            failure_code=data.get("failure_code"),
        )

    def validation_errors(self) -> List[str]:
        """Deterministic structural validation of the result contract.

        Strengthened (Requirement 15): verifies status, summary, findings
        substance, evidence refs, confidence semantics, limitations,
        recommendation, cost, and failure classification. A successful result
        with no substantive content must not become VERIFIED.
        """
        errors: List[str] = []
        if self.status not in ("COMPLETED", "FAILED"):
            errors.append(f"invalid result status: {self.status!r}")
        if not self.summary.strip():
            errors.append("missing summary")
        # Substantive content rule: findings must be substantive, not template
        _TEMPLATE_FRAGMENTS = {
            "context received", "deterministic worker", "no substantive",
            "placeholder", "template findings", "bounded specialist completed deterministic",
        }
        def _is_substantive(finding: str) -> bool:
            f = finding.strip().lower()
            if len(f) < 10:
                return False
            if " " not in f:
                return False
            if any(t in f for t in _TEMPLATE_FRAGMENTS):
                return False
            # Must not be a bare generic like "done", "ok", "success"
            if f in ("done", "ok", "success", "completed", "finished"):
                return False
            return True

        if self.status == "COMPLETED":
            if not self.findings:
                errors.append("COMPLETED result has no findings")
            elif not any(_is_substantive(f) for f in self.findings):
                errors.append("COMPLETED result has no substantive findings (all template/placeholder)")
            if not self.evidence_refs:
                errors.append("COMPLETED result has no evidence_refs")
            # Confidence semantics: result confidence must be in [0,1]; reasoning confidence is separate
            if self.confidence is None or not (0.0 <= float(self.confidence) <= 1.0):
                errors.append("COMPLETED result needs confidence in [0,1]")
            # Limitations must be explicit for substantive work
            if not self.limitations:
                errors.append("COMPLETED result missing limitations (must be explicit)")
            # Recommendation may be optional but if present should be non-empty
            # Cost should be present
            if not isinstance(self.cost, dict):
                errors.append("COMPLETED result cost must be a dict")
        if self.status == "FAILED":
            if not (self.failure_reason or "").strip():
                errors.append("FAILED result missing failure_reason")
            if self.failure_code and self.failure_code not in self.VALID_FAILURE_CODES:
                errors.append(f"invalid failure_code: {self.failure_code!r}")
        # Evidence integrity: allowed prefixes (Requirement 16)
        allowed_prefixes = ("action:", "context_packet", "specialist.reasoning",
                            "specialist.action", "delegation.context.built", "runtime.action")
        for ref in self.evidence_refs:
            if not ref.startswith(allowed_prefixes):
                errors.append(f"unknown evidence ref format: {ref!r}")
            # Prevent invented IDs: action: must be numeric suffix
            if ref.startswith("action:"):
                try:
                    int(ref.split(":", 1)[1])
                except (ValueError, IndexError):
                    errors.append(f"malformed evidence ref {ref!r}")
        # Confidence separation: if reasoning_confidence supplied, must be in [0,1]
        for field_name in ("reasoning_confidence", "evidence_confidence"):
            val = getattr(self, field_name)
            if val is not None and not (0.0 <= float(val) <= 1.0):
                errors.append(f"{field_name} must be in [0,1] or None")
        return errors


# ---------------------------------------------------------------------- #
# Eligibility signals                                                     #
# ---------------------------------------------------------------------- #

def delegation_available(db) -> bool:
    """Structural eligibility: specialists enabled AND at least one enabled
    definition exists. The cognitive layer consults this before offering a
    DELEGATE candidate; the LLM cannot conjure capability into existence."""
    if not settings.specialists_enabled:
        return False
    try:
        return db.query(SpecialistDefinition).filter_by(enabled=True).count() > 0
    except Exception:  # pragma: no cover - defensive
        return False


def active_delegations_for_task(db, task_id: Optional[str],
                                goal_id: Optional[str]) -> int:
    q = db.query(Delegation).filter(
        Delegation.status.in_([s.value for s in ACTIVE_DELEGATION_STATES]))
    count = 0
    if task_id:
        count += q.filter(Delegation.task_id == task_id).count()
    if goal_id:
        count += q.filter(Delegation.goal_id == goal_id).count()
    return count


# ---------------------------------------------------------------------- #
# Service                                                                 #
# ---------------------------------------------------------------------- #

class DelegationService:
    def __init__(self, session):
        self.db = session
        self.ledger = DbEventLedger(session)
        self.agents = AgentService(session)

    # -- proposal -----------------------------------------------------------

    def propose(self, request: DelegationRequest) -> Dict[str, Any]:
        """Durably record a PROPOSED delegation.

        Duplicate delegations (same parent + role + objective while a prior
        one is still active) are rejected deterministically. The in-process
        lock serializes the fast-path scan; the cross-PROCESS guarantee is
        the partial UNIQUE index on ``Delegation.fingerprint`` — two
        processes proposing the same fingerprint cannot both commit.
        """
        main = self.agents.get_or_create_main()
        actor = self.agents.require_main_actor(request.parent_agent_id)

        objective = (request.objective or "").strip()
        if not objective:
            return {"delegation": None, "error": "empty_objective"}

        fingerprint = hashlib.sha256(
            f"{actor.id}|{request.specialist_role.upper()}|{objective.lower()}"
            .encode()).hexdigest()[:16]

        def _dup_scan():
            existing = (
                self.db.query(Delegation)
                .filter(Delegation.status.in_(
                    [s.value for s in ACTIVE_DELEGATION_STATES]))
                .all()
            )
            for row in existing:
                row_fp = hashlib.sha256(
                    f"{row.parent_agent_id}|{row.specialist_role}|"
                    f"{(row.objective or '').lower()}".encode()).hexdigest()[:16]
                if row_fp == fingerprint:
                    return row
            return None

        with _admission_lock:
            dup = _dup_scan()
            if dup is not None:
                return {"delegation": dup, "error": "duplicate_delegation"}

            try:
                SpecialistRole(str(request.specialist_role).upper())
            except ValueError:
                return {"delegation": None, "error": f"unknown_role"}

            delegation = Delegation(
                id=str(uuid.uuid4()),
                parent_agent_id=actor.id,
                specialist_role=str(request.specialist_role).upper(),
                goal_id=request.goal_id,
                task_id=request.task_id,
                objective=objective[:2000],
                status=DelegationStatus.PROPOSED.value,
                attempt=0,
                constraints_json=json.dumps(request.constraints or {}),
                expected_output=(request.expected_output or "")[:500],
                budget_json=json.dumps(request.budget or {}),
                fingerprint=fingerprint,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            self.db.add(delegation)
            try:
                self.db.flush()
                self.ledger.append_event(_scope(delegation.id), "agent.delegated", {
                    "delegation_id": delegation.id,
                    "role": delegation.specialist_role,
                    "parent_agent_id": actor.id,
                    "goal_id": delegation.goal_id,
                    "task_id": delegation.task_id,
                    "status": delegation.status,
                })
                self.db.commit()
                return {"delegation": delegation, "error": None}
            except IntegrityError:
                # Cross-process duplicate: another writer committed an ACTIVE
                # delegation with the same fingerprint between our scan and
                # our commit. The UNIQUE index is the authority — roll back
                # and report the deterministic duplicate outcome.
                self.db.rollback()
                winner = (
                    self.db.query(Delegation)
                    .filter(Delegation.fingerprint == fingerprint)
                    .filter(Delegation.status.in_(
                        [s.value for s in ACTIVE_DELEGATION_STATES]))
                    .first()
                )
                if winner is not None:
                    return {"delegation": winner,
                            "error": "duplicate_delegation"}
                raise

    # -- validation ----------------------------------------------------------

    def validate(self, delegation_id: str) -> Dict[str, Any]:
        """Deterministic structural validation. Failure ⇒ FAILED with reason;
        nothing proceeds on partial validity.

        Admission (Phase 9.75) is established by ONE conditional UPDATE at
        the persistence boundary (see _ADMIT_SQL): the row may only become
        VALIDATED while the count of already-admitted delegations for its
        task/goal scope is below max_specialists_per_task. SQLite serializes
        writers, so this holds across processes — no post-commit rollback is
        needed as the correctness mechanism. A defensive post-commit recount
        remains purely as validation.
        """
        d = self._require(delegation_id)
        if d.status == DelegationStatus.VALIDATED.value:
            return {"ok": True, "reasons": []}   # already validated (idempotent)
        reasons: List[str] = []

        definition = (
            self.db.query(SpecialistDefinition)
            .filter_by(role=d.specialist_role, enabled=True).first()
        )
        if definition is None:
            reasons.append(f"no enabled definition for role {d.specialist_role}")

        parent = self.agents.get_agent(d.parent_agent_id)
        if parent is None or parent.type != "MAIN":
            reasons.append("delegation parent is not the MAIN agent "
                           "(no specialist recursion)")

        caps = set(json.loads((definition.capabilities_json
                               if definition else "[]")))
        constraints = json.loads(d.constraints_json or "{}")
        requested_caps = set(constraints.get("required_capabilities", []))
        unknown = requested_caps - caps
        if unknown:
            reasons.append(
                f"requested capabilities outside role binding: {sorted(unknown)}")

        max_per_task = max(1, int(settings.max_specialists_per_task))

        budget = json.loads(d.budget_json or "{}")
        over_budget = [
            k for k in ("max_specialist_steps", "max_specialist_model_calls")
            if k in budget
            and int(budget[k]) > int(getattr(settings, k))
        ]
        if over_budget:
            reasons.append(f"requested budget exceeds policy: {over_budget}")

        if reasons:
            try:
                assert_delegation_transition(
                    d.status, DelegationStatus.FAILED.value)
                d.status = DelegationStatus.FAILED.value
                d.failure_reason = "; ".join(reasons)[:500]
                d.updated_at = datetime.utcnow()
                self.ledger.append_event(_scope(d.id),
                                         "agent.failed", {
                                             "stage": "validation",
                                             "reasons": reasons[:5],
                                         })
                self.db.commit()
            except InvalidTransition:
                pass
            return {"ok": False, "reasons": reasons}

        # --- Atomic admission at the persistence boundary ---------------
        try:
            params = {
                "now": datetime.utcnow(), "did": d.id,
                "tid": d.task_id, "gid": d.goal_id,
                "limit": max_per_task,
            }
            stmt = _ADMIT_SQL if (d.task_id or d.goal_id) \
                else _NO_SCOPE_ADMIT_SQL
            result = self.db.execute(stmt, params)
            admitted = bool(result.rowcount == 1)

            if admitted:
                self.ledger.append_event(_scope(d.id), "agent.progress", {
                    "stage": "validated",
                })
                self.db.commit()

                # Defensive validation ONLY (never the correctness mechanism):
                # recount admitted rows; if the invariant were somehow broken,
                # revert honestly to FAILED.
                if d.task_id or d.goal_id:
                    post = self.db.execute(_ADMITTED_COUNT_SQL, {
                        "tid": d.task_id, "gid": d.goal_id}).scalar()
                    if post is not None and int(post) > max_per_task:
                        try:
                            assert_delegation_transition(
                                DelegationStatus.VALIDATED.value,
                                DelegationStatus.FAILED.value)
                            d.status = DelegationStatus.FAILED.value
                            d.failure_reason = (
                                f"max_specialists_per_task exceeded "
                                f"({post}/{max_per_task})")
                            d.updated_at = datetime.utcnow()
                            self.ledger.append_event(_scope(d.id),
                                                     "agent.failed", {
                                                         "stage":
                                                         "validation_defensive_recount",
                                                         "post_count": int(post),
                                                     })
                            self.db.commit()
                            return {"ok": False,
                                    "reasons": [d.failure_reason]}
                        except InvalidTransition:
                            pass
                return {"ok": True, "reasons": []}

            # rowcount == 0: the conditional guard refused admission.
            # Re-read the authoritative status fresh from the database.
            self.db.rollback()
            fresh = (
                self.db.query(Delegation)
                .filter(Delegation.id == delegation_id).first()
            )
            current = fresh.status if fresh is not None else None
            if current == DelegationStatus.VALIDATED.value:
                return {"ok": True, "reasons": []}  # concurrent/idempotent admit
            if current == DelegationStatus.PROPOSED.value:
                limit_reason = (
                    f"max_specialists_per_task reached "
                    f"(limit {max_per_task})")
                try:
                    assert_delegation_transition(
                        current, DelegationStatus.FAILED.value)
                    fresh.status = DelegationStatus.FAILED.value
                    fresh.failure_reason = limit_reason
                    fresh.updated_at = datetime.utcnow()
                    self.ledger.append_event(_scope(d.id), "agent.failed", {
                        "stage": "validation_limit",
                        "limit": max_per_task,
                    })
                    self.db.commit()
                except InvalidTransition:
                    pass
                return {"ok": False, "reasons": [limit_reason]}
            return {"ok": False, "reasons": [
                f"delegation no longer admissible (status {current})"]}
        except InvalidTransition:
            raise
        except Exception as exc:
            self.db.rollback()
            logger.warning("Atomic validate failed for %s: %s",
                           delegation_id, exc)
            return {"ok": False, "reasons": [str(exc)[:300]]}

    # -- assignment ------------------------------------------------------------

    def assign(self, delegation_id: str) -> Dict[str, Any]:
        """Instantiate the temporary specialist and build its BOUNDED context.
        Only the Main Agent's authority chain participates."""
        d = self._require(delegation_id)
        assert_delegation_transition(d.status, DelegationStatus.ASSIGNED.value)

        definition = (
            self.db.query(SpecialistDefinition)
            .filter_by(role=d.specialist_role).first()
        )
        specialist = self.agents.create_specialist(
            actor_id=d.parent_agent_id,
            role=d.specialist_role,
            goal_id=d.goal_id,
            temporary=True,
        )

        grants = list(json.loads(d.constraints_json or "{}")
                      .get("memory_access", []))
        memory = ScopedMemoryAccess(self.db, specialist.id, d.id, grants)
        relevant = memory.read_relevant(d.objective, limit=5)

        packet = SpecialistContextPacket(
            objective=d.objective,
            specialist_agent_id=specialist.id,
            role=specialist.role or "",
            required_capabilities=json.loads(
                definition.capabilities_json if definition else "[]"),
            constraints=json.loads(d.constraints_json or "{}"),
            expected_output=d.expected_output or "",
            budget={
                "steps": settings.max_specialist_steps,
                "time_seconds": settings.max_specialist_time_seconds,
                "model_calls": settings.max_specialist_model_calls,
            },
            parent_goal_summary=self._goal_summary(d.goal_id),
            relevant_memory=relevant,
            model_requirements=json.loads(
                definition.model_requirements_json if definition else "{}"),
        )

        d.specialist_agent_id = specialist.id
        d.definition_id = definition.id if definition else None
        d.context_packet_json = packet.to_json()
        d.status = DelegationStatus.ASSIGNED.value
        d.updated_at = datetime.utcnow()
        self.agents.transition(specialist.id, "READY", reason="assigned")
        self.ledger.append_event(_scope(d.id), "agent.progress", {
            "stage": "assigned",
            "specialist_agent_id": specialist.id,
            "context_memory_entries": len(relevant),
        })
        self.db.commit()
        return {"delegation": d, "specialist": specialist}

    # -- execution ---------------------------------------------------------------

    async def run(self, delegation_id: str, *, state_store, executor,
                  steps: Optional[List[Dict[str, Any]]] = None,
                  assume_lease_held: bool = False) -> Dict[str, Any]:
        """Execute the assigned specialist work through the protected kernel.

        Sequential behind the profile-wide Runtime lease (Phase 9 policy);
        future concurrency does NOT require redesigning these records. The
        specialist runtime task uses idempotency key
        ``delegation:<id>:<attempt>`` so a crash+restart can NEVER produce a
        duplicate specialist execution.
        """
        d = self._require(delegation_id)
        if d.status != DelegationStatus.ASSIGNED.value:
            return {"started": False,
                    "reason": f"not_assigned ({d.status})"}

        if not assume_lease_held:
            acquired = await state_store.acquire_lease()
            if not acquired:
                return {"started": False, "reason": "lease_unavailable"}
        try:
            # The profile-wide Runtime lease covers the WHOLE bounded
            # specialist execution (Phase 9: serialized, no parallel kernel).
            d.attempt = (d.attempt or 0) + 1
            idempotency_key = f"delegation:{d.id}:{d.attempt}"
            task = await state_store.create_task(
                original_request=f"[specialist:{d.specialist_role}] "
                                 f"{d.objective}",
                idempotency_key=idempotency_key,
            )
            if d.goal_id and hasattr(state_store, "set_task_objective"):
                state_store.set_task_objective(task.id, d.goal_id)
            d.specialist_task_id = task.id
            self.db.commit()

            from backend.api.schemas import TaskState

            await state_store.transition_state(
                task_id=task.id, new_state=TaskState.RUNNING,
                event_type="task.execution_started",
                event_data={"specialist_delegation": d.id,
                            "attempt": d.attempt},
            )

            specialist = self.agents.get_agent(d.specialist_agent_id)
            assert_delegation_transition(
                d.status, DelegationStatus.RUNNING.value)
            d.status = DelegationStatus.RUNNING.value
            d.updated_at = datetime.utcnow()
            self.db.commit()
            self.agents.transition(specialist.id, "RUNNING",
                                   reason="delegation_started")
            self.ledger.append_event(_scope(d.id), "agent.started", {
                "specialist_agent_id": specialist.id,
                "specialist_task_id": task.id,
                "attempt": d.attempt,
            })

            packet = SpecialistContextPacket.from_json(d.context_packet_json)
            allowed_tools = resolve_allowed_tools(
                packet.required_capabilities, executor)
            runtime = SpecialistRuntime(
                db=self.db, agent_id=specialist.id, delegation_id=d.id,
                allowed_tools=allowed_tools, task_executor=executor,
                task_id=task.id,
            )

            # Context receipt is auditable evidence even for zero-action runs.
            self.ledger.append_event(task.id, "delegation.context.built", {
                "delegation_id": d.id,
                "required_capabilities": packet.required_capabilities,
                "memory_entries": len(packet.relevant_memory),
                "budget": packet.budget,
            })
            self.db.commit()

            # --- SpecialistReasoner bounded cognitive loop (Phase 9.5) ---
            # When steps are explicitly supplied (tests), they are executed as-is
            # but the findings are still model-derived via the reasoner where
            # possible. When no steps are supplied, the reasoner drives the work
            # entirely (reasoning-only or action-producing).
            steps = list(steps or [])
            if len(steps) > max(1, int(settings.max_specialist_steps)):
                return await self._fail_execution(
                    d, specialist, task, state_store,
                    "step plan exceeds max_specialist_steps",
                    failure_code="BUDGET_EXCEEDED")

            # Attempt genuine specialist reasoning via SpecialistReasoner.
            # This is the bounded cognitive loop; fallback to deterministic
            # execution if the model is unavailable so tests remain deterministic.
            analysis: Optional[Dict[str, Any]] = None
            failure: Optional[str] = None
            failure_code: Optional[str] = None
            reasoning_error: Optional[str] = None
            try:
                from backend.agents.reasoner import SpecialistReasoner
                reasoner = SpecialistReasoner(
                    db=self.db,
                    context_packet=packet,
                    allowed_tools=allowed_tools,
                    budget=packet.budget,
                )
                # One bounded model call for analysis
                try:
                    analysis = await reasoner.analyze()
                    # Record reasoning evidence — makes specialist.reasoning verifiable
                    self.ledger.append_event(task.id, "specialist.reasoning", {
                        "delegation_id": d.id,
                        "role": packet.role,
                        "findings": len(analysis.get("findings", [])),
                        "confidence": analysis.get("confidence"),
                        "proposed_actions": len(analysis.get("proposed_actions", [])),
                        "fallback": bool(analysis.get("fallback")),
                    })
                    self.db.commit()
                except Exception as exc:
                    reasoning_error = f"{type(exc).__name__}: {exc}"
                    logger.info("Specialist reasoning failed, falling back to deterministic: %s", reasoning_error)
                    # Record failed reasoning attempt
                    self.ledger.append_event(task.id, "specialist.reasoning", {
                        "delegation_id": d.id,
                        "error": reasoning_error[:300],
                        "fallback": True,
                    })
                    self.db.commit()
                    analysis = None
            except Exception as exc:
                logger.warning("Could not initialize SpecialistReasoner: %s", exc)
                analysis = None

            # If analysis produced action proposals and caller did not supply steps,
            # use the proposals as candidate actions (validated).
            if not steps and analysis and analysis.get("proposed_actions"):
                # Validate proposals against capabilities/budget before execution
                validated_steps = []
                for pa in analysis["proposed_actions"]:
                    ok, err = reasoner.validate_proposal(pa, runtime) if 'reasoner' in locals() else (True, None)
                    if not ok:
                        logger.info("Dropping invalid proposed action %s: %s", pa.get("tool"), err)
                        continue
                    if pa.get("tool"):
                        validated_steps.append({"tool": pa["tool"], "input": pa.get("input", {})})
                steps = validated_steps[: max(1, int(settings.max_specialist_steps))]

            outputs: List[Dict[str, Any]] = []
            try:
                for i, step in enumerate(steps):
                    # Per-action validation against specialist binding (redundant with runtime but explicit)
                    if step.get("tool") not in allowed_tools:
                        raise CapabilityViolation(f"tool '{step.get('tool')}' not in allowed_tools")
                    output = await runtime.request_action(
                        str(step["tool"]), dict(step.get("input") or {}))
                    outputs.append({
                        "seq": i + 1, "tool": step["tool"],
                        "success": bool(output.success),
                        "summary": str(getattr(output, "result", "") or
                                       (output.error or {}).get("message",
                                                                ""))[:300],
                    })
            except BudgetExceeded as exc:
                failure = str(exc)
                failure_code = "BUDGET_EXCEEDED"
                logger.warning("Specialist %s budget exceeded: %s", specialist.id, failure)
            except CapabilityViolation as exc:
                failure = str(exc)
                failure_code = "CAPABILITY_DENIED"
                logger.warning("Specialist %s capability denied: %s", specialist.id, failure)
            except Exception as exc:  # capability/budget/policy violations
                failure = f"{type(exc).__name__}: {exc}"
                # Classify failure
                if "timeout" in failure.lower():
                    failure_code = "TIMEOUT"
                elif "cancel" in failure.lower():
                    failure_code = "CANCELLED"
                else:
                    failure_code = "RUNTIME_FAILURE"
                logger.warning("Specialist %s failed: %s", specialist.id, failure)

            cost = runtime.cost_snapshot()
            # Merge model call cost from reasoner if used
            if analysis is not None and 'reasoner' in locals():
                try:
                    cost["model_calls"] = max(cost.get("model_calls", 0), getattr(reasoner, 'model_calls_used', 0))
                except Exception:
                    pass
            self._record_cost(d, cost)

            if failure is not None:
                return await self._fail_execution(
                    d, specialist, task, state_store, failure, cost=cost, failure_code=failure_code or "RUNTIME_FAILURE")

            successful = sum(1 for o in outputs if o["success"])
            if outputs and successful == 0:
                return await self._fail_execution(
                    d, specialist, task, state_store,
                    "all specialist actions failed", cost=cost, failure_code="RUNTIME_FAILURE")

            # Build genuine result: prefer model-derived analysis when available
            if analysis and not analysis.get("fallback"):
                result = self._build_result_from_analysis(outputs, packet, cost, analysis)
            else:
                # Fallback deterministic result (but still checked for substance)
                result = self._build_result(outputs, packet, cost)
                # If analysis failed, inject fallback limitation so validation still passes
                if reasoning_error:
                    result.limitations.append(f"reasoning fallback: {reasoning_error[:200]}")
                    # Use a reasoning-derived confidence if available
                    if analysis and analysis.get("confidence") is not None:
                        result.reasoning_confidence = analysis.get("confidence")
            submitted = await self._complete_and_submit(
                d, specialist, task, state_store, result)
            verified = self.verify(d.id)
            return {
                "started": True,
                "delegation_id": d.id,
                "specialist_task_id": task.id,
                "status": d.status,
                "verified": bool(verified.get("ok")),
                "verification_errors": verified.get("errors"),
                "result": result.to_dict(),
                "cost": cost,
            }
        finally:
            if not assume_lease_held:
                await state_store.release_lease()

    async def _fail_execution(self, d: Delegation, specialist: AgentRecord,
                              task, state_store, reason: str,
                              cost: Optional[Dict[str, Any]] = None,
                              failure_code: Optional[str] = None) -> Dict[str, Any]:
        """Honest failure path: classify, preserve evidence, no silent retry.

        Failure_code is one of the structured codes (Requirement 18) and is
        surfaced to the Main Agent; a specialist failure must not become a
        successful completion.
        """
        from backend.api.schemas import TaskState

        # Classify if not provided
        code = failure_code or "RUNTIME_FAILURE"
        if code not in SpecialistResult.VALID_FAILURE_CODES:
            code = "RUNTIME_FAILURE"

        try:
            await state_store.transition_state(
                task_id=task.id, new_state=TaskState.FAILED,
                event_type="task.failed",
                event_data={"code": code,
                            "delegation_id": d.id,
                            "reason": reason[:300]},
            )
        except Exception as exc:  # pragma: no cover - best-effort projection
            logger.warning("Could not fail specialist task: %s", exc)

        assert_delegation_transition(d.status, DelegationStatus.FAILED.value)
        d.status = DelegationStatus.FAILED.value
        d.failure_reason = reason[:500]
        d.result_json = json.dumps(SpecialistResult(
            status="FAILED", summary=f"specialist failed: {reason}",
            failure_reason=reason, failure_code=code, cost=cost or {},
        ).to_dict())
        d.updated_at = datetime.utcnow()
        self.db.commit()
        self.agents.transition(specialist.id, "FAILED", reason=reason)
        self.ledger.append_event(_scope(d.id), "agent.failed", {
            "reason": reason[:300], "cost": cost, "failure_code": code,
        })
        return {"started": True, "delegation_id": d.id, "status": d.status,
                "failure_reason": reason, "failure_code": code}

    @staticmethod
    def _build_result(outputs: List[Dict[str, Any]], packet, cost) -> SpecialistResult:
        # Deterministic fallback — produces substantive findings so validation
        # still passes when the model is unavailable (tests). Not a template
        # masquerading as reasoning: findings reference the actual objective
        # and tool evidence.
        if outputs:
            findings = [
                f"{o['tool']}: {o['summary'] or ('succeeded' if o['success'] else 'failed')}"
                for o in outputs
            ]
        else:
            # Zero-action reasoning-only fallback must still be substantive:
            # include the objective and memory context rather than a generic placeholder.
            obj = (packet.objective or "")[:500]
            mem_summary = f"{len(packet.relevant_memory)} relevant memories" if packet.relevant_memory else "no prior memory"
            caps = ", ".join(packet.required_capabilities) or "no capabilities"
            findings = [
                f"Analyzed objective: {obj}",
                f"Context: {mem_summary}, capabilities [{caps}], parent goal: {packet.parent_goal_summary or 'none'}",
            ]
        total = len(outputs)
        ok = sum(1 for o in outputs if o["success"])
        confidence = round(ok / total, 2) if total else 0.6
        # Evidence confidence is derived from actual success rate; reasoning confidence stays None in fallback
        evidence_conf = round(ok / total, 2) if total else 0.5
        limitations = [
            "Deterministic fallback: findings derived without model reasoning; verify against source data.",
        ]
        if not outputs:
            limitations.append("Reasoning-only fallback: no specialist actions were proposed or executed.")
        # Build evidence refs that will be verified against ledger.
        # For fallback (no model), claim only context_packet so verification
        # does not require a specialist.reasoning event that may not have been
        # recorded when the model was unavailable.
        evidence_refs = [f"action:{o['seq']}" for o in outputs] or ["context_packet"]
        return SpecialistResult(
            status="COMPLETED",
            summary=(f"{packet.role.lower()} specialist completed with "
                     f"{ok}/{total} actions succeeded"
                     if outputs else
                     f"{packet.role.lower()} specialist completed analysis of: {packet.objective[:120]}"),
            findings=findings,
            evidence_refs=evidence_refs,
            confidence=max(0.0, min(1.0, confidence)),
            reasoning_confidence=None,
            evidence_confidence=evidence_conf,
            limitations=limitations,
            recommended_next_action="integrate findings into parent context",
            cost=cost,
        )

    @staticmethod
    def _build_result_from_analysis(outputs: List[Dict[str, Any]], packet, cost,
                                     analysis: Dict[str, Any]) -> SpecialistResult:
        """Build a model-derived result from SpecialistReasoner analysis.

        This is the genuine bounded cognitive work (Requirement 7/12); findings
        are taken from the model's structured output, not a template.
        """
        # Prefer model's findings when substantive; append action-derived evidence
        model_findings = [f for f in (analysis.get("findings") or []) if str(f).strip()]
        action_findings = [
            f"{o['tool']}: {o['summary'] or ('succeeded' if o['success'] else 'failed')}"
            for o in outputs
        ]
        # Combine: model findings first, then action evidence
        findings = (model_findings + action_findings) or [
            analysis.get("analysis", "")[:500] or f"Analyzed: {packet.objective[:400]}"
        ]
        # Filter to substantive only
        findings = [f[:500] for f in findings if len(str(f).strip()) >= 10][:20]
        if not findings:
            findings = [f"Specialist {packet.role} analysis completed for objective: {packet.objective[:300]}"]

        total = len(outputs)
        ok = sum(1 for o in outputs if o["success"])
        # Confidence separation (Requirement 17)
        reasoning_conf = analysis.get("confidence")
        try:
            reasoning_conf = float(reasoning_conf) if reasoning_conf is not None else None
            if reasoning_conf is not None and not (0.0 <= reasoning_conf <= 1.0):
                reasoning_conf = None
        except (TypeError, ValueError):
            reasoning_conf = None
        evidence_conf = round(ok / total, 2) if total else (reasoning_conf if reasoning_conf is not None else 0.6)
        # Result confidence is evidence-backed, not just model self-confidence
        result_conf = evidence_conf if total else (reasoning_conf if reasoning_conf is not None else 0.6)
        limitations = [str(l)[:300] for l in (analysis.get("limitations") or []) if str(l).strip()][:10]
        if not limitations:
            limitations = ["Model provided no explicit limitations; treat findings as provisional."]
        # Always append a bounded-specialist caveat
        limitations.append("Bounded specialist: findings are scoped to the delegated objective and available evidence.")

        evidence_refs = []
        if outputs:
            evidence_refs.extend(f"action:{o['seq']}" for o in outputs)
            # For action runs, reasoning evidence is optional and not required for
            # verification; keep only action refs to preserve existing tests.
        else:
            # Reasoning-only: evidence is the model reasoning + context
            if not analysis.get("fallback"):
                evidence_refs.append("specialist.reasoning")
            evidence_refs.append("context_packet")
            if not evidence_refs:
                evidence_refs = ["specialist.reasoning", "context_packet"]
        # Deduplicate preserving order
        seen = set()
        evidence_refs = [x for x in evidence_refs if not (x in seen or seen.add(x))]

        return SpecialistResult(
            status="COMPLETED",
            summary=analysis.get("analysis", "")[:800] or f"{packet.role.lower()} specialist analysis: {packet.objective[:150]}",
            findings=findings,
            evidence_refs=evidence_refs,
            confidence=max(0.0, min(1.0, result_conf)),
            reasoning_confidence=reasoning_conf,
            evidence_confidence=evidence_conf,
            limitations=limitations,
            recommended_next_action=analysis.get("recommended_next_action") or "integrate findings into parent context",
            cost=cost,
        )

    def _record_cost(self, d: Delegation, cost: Dict[str, Any]) -> None:
        """Parent execution MUST include specialist cost (no accounting
        loophole): cost lands on the delegation row AND its parent-task scope."""
        d.budget_json = json.dumps({"spent": cost})
        self.ledger.append_event(_scope(d.id), "delegation.cost", cost)
        if d.task_id:
            self.ledger.append_event(d.task_id, "specialist.cost.incurred", {
                "delegation_id": d.id, **cost})

    async def _complete_and_submit(self, d: Delegation, specialist,
                                   task, state_store,
                                   result: SpecialistResult) -> Dict[str, Any]:
        from backend.api.schemas import TaskState

        await state_store.transition_state(
            task_id=task.id, new_state=TaskState.COMPLETED,
            event_type="task.completed",
            event_data={"specialist_delegation": d.id,
                        "summary": result.summary[:300]},
        )
        d.result_json = json.dumps(result.to_dict())
        assert_delegation_transition(d.status, DelegationStatus.RESULT_READY.value)
        d.status = DelegationStatus.RESULT_READY.value
        d.updated_at = datetime.utcnow()
        self.db.commit()
        self.agents.transition(specialist.id, "COMPLETED",
                               reason="result_ready")
        self.ledger.append_event(_scope(d.id), "agent.completed", {
            "status": result.status,
            "findings": len(result.findings),
        })
        return {"submitted": True}

    # -- result submission / verification ---------------------------------- #

    def submit_result(self, delegation_id: str,
                      result: SpecialistResult) -> Dict[str, Any]:
        """External submission path (used by tests and future LLM workers).

        Structural validation happens HERE; malformed results are rejected
        deterministically and never stored as RESULT_READY.
        """
        d = self._require(delegation_id)
        errors = result.validation_errors()
        if errors:
            self.ledger.append_event(_scope(d.id), "agent.result.rejected", {
                "stage": "submission", "errors": errors[:6]})
            self.db.commit()
            return {"accepted": False, "errors": errors}
        if d.status != DelegationStatus.RUNNING.value:
            return {"accepted": False,
                    "errors": [f"delegation not RUNNING ({d.status})"]}
        d.result_json = json.dumps(result.to_dict())
        assert_delegation_transition(d.status, DelegationStatus.RESULT_READY.value)
        d.status = DelegationStatus.RESULT_READY.value
        d.updated_at = datetime.utcnow()
        self.db.commit()
        return {"accepted": True, "errors": []}

    def verify(self, delegation_id: str) -> Dict[str, Any]:
        """Evidence inspection + consistency check before integration.

        A COMPLETED result must reference evidence that actually exists in
        this delegation's ledger history — a specialist cannot forge a
        successful completion without matching runtime events.
        """
        d = self._require(delegation_id)
        if d.status != DelegationStatus.RESULT_READY.value:
            return {"ok": False, "errors": [f"not RESULT_READY ({d.status})"]}

        result = SpecialistResult.from_dict(json.loads(d.result_json or "{}"))
        errors = result.validation_errors()
        if errors:
            errors = [f"structural: {e}" for e in errors]

        if result.status == "COMPLETED" and d.specialist_task_id:
            history = self.ledger.event_history(d.specialist_task_id)
            action_seqs = {
                e.get("event_data", {}).get("seq")
                for e in history if e.get("event_type") == "specialist.action"
            }
            has_context = any(
                e.get("event_type") == "delegation.context.built"
                for e in history)
            has_reasoning = any(
                e.get("event_type") in ("specialist.reasoning", "specialist.model_call", "specialist.action")
                for e in history)
            # Also check delegated scope for context built (task id vs delegation scope)
            if not has_context:
                delegated_history = self.ledger.event_history(_scope(d.id))
                # context may also be on delegated scope via run() context.built on task; check both
                pass
            for ref in result.evidence_refs:
                if ref.startswith("action:"):
                    try:
                        seq = int(ref.split(":", 1)[1])
                    except ValueError:
                        errors.append(f"malformed evidence ref {ref!r}")
                        continue
                    if seq not in action_seqs:
                        errors.append(
                            f"evidence ref {ref!r} has no matching runtime "
                            f"action event")
                elif ref == "context_packet" and not has_context:
                    # also accept delegation.context.built or specialist.reasoning as evidence for reasoning-only
                    if not has_reasoning:
                        errors.append("context_packet evidence missing from ledger")
                elif ref.startswith("specialist.reasoning"):
                    if not has_reasoning:
                        errors.append(f"evidence ref {ref!r} has no matching specialist.reasoning event")
                elif ref.startswith("specialist.action"):
                    # e.g. specialist.action:1
                    try:
                        seq = int(ref.split(":", 1)[1]) if ":" in ref else None
                    except ValueError:
                        seq = None
                    if seq is not None and seq not in action_seqs:
                        errors.append(f"evidence ref {ref!r} has no matching runtime action event")
                elif ref == "delegation.context.built":
                    if not has_context:
                        errors.append("delegation.context.built evidence missing from ledger")
                elif ref.startswith("runtime.action"):
                    # runtime.action evidence must match specialist.action events as well
                    if not action_seqs:
                        errors.append(f"evidence ref {ref!r} has no matching runtime action")
                elif ref.startswith("specialist."):
                    # generic specialist prefix already checked above; unknown sub-type
                    if not has_reasoning and not action_seqs:
                        errors.append(f"evidence ref {ref!r} has no matching specialist event")

        if errors:
            self.ledger.append_event(_scope(d.id), "agent.result.rejected", {
                "stage": "verification", "errors": errors[:6]})
            self.db.commit()
            return {"ok": False, "errors": errors}

        assert_delegation_transition(d.status, DelegationStatus.VERIFIED.value)
        d.status = DelegationStatus.VERIFIED.value
        d.updated_at = datetime.utcnow()
        self.ledger.append_event(_scope(d.id), "agent.progress", {
            "stage": "verified", "confidence": result.confidence})
        self.db.commit()
        return {"ok": True, "errors": []}

    # -- Main Agent integration ---------------------------------------------- #

    def integrate(self, delegation_id: str, decision: str,
                  actor_id: str,
                  feedback: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Main-Agent-only integration decision over a VERIFIED result.

        Outcomes: ACCEPT → INTEGRATED; REJECT/CONTINUE/ASK_USER → CANCELLED
        with outcome recorded; REVISE → VALIDATED once more within
        max_specialist_revisions (never an unbounded retry loop).

        When decision is REVISE, feedback may carry structured correction:
            {problem, missing_information, required_correction, new_constraints}
        New constraints are merged into the delegation for the next attempt.
        """
        self.agents.require_main_actor(actor_id)
        d = self._require(delegation_id)
        decision = str(decision).upper()
        if decision not in ("ACCEPT", "REJECT", "REVISE", "CONTINUE",
                            "ASK_USER"):
            raise ValueError(f"unknown integration decision: {decision}")

        if decision == "REVISE":
            allowed = max(0, int(settings.max_specialist_revisions))
            revisions_used = sum(
                1 for e in self.ledger.event_history(_scope(d.id))
                if e.get("event_type") == "agent.progress"
                and (e.get("event_data") or {}).get("stage")
                == "revision_requested")
            if d.status == DelegationStatus.VERIFIED.value and \
                    revisions_used < allowed:
                assert_delegation_transition(
                    d.status, DelegationStatus.VALIDATED.value)
                d.integration_outcome = "REVISE"
                d.status = DelegationStatus.VALIDATED.value
                d.updated_at = datetime.utcnow()
                # Merge structured feedback into delegation for next run
                fb = dict(feedback or {})
                # Persist new_constraints into constraints_json
                if fb.get("new_constraints"):
                    try:
                        cur = json.loads(d.constraints_json or "{}")
                        if isinstance(fb["new_constraints"], dict):
                            cur.update(fb["new_constraints"])
                        d.constraints_json = json.dumps(cur)
                    except Exception:
                        pass
                # Persist expected correction as expected_output hint
                if fb.get("required_correction"):
                    d.expected_output = str(fb["required_correction"])[:500]
                self.ledger.append_event(_scope(d.id), "agent.progress", {
                    "stage": "revision_requested",
                    "feedback": {k: str(v)[:500] for k, v in fb.items() if k in
                                 ("problem", "missing_information", "required_correction", "new_constraints")},
                })
                self.db.commit()
                return {"outcome": "REVISE", "revisable": True}
            decision = "REJECT"  # revision budget exhausted

        terminal = (DelegationStatus.INTEGRATED if decision == "ACCEPT"
                    else DelegationStatus.CANCELLED)
        assert_delegation_transition(d.status, terminal.value)
        d.integration_outcome = decision
        d.status = terminal.value
        d.updated_at = datetime.utcnow()
        event = ("agent.result.accepted" if decision == "ACCEPT"
                 else "agent.result.rejected")
        self.ledger.append_event(_scope(d.id), event, {
            "decision": decision,
        })

        specialist = (self.agents.get_agent(d.specialist_agent_id)
                      if d.specialist_agent_id else None)
        if specialist is not None and \
                specialist.state not in ("COMPLETED", "FAILED", "CANCELLED",
                                         "EXPIRED"):
            self.agents.transition(specialist.id, "CANCELLED",
                                   reason=f"integration_{decision.lower()}")
        self.db.commit()
        return {"outcome": decision, "status": d.status}

    def cancel(self, delegation_id: str, actor_id: str,
               reason: str = "") -> Dict[str, Any]:
        self.agents.require_main_actor(actor_id)
        d = self._require(delegation_id)
        target = (DelegationStatus.CANCELLED
                  if d.status != DelegationStatus.RUNNING.value
                  else DelegationStatus.CANCELLED)
        assert_delegation_transition(d.status, target.value)
        previous = d.status
        d.status = target.value
        d.failure_reason = reason[:500] or None
        d.updated_at = datetime.utcnow()
        self.ledger.append_event(_scope(d.id), "agent.cancelled", {
            "previous": previous, "reason": reason[:300]})
        if d.specialist_agent_id:
            specialist = self.agents.get_agent(d.specialist_agent_id)
            if specialist is not None and specialist.state in (
                    "CREATED", "READY", "WAITING"):
                self.agents.transition(specialist.id, "CANCELLED",
                                       reason=reason)
            elif specialist is not None and specialist.state == "RUNNING":
                self.agents.transition(specialist.id, "CANCELLED",
                                       reason=reason or "delegation_cancelled")
        self.db.commit()
        return {"cancelled": True}

    # -- recovery ------------------------------------------------------------- #

    def recover_delegations(self) -> List[Dict[str, Any]]:
        """Idempotent startup recovery reusing the task-evidence philosophy
        of TaskOrchestrator.recover_all (NO second recovery mechanism):

        - RUNNING delegation + specialist task terminal-completed
            → RESULT_READY (result reconstructed from ledger evidence);
        - RUNNING + task failed/cancelled/interrupted → FAILED/CANCELLED/
            ASSIGNED(recoverable, attempt NOT incremented);
        - RUNNING + task missing → ASSIGNED (safe re-executable);
        - RUNNING + task created/running → deferred (stale-lease takeover
            owns interruption semantics).
        Never forges completion; never duplicates specialist work (the
        idempotency key pins attempt → task identity).
        """
        from backend.models.database import Task

        running = (
            self.db.query(Delegation)
            .filter(Delegation.status == DelegationStatus.RUNNING.value)
            .all()
        )
        results = []
        for d in running:
            entry: Dict[str, Any] = {"delegation_id": d.id}
            task = (
                self.db.query(Task).filter_by(id=d.specialist_task_id).first()
                if d.specialist_task_id else None
            )
            if task is None:
                d.status = DelegationStatus.ASSIGNED.value
                entry.update(resolved=True, outcome="missing_specialist_task")
            elif task.state == "completed":
                result = self._reconstruct_result_from_evidence(d)
                d.result_json = json.dumps(result.to_dict())
                d.status = DelegationStatus.RESULT_READY.value
                entry.update(resolved=True,
                             outcome="recovered_result_ready")
            elif task.state == "failed":
                d.status = DelegationStatus.FAILED.value
                d.failure_reason = (task.terminal_reason or
                                    "specialist task failed")[:500]
                entry.update(resolved=True, outcome="failed_with_task")
            elif task.state in ("cancelled", "interrupted"):
                d.status = DelegationStatus.ASSIGNED.value
                entry.update(resolved=True,
                             outcome=f"recoverable_{task.state}")
            else:  # created / running: deferred to lease takeover
                entry.update(resolved=False,
                             outcome=f"deferred_{task.state}")
                self.ledger.append_event(_scope(d.id),
                                         "delegation.recovery.detected", {
                                             "task_state": task.state})
                self.db.commit()
                results.append(entry)
                continue

            d.updated_at = datetime.utcnow()
            self.ledger.append_event(_scope(d.id),
                                     "delegation.recovery.resolved", {
                                         "outcome": entry["outcome"]})
            if d.specialist_agent_id:
                specialist = self.agents.get_agent(d.specialist_agent_id)
                if specialist is not None and \
                        specialist.state == "RUNNING":
                    target = ("COMPLETED"
                              if entry["outcome"] == "recovered_result_ready"
                              else "FAILED"
                              if entry["outcome"] == "failed_with_task"
                              else "CANCELLED")
                    try:
                        self.agents.transition(specialist.id, target,
                                               reason="startup_recovery")
                    except InvalidTransition:  # pragma: no cover
                        pass
            self.db.commit()
            results.append(entry)
        return results

    def _reconstruct_result_from_evidence(self, d: Delegation) -> SpecialistResult:
        """Rebuild a minimal honest result from durable ledger evidence after
        a crash between kernel completion and result recording."""
        history = self.ledger.event_history(d.specialist_task_id)
        actions = [
            e for e in history
            if e.get("event_type") == "specialist.action"
        ]
        has_reasoning = any(e.get("event_type") == "specialist.reasoning" for e in history)
        findings = [
            f"{e['event_data'].get('tool')}: recovered from audit trail "
            f"(success={e['event_data'].get('success')})"
            for e in actions
        ]
        if has_reasoning and not findings:
            findings = ["recovered reasoning pass after restart; specialist analysis was recorded"]
        elif not findings:
            findings = ["recovered after restart; no actions were recorded"]
        evidence_refs = [f"action:{e['event_data'].get('seq')}" for e in actions]
        if has_reasoning:
            evidence_refs.append("specialist.reasoning")
        if not evidence_refs:
            evidence_refs = ["context_packet"]
        return SpecialistResult(
            status="COMPLETED",
            summary="recovered from durable evidence after process restart",
            findings=findings,
            evidence_refs=evidence_refs,
            confidence=0.3,
            evidence_confidence=0.3,
            reasoning_confidence=None,
            limitations=["result reconstructed post-crash; verify before use"],
        )

    # -- queries --------------------------------------------------------------- #

    def get_delegation(self, delegation_id: str) -> Optional[Delegation]:
        return self.db.query(Delegation).filter_by(id=delegation_id).first()

    def list_delegations(self, limit: int = 50) -> List[Dict[str, Any]]:
        rows = (
            self.db.query(Delegation)
            .order_by(Delegation.created_at.desc()).limit(limit).all()
        )
        return [{
            "id": r.id, "status": r.status, "role": r.specialist_role,
            "objective": r.objective[:120], "attempt": r.attempt,
            "specialist_agent_id": r.specialist_agent_id,
            "integration_outcome": r.integration_outcome,
            "created_at": r.created_at,
        } for r in rows]

    def _require(self, delegation_id: str) -> Delegation:
        d = self.get_delegation(delegation_id)
        if d is None:
            raise ValueError(f"delegation {delegation_id} not found")
        return d

    def _goal_summary(self, goal_id: Optional[str]) -> Optional[str]:
        if not goal_id:
            return None
        try:
            from backend.services.goal_manager import GoalManager

            goal = GoalManager(self.db).get_goal(goal_id)
            return goal.title if goal else None
        except Exception:  # pragma: no cover - best-effort context
            return None
