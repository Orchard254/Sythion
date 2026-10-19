"""Phase 9.75 — deterministic provider FAILURE tests (mocked, no network).

Companion to the gated real-provider E2E suite: the same SpecialistReasoner
boundaries are proven deterministically for

    - unavailable provider
    - malformed model output (after bounded repair)
    - timeout
    - budget exhaustion (model-call and action budgets)

These run in EVERY normal test invocation — no gate, no cost.
"""

import asyncio

import pytest

from backend.agents.delegation import DelegationRequest, DelegationService
from backend.agents.reasoner import SpecialistReasoner
from backend.agents.specialist import SpecialistContextPacket


class _BrokenProvider:
    def __init__(self, behavior):
        self._behavior = behavior
        self.calls = 0

    def provider_id(self):
        return "broken"

    async def chat(self, messages, max_tokens=4000, timeout=30,
                   model_id=None):
        self.calls += 1
        if callable(self._behavior):
            return self._behavior()
        raise self._behavior


def _packet(budget=None):
    return SpecialistContextPacket(
        objective="Compare A and B honestly.",
        specialist_agent_id="s-fail", role="ANALYSIS",
        required_capabilities=["system_observation"],
        budget=budget or {"steps": 3, "time_seconds": 300, "model_calls": 5},
    )


@pytest.fixture
def env(test_db):
    from backend.agents.service import AgentService

    svc = AgentService(test_db)
    svc.ensure_definitions()
    main = svc.get_or_create_main()
    return test_db, svc, main


class TestProviderFailureDeterminism:

    def test_unavailable_provider_falls_back_honestly(self, env,
                                                      monkeypatch):
        db, _svc, _main = env
        import backend.runtime.model_provider as mp

        def _boom():
            raise ConnectionError("provider unreachable")

        monkeypatch.setattr(mp, "get_default_provider",
                            lambda: (_boom() if False else _BrokenProvider(
                                ConnectionError("provider unreachable"))))
        reasoner = SpecialistReasoner(db, _packet(),
                                      allowed_tools=["system_time"])
        out = asyncio.run(reasoner.analyze())
        assert out["fallback"] is True
        assert out["findings"] == []
        assert any("fallback" in l for l in out["limitations"])

    def test_malformed_output_exhausts_repair_then_falls_back(self, env,
                                                              monkeypatch):
        db, _svc, _main = env
        broken = _BrokenProvider(lambda: {"message": {"content":
                                          "not json at all {"}})
        reasoner = SpecialistReasoner(db, _packet(), allowed_tools=[],
                                      model_provider=broken)
        out = asyncio.run(reasoner.analyze())
        assert out["fallback"] is True
        # One bounded repair attempt happened (2 calls total), not a loop.
        assert broken.calls == 2
        assert any("malformed" in l for l in out["limitations"])

    def test_timeout_maps_to_fallback_not_crash(self, env, monkeypatch):
        db, _svc, _main = env
        reasoner = SpecialistReasoner(
            db, _packet(), allowed_tools=[],
            model_provider=_BrokenProvider(TimeoutError("model timed out")))
        out = asyncio.run(reasoner.analyze())
        assert out["fallback"] is True
        assert out["confidence"] is None

    def test_model_call_budget_is_enforced(self, env):
        db, _svc, _main = env
        from backend.agents.state import BudgetExceeded

        reasoner = SpecialistReasoner(
            db, _packet(), allowed_tools=[],
            budget={"steps": 3, "time_seconds": 300, "model_calls": 1},
            model_provider=_BrokenProvider(
                lambda: {"message": {"content":
                                     '{"findings":["x"],"confidence":0.5}'}}))
        first = asyncio.run(reasoner.analyze())
        assert first["fallback"] is False
        with pytest.raises(BudgetExceeded):
            asyncio.run(reasoner.analyze())

    def test_runtime_action_budget_is_enforced(self, env, tool_executor):
        db, _svc, _main = env
        from backend.agents.state import BudgetExceeded

        runtime = _make_runtime(db, tool_executor, steps_used=None)
        # Exhaust the step budget; the next request must be refused.
        for _ in range(runtime_max_steps()):
            asyncio.run(runtime.request_action("system_time", {}))
        with pytest.raises(BudgetExceeded):
            asyncio.run(runtime.request_action("system_time", {}))


def runtime_max_steps():
    from backend.core.config import settings
    return max(1, int(settings.max_specialist_steps))


def _make_runtime(db, executor, steps_used=None):
    from backend.agents.specialist import SpecialistRuntime

    rt = SpecialistRuntime(db=db, agent_id="s-budget", delegation_id="d-budget",
                           allowed_tools=["system_time"],
                           task_executor=executor, task_id="t-budget")
    if steps_used is not None:
        rt.actions_used = steps_used
    return rt


class TestDelegationBudgetAccounting:

    def test_specialist_failure_never_becomes_success(self, env):
        """A delegation whose specialist cannot run ends FAILED with a
        structured code — never COMPLETED."""
        db, svc_a, main = env
        ds = DelegationService(db)
        proposal = ds.propose(DelegationRequest(
            parent_agent_id=main.id, specialist_role="ANALYSIS",
            objective="structured failure accounting probe"))
        assert proposal["error"] is None
        d = proposal["delegation"]
        bad = ds.propose(DelegationRequest(
            parent_agent_id=main.id, specialist_role="ANALYSIS",
            objective="over budget probe",
            constraints={"required_capabilities": ["ghost_capability"]}))
        validation = ds.validate(bad["delegation"].id)
        assert validation["ok"] is False
        fresh = ds.get_delegation(bad["delegation"].id)
        db.refresh(fresh)
        assert fresh.status == "FAILED"
