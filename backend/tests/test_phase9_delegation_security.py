"""Phase 9 — adversarial security tests for bounded specialist architecture.

The ten invariants under proof:
 1. specialist cannot exceed assigned capabilities;
 2. specialist cannot directly call tools outside the kernel facade;
 3. specialist cannot modify Main Agent state;
 4. specialist cannot modify Goal state directly;
 5. specialist cannot directly write permanent memory;
 6. specialist cannot bypass runtime policy;
 7. specialist cannot spawn another specialist;
 8. specialist cannot exceed configured budget;
 9. specialist cannot forge successful completion;
10. specialist result cannot bypass verification.
"""

import asyncio
import json

import pytest

from backend.agents.delegation import (
    DelegationRequest,
    DelegationService,
    SpecialistResult,
)
from backend.agents.service import AgentService
from backend.agents.specialist import (
    ScopedMemoryAccess,
    SpecialistContextPacket,
    SpecialistRuntime,
    resolve_allowed_tools,
)
from backend.agents.state import (
    AuthorityError,
    BudgetExceeded,
    CapabilityViolation,
    InvalidTransition,
)


@pytest.fixture
def env(test_db):
    service = AgentService(test_db)
    service.ensure_definitions()
    main = service.get_or_create_main()
    return test_db, service, main


def _delegated(test_db, main, role="ANALYSIS"):
    svc = DelegationService(test_db)
    proposal = svc.propose(DelegationRequest(
        parent_agent_id=main.id, specialist_role=role,
        objective="bounded adversarial probe"))
    assert proposal["error"] is None
    d = proposal["delegation"]
    assert svc.validate(d.id)["ok"]
    svc.assign(d.id)
    return svc, d


def test_1_specialist_cannot_exceed_assigned_capabilities(env, tool_executor):
    db, service, main = env
    packet = SpecialistContextPacket(
        objective="probe", specialist_agent_id="x", role="ANALYSIS",
        required_capabilities=["system_observation"])
    tools = resolve_allowed_tools(packet.required_capabilities, tool_executor)
    runtime = SpecialistRuntime(db=db, agent_id="spec-1",
                                delegation_id="d-1", allowed_tools=tools,
                                task_executor=tool_executor, task_id="t-1")
    with pytest.raises(CapabilityViolation):
        asyncio.run(runtime.request_action("package_install", {}))
    with pytest.raises(CapabilityViolation):
        asyncio.run(runtime.request_action("file_delete", {}))


def test_2_no_direct_tool_path_outside_facade(env, tool_executor):
    """A specialist's only executable surface is SpecialistRuntime, which
    routes through ToolExecutor.execute_tool; there is no direct tool
    reference and no OS/DB execution API on the specialist surface."""
    db, service, main = env
    spec = service.create_specialist(main.id, "ANALYSIS")
    public_api = [m for m in dir(spec) if not m.startswith("_")]
    # The Agent ROW is data only — no execution methods exist on it.
    assert not any("exec" in m or "run" in m or "tool" in m
                   for m in public_api)


def test_3_specialist_cannot_modify_main_agent_state(env):
    db, service, main = env
    spec = service.create_specialist(main.id, "REVIEW")
    with pytest.raises(AuthorityError):
        service.set_definition_enabled(spec.id, "CODING", False)
    # MAIN-row transitions demand a MAIN actor.
    with pytest.raises(AuthorityError):
        service.transition(main.id, "READY", actor_id=spec.id)
    from backend.models.database import MainAgent

    identity = service.db.query(MainAgent).filter_by(id=1).first()
    assert identity.state == "active"  # untouched


def test_4_specialist_cannot_modify_goal_state(env):
    """Goal authority lives in GoalManager behind the Main Agent; the
    specialist-facing surface exposes no goal mutation path at all."""
    db, service, main = env
    svc, d = _delegated(db, main)
    spec = service.get_agent(d.specialist_agent_id)
    goal_surface = [m for m in dir(SpecialistRuntime)
                    if not m.startswith("_")]
    forbidden = {"transition_goal", "complete_goal", "pause_goal",
                 "fail_goal", "create_goal", "resume_goal"}
    assert not (forbidden & set(goal_surface))
    # And delegation authority operations reject non-MAIN actors.
    with pytest.raises(AuthorityError):
        svc.integrate(d.id, "ACCEPT", actor_id=spec.id)


def test_5_specialist_cannot_write_permanent_memory(env):
    db, service, main = env
    scoped = ScopedMemoryAccess(db, "spec-5", "del-5")
    entry = scoped.write_working("note", "temp observation")
    from backend.models.database import MemoryEntry

    row = db.query(MemoryEntry).filter_by(id=entry.id).first()
    assert row.scope == "TASK"
    permanent_scopes = [
        e for e in db.query(MemoryEntry).all() if e.scope in ("USER", "GLOBAL")
    ]
    assert permanent_scopes == []
    proposal = scoped.propose_permanent_memory("k", "v")
    assert db.query(MemoryEntry).filter_by(key="k").first() is None
    assert proposal["proposed_by"] == "spec-5"


def test_6_policy_denial_not_bypassable(env, tool_executor):
    """Even inside its own binding (file_operations), a specialist is still
    subject to executor policy: mutating actions require a direct user
    instruction, which PolicyContext(direct_user_instruction=False) can never
    provide."""
    db, service, main = env
    svc, d = _delegated(db, main, role="CODING")
    from backend.services.task_runner import TaskRunner

    outcome = asyncio.run(svc.run(
        d.id, state_store=TaskRunner(db), executor=tool_executor,
        steps=[{"tool": "file_write",
                "input": {"path": "x.txt", "content": "x"}}]))
    assert outcome.get("failure_reason") == "all specialist actions failed"
    delegation = svc.get_delegation(d.id)
    assert delegation.status == "FAILED"


def test_7_specialist_cannot_spawn_another_specialist(env):
    db, service, main = env
    spec = service.create_specialist(main.id, "ANALYSIS")
    with pytest.raises(AuthorityError):
        service.create_specialist(spec.id, "CODING")


def test_8_budget_enforced_deterministically(env, tool_executor):
    from backend.core.config import settings as s

    db, service, main = env
    svc, d = _delegated(db, main)
    original = s.max_specialist_steps
    s.max_specialist_steps = 1
    try:
        packet = SpecialistContextPacket(
            objective="b", specialist_agent_id="s8", role="ANALYSIS",
            required_capabilities=["system_observation"])
        runtime = SpecialistRuntime(
            db=db, agent_id="spec-8", delegation_id="d-8",
            allowed_tools=resolve_allowed_tools(
                packet.required_capabilities, tool_executor),
            task_executor=tool_executor, task_id="t-8")
        asyncio.run(runtime.request_action("system_time", {}))
        with pytest.raises(BudgetExceeded):
            asyncio.run(runtime.request_action("system_time", {}))
    finally:
        s.max_specialist_steps = original


def test_9_forged_completion_rejected(env, tool_executor):
    db, service, main = env
    svc, d = _delegated(db, main)
    forged = SpecialistResult(
        status="COMPLETED",
        summary="I definitely did everything successfully.",
        findings=["everything worked"],
        evidence_refs=["action:1", "action:2"],
        confidence=0.99)
    submission = svc.submit_result(d.id, forged)
    # Structurally well-formed but unverifiable: verification fails because
    # no matching kernel evidence exists.
    if submission["accepted"]:
        verdict = svc.verify(d.id)
        assert not verdict["ok"]


def test_10_result_cannot_bypass_verification_gate(env):
    db, service, main = env
    svc, d = _delegated(db, main)
    # RESULT_READY (not VERIFIED) can never be integrated.
    d.status = "RESULT_READY"
    d.result_json = json.dumps(SpecialistResult(
        status="COMPLETED", summary="s", findings=["f"],
        evidence_refs=["context_packet"], confidence=0.5).to_dict())
    db.commit()
    with pytest.raises(InvalidTransition):
        svc.integrate(d.id, "ACCEPT", actor_id=main.id)
    assert svc.get_delegation(d.id).status == "RESULT_READY"
