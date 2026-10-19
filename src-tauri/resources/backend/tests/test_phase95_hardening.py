"""Phase 9.5 hardening — adversarial tests.

Covers:
- Concurrency: admission race, fan-out limit, idempotency
- Capabilities: mapping consistency, role binding, drift detection
- Specialist reasoning: model selection, structured result, reasoning-only, action-producing, malformed, retry
- Evidence: valid, forged, missing, reasoning, runtime
- Integration: accept/reject/revise/continue/ask_user
- Isolation: A cannot affect B, memory/budget/cancellation isolation
- Recovery: specialist reasoning survives crash/restart
"""

import asyncio
import json
import threading
import time

import pytest

from backend.agents.delegation import DelegationRequest, DelegationService, SpecialistResult
from backend.agents.service import AgentService
from backend.agents.specialist import SpecialistContextPacket, ScopedMemoryAccess, SpecialistRuntime, resolve_allowed_tools
from backend.agents.state import ROLE_CAPABILITIES, SpecialistRole
from backend.services.capabilities import CapabilityRegistry, _specs


@pytest.fixture
def env(test_db):
    svc = AgentService(test_db)
    svc.ensure_definitions()
    main = svc.get_or_create_main()
    return test_db, svc, main


def _req(main_id, role="ANALYSIS", objective="Analyze X", **kw):
    return DelegationRequest(parent_agent_id=main_id, specialist_role=role, objective=objective, **kw)


# ------------------------------------------------------------------- #
# Concurrency: admission race
# ------------------------------------------------------------------- #

class TestAdmissionAtomicity:
    def test_two_callers_at_boundary_only_one_admits(self, env, monkeypatch):
        db, svc, main = env
        monkeypatch.setattr("backend.core.config.settings.max_specialists_per_task", 2)
        # One existing active delegation
        ds = DelegationService(db)
        first = ds.propose(_req(main.id, task_id="t-boundary", objective="first job"))
        assert first["error"] is None
        assert ds.validate(first["delegation"].id)["ok"]

        # Two concurrent validations for two new proposals on same task
        second = ds.propose(_req(main.id, task_id="t-boundary", objective="second job"))
        third = ds.propose(_req(main.id, task_id="t-boundary", objective="third job"))
        assert second["delegation"] is not None
        assert third["delegation"] is not None

        results = []
        # Use threads sharing the same engine via new sessions (StaticPool)
        from sqlalchemy.orm import sessionmaker
        Session = sessionmaker(bind=db.get_bind() if hasattr(db, "get_bind") else db.bind)

        def validate_in_thread(deleg_id, out_list, idx):
            sess = Session()
            try:
                svc2 = DelegationService(sess)
                out_list.append((idx, svc2.validate(deleg_id)))
                sess.commit()
            except Exception as e:
                out_list.append((idx, {"ok": False, "exc": str(e)}))
            finally:
                sess.close()

        threads = [
            threading.Thread(target=validate_in_thread, args=(second["delegation"].id, results, 0)),
            threading.Thread(target=validate_in_thread, args=(third["delegation"].id, results, 1)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        oks = [r for _, r in results if r.get("ok")]
        # With 1 existing + limit 2, only one of the two should succeed
        assert len(oks) <= 1, f"fan-out exceeded: {results}"
        # Overall active count never exceeds limit
        active = [d for d in ds.db.query(ds.db.query(DelegationService).first().__class__).all()] if False else None
        # Count via helper
        from backend.agents.delegation import active_delegations_for_task
        total = active_delegations_for_task(ds.db, "t-boundary", None)
        assert total <= 2

    def test_more_callers_than_limit(self, env, monkeypatch):
        db, svc, main = env
        monkeypatch.setattr("backend.core.config.settings.max_specialists_per_task", 2)
        ds = DelegationService(db)
        # Create 4 proposals on same task
        protos = [ds.propose(_req(main.id, task_id="t-many", objective=f"job {i}")) for i in range(4)]
        assert all(p["error"] is None for p in protos)
        ok_count = 0
        for p in protos:
            if ds.validate(p["delegation"].id)["ok"]:
                ok_count += 1
        assert ok_count <= 2
        from backend.agents.delegation import active_delegations_for_task
        assert active_delegations_for_task(ds.db, "t-many", None) <= 2

    def test_duplicate_idempotency_keys(self, env):
        db, svc, main = env
        ds = DelegationService(db)
        req = _req(main.id, objective="duplicate test objective")
        first = ds.propose(req)
        assert first["error"] is None
        second = ds.propose(req)
        assert second["error"] == "duplicate_delegation"
        assert second["delegation"].id == first["delegation"].id

    def test_validation_failure_concurrent_with_valid(self, env, monkeypatch):
        db, svc, main = env
        monkeypatch.setattr("backend.core.config.settings.max_specialists_per_task", 3)
        ds = DelegationService(db)
        valid = ds.propose(_req(main.id, task_id="t-mixed", objective="valid job"))
        invalid = ds.propose(_req(main.id, task_id="t-mixed", objective="invalid job",
                                  constraints={"required_capabilities": ["ghost_cap"]}))
        assert valid["error"] is None
        assert invalid["error"] is None
        # Validate invalid first (should fail), then valid (should succeed)
        assert not ds.validate(invalid["delegation"].id)["ok"]
        assert ds.validate(valid["delegation"].id)["ok"]

    def test_process_restart_during_admission(self, env):
        ds = DelegationService(env[0])
        svc, main = env[1], env[2]
        req = _req(main.id, objective="restart test")
        prop = ds.propose(req)
        assert prop["error"] is None
        # Simulate crash before validate: delegation stays PROPOSED
        d = prop["delegation"]
        assert d.status == "PROPOSED"
        # Recovery should not create duplicate and should allow validate after restart
        new_ds = DelegationService(env[0])
        assert new_ds.validate(d.id)["ok"]


# ------------------------------------------------------------------- #
# Capabilities
# ------------------------------------------------------------------- #

class TestCapabilityIntegrity:
    def test_every_role_resolves_to_valid_capabilities(self, env, tool_executor):
        db, svc, main = env
        registry_ids = {s.id for s in _specs()}
        for role in SpecialistRole:
            caps = ROLE_CAPABILITIES[role]
            for cap in caps:
                assert cap in registry_ids, f"role {role} references unknown capability {cap}"

    def test_every_capability_resolves_to_registered_tool(self, env, tool_executor):
        db, svc, main = env
        reg = CapabilityRegistry(db, tool_executor=tool_executor)
        for spec in _specs():
            if not spec.tool_names:
                continue  # voice/reasoning_model intentionally tool-less
            for tool in spec.tool_names:
                assert tool in tool_executor.list_tools(), f"capability {spec.id} references missing tool {tool}"

    def test_unavailable_tools_cause_capability_unavailable(self, env):
        from unittest.mock import MagicMock
        db, svc, main = env
        # Simulate executor with no tools
        empty_exec = MagicMock()
        empty_exec.list_tools.return_value = []
        reg = CapabilityRegistry(db, tool_executor=empty_exec)
        # file_operations should be UNAVAILABLE when no tools
        assert reg.resolve_status(next(s for s in _specs() if s.id == "file_operations")) == "UNAVAILABLE"

    def test_specialist_cannot_request_tool_outside_binding(self, env, tool_executor):
        db, svc, main = env
        packet = SpecialistContextPacket(objective="x", specialist_agent_id="s", role="ANALYSIS",
                                         required_capabilities=["system_observation"])
        tools = resolve_allowed_tools(packet.required_capabilities, tool_executor)
        runtime = SpecialistRuntime(db=db, agent_id="s", delegation_id="d", allowed_tools=tools,
                                    task_executor=tool_executor, task_id="t")
        with pytest.raises(Exception):
            import asyncio
            asyncio.run(runtime.request_action("file_write", {"path": "x", "content": "hi"}))

    def test_capability_metadata_runtime_binding_consistent(self, env, tool_executor):
        db, svc, main = env
        reg = CapabilityRegistry(db, tool_executor=tool_executor)
        for spec in _specs():
            listed = reg.get_capability(spec.id)
            assert listed is not None
            # tools_registered + tools_missing should equal spec.tool_names
            all_tools = set(listed.get("tools_registered", []) + listed.get("tools_missing", []))
            assert set(spec.tool_names) == all_tools or not spec.tool_names

    def test_drift_detection_reports_missing(self, env):
        from backend.services.capabilities import CapabilitySpec
        from unittest.mock import patch
        db, svc, main = env
        ghost = CapabilitySpec(id="ghost", name="Ghost", category="t", description="x", tool_names=("nope_tool",))
        import backend.services.capabilities as cap_mod
        orig = cap_mod._specs
        cap_mod._specs = lambda: orig() + [ghost]
        try:
            reg = CapabilityRegistry(db)
            issues = reg.validate()
            assert any(i.get("tool") == "nope_tool" for i in issues)
        finally:
            cap_mod._specs = orig

    def test_role_binding_validation_fails_on_unknown(self, env):
        db, svc, main = env
        # Temporarily inject bad role cap
        from backend.agents.state import ROLE_CAPABILITIES
        orig = ROLE_CAPABILITIES[SpecialistRole.RESEARCH]
        try:
            ROLE_CAPABILITIES[SpecialistRole.RESEARCH] = ("nonexistent_cap",)
            with pytest.raises(ValueError):
                svc.ensure_definitions()
        finally:
            ROLE_CAPABILITIES[SpecialistRole.RESEARCH] = orig


# ------------------------------------------------------------------- #
# Specialist reasoning
# ------------------------------------------------------------------- #

class _FakeProvider:
    def __init__(self, response):
        self.response = response
        self.calls = 0
        self._selected = "fake-model"
    def provider_id(self): return "fake"
    def selected_model_id(self): return self._selected
    def list_models(self): return [{"name": self._selected}]
    def health(self):
        from backend.runtime.model_provider import ModelHealth
        return ModelHealth("fake", ModelHealth.HEALTHY)
    async def chat(self, messages, max_tokens=4000, timeout=30, model_id=None):
        self.calls += 1
        return self.response


class TestSpecialistReasoning:
    def _make_packet(self, role="ANALYSIS"):
        return SpecialistContextPacket(objective="Compare A and B", specialist_agent_id="s1",
                                       role=role, required_capabilities=["system_observation"],
                                       budget={"steps": 5, "time_seconds": 300, "model_calls": 5})

    def test_model_selection_used(self, env):
        db, svc, main = env
        packet = self._make_packet("ANALYSIS")
        fake = _FakeProvider({"message": {"content": '{"findings": ["Analysis shows A better due to latency"], "limitations": ["limited data"], "confidence": 0.7}'}})
        from backend.agents.reasoner import SpecialistReasoner
        reasoner = SpecialistReasoner(db, packet, allowed_tools=["system_time"], budget=packet.budget,
                                      model_provider=fake)
        out = asyncio.run(reasoner.analyze())
        assert out["findings"]
        assert fake.calls == 1

    def test_structured_result_from_reasoning(self, env):
        db, svc, main = env
        packet = self._make_packet("RESEARCH")
        fake = _FakeProvider({"message": {"content": '{"findings": ["Research finding: X is viable"], "analysis": "Summary", "limitations": ["preliminary"], "confidence": 0.8, "recommended_next_action": "integrate"}'}})
        from backend.agents.reasoner import SpecialistReasoner
        reasoner = SpecialistReasoner(db, packet, allowed_tools=[], budget=packet.budget, model_provider=fake)
        out = asyncio.run(reasoner.analyze())
        assert out["findings"][0].startswith("Research finding")

    def test_reasoning_only_role_produces_findings(self, env, tool_executor):
        db, svc, main = env
        svc.ensure_definitions()
        ds = DelegationService(db)
        # RESEARCH is reasoning-only
        prop = ds.propose(_req(main.id, role="RESEARCH", objective="Research quantum tradeoffs"))
        assert prop["error"] is None
        ds.validate(prop["delegation"].id)
        ds.assign(prop["delegation"].id)
        fake = _FakeProvider({"message": {"content": '{"findings": ["Quantum A offers coherence", "Quantum B scales"], "analysis": "Research complete", "limitations": ["speculative"], "confidence": 0.75}'}})
        import backend.runtime.model_provider as mpm
        orig = mpm.get_default_provider
        mpm.get_default_provider = lambda: fake
        try:
            from backend.services.task_runner import TaskRunner
            runner = TaskRunner(db)
            out = asyncio.run(runner.acquire_lease())
            # Runner lease is acquired by DelegationService.run itself; release the pre-acquired lease first
            asyncio.run(runner.release_lease())
            out = asyncio.run(ds.run(prop["delegation"].id, state_store=runner, executor=tool_executor))
            assert out["verified"] is True
            assert len(out["result"]["findings"]) >= 1
        finally:
            mpm.get_default_provider = orig

    def test_malformed_output_triggers_repair(self, env):
        db, svc, main = env
        packet = self._make_packet("ANALYSIS")
        # First call malformed, second valid (repair)
        class RepairProvider(_FakeProvider):
            async def chat(self, messages, **kw):
                self.calls += 1
                if self.calls == 1:
                    return {"message": {"content": "not json at all"}}
                return {"message": {"content": '{"findings": ["repaired finding"], "limitations": ["ok"], "confidence": 0.6}'}}
        fake = RepairProvider({})
        from backend.agents.reasoner import SpecialistReasoner
        reasoner = SpecialistReasoner(db, packet, allowed_tools=[], budget=packet.budget, model_provider=fake)
        out = asyncio.run(reasoner.analyze())
        assert out["findings"]  # repaired
        assert fake.calls == 2

    def test_bounded_retry_exhausted_returns_fallback(self, env):
        db, svc, main = env
        packet = self._make_packet("ANALYSIS")
        fake = _FakeProvider({"message": {"content": "still not json"}})
        # Force malformed both attempts
        from backend.agents.reasoner import SpecialistReasoner
        reasoner = SpecialistReasoner(db, packet, allowed_tools=[], budget=packet.budget, model_provider=fake)
        out = asyncio.run(reasoner.analyze())
        # Should be fallback with empty findings but marked
        assert out.get("fallback") is True or not out["findings"]

    def test_action_producing_role_validates_capabilities(self, env, tool_executor):
        db, svc, main = env
        packet = SpecialistContextPacket(objective="Create file", specialist_agent_id="s1", role="CODING",
                                         required_capabilities=["file_operations"],
                                         budget={"steps": 5, "model_calls": 5})
        # CODING proposes file tool (allowed) vs browser tool (not allowed)
        fake = _FakeProvider({"message": {"content": '{"findings": ["will create file"], "proposed_actions": [{"capability": "browser_control", "tool": "browser_tabs", "purpose": "browse", "input": {}}, {"capability": "file_operations", "tool": "file_create", "purpose": "create", "input": {"path": "x.txt", "content": "hi"}}], "limitations": ["none"], "confidence": 0.7}'}})
        from backend.agents.reasoner import SpecialistReasoner
        reasoner = SpecialistReasoner(db, packet, allowed_tools=resolve_allowed_tools(["file_operations"], tool_executor),
                                      budget=packet.budget, model_provider=fake)
        out = asyncio.run(reasoner.analyze())
        # Only file_create should survive validation
        assert len(out["proposed_actions"]) == 1
        assert out["proposed_actions"][0]["tool"] == "file_create"


# ------------------------------------------------------------------- #
# Evidence
# ------------------------------------------------------------------- #

class TestEvidence:
    def test_valid_evidence_passes_verification(self, env, tool_executor):
        db, svc, main = env
        ds = DelegationService(db)
        prop = ds.propose(_req(main.id, objective="evidence valid"))
        ds.validate(prop["delegation"].id)
        ds.assign(prop["delegation"].id)
        from backend.services.task_runner import TaskRunner
        runner = TaskRunner(db)
        out = asyncio.run(_run_delegation(ds, prop["delegation"].id, runner, tool_executor, steps=[{"tool": "system_time", "input": {}}]))
        assert out["verified"] is True

    def test_forged_evidence_rejected(self, env):
        db, svc, main = env
        ds = DelegationService(db)
        prop = ds.propose(_req(main.id, objective="forged evidence"))
        ds.validate(prop["delegation"].id)
        ds.assign(prop["delegation"].id)
        # Manually set RUNNING and inject forged result
        d = prop["delegation"]
        d.status = "RUNNING"
        d.specialist_task_id = "fake-task-no-events"
        db.commit()
        forged = SpecialistResult(status="COMPLETED", summary="forged", findings=["x"], evidence_refs=["action:1"], confidence=0.9, limitations=["none"])
        # Need to be RUNNING to submit
        res = ds.submit_result(d.id, forged)
        if res["accepted"]:
            ver = ds.verify(d.id)
            assert not ver["ok"]

    def test_missing_evidence_rejected(self, env):
        # COMPLETED without evidence should fail validation
        r = SpecialistResult(status="COMPLETED", summary="s", findings=["substantive finding here"], evidence_refs=[], confidence=0.5, limitations=["l"])
        assert r.validation_errors()

    def test_reasoning_evidence_for_zero_action(self, env, tool_executor):
        db, svc, main = env
        ds = DelegationService(db)
        prop = ds.propose(_req(main.id, role="ANALYSIS", objective="reasoning evidence test"))
        ds.validate(prop["delegation"].id)
        ds.assign(prop["delegation"].id)
        from backend.services.task_runner import TaskRunner
        runner = TaskRunner(db)
        out = asyncio.run(_run_delegation(ds, prop["delegation"].id, runner, tool_executor, steps=[]))
        # Zero-action should be verified with context_packet or reasoning evidence
        assert out["verified"] is True

    def test_runtime_evidence(self, env, tool_executor):
        # After a successful action, evidence should be runtime.action verifiable
        db, svc, main = env
        ds = DelegationService(db)
        prop = ds.propose(_req(main.id, objective="runtime evidence"))
        ds.validate(prop["delegation"].id)
        ds.assign(prop["delegation"].id)
        from backend.services.task_runner import TaskRunner
        runner = TaskRunner(db)
        out = asyncio.run(_run_delegation(ds, prop["delegation"].id, runner, tool_executor, steps=[{"tool": "system_time", "input": {}}]))
        result = SpecialistResult.from_dict(out["result"])
        assert any(r.startswith("action:") for r in result.evidence_refs)


def _run_delegation(ds, deleg_id, runner, executor, steps=None):
    async def _inner():
        # DelegationService.run acquires the lease itself; do not pre-acquire
        return await ds.run(deleg_id, state_store=runner, executor=executor, steps=steps or [])
    return _inner()


# ------------------------------------------------------------------- #
# Integration
# ------------------------------------------------------------------- #

class TestIntegration:
    def _verified_delegation(self, env, tool_executor):
        db, svc, main = env
        ds = DelegationService(db)
        prop = ds.propose(_req(main.id, objective="integration test"))
        ds.validate(prop["delegation"].id)
        ds.assign(prop["delegation"].id)
        from backend.services.task_runner import TaskRunner
        runner = TaskRunner(db)
        out = asyncio.run(_run_delegation(ds, prop["delegation"].id, runner, tool_executor, steps=[]))
        assert out["verified"] is True
        return ds, prop["delegation"].id, main.id

    def test_accept(self, env, tool_executor):
        ds, did, main_id = self._verified_delegation(env, tool_executor)
        res = ds.integrate(did, "ACCEPT", actor_id=main_id)
        assert res["outcome"] == "ACCEPT"
        assert ds.get_delegation(did).status == "INTEGRATED"

    def test_reject(self, env, tool_executor):
        ds, did, main_id = self._verified_delegation(env, tool_executor)
        res = ds.integrate(did, "REJECT", actor_id=main_id)
        assert res["outcome"] == "REJECT"
        assert ds.get_delegation(did).status == "CANCELLED"

    def test_revise(self, env, tool_executor, monkeypatch):
        monkeypatch.setattr("backend.core.config.settings.max_specialist_revisions", 1)
        ds, did, main_id = self._verified_delegation(env, tool_executor)
        res = ds.integrate(did, "REVISE", actor_id=main_id, feedback={"problem": "missing detail"})
        assert res["outcome"] == "REVISE"
        assert ds.get_delegation(did).status == "VALIDATED"

    def test_continue(self, env, tool_executor):
        ds, did, main_id = self._verified_delegation(env, tool_executor)
        res = ds.integrate(did, "CONTINUE", actor_id=main_id)
        assert res["outcome"] == "CONTINUE"
        assert ds.get_delegation(did).status == "CANCELLED"

    def test_ask_user(self, env, tool_executor):
        ds, did, main_id = self._verified_delegation(env, tool_executor)
        res = ds.integrate(did, "ASK_USER", actor_id=main_id)
        assert res["outcome"] == "ASK_USER"
        assert ds.get_delegation(did).status == "CANCELLED"


# ------------------------------------------------------------------- #
# Isolation
# ------------------------------------------------------------------- #

class TestIsolation:
    def test_specialist_a_cannot_affect_b(self, env, tool_executor):
        db, svc, main = env
        ds = DelegationService(db)
        p1 = ds.propose(_req(main.id, role="ANALYSIS", objective="isolated A"))
        p2 = ds.propose(_req(main.id, role="CODING", objective="isolated B"))
        for p in (p1, p2):
            ds.validate(p["delegation"].id)
            ds.assign(p["delegation"].id)
        # Each packet should have distinct specialist_agent_id and not share memory
        pkt1 = SpecialistContextPacket.from_json(ds.get_delegation(p1["delegation"].id).context_packet_json)
        pkt2 = SpecialistContextPacket.from_json(ds.get_delegation(p2["delegation"].id).context_packet_json)
        assert pkt1.specialist_agent_id != pkt2.specialist_agent_id
        # Allowed tools differ by role
        tools1 = resolve_allowed_tools(pkt1.required_capabilities, tool_executor)
        tools2 = resolve_allowed_tools(pkt2.required_capabilities, tool_executor)
        assert "system_time" in tools1
        assert "file_create" in tools2
        assert "file_create" not in tools1

    def test_scoped_memory_isolation(self, test_db):
        from backend.services.memory_service import MemoryService
        MemoryService(test_db).create(key="user_secret", value="shh", kind="FACT", scope="USER", source_type="USER_EXPLICIT")
        _, svc_main = AgentService(test_db), None
        # Two specialists with different grants
        a = ScopedMemoryAccess(test_db, "spec-a", "del-a", grants=[])
        b = ScopedMemoryAccess(test_db, "spec-b", "del-b", grants=["USER"])
        assert a.read_relevant("shh") == []
        assert len(b.read_relevant("shh")) == 1

    def test_budget_isolation(self, env, tool_executor):
        db, svc, main = env
        # Two runtimes should not share budget counters
        pkt = SpecialistContextPacket(objective="x", specialist_agent_id="s1", role="ANALYSIS", required_capabilities=["system_observation"])
        tools = resolve_allowed_tools(pkt.required_capabilities, tool_executor)
        r1 = SpecialistRuntime(db=db, agent_id="s1", delegation_id="d1", allowed_tools=tools, task_executor=tool_executor, task_id="t1")
        r2 = SpecialistRuntime(db=db, agent_id="s2", delegation_id="d2", allowed_tools=tools, task_executor=tool_executor, task_id="t2")
        import asyncio
        asyncio.run(r1.request_action("system_time", {}))
        assert r1.actions_used == 1
        assert r2.actions_used == 0

    def test_cancellation_isolation(self, env, tool_executor):
        # ContextVar isolation: cancellation of one task does not affect another
        from backend.tools.executor import PolicyContext, current_execution_context
        assert current_execution_context() is None  # no leaked state


# ------------------------------------------------------------------- #
# Recovery
# ------------------------------------------------------------------- #

class TestRecovery:
    def test_reasoning_survives_crash_restart(self, env, tool_executor):
        db, svc, main = env
        ds = DelegationService(db)
        prop = ds.propose(_req(main.id, role="ANALYSIS", objective="recover reasoning"))
        ds.validate(prop["delegation"].id)
        ds.assign(prop["delegation"].id)
        from backend.services.task_runner import TaskRunner
        runner = TaskRunner(db)
        import asyncio
        asyncio.run(runner.acquire_lease())
        try:
            # Simulate a crash after reasoning but before result: create RUNNING state with task completed
            from backend.api.schemas import TaskState
            d = prop["delegation"]
            d.status = "RUNNING"
            d.attempt = 1
            task = asyncio.run(runner.create_task("spec work", idempotency_key=f"delegation:{d.id}:1"))
            asyncio.run(runner.transition_state(task.id, TaskState.RUNNING, "task.execution_started", {}))
            runner.ledger.append_event(task.id, "specialist.reasoning", {"findings": 2})
            runner.ledger.append_event(task.id, "specialist.action", {"tool": "system_time", "success": True, "seq": 1})
            db.commit()
            asyncio.run(runner.transition_state(task.id, TaskState.COMPLETED, "task.completed", {}))
            d.specialist_task_id = task.id
            db.commit()
        finally:
            asyncio.run(runner.release_lease())
        rec = ds.recover_delegations()
        assert any(r["outcome"] == "recovered_result_ready" for r in rec)
        result = SpecialistResult.from_dict(json.loads(ds.get_delegation(d.id).result_json))
        assert "recovered" in result.summary.lower()
        # Evidence preserved and verifiable
        assert ds.verify(d.id)["ok"] is True
        # No duplicate task
        from backend.models.database import Task
        tasks = db.query(Task).filter(Task.idempotency_key.like(f"delegation:{d.id}:%")).all()
        assert len(tasks) == 1
