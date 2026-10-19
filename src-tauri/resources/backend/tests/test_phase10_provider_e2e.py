"""Phase 10 provider E2E: two reasoning-only specialists in one plan + aggregation.

Gated like phase975: set SYTHION_RUN_PROVIDER_E2E=1
"""
import asyncio
import json
import os
import sys

import pytest

PROJECT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT not in sys.path:
    sys.path.insert(0, PROJECT)

from backend.core.secrets import resolve_ollama_api_key

def _provider_ready():
    from backend.core.config import settings, resolve_selected_model
    try:
        if not resolve_ollama_api_key(settings.ollama_api_key):
            return False
        return bool(resolve_selected_model())
    except Exception:
        return False

pytestmark = [
    pytest.mark.provider_e2e,
    pytest.mark.skipif(os.environ.get("SYTHION_RUN_PROVIDER_E2E") != "1", reason="set SYTHION_RUN_PROVIDER_E2E=1"),
    pytest.mark.skipif(not _provider_ready(), reason="no provider key/model"),
]

@pytest.fixture(autouse=True)
def _fresh_provider(monkeypatch):
    try:
        from backend.adapters.ollama_adapter import get_adapter
    except Exception:
        yield
        return
    adapter = get_adapter()
    adapter._client = None
    orig = adapter._get_client
    async def fresh():
        adapter._client=None
        return await orig()
    monkeypatch.setattr(adapter, "_get_client", fresh)
    yield

@pytest.mark.asyncio
async def test_two_reasoning_specialists_aggregation():
    from backend.core.db_manager import DatabaseManager
    from backend.agents.service import AgentService
    from backend.agents.coordination import DelegationPlanService, ResultAggregator
    from backend.agents.state import DelegationStatus
    from backend.agents.delegation import DelegationService, SpecialistResult
    from backend.agents.specialist import SpecialistRuntime, resolve_allowed_tools
    from backend.agents.reasoner import SpecialistReasoner
    from backend.services.task_runner import TaskRunner
    from backend.api.dependencies import get_tool_executor
    import tempfile
    p=tempfile.mktemp(suffix=".db")
    m=DatabaseManager(p)
    sess=m.new_session()
    try:
        ags=AgentService(sess)
        ags.ensure_definitions()
        main=ags.get_or_create_main()
        plan_svc=DelegationPlanService(sess)
        res=plan_svc.create_plan(parent_agent_id=main.id, objective="Compare deployment options", specialist_specs=[
            {"role":"RESEARCH","objective":"Research option A scalability"},
            {"role":"ANALYSIS","objective":"Analyze option B cost vs A","depends_on":["0"]},
        ])
        assert res["error"] is None
        pid=res["plan"].id
        d0,d1=res["delegations"]
        # Validate and assign first
        del_svc=DelegationService(sess)
        # Simulate assignment: need to move VALIDATED->ASSIGNED via del_svc.assign
        assigned0=del_svc.assign(d0.id)
        assert "error" not in assigned0 or assigned0.get("delegation")
        # Build packet and run reasoning for d0
        pkt0=plan_svc.build_specialist_packet(sess.query(type(d0)).filter_by(id=d0.id).first())
        allowed0=resolve_allowed_tools(pkt0.required_capabilities, get_tool_executor())
        reasoner0=SpecialistReasoner(db=sess, context_packet=pkt0, allowed_tools=allowed0, budget=pkt0.budget)
        analysis0=await reasoner0.analyze()
        # must be non-fallback when provider is real
        assert not analysis0.get("fallback"), f"fallback: {analysis0}"
        assert analysis0.get("findings")
        # Create result from analysis
        from backend.agents.delegation import SpecialistResult
        # Build result via delegation helper
        cost0={"actions":0,"model_calls":reasoner0.model_calls_used}
        result0=DelegationService._build_result_from_analysis([], pkt0, cost0, analysis0)
        assert not result0.validation_errors()
        # Submit and verify
        runner=TaskRunner(sess)
        await runner.acquire_lease()
        # Need specialist task for evidence: create minimal task
        task0=await runner.create_task("[specialist:RESEARCH] Research", idempotency_key=f"delegation:{d0.id}:1")
        from backend.api.schemas import TaskState
        await runner.transition_state(task0.id, TaskState.RUNNING, "task.execution_started", {})
        from backend.runtime.events import DbEventLedger
        ledger=DbEventLedger(sess)
        ledger.append_event(task0.id, "specialist.reasoning", {"findings": len(analysis0["findings"])})
        ledger.append_event(task0.id, "delegation.context.built", {"delegation_id": d0.id})
        sess.commit()
        d0.specialist_task_id=task0.id
        d0.result_json=json.dumps(result0.to_dict())
        d0.status=DelegationStatus.RESULT_READY.value
        sess.commit()
        ver0=del_svc.verify(d0.id)
        assert ver0["ok"], ver0
        # Now second specialist should be ready (depends on first VERIFIED)
        assert d0.status=="VERIFIED"
        ready=plan_svc.ready_delegations(pid)
        assert any(d.id==d1.id for d in ready)
        pkt1=plan_svc.build_specialist_packet(sess.query(type(d1)).filter_by(id=d1.id).first())
        assert "_dependency_results" in pkt1.constraints
        allowed1=resolve_allowed_tools(pkt1.required_capabilities, get_tool_executor())
        reasoner1=SpecialistReasoner(db=sess, context_packet=pkt1, allowed_tools=allowed1, budget=pkt1.budget)
        analysis1=await reasoner1.analyze()
        assert not analysis1.get("fallback")
        result1=DelegationService._build_result_from_analysis([], pkt1, {"actions":0,"model_calls":reasoner1.model_calls_used}, analysis1)
        task1=await runner.create_task("[specialist:ANALYSIS] Analyze", idempotency_key=f"delegation:{d1.id}:1")
        await runner.transition_state(task1.id, TaskState.RUNNING, "task.execution_started", {})
        ledger.append_event(task1.id, "specialist.reasoning", {"findings": len(analysis1["findings"])})
        ledger.append_event(task1.id, "delegation.context.built", {"delegation_id": d1.id})
        sess.commit()
        d1.specialist_task_id=task1.id
        d1.result_json=json.dumps(result1.to_dict())
        d1.status=DelegationStatus.RESULT_READY.value
        sess.commit()
        ver1=del_svc.verify(d1.id)
        assert ver1["ok"], ver1
        # Aggregation
        agg=ResultAggregator()
        synth=agg.synthesize(plan_svc.get_plan(pid), plan_svc.list_delegations(pid))
        assert synth["verified_count"]==2
        assert synth["aggregation_confidence"]["aggregation_confidence"] is not None
        await runner.release_lease()
    finally:
        sess.close()
        m.engine.dispose()
        import gc, time
        gc.collect(); time.sleep(0.2)
        for s in ("","-wal","-shm"):
            try: os.remove(p+s)
            except: pass
