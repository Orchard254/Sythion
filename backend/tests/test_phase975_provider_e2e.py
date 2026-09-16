"""Phase 9.75 — REAL provider-backed specialist end-to-end tests.

These tests exercise the FULL production path with a real configured model
provider — no mocks, no adapter shortcuts:

    Main Agent decision (DELEGATE)
      → DelegationService (atomic admission)
      → SpecialistContextPacket
      → real ModelSelector → real ModelProvider → SpecialistReasoner
      → structured SpecialistResult
      → evidence verification
      → Main Agent integration

Gating (mirrors the process_kill convention):

    set SYTHION_RUN_PROVIDER_E2E=1          # explicit opt-in
    set SYTHION_OLLAMA_API_KEY=...          # or Credential Manager secret
    set SYTHION_SELECTED_MODEL=...          # or onboarded selection

    python -m pytest backend/tests/test_phase975_provider_e2e.py -q -m provider_e2e

Without the gate the suite SKIPS — normal CI never incurs provider cost and
never needs credentials. The tests are reasoning-only plus a harmless fixture
tool; they never touch files, credentials, OS state, or destructive tools.
"""

import asyncio
import json
import os
import sys

import pytest

PROJECT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT not in sys.path:
    sys.path.insert(0, PROJECT)

from backend.core.secrets import resolve_ollama_api_key  # noqa: E402


def _provider_ready() -> bool:
    """A real provider is only usable when a key AND a model resolve."""
    from backend.core.config import settings, resolve_selected_model

    try:
        if not resolve_ollama_api_key(settings.ollama_api_key):
            return False
        return bool(resolve_selected_model())
    except Exception:
        return False


pytestmark = [
    pytest.mark.provider_e2e,
    pytest.mark.skipif(
        os.environ.get("SYTHION_RUN_PROVIDER_E2E") != "1",
        reason="set SYTHION_RUN_PROVIDER_E2E=1 to run provider-backed E2E tests",
    ),
    pytest.mark.skipif(not _provider_ready(),
                       reason="no provider key/model configured"),
]

ANALYSIS_OBJECTIVE = (
    "Which of these three approaches is best for our rollout when we "
    "analyze scalability, cost, and maintainability trade-offs? "
    "(A) single shared VM, (B) two containers behind a load balancer, "
    "(C) serverless functions. Compare them thoroughly and recommend one "
    "with evidence."
)


@pytest.fixture(autouse=True)
def _fresh_provider_http_client(monkeypatch):
    """Each ``asyncio.run`` in a test process is a NEW event loop, while the
    provider adapter caches one httpx.AsyncClient bound to whichever loop
    created it (production runs a single long-lived loop, so this never
    triggers there). Reset the cached client per request so the REAL provider
    is exercised honestly across loop boundaries. The provider itself is NOT
    faked or bypassed."""
    try:
        from backend.adapters.ollama_adapter import get_adapter
    except Exception:
        yield
        return
    adapter = get_adapter()
    adapter._client = None
    orig_get_client = adapter._get_client

    async def fresh_client():
        adapter._client = None
        return await orig_get_client()

    monkeypatch.setattr(adapter, "_get_client", fresh_client)
    yield

CODING_OBJECTIVE = (
    "Propose ONE action using capability 'fixture_observation' and tool "
    '"fixture_observation" with input {"probe": "alpha"}. Then report what '
    "the fixture returned as your primary finding."
)


class FixtureObservationTool:
    """Harmless deterministic fixture: isolated from all real user data."""

    name = "fixture_observation"
    description = ("Deterministic observation fixture returning fixed probe "
                   "data; no filesystem, network, or OS access.")

    from pydantic import BaseModel as _BM

    class input_schema(_BM):  # noqa: N801 - executor input contract
        probe: str = "alpha"

    CALLS = []

    def validate_targets(self, input_data):
        return None

    async def execute(self, input_data):
        FixtureObservationTool.CALLS.append({"probe": input_data.probe})
        from backend.api.schemas import ToolOutput

        return ToolOutput(
            success=True,
            data={"probe": input_data.probe,
                  "reading": f"fixture-ok:{input_data.probe}"},
            model_summary=f"Fixture observed probe {input_data.probe}.",
        )

    async def verify(self, input_data, output):
        return bool(output.success)

    def get_policy_requirements(self):
        return {"risk_level": "none", "requires_confirmation": False,
                "read_only": True}


def _executor_with_fixture():
    from backend.tools.executor import ToolExecutor
    from backend.tools.registry import create_all_tools

    ex = ToolExecutor()
    for tool in create_all_tools():
        ex.register_tool(tool)
    ex.register_tool(FixtureObservationTool())
    return ex


@pytest.fixture
def seeded(test_db):
    from backend.agents.service import AgentService

    svc = AgentService(test_db)
    svc.ensure_definitions()
    main = svc.get_or_create_main()
    return test_db, main


def _ledger_events(db, scope):
    from backend.runtime.events import DbEventLedger

    return DbEventLedger(db).event_history(scope)


class TestProviderReasoningOnlyE2E:

    def test_analysis_specialist_full_path_with_real_model(self, seeded):
        db, main = seeded
        from backend.core.config import settings
        from backend.agents.delegation import (
            DelegationRequest, DelegationService, SpecialistResult)

        # Main Agent DELEGATE decision comes from the real cognitive layer.
        from backend.cognitive.pipeline import CognitivePipeline
        from backend.cognitive.state import DecisionType

        decision = CognitivePipeline(db).process(ANALYSIS_OBJECTIVE)
        assert decision.decision.decision_type == DecisionType.DELEGATE

        ds = DelegationService(db)
        proposal = ds.propose(DelegationRequest(
            parent_agent_id=main.id,
            specialist_role="ANALYSIS",
            objective=ANALYSIS_OBJECTIVE,
            expected_output="structured comparison + one recommendation",
        ))
        assert proposal["error"] is None, proposal
        d = proposal["delegation"]
        assert ds.validate(d.id)["ok"]           # atomic admission boundary
        assert ds.assign(d.id)["specialist"]     # bounded context packet

        outcome = asyncio.run(ds.run(
            d.id, state_store=_StateStoreStub(db),
            executor=_executor_with_fixture()))
        assert outcome["started"] is True
        assert outcome["verified"] is True, outcome.get("verification_errors")

        result = SpecialistResult.from_dict(outcome["result"])
        # --- genuine specialist work, not HTTP-200/JSON validity ---
        assert result.status == "COMPLETED"
        assert len(result.summary) >= 40
        substantive = [f for f in result.findings if len(f) >= 15 and " " in f]
        assert substantive, result.findings
        assert any(
            kw in " ".join(substantive).lower()
            for kw in ("option", "vm", "container", "serverless", "cost",
                       "scal", "complex"))
        assert result.limitations, "specialist must state limitations"
        assert result.confidence is not None and \
            0.0 <= float(result.confidence) <= 1.0
        assert result.evidence_refs
        assert not any("deterministic fallback" in l
                       for l in result.limitations), (
            "fallback artifact leaked into a REAL provider run")

        # --- real specialist.reasoning ledger event exists ---
        events = _ledger_events(db, outcome["specialist_task_id"])
        reasoning = [e for e in events
                     if e["event_type"] == "specialist.reasoning"]
        assert reasoning and \
            reasoning[0]["event_data"].get("fallback") is not True

        # --- budget integrity: model calls counted against specialist budget ---
        cost = outcome["cost"]
        assert cost["model_calls"] >= 1
        assert cost["model_calls"] <= int(settings.max_specialist_model_calls)
        spent = json.loads(d.budget_json or "{}").get("spent") or {}
        assert spent.get("model_calls", 0) >= 1

        # --- Main Agent integration of the VERIFIED result ---
        integrated = ds.integrate(d.id, "ACCEPT", actor_id=main.id)
        assert integrated["outcome"] == "ACCEPT"
        fresh = ds.get_delegation(d.id)
        assert fresh.status == "INTEGRATED"


class TestProviderActionProducingE2E:

    def test_coding_specialist_proposes_and_executes_fixture_tool(
            self, seeded, monkeypatch):
        db, main = seeded

        # Fixture capability joins CODING's binding for THIS test only;
        # production taxonomy stays untouched.
        import backend.services.capabilities as cap_mod
        from backend.agents.state import ROLE_CAPABILITIES, SpecialistRole
        from backend.services.capabilities import CapabilitySpec

        orig_specs = cap_mod._specs
        orig_role_caps = ROLE_CAPABILITIES[SpecialistRole.CODING]
        fixture_spec = CapabilitySpec(
            id="fixture_observation", name="Fixture Observation",
            category="fixture",
            description="Deterministic harmless fixture.",
            tool_names=("fixture_observation",),
        )

        def patched_specs():
            return orig_specs() + [fixture_spec]

        monkeypatch.setattr(cap_mod, "_specs", patched_specs)
        monkeypatch.setitem(ROLE_CAPABILITIES, SpecialistRole.CODING,
                            orig_role_caps + ("fixture_observation",))

        from backend.agents.delegation import (
            DelegationRequest, DelegationService, SpecialistResult)
        from backend.agents.service import AgentService

        # The CODING definition was already seeded by the fixture; grant it
        # the fixture capability explicitly (roles are never silently
        # re-seeded).
        svc_agents = AgentService(db)
        coding_def = next(d for d in svc_agents.list_definitions()
                          if d.role == "CODING")
        assert "fixture_observation" not in coding_def.capabilities_json
        coding_def.capabilities_json = json.dumps(
            ["file_operations", "fixture_observation"])
        db.commit()

        ds = DelegationService(db)
        proposal = ds.propose(DelegationRequest(
            parent_agent_id=main.id,
            specialist_role="CODING",
            objective=CODING_OBJECTIVE,
            expected_output="one fixture_observation action, then findings",
            constraints={"required_capabilities": ["fixture_observation"]},
        ))
        assert proposal["error"] is None, proposal
        d = proposal["delegation"]
        assert ds.validate(d.id)["ok"]
        assert ds.assign(d.id)["specialist"]

        FixtureObservationTool.CALLS.clear()
        outcome = asyncio.run(ds.run(
            d.id, state_store=_StateStoreStub(db),
            executor=_executor_with_fixture()))

        assert outcome["started"] is True
        # The REAL model's proposal traveled the full action path:
        # proposal → capability validation → SpecialistRuntime →
        # ToolExecutor → fixture tool.
        assert FixtureObservationTool.CALLS, (
            f"fixture tool never executed; outcome={outcome}")

        result = SpecialistResult.from_dict(outcome["result"])
        assert result.status == "COMPLETED"
        assert any("fixture" in f.lower() for f in result.findings), \
            result.findings
        assert any(r.startswith("action:") for r in result.evidence_refs), \
            result.evidence_refs
        assert outcome["verified"] is True, outcome.get("verification_errors")

        # Evidence references REAL runtime events (nothing fabricated).
        events = _ledger_events(db, outcome["specialist_task_id"])
        types = [e["event_type"] for e in events]
        assert "specialist.reasoning" in types
        actions = [e for e in events if e["event_type"] == "specialist.action"]
        assert actions and \
            actions[0]["event_data"]["tool"] == "fixture_observation"
        seqs = {e["event_data"]["seq"] for e in actions}
        for ref in result.evidence_refs:
            if ref.startswith("action:"):
                assert int(ref.split(":", 1)[1]) in seqs

        cost = outcome["cost"]
        assert cost["actions"] >= 1 and cost["model_calls"] >= 1

        integrated = ds.integrate(d.id, "ACCEPT", actor_id=main.id)
        assert integrated["outcome"] == "ACCEPT"


class _StateStoreStub:
    """Minimal lease/task store over the SAME DB so DelegationService.run
    can execute without the full TaskRunner machinery. The lease is local to
    this single-process E2E test; admission/execution semantics are unchanged.
    """

    def __init__(self, db):
        self.db = db

    async def acquire_lease(self):
        return True

    async def release_lease(self):
        return True

    async def create_task(self, original_request, idempotency_key=None):
        import uuid

        from backend.models.database import Task

        task = Task(id=str(uuid.uuid4()),
                    original_request=original_request,
                    idempotency_key=idempotency_key,
                    state="created")
        self.db.add(task)
        self.db.commit()
        return task

    def set_task_objective(self, task_id, goal_id):
        return None

    async def transition_state(self, task_id, new_state, event_type,
                               event_data=None):
        from backend.runtime.events import DbEventLedger

        DbEventLedger(self.db).append_event(task_id, event_type,
                                            dict(event_data or {}))
        self.db.commit()
        return True
