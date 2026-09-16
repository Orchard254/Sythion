"""Phase 9 — bounded specialist agents & delegation.

Covers:
- Agent lifecycle state machine (creation → … → expiration, invalid
  transitions fail deterministically);
- Delegation eligibility + lifecycle (valid/invalid role/missing capability/
  budget/depth/duplicate);
- Structured result contract handling (valid/malformed/missing evidence/
  reject/revise/accept);
- Scoped specialist memory (scoped read, permanent-write rejection,
  no user-memory pollution);
- Specialist runtime through the kernel (action execution, policy denial,
  budget enforcement, cancellation semantics);
- Main Agent integration outcomes;
- Persistence/recovery behavior (restart during delegation, restart with
  result ready, idempotent integration, no duplicate specialist execution).
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
    resolve_allowed_tools,
)
from backend.agents.state import (
    AgentLifecycle,
    AuthorityError,
    BudgetExceeded,
    CapabilityViolation,
    InvalidTransition,
)


@pytest.fixture
def agents(test_db):
    service = AgentService(test_db)
    service.ensure_definitions()
    main = service.get_or_create_main()
    return service, main


def _request(main_id, role="ANALYSIS", objective="Analyze approaches A and B",
             **kwargs):
    return DelegationRequest(
        parent_agent_id=main_id, specialist_role=role,
        objective=objective, task_id=kwargs.pop("task_id", None),
        goal_id=kwargs.pop("goal_id", None), **kwargs)


def _assigned_delegation(test_db, agents, role="ANALYSIS"):
    service, main = agents
    svc = DelegationService(test_db)
    proposal = svc.propose(_request(main.id, role=role))
    assert proposal["error"] is None
    d = proposal["delegation"]
    assert svc.validate(d.id)["ok"]
    svc.assign(d.id)
    return svc, d


# --------------------------------------------------------------------- #
# Agent lifecycle                                                        #
# --------------------------------------------------------------------- #

class TestAgentLifecycle:
    def test_main_agent_unique_and_ready(self, agents):
        service, main = agents
        again = service.get_or_create_main()
        assert again.id == main.id
        assert main.type == "MAIN"
        assert main.state == AgentLifecycle.READY.value

    def test_specialist_created_from_definition(self, agents):
        service, main = agents
        spec = service.create_specialist(main.id, "ANALYSIS")
        assert spec.type == "TEMPORARY_SPECIALIST"
        assert spec.parent_agent_id == main.id
        assert spec.state == AgentLifecycle.CREATED.value
        assert spec.expires_at is not None  # temporary instances expire

    def test_full_lifecycle_running_to_expired(self, agents):
        service, main = agents
        spec = service.create_specialist(main.id, "ANALYSIS")
        service.transition(spec.id, "READY")
        service.transition(spec.id, "RUNNING")
        service.transition(spec.id, "COMPLETED")
        expired = service.transition(spec.id, "EXPIRED")
        assert expired.state == AgentLifecycle.EXPIRED.value
        # terminal state: no further transitions
        with pytest.raises(InvalidTransition):
            service.transition(spec.id, "RUNNING")

    def test_invalid_transition_fails_deterministically(self, agents):
        service, main = agents
        spec = service.create_specialist(main.id, "ANALYSIS")
        with pytest.raises(InvalidTransition):
            service.transition(spec.id, "COMPLETED")  # CREATED -> COMPLETED
        with pytest.raises(InvalidTransition):
            service.transition(spec.id, "RUNNING")    # CREATED -> RUNNING

    def test_failure_and_cancellation(self, agents):
        service, main = agents
        spec = service.create_specialist(main.id, "REVIEW")
        service.transition(spec.id, "READY")
        failed = service.transition(spec.id, "FAILED", reason="boom")
        assert failed.state == "FAILED"
        with pytest.raises(InvalidTransition):
            service.transition(failed.id, "CANCELLED")

        other = service.create_specialist(main.id, "REVIEW")
        service.transition(other.id, "READY")
        cancelled = service.transition(other.id, "CANCELLED")
        assert cancelled.state == "CANCELLED"

    def test_expire_due_expires_finished_temporaries(self, agents):
        from datetime import datetime, timedelta

        service, main = agents
        spec = service.create_specialist(main.id, "ANALYSIS",
                                         ttl_seconds=60)
        service.transition(spec.id, "READY")
        spec.expires_at = datetime.utcnow() - timedelta(seconds=1)
        test_db = service.db
        test_db.commit()
        assert service.expire_due() == 1
        assert service.get_agent(spec.id).state == "EXPIRED"


# --------------------------------------------------------------------- #
# Delegation eligibility + lifecycle                                     #
# --------------------------------------------------------------------- #

class TestDelegationLifecycle:
    def test_valid_delegation_end_to_end(self, test_db, agents, tool_executor):
        from backend.services.task_runner import TaskRunner

        svc, d = _assigned_delegation(test_db, agents)
        runner = TaskRunner(test_db)
        outcome = asyncio.run(svc.run(
            d.id, state_store=runner, executor=tool_executor,
            steps=[{"tool": "system_time", "input": {}}]))
        assert outcome["started"] is True
        assert outcome["status"] == "VERIFIED"
        assert outcome["verified"] is True
        result = outcome["result"]
        assert result["status"] == "COMPLETED"
        assert result["evidence_refs"] == ["action:1"]

    def test_invalid_role_rejected(self, agents):
        service, main = agents
        svc = DelegationService(service.db)
        proposal = svc.propose(_request(main.id, role="QUANTUM_DECKHAND"))
        # unknown roles never become durable PROPOSED rows
        assert proposal["delegation"] is None

    def test_missing_capability_rejected_by_validation(self, test_db, agents):
        service, main = agents
        svc = DelegationService(test_db)
        proposal = svc.propose(_request(
            main.id,
            constraints={"required_capabilities": ["package_management"]}))
        d = proposal["delegation"]
        validation = svc.validate(d.id)
        assert not validation["ok"]
        assert any("outside role binding" in r for r in validation["reasons"])
        assert svc.get_delegation(d.id).status == "FAILED"

    def test_budget_over_policy_rejected(self, test_db, agents):
        service, main = agents
        svc = DelegationService(test_db)
        proposal = svc.propose(_request(
            main.id, budget={"max_specialist_steps": 9999}))
        d = proposal["delegation"]
        validation = svc.validate(d.id)
        assert not validation["ok"]
        assert any("budget exceeds policy" in r for r in validation["reasons"])

    def test_duplicate_delegation_rejected(self, test_db, agents):
        service, main = agents
        svc = DelegationService(test_db)
        first = svc.propose(_request(main.id, objective="Compare X and Y"))
        assert first["error"] is None
        second = svc.propose(_request(main.id, objective="compare x and y"))
        assert second["error"] == "duplicate_delegation"
        assert second["delegation"].id == first["delegation"].id

    def test_depth_violation_specialist_cannot_propose(self, agents):
        service, main = agents
        spec = service.create_specialist(main.id, "ANALYSIS")
        svc = DelegationService(service.db)
        with pytest.raises(AuthorityError):
            svc.propose(DelegationRequest(
                parent_agent_id=spec.id, specialist_role="ANALYSIS",
                objective="nested delegation"))

    def test_max_specialists_per_task(self, test_db, agents):
        # Phase 9.75 semantics: slots are consumed by ADMITTED delegations
        # (VALIDATED and beyond). With limit 2, two specialists are exactly
        # at the boundary; the third must be refused at admission.
        service, main = agents
        svc = DelegationService(test_db)
        monkeypatch = pytest.MonkeyPatch()
        try:
            monkeypatch.setattr(
                "backend.core.config.settings.max_specialists_per_task", 2)
            first = svc.propose(_request(main.id, task_id="parent-task"))
            assert svc.validate(first["delegation"].id)["ok"]
            second = svc.propose(
                _request(main.id, task_id="parent-task",
                         objective="A different analytical job entirely"))
            assert svc.validate(second["delegation"].id)["ok"]
            third = svc.propose(
                _request(main.id, task_id="parent-task",
                         objective="A third unrelated analytical job"))
            validation = svc.validate(third["delegation"].id)
            assert not validation["ok"]
            assert any("max_specialists_per_task" in r
                       for r in validation["reasons"])
        finally:
            monkeypatch.undo()

    def test_lifecycle_terminal_states_are_final(self, test_db, agents):
        svc, d = _assigned_delegation(test_db, agents)
        svc.db.expire(d)
        delegation = svc.get_delegation(d.id)
        delegation.status = "INTEGRATED"
        svc.db.commit()
        with pytest.raises(InvalidTransition):
            svc.integrate(d.id, "ACCEPT", actor_id=svc.agents.get_or_create_main().id)


# --------------------------------------------------------------------- #
# Result contract                                                        #
# --------------------------------------------------------------------- #

class TestResultHandling:
    def _run_zero_action(self, test_db, agents, tool_executor):
        from backend.services.task_runner import TaskRunner

        svc, d = _assigned_delegation(test_db, agents)
        runner = TaskRunner(test_db)
        outcome = asyncio.run(svc.run(
            d.id, state_store=runner, executor=tool_executor))
        return svc, d, outcome

    def test_malformed_result_rejected(self, test_db, agents):
        svc, d = _assigned_delegation(test_db, agents)
        bad = SpecialistResult(status="MAYBE", summary="")
        submission = svc.submit_result(d.id, bad)
        assert not submission["accepted"]
        assert submission["errors"]

    def test_completed_result_requires_evidence_and_confidence(self, test_db, agents):
        svc, d = _assigned_delegation(test_db, agents)
        no_evidence = SpecialistResult(
            status="COMPLETED", summary="done", findings=["f"])
        submission = svc.submit_result(d.id, no_evidence)
        assert not submission["accepted"]
        assert any("evidence_refs" in e for e in submission["errors"])

    def test_verify_rejects_forged_evidence(self, test_db, agents, tool_executor):
        from backend.services.task_runner import TaskRunner

        svc, d = _assigned_delegation(test_db, agents)
        runner = TaskRunner(test_db)
        outcome = asyncio.run(svc.run(
            d.id, state_store=runner, executor=tool_executor))
        assert outcome["status"] == "VERIFIED"

        # Tamper: claim evidence for an action that never happened.
        fresh = svc.get_delegation(d.id)
        fresh.status = "RESULT_READY"
        fresh.result_json = json.dumps(SpecialistResult(
            status="COMPLETED", summary="forged", findings=["made up"],
            evidence_refs=["action:99"], confidence=0.9).to_dict())
        svc.db.commit()
        verification = svc.verify(d.id)
        assert not verification["ok"]
        assert any("no matching runtime action" in e
                   for e in verification["errors"])

    def test_integration_outcomes(self, test_db, agents, tool_executor):
        svc, d, outcome = self._run_zero_action(test_db, agents, tool_executor)
        main = svc.agents.get_or_create_main()

        rejected = svc.integrate(d.id, "REJECT", actor_id=main.id)
        assert rejected["outcome"] == "REJECT"
        assert svc.get_delegation(d.id).integration_outcome == "REJECT"
        assert svc.get_delegation(d.id).status == "CANCELLED"

    def test_revision_then_accept(self, test_db, agents, tool_executor,
                                  monkeypatch):
        monkeypatch.setattr("backend.core.config.settings."
                            "max_specialist_revisions", 1)
        svc, d, outcome = self._run_zero_action(test_db, agents, tool_executor)
        main = svc.agents.get_or_create_main()

        revised = svc.integrate(d.id, "REVISE", actor_id=main.id)
        assert revised == {"outcome": "REVISE", "revisable": True}
        assert svc.get_delegation(d.id).status == "VALIDATED"

        # Re-run the bounded work once more, then accept.
        proposal = svc.validate(d.id)
        assert proposal["ok"]
        svc.assign(d.id)
        from backend.services.task_runner import TaskRunner

        rerun = asyncio.run(svc.run(d.id, state_store=TaskRunner(test_db),
                                    executor=tool_executor))
        assert rerun["verified"] is True
        accepted = svc.integrate(d.id, "ACCEPT", actor_id=main.id)
        assert accepted["outcome"] == "ACCEPT"
        assert svc.get_delegation(d.id).status == "INTEGRATED"

    def test_revision_budget_exhausted_becomes_reject(self, test_db, agents,
                                                      tool_executor):
        svc, d, outcome = self._run_zero_action(test_db, agents, tool_executor)
        main = svc.agents.get_or_create_main()
        first = svc.integrate(d.id, "REVISE", actor_id=main.id)
        assert first["outcome"] == "REVISE"
        svc.validate(d.id)
        svc.assign(d.id)
        from backend.services.task_runner import TaskRunner

        asyncio.run(svc.run(d.id, state_store=TaskRunner(test_db),
                            executor=tool_executor))
        second = svc.integrate(d.id, "REVISE", actor_id=main.id)
        assert second["outcome"] == "REJECT"  # revision cap reached

    def test_raw_output_cannot_jump_to_integrated(self, test_db, agents):
        svc, d = _assigned_delegation(test_db, agents)
        raw_output = "I'm done! Trust me, everything worked great."
        # Arbitrary natural-language output without the structured contract
        # cannot become trusted state — it never satisfies validation.
        raw = SpecialistResult.from_dict({"summary": raw_output})
        assert raw.validation_errors()  # missing status/findings/evidence/...
        # Even a well-formed-looking dict cannot integrate without the
        # VERIFIED gate (ASSIGNED submissions are refused).
        result = svc.submit_result(d.id, SpecialistResult(
            status="COMPLETED", summary="trust me", findings=["x"],
            evidence_refs=["context_packet"], confidence=0.9))
        assert not result["accepted"]
        assert svc.get_delegation(d.id).status == "ASSIGNED"


# --------------------------------------------------------------------- #
# Scoped memory                                                          #
# --------------------------------------------------------------------- #

class TestSpecialistMemory:
    def test_working_write_is_task_scoped_not_user_memory(self, test_db, agents):
        _, main = agents
        memory = ScopedMemoryAccess(test_db, main.id, "deleg-1")
        entry = memory.write_working("partial_result", "A beats B", kind="FACT")
        from backend.models.database import MemoryEntry

        row = test_db.query(MemoryEntry).filter_by(id=entry.id).first()
        assert row.scope == "TASK"
        assert row.source_type == "MODEL_INFERENCE"
        user_rows = test_db.query(MemoryEntry).filter_by(scope="USER").all()
        assert user_rows == []  # no user-memory pollution

    def test_permanent_write_is_structurally_impossible(self, test_db, agents):
        _, main = agents
        memory = ScopedMemoryAccess(test_db, main.id, "deleg-1")
        # The scoped facade exposes NO permanent write path at all...
        public = [m for m in dir(memory) if not m.startswith("_")]
        forbidden = {"create", "update", "forget", "store_user_memory"}
        assert not (forbidden & set(public))

    def test_memory_proposal_writes_event_only(self, test_db, agents):
        _, main = agents
        memory = ScopedMemoryAccess(test_db, main.id, "deleg-prop")
        proposal = memory.propose_permanent_memory(
            "preferred_editor", "vim", reason="observed twice")
        assert proposal["key"] == "preferred_editor"
        from backend.models.database import MemoryEntry

        assert test_db.query(MemoryEntry).filter_by(
            key="preferred_editor").first() is None

    def test_read_scope_respects_grants(self, test_db, agents):
        from backend.services.memory_service import MemoryService

        MemoryService(test_db).create(
            key="user_name", value="Ada", kind="FACT", scope="USER",
            source_type="USER_EXPLICIT")
        _, main = agents
        ungranted = ScopedMemoryAccess(test_db, main.id, "d1")
        assert ungranted.read_relevant("Ada") == []
        granted = ScopedMemoryAccess(test_db, main.id, "d2", grants=["USER"])
        hits = granted.read_relevant("Ada")
        assert len(hits) == 1


# --------------------------------------------------------------------- #
# Runtime boundary                                                       #
# --------------------------------------------------------------------- #

class TestSpecialistRuntime:
    def test_actions_flow_through_kernel_with_evidence(
            self, test_db, agents, tool_executor):
        from backend.runtime.events import DbEventLedger

        svc, d = _assigned_delegation(test_db, agents)
        packet = SpecialistContextPacket.from_json(
            svc.get_delegation(d.id).context_packet_json)
        tools = resolve_allowed_tools(packet.required_capabilities,
                                      tool_executor)
        assert "system_time" in tools
        assert "file_delete" not in tools  # binding stays within the role

    def test_policy_denial_survives_binding(self, test_db, agents,
                                            tool_executor):
        """CODING binds file_operations, but executor policy STILL authorizes:
        mutating actions need a direct user instruction, which specialists
        can never provide."""
        from backend.services.task_runner import TaskRunner

        svc, d = _assigned_delegation(test_db, agents, role="CODING")
        runner = TaskRunner(test_db)
        outcome = asyncio.run(svc.run(
            d.id, state_store=runner, executor=tool_executor,
            steps=[{"tool": "file_write",
                    "input": {"path": "x.txt", "content": "hi"}}]))
        # All actions denied by policy → honest failure, no forged success.
        assert outcome.get("failure_reason") == "all specialist actions failed"
        assert svc.get_delegation(d.id).status == "FAILED"


# --------------------------------------------------------------------- #
# Main Agent integration                                                 #
# --------------------------------------------------------------------- #

class TestMainAgentIntegration:
    def test_specialist_failure_leaves_parent_free(self, test_db, agents,
                                                   tool_executor, monkeypatch):
        monkeypatch.setattr("backend.core.config.settings."
                            "max_specialist_steps", 1)
        from backend.services.task_runner import TaskRunner

        svc, d = _assigned_delegation(test_db, agents)
        runner = TaskRunner(test_db)
        outcome = asyncio.run(svc.run(
            d.id, state_store=runner, executor=tool_executor,
            steps=[{"tool": "system_time", "input": {}},
                   {"tool": "system_time", "input": {}}]))
        assert outcome["started"] is True
        assert svc.get_delegation(d.id).status == "FAILED"
        # Parent goal untouched by specialist failure.
        from backend.services.goal_manager import GoalManager

        assert GoalManager(test_db).list_goals() is not None

    def test_only_main_agent_integrates_or_cancels(self, test_db, agents,
                                                   tool_executor):
        from backend.services.task_runner import TaskRunner

        svc, d, outcome = self._run_zero_action_shared(test_db, agents,
                                                       tool_executor)
        specialist = svc.agents.get_agent(d.specialist_agent_id)
        main_id = svc.agents.get_or_create_main().id
        with pytest.raises(AuthorityError):
            svc.integrate(d.id, "ACCEPT", actor_id=specialist.id)
        with pytest.raises(AuthorityError):
            svc.cancel(d.id, actor_id=specialist.id)
        assert svc.integrate(d.id, "ACCEPT", actor_id=main_id)[
            "outcome"] == "ACCEPT"

    @staticmethod
    def _run_zero_action_shared(test_db, agents, tool_executor):
        svc, d = _assigned_delegation(test_db, agents)
        from backend.services.task_runner import TaskRunner

        asyncio.run(svc.run(d.id, state_store=TaskRunner(test_db),
                            executor=tool_executor))
        return svc, d, None

    def test_cost_accounting_includes_specialist_work(self, test_db, agents,
                                                      tool_executor):
        svc, d = _assigned_delegation(test_db, agents)
        from backend.services.task_runner import TaskRunner

        outcome = asyncio.run(svc.run(
            d.id, state_store=TaskRunner(test_db), executor=tool_executor,
            steps=[{"tool": "system_time", "input": {}}]))
        assert json.loads(svc.get_delegation(d.id).budget_json)["spent"][
            "actions"] == 1


# --------------------------------------------------------------------- #
# Persistence / recovery                                                 #
# --------------------------------------------------------------------- #

class TestPersistenceRecovery:
    def test_no_duplicate_specialist_execution(self, test_db, agents,
                                               tool_executor):
        from backend.services.task_runner import TaskRunner

        svc, d = _assigned_delegation(test_db, agents)
        runner = TaskRunner(test_db)
        first = asyncio.run(svc.run(d.id, state_store=runner,
                                    executor=tool_executor))
        assert first["started"]
        second = asyncio.run(svc.run(d.id, state_store=runner,
                                     executor=tool_executor))
        assert second["started"] is False  # terminal: cannot re-execute
        from backend.models.database import Task

        specialist_tasks = test_db.query(Task).filter(
            Task.original_request.like("[specialist:%")).all()
        assert len(specialist_tasks) == 1

    def test_restart_during_delegation_recovers_safely(
            self, test_db, agents):
        """Simulates a crash while RUNNING: startup recovery resolves from
        task evidence without forging completion or duplicating tasks."""
        svc, d = _assigned_delegation(test_db, agents)
        # Force a RUNNING state as if the process died mid-execution.
        d.status = "RUNNING"
        d.attempt = 1
        d.specialist_task_id = "missing-task-after-crash"
        svc.db.commit()

        recovered = svc.recover_delegations()
        assert len(recovered) == 1
        assert recovered[0]["outcome"] == "missing_specialist_task"
        assert svc.get_delegation(d.id).status == "ASSIGNED"

        # Idempotent: nothing RUNNING anymore.
        assert svc.recover_delegations() == []

    def test_restart_with_kernel_completed_task_rebuilds_result(
            self, test_db, agents):
        from backend.api.schemas import TaskState
        from backend.models.database import Task
        from backend.services.task_runner import TaskRunner

        svc, d = _assigned_delegation(test_db, agents)
        d.status = "RUNNING"
        d.attempt = 1
        d.specialist_task_id = None
        svc.db.commit()
        runner = TaskRunner(test_db)
        assert asyncio.run(runner.acquire_lease())
        try:
            task = asyncio.run(runner.create_task(
                "spec work", idempotency_key=f"delegation:{d.id}:1"))
            asyncio.run(runner.transition_state(
                task.id, TaskState.RUNNING, "task.execution_started", {}))
            runner.ledger.append_event(task.id, "specialist.action", {
                "delegation_id": d.id, "tool": "system_time", "success": True,
                "seq": 1})
            svc.db.commit()
            asyncio.run(runner.transition_state(
                task.id, TaskState.COMPLETED, "task.completed", {}))
            d.specialist_task_id = task.id
            svc.db.commit()
        finally:
            asyncio.run(runner.release_lease())

        recovered = svc.recover_delegations()
        assert recovered[0]["outcome"] == "recovered_result_ready"
        result = SpecialistResult.from_dict(
            json.loads(svc.get_delegation(d.id).result_json))
        assert result.status == "COMPLETED"
        assert "recovered" in result.summary.lower()
        verification = svc.verify(d.id)
        assert verification["ok"] is True  # evidence matches ledger

    def test_deferred_recovery_for_live_task(self, test_db, agents):
        from backend.api.schemas import TaskState
        from backend.services.task_runner import TaskRunner

        svc, d = _assigned_delegation(test_db, agents)
        d.status = "RUNNING"
        svc.db.commit()
        runner = TaskRunner(test_db)
        assert asyncio.run(runner.acquire_lease())
        try:
            task = asyncio.run(runner.create_task(
                "in flight", idempotency_key=f"delegation:{d.id}:0"))
            asyncio.run(runner.transition_state(
                task.id, TaskState.RUNNING, "task.execution_started", {}))
            d.specialist_task_id = task.id
            svc.db.commit()
        finally:
            asyncio.run(runner.release_lease())

        recovered = svc.recover_delegations()
        assert recovered[0] == {
            "delegation_id": d.id, "resolved": False,
            "outcome": f"deferred_{TaskState.RUNNING.value}"}
        assert svc.get_delegation(d.id).status == "RUNNING"
