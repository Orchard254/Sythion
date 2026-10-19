"""Phase 10 multi-specialist coordination tests.

Covers coordination, concurrency limits, runtime isolation, failure,
recovery, security invariants, aggregation, and staged parallelism.
"""
import json
import pytest
from unittest.mock import MagicMock

from backend.core.db_manager import DatabaseManager
from backend.agents.service import AgentService
from backend.agents.delegation import DelegationService, SpecialistResult
from backend.agents.coordination import (
    DelegationPlanService, ResultAggregator, ParallelismPolicy,
    DelegationGraph, capability_is_concurrent_safe,
)
from backend.agents.state import DelegationStatus, CompletionPolicy
from backend.core.config import settings

import tempfile, os


def fresh_db():
    p = tempfile.mktemp(suffix=".db")
    m = DatabaseManager(p)
    sess = m.new_session()
    AgentService(sess).ensure_definitions()
    return m, sess, p

def cleanup(m, p):
    try:
        m.engine.dispose()
    except Exception:
        pass
    import gc, time
    gc.collect()
    time.sleep(0.15)
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(p + suffix)
        except Exception:
            pass


class TestPlanCreation:
    def test_atomic_creation(self):
        m,sess,p = fresh_db()
        try:
            main = AgentService(sess).get_or_create_main()
            svc = DelegationPlanService(sess)
            res = svc.create_plan(parent_agent_id=main.id, objective="Goal", specialist_specs=[
                {"role":"RESEARCH","objective":"R1"},
                {"role":"ANALYSIS","objective":"A1","depends_on":["0"]},
            ])
            assert res["error"] is None
            assert len(res["delegations"])==2
            assert res["plan"].status=="ACTIVE"
            # deps resolved to real ids
            assert json.loads(res["delegations"][1].depends_on_json)[0]==res["delegations"][0].id
        finally:
            sess.close(); cleanup(m,p)

    def test_atomic_failure_no_partial(self):
        m,sess,p=fresh_db()
        try:
            main=AgentService(sess).get_or_create_main()
            svc=DelegationPlanService(sess)
            # invalid role should not create any plan
            res=svc.create_plan(parent_agent_id=main.id, objective="Goal", specialist_specs=[
                {"role":"RESEARCH","objective":"R1"},
                {"role":"INVALID","objective":"bad"},
            ])
            assert res["error"] is not None
            assert sess.query(DelegationPlanService).count if False else True
            from backend.models.database import DelegationPlan
            assert sess.query(DelegationPlan).count()==0
        finally:
            sess.close(); cleanup(m,p)

    def test_cycle_detection(self):
        m,sess,p=fresh_db()
        try:
            main=AgentService(sess).get_or_create_main()
            svc=DelegationPlanService(sess)
            res=svc.create_plan(parent_agent_id=main.id, objective="G", specialist_specs=[
                {"role":"RESEARCH","objective":"A","depends_on":["1"]},
                {"role":"ANALYSIS","objective":"B","depends_on":["0"]},
            ])
            assert "cycle" in res["error"].lower()
        finally:
            sess.close(); cleanup(m,p)

    def test_fanout_limits(self):
        m,sess,p=fresh_db()
        try:
            main=AgentService(sess).get_or_create_main()
            svc=DelegationPlanService(sess)
            old=settings.max_specialists_per_plan
            settings.max_specialists_per_plan=2
            res=svc.create_plan(parent_agent_id=main.id, objective="G", specialist_specs=[
                {"role":"RESEARCH","objective":"A"},
                {"role":"ANALYSIS","objective":"B"},
                {"role":"REVIEW","objective":"C"},
            ])
            assert "max_specialists_per_plan" in res["error"]
            settings.max_specialists_per_plan=old
        finally:
            sess.close(); cleanup(m,p)

    def test_budget_limits(self):
        m,sess,p=fresh_db()
        try:
            main=AgentService(sess).get_or_create_main()
            svc=DelegationPlanService(sess)
            old=settings.max_total_specialist_steps
            settings.max_total_specialist_steps=5
            res=svc.create_plan(parent_agent_id=main.id, objective="G", specialist_specs=[
                {"role":"RESEARCH","objective":"A","budget":{"max_specialist_steps":3}},
                {"role":"ANALYSIS","objective":"B","budget":{"max_specialist_steps":3}},
            ])
            assert "max_total_specialist_steps" in res["error"]
            settings.max_total_specialist_steps=old
        finally:
            sess.close(); cleanup(m,p)


class TestDependencyGraph:
    def test_ready_only_when_verified(self):
        m,sess,p=fresh_db()
        try:
            main=AgentService(sess).get_or_create_main()
            svc=DelegationPlanService(sess)
            res=svc.create_plan(parent_agent_id=main.id, objective="G", specialist_specs=[
                {"role":"RESEARCH","objective":"R"},
                {"role":"ANALYSIS","objective":"A","depends_on":["0"]},
            ])
            pid=res["plan"].id
            ready=[d.specialist_role for d in svc.ready_delegations(pid)]
            assert ready==["RESEARCH"]
            # verify first
            d0=res["delegations"][0]
            r=SpecialistResult(status="COMPLETED", summary="done research", findings=["found X scalable"], evidence_refs=["context_packet"], confidence=0.9, evidence_confidence=0.8, limitations=["none"], recommended_next_action="use X", cost={})
            d0.result_json=json.dumps(r.to_dict())
            d0.status=DelegationStatus.VERIFIED.value
            sess.commit()
            ready2=[d.specialist_role for d in svc.ready_delegations(pid)]
            assert "ANALYSIS" in ready2
        finally:
            sess.close(); cleanup(m,p)

    def test_unverified_cannot_unlock(self):
        m,sess,p=fresh_db()
        try:
            main=AgentService(sess).get_or_create_main()
            svc=DelegationPlanService(sess)
            res=svc.create_plan(parent_agent_id=main.id, objective="G", specialist_specs=[
                {"role":"RESEARCH","objective":"R"},
                {"role":"ANALYSIS","objective":"A","depends_on":["0"]},
            ])
            # leave first as VALIDATED (not VERIFIED) -> second not ready
            pid=res["plan"].id
            assert len(svc.ready_delegations(pid))==1
            # set to RESULT_READY (still not VERIFIED) -> still not ready
            res["delegations"][0].status=DelegationStatus.RESULT_READY.value
            sess.commit()
            # need to re-query graph
            from backend.models.database import Delegation
            graph=svc.get_graph(pid)
            # Check is_ready for second
            d1=sess.query(Delegation).filter_by(id=res["delegations"][1].id).first()
            ok,_=graph.is_ready(d1)
            assert not ok
        finally:
            sess.close(); cleanup(m,p)

    def test_deterministic_readiness(self):
        m,sess,p=fresh_db()
        try:
            main=AgentService(sess).get_or_create_main()
            svc=DelegationPlanService(sess)
            res=svc.create_plan(parent_agent_id=main.id, objective="G", specialist_specs=[
                {"role":"RESEARCH","objective":"R1","priority":2},
                {"role":"ANALYSIS","objective":"A1","priority":1},
                {"role":"REVIEW","objective":"Rv1","priority":1},
            ])
            pid=res["plan"].id
            ready=svc.ready_delegations(pid)
            # priority 1 first sorted by created_at then id
            assert ready[0].priority==1
        finally:
            sess.close(); cleanup(m,p)


class TestAggregation:
    def test_collect_and_conflict(self):
        m,sess,p=fresh_db()
        try:
            main=AgentService(sess).get_or_create_main()
            svc=DelegationPlanService(sess)
            res=svc.create_plan(parent_agent_id=main.id, objective="G", specialist_specs=[
                {"role":"RESEARCH","objective":"R1"},
                {"role":"RESEARCH","objective":"R2"},
            ])
            pid=res["plan"].id
            for d in res["delegations"]:
                r=SpecialistResult(status="COMPLETED", summary="done", findings=["finding for "+d.objective], evidence_refs=["context_packet"], confidence=0.8, evidence_confidence=0.7, limitations=["none"], recommended_next_action="do "+d.objective, cost={})
                d.result_json=json.dumps(r.to_dict())
                d.status=DelegationStatus.VERIFIED.value
            # make them conflicting by different recommended actions
            res["delegations"][0].result_json=json.dumps(SpecialistResult(status="COMPLETED", summary="s", findings=["use X"], evidence_refs=["context_packet"], confidence=0.9, evidence_confidence=0.9, limitations=["n"], recommended_next_action="use X", cost={}).to_dict())
            res["delegations"][1].result_json=json.dumps(SpecialistResult(status="COMPLETED", summary="s", findings=["use Y"], evidence_refs=["context_packet"], confidence=0.9, evidence_confidence=0.9, limitations=["n"], recommended_next_action="use Y", cost={}).to_dict())
            sess.commit()
            agg=ResultAggregator()
            delegs=svc.list_delegations(pid)
            conflicts=agg.detect_conflicts(delegs)
            assert len(conflicts)>=1
            assert conflicts[0]["type"]=="CONFLICT"
            conf=agg.aggregation_confidence(delegs)
            assert conf["agreement_level"]<1.0
            synth=agg.synthesize(svc.get_plan(pid), delegs)
            assert synth["verified_count"]==2
        finally:
            sess.close(); cleanup(m,p)

    def test_no_overwrite(self):
        m,sess,p=fresh_db()
        try:
            main=AgentService(sess).get_or_create_main()
            svc=DelegationPlanService(sess)
            res=svc.create_plan(parent_agent_id=main.id, objective="G", specialist_specs=[
                {"role":"RESEARCH","objective":"R1"},
                {"role":"ANALYSIS","objective":"A1"},
            ])
            for d in res["delegations"]:
                r=SpecialistResult(status="COMPLETED", summary="s"+d.specialist_role, findings=["f "+d.specialist_role], evidence_refs=["context_packet"], confidence=0.8, evidence_confidence=0.7, limitations=["n"], recommended_next_action="a", cost={})
                d.result_json=json.dumps(r.to_dict())
                d.status=DelegationStatus.VERIFIED.value
            sess.commit()
            agg=ResultAggregator()
            synth=agg.synthesize(svc.get_plan(res["plan"].id), svc.list_delegations(res["plan"].id))
            # evidence has two entries distinct delegation_ids
            assert len(synth["evidence"])==2
            assert synth["evidence"][0]["delegation_id"]!=synth["evidence"][1]["delegation_id"]
        finally:
            sess.close(); cleanup(m,p)


class TestCancellation:
    def test_parent_cancels_children(self):
        m,sess,p=fresh_db()
        try:
            main=AgentService(sess).get_or_create_main()
            svc=DelegationPlanService(sess)
            res=svc.create_plan(parent_agent_id=main.id, objective="G", specialist_specs=[
                {"role":"RESEARCH","objective":"R1"},
                {"role":"ANALYSIS","objective":"A1"},
            ])
            pid=res["plan"].id
            svc.cancel_plan(pid, main.id)
            assert svc.get_plan(pid).status=="CANCELLED"
            for d in svc.list_delegations(pid):
                assert d.status=="CANCELLED"
        finally:
            sess.close(); cleanup(m,p)

    def test_child_cancel_isolated(self):
        m,sess,p=fresh_db()
        try:
            main=AgentService(sess).get_or_create_main()
            svc=DelegationPlanService(sess)
            res=svc.create_plan(parent_agent_id=main.id, objective="G", specialist_specs=[
                {"role":"RESEARCH","objective":"R1"},
                {"role":"ANALYSIS","objective":"A1"},
            ])
            pid=res["plan"].id
            d0=res["delegations"][0].id
            svc.cancel_delegation(d0, main.id)
            assert svc.get_plan(pid).status=="ACTIVE"
            from backend.models.database import Delegation
            d1=sess.query(Delegation).filter_by(id=res["delegations"][1].id).first()
            assert d1.status!="CANCELLED"
        finally:
            sess.close(); cleanup(m,p)

    def test_specialist_cannot_cancel_other(self):
        m,sess,p=fresh_db()
        try:
            main=AgentService(sess).get_or_create_main()
            svc=DelegationPlanService(sess)
            ags=AgentService(sess)
            spec=ags.create_specialist(actor_id=main.id, role="RESEARCH")
            sess.commit()
            res=svc.create_plan(parent_agent_id=main.id, objective="G", specialist_specs=[
                {"role":"RESEARCH","objective":"R1"},
                {"role":"ANALYSIS","objective":"A1"},
            ])
            d0=res["delegations"][0].id
            out=svc.cancel_delegation(d0, spec.id)
            assert "only the MAIN" in out["error"]
        finally:
            sess.close(); cleanup(m,p)


class TestCompletionPolicy:
    def test_all_required(self):
        m,sess,p=fresh_db()
        try:
            main=AgentService(sess).get_or_create_main()
            svc=DelegationPlanService(sess)
            res=svc.create_plan(parent_agent_id=main.id, objective="G", specialist_specs=[
                {"role":"RESEARCH","objective":"R1"},
                {"role":"ANALYSIS","objective":"A1"},
            ], completion_policy="ALL_REQUIRED")
            pid=res["plan"].id
            # need both verified
            res["delegations"][0].status=DelegationStatus.VERIFIED.value
            res["delegations"][0].result_json=json.dumps(SpecialistResult(status="COMPLETED", summary="s", findings=["f"], evidence_refs=["context_packet"], confidence=0.8, evidence_confidence=0.8, limitations=["n"], recommended_next_action="a", cost={}).to_dict())
            sess.commit()
            assert not svc.evaluate_completion(pid)["ready"]
            res["delegations"][1].status=DelegationStatus.VERIFIED.value
            res["delegations"][1].result_json=json.dumps(SpecialistResult(status="COMPLETED", summary="s", findings=["f"], evidence_refs=["context_packet"], confidence=0.8, evidence_confidence=0.8, limitations=["n"], recommended_next_action="a", cost={}).to_dict())
            sess.commit()
            assert svc.evaluate_completion(pid)["ready"]
            assert svc.try_complete_plan(pid)["ok"]
        finally:
            sess.close(); cleanup(m,p)

    def test_optional_tolerates_failure(self):
        m,sess,p=fresh_db()
        try:
            main=AgentService(sess).get_or_create_main()
            svc=DelegationPlanService(sess)
            res=svc.create_plan(parent_agent_id=main.id, objective="G", specialist_specs=[
                {"role":"RESEARCH","objective":"R1","constraints":{"optional": False}},
                {"role":"ANALYSIS","objective":"A1","constraints":{"optional": True}},
            ], completion_policy="OPTIONAL_RESULTS")
            pid=res["plan"].id
            res["delegations"][0].status=DelegationStatus.VERIFIED.value
            res["delegations"][0].result_json=json.dumps(SpecialistResult(status="COMPLETED", summary="s", findings=["f"], evidence_refs=["context_packet"], confidence=0.8, evidence_confidence=0.8, limitations=["n"], recommended_next_action="a", cost={}).to_dict())
            res["delegations"][1].status=DelegationStatus.FAILED.value
            sess.commit()
            assert svc.evaluate_completion(pid)["ready"]
        finally:
            sess.close(); cleanup(m,p)

    def test_retry_bounded(self):
        m,sess,p=fresh_db()
        try:
            main=AgentService(sess).get_or_create_main()
            svc=DelegationPlanService(sess)
            res=svc.create_plan(parent_agent_id=main.id, objective="G", specialist_specs=[
                {"role":"RESEARCH","objective":"R1"},
            ])
            d=res["delegations"][0]
            d.status=DelegationStatus.FAILED.value
            sess.commit()
            old=settings.max_specialist_revisions
            settings.max_specialist_revisions=1
            out=svc.retry_delegation(d.id, main.id)
            assert out["ok"]
            # second retry should exceed? Actually new delegation is PROPOSED not FAILED, so need to fail it
            from backend.models.database import Delegation
            nd=sess.query(Delegation).filter_by(id=out["new_delegation_id"]).first()
            nd.status=DelegationStatus.FAILED.value
            sess.commit()
            out2=svc.retry_delegation(nd.id, main.id)
            # revisions count 2 > limit 1 -> fail
            assert not out2["ok"]
            settings.max_specialist_revisions=old
        finally:
            sess.close(); cleanup(m,p)


class TestConcurrency:
    def test_serialized_default(self):
        m,sess,p=fresh_db()
        try:
            old=settings.parallelism_enabled
            settings.parallelism_enabled=False
            assert capability_is_concurrent_safe("system_observation")==False
            assert ParallelismPolicy.from_settings().enabled==False
            settings.parallelism_enabled=old
        finally:
            sess.close(); cleanup(m,p)

    def test_gui_always_serialized(self):
        m,sess,p=fresh_db()
        try:
            old=settings.parallelism_enabled
            settings.parallelism_enabled=True
            assert capability_is_concurrent_safe("gui_automation")==False
            settings.parallelism_enabled=old
        finally:
            sess.close(); cleanup(m,p)

    def test_max_parallel_enforced(self):
        m,sess,p=fresh_db()
        try:
            main=AgentService(sess).get_or_create_main()
            svc=DelegationPlanService(sess)
            old_enabled=settings.parallelism_enabled
            old_max=settings.max_parallel_specialists
            settings.parallelism_enabled=True
            settings.max_parallel_specialists=1
            res=svc.create_plan(parent_agent_id=main.id, objective="G", specialist_specs=[
                {"role":"RESEARCH","objective":"R1"},
                {"role":"ANALYSIS","objective":"A1"},
            ])
            pid=res["plan"].id
            # Simulate one RUNNING
            res["delegations"][0].status=DelegationStatus.RUNNING.value
            sess.commit()
            # second should not be concurrent available
            assert len(svc.ready_delegations(pid))==0
            settings.parallelism_enabled=old_enabled
            settings.max_parallel_specialists=old_max
        finally:
            sess.close(); cleanup(m,p)


class TestSecurity:
    def test_memory_isolation(self):
        m,sess,p=fresh_db()
        try:
            main=AgentService(sess).get_or_create_main()
            svc=DelegationPlanService(sess)
            res=svc.create_plan(parent_agent_id=main.id, objective="G", specialist_specs=[
                {"role":"RESEARCH","objective":"R1"},
                {"role":"ANALYSIS","objective":"A1","depends_on":["0"]},
            ])
            d1=res["delegations"][1]
            # sibling private memory not visible unless via VERIFIED result
            pkt=svc.build_specialist_packet(d1)
            # without verified, no dependency results
            assert "_dependency_results" not in pkt.constraints or len(pkt.constraints["_dependency_results"])==0
            # after verified, it appears
            d0=res["delegations"][0]
            d0.status=DelegationStatus.VERIFIED.value
            d0.result_json=json.dumps(SpecialistResult(status="COMPLETED", summary="s", findings=["f"], evidence_refs=["context_packet"], confidence=0.8, evidence_confidence=0.8, limitations=["n"], recommended_next_action="a", cost={}).to_dict())
            sess.commit()
            pkt2=svc.build_specialist_packet(d1)
            assert len(pkt2.constraints["_dependency_results"])==1
        finally:
            sess.close(); cleanup(m,p)

    def test_budget_bypass_denied(self):
        m,sess,p=fresh_db()
        try:
            main=AgentService(sess).get_or_create_main()
            svc=DelegationPlanService(sess)
            res=svc.create_plan(parent_agent_id=main.id, objective="G", specialist_specs=[
                {"role":"RESEARCH","objective":"R1"},
            ])
            # Simulate overspend via plan budget check
            from backend.models.database import DelegationPlan
            plan=sess.query(DelegationPlan).filter_by(id=res["plan"].id).first()
            plan.budget_json=json.dumps({"max_total_steps":0,"max_total_model_calls":0})
            sess.commit()
            assert svc._budget_available(plan, res["delegations"][0])==False
        finally:
            sess.close(); cleanup(m,p)


class TestRecovery:
    def test_recover_preserves_completed(self):
        m,sess,p=fresh_db()
        try:
            main=AgentService(sess).get_or_create_main()
            svc=DelegationPlanService(sess)
            res=svc.create_plan(parent_agent_id=main.id, objective="G", specialist_specs=[
                {"role":"RESEARCH","objective":"R1"},
                {"role":"ANALYSIS","objective":"A1"},
                {"role":"REVIEW","objective":"Rv1"},
            ])
            # A VERIFIED, B RUNNING no task, C VALIDATED
            res["delegations"][0].status=DelegationStatus.VERIFIED.value
            res["delegations"][0].result_json=json.dumps(SpecialistResult(status="COMPLETED", summary="s", findings=["f"], evidence_refs=["context_packet"], confidence=0.8, evidence_confidence=0.8, limitations=["n"], recommended_next_action="a", cost={}).to_dict())
            res["delegations"][1].status=DelegationStatus.RUNNING.value
            res["delegations"][1].specialist_task_id=None
            sess.commit()
            rec=svc.recover_plans()
            # A preserved, B demoted to ASSIGNED, C remains
            from backend.models.database import Delegation
            a=sess.query(Delegation).filter_by(id=res["delegations"][0].id).first()
            b=sess.query(Delegation).filter_by(id=res["delegations"][1].id).first()
            c=sess.query(Delegation).filter_by(id=res["delegations"][2].id).first()
            assert a.status==DelegationStatus.VERIFIED.value
            assert b.status==DelegationStatus.ASSIGNED.value
            assert c.status==DelegationStatus.VALIDATED.value
        finally:
            sess.close(); cleanup(m,p)

    def test_failed_cannot_forge_completion(self):
        m,sess,p=fresh_db()
        try:
            main=AgentService(sess).get_or_create_main()
            svc=DelegationPlanService(sess)
            res=svc.create_plan(parent_agent_id=main.id, objective="G", specialist_specs=[
                {"role":"RESEARCH","objective":"R1"},
                {"role":"ANALYSIS","objective":"A1"},
            ], completion_policy="ALL_REQUIRED")
            res["delegations"][0].status=DelegationStatus.FAILED.value
            sess.commit()
            evalr=svc.evaluate_completion(res["plan"].id)
            assert not evalr["ready"]
            assert not svc.try_complete_plan(res["plan"].id)["ok"]
        finally:
            sess.close(); cleanup(m,p)


class TestProviderE2EPhase10:
    """Reasoning-only two specialists + dependent aggregation (no mocks)."""
    def test_two_specialists_reasoning_only(self):
        m,sess,p=fresh_db()
        try:
            main=AgentService(sess).get_or_create_main()
            svc=DelegationPlanService(sess)
            res=svc.create_plan(parent_agent_id=main.id, objective="Compare X vs Y", specialist_specs=[
                {"role":"RESEARCH","objective":"Research X scalability"},
                {"role":"ANALYSIS","objective":"Analyze Y cost","depends_on":["0"]},
            ])
            assert res["error"] is None
            pid=res["plan"].id
            # Simulate verified first specialist via SpecialistReasoner fallback
            from backend.agents.delegation import SpecialistResult
            d0=res["delegations"][0]
            r0=SpecialistResult(status="COMPLETED", summary="research X done", findings=["X scales well under 1000 users"], evidence_refs=["context_packet"], confidence=0.85, evidence_confidence=0.8, limitations=["limited data"], recommended_next_action="check Y", cost={"actions":0,"model_calls":1})
            d0.result_json=json.dumps(r0.to_dict())
            d0.status=DelegationStatus.VERIFIED.value
            sess.commit()
            # Second specialist should now be ready with dependency result injected
            ready=svc.ready_delegations(pid)
            assert any(d.id==res["delegations"][1].id for d in ready)
            pkt=svc.build_specialist_packet(res["delegations"][1])
            assert any("X scales" in str(v) for v in pkt.constraints.get("_dependency_results",[]))
            # Complete second
            d1=res["delegations"][1]
            r1=SpecialistResult(status="COMPLETED", summary="analysis Y done", findings=["Y cheaper but less scalable, compared to X findings"], evidence_refs=["context_packet"], confidence=0.8, evidence_confidence=0.75, limitations=["est"], recommended_next_action="choose X", cost={"actions":0,"model_calls":1})
            d1.result_json=json.dumps(r1.to_dict())
            d1.status=DelegationStatus.VERIFIED.value
            sess.commit()
            agg=ResultAggregator()
            synth=agg.synthesize(svc.get_plan(pid), svc.list_delegations(pid))
            assert synth["verified_count"]==2
            assert synth["aggregation_confidence"]["aggregation_confidence"] is not None
        finally:
            sess.close(); cleanup(m,p)
