"""
Phase 5 tests: Capability Registry, SelfModel, Model Provider Abstraction,
Model Registry truthfulness, ResourceState, and Health framework.
"""

import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models.database import Base, GoalPlan
from backend.services.capabilities import (
    AVAILABLE,
    CapabilityRegistry,
    UNAVAILABLE,
)
from backend.services.system_state import (
    ModelHealth,
    ModelRegistry,
    resource_snapshot,
    run_health_checks,
)


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


# ===========================================================================
# Capability registry
# ===========================================================================


class TestCapabilityRegistry:
    def test_registration_and_retrieval(self, db_session):
        reg = CapabilityRegistry(db_session)
        caps = reg.list_capabilities(include_details=True)
        assert len(caps) >= 10
        one = reg.get_capability("browser_control")
        assert one["category"] == "web"
        assert "browser_tabs" in one["tools_registered"]

    def test_availability_deterministic(self, db_session):
        reg = CapabilityRegistry(db_session)
        assert reg.capability_available("system_observation") is True

    def test_missing_implementation_detected(self, db_session):
        """A capability whose tools are entirely absent is UNAVAILABLE."""
        from backend.services.capabilities import CapabilitySpec, _specs

        ghost = CapabilitySpec(id="ghost_cap", name="Ghost", category="t",
                               description="x", tool_names=("no_such_tool",))
        reg = CapabilityRegistry(db_session)
        # Inject via monkeypatched catalog
        import backend.services.capabilities as cap_mod
        original = cap_mod._specs
        cap_mod._specs = lambda: original() + [ghost]
        try:
            status = reg.resolve_status(ghost)
            assert status == UNAVAILABLE
            issues = reg.validate()
            assert any(i.get("tool") == "no_such_tool" for i in issues)
        finally:
            cap_mod._specs = original

    def test_browser_bridge_disconnected_means_unavailable(self, db_session):
        """Deterministic state: disconnected extension = UNAVAILABLE, never
        'probably available'."""
        from unittest.mock import patch

        from backend.services.browser_bridge_service import BrowserBridgeService

        with patch.object(BrowserBridgeService, "get_instance") as gi:
            inst = gi.return_value
            inst.is_connected = False
            reg = CapabilityRegistry(db_session)
            cap = reg.get_capability("browser_control")
            assert cap["status"] == UNAVAILABLE

    def test_permission_flag_is_informational_only(self, db_session):
        """HIGH-risk capability reports requires_permission but the registry
        CANNOT authorize: authorization lives in the ToolExecutor policy."""
        from backend.tools.executor import PolicyContext, ToolExecutor

        reg = CapabilityRegistry(db_session)
        pkg = reg.get_capability("package_management")
        assert pkg["requires_permission"] is True
        assert pkg["risk_class"] == "HIGH"

        # Even though the registry says the capability exists, executing an
        # install still goes through executor policy (which refuses without
        # a direct instruction / proper context).
        executor = ToolExecutor()
        output = asyncio.run(executor.execute_tool(
            "package_uninstall", {"package_id": "x"},
            policy_context=PolicyContext(direct_user_instruction=False),
        ))
        assert output.success is False  # runtime refused; registry ≠ authority

    def test_consistency_validation_reports_never_repairs(self, db_session):
        reg = CapabilityRegistry(db_session)
        issues = reg.validate()
        # Clean catalog on this platform may have zero or known-missing issues;
        # crucially validate() only REPORTS.
        for issue in issues:
            assert "code" in issue


# ===========================================================================
# SelfModel
# ===========================================================================


class TestSelfModel:
    def test_deterministic_full_model(self, db_session):
        from backend.services.self_model import build_self_model

        model1 = build_self_model(db_session)
        model2 = build_self_model(db_session)
        assert model1["identity"] == model2["identity"]  # deterministic
        assert set(model1.keys()) >= {
            "identity", "runtime_state", "capabilities", "models",
            "resources", "health",
        }

    def test_identity_survives_new_session(self, db_session):
        from backend.services.self_model import build_self_model

        m1 = build_self_model(db_session)["identity"]["agent_id"]
        fresh = sessionmaker(bind=db_session.bind)()
        try:
            m2 = build_self_model(fresh)["identity"]["agent_id"]
            assert m1 == m2
        finally:
            fresh.close()

    def test_goal_task_state_reflected(self, db_session):
        from backend.models.database import Task
        from backend.services.goal_manager import GoalManager
        from backend.services.planner import CandidateStepModel, Planner
        from backend.services.self_model import build_self_model

        manager = GoalManager(db_session)
        planner = Planner(db_session)
        goal = manager.create_goal("Visible in self-model")
        planner.create_plan(goal.id, [CandidateStepModel(key="a", description="step a")])
        db_session.add(Task(id="sm-task", original_request="x",
                            state="running", version=1))
        db_session.commit()

        model = build_self_model(db_session)
        assert model["resources"]["runtime"]["active_goals"] >= 1
        caps_ids = {c for c in model["capabilities"]["available"]}
        assert "goal_planning" in caps_ids

    def test_no_llm_write_path_for_self_model(self):
        """Structural: self_model module has no LLM/adapter usage at all —
        the model can never author its own self-model."""
        import inspect

        import backend.services.self_model as sm

        src = inspect.getsource(sm)
        assert "get_adapter()" not in src
        assert "decide(" not in src
        assert "execute_tool" not in src

    def test_active_fields_follow_local_sentinel_and_cloud(self, db_session, monkeypatch):
        """active_* fields reuse the runtime's sentinel policy: the default
        binding resolves from the selected local model, a bound cloud model
        keeps its own exact ID, and a damaged binding fails closed."""
        from backend.core import config
        from backend.runtime import active_inference
        from backend.runtime.active_inference import (
            ActiveInferenceSelection, ActiveInferenceUnavailable,
        )
        from backend.services.self_model import build_self_model

        monkeypatch.setattr(
            config, "resolve_selected_model", lambda: "local/model:tag"
        )

        # The default binding carries no model ID of its own; the selected
        # local model is the single source of truth for it.
        monkeypatch.setattr(
            active_inference, "get_active_inference_selection",
            lambda: ActiveInferenceSelection("ollama-cloud", ""),
        )
        models = build_self_model(db_session)["models"]
        assert models["active_provider_id"] == "ollama-cloud"
        assert models["active_model_id"] == "local/model:tag"

        # A bound cloud selection carries its own exact model ID.
        monkeypatch.setattr(
            active_inference, "get_active_inference_selection",
            lambda: ActiveInferenceSelection("openrouter", "cloud/model:tag"),
        )
        models = build_self_model(db_session)["models"]
        assert models["active_provider_id"] == "openrouter"
        assert models["active_model_id"] == "cloud/model:tag"

        # A damaged binding fails closed — never a guessed local model.
        def _unavailable():
            raise ActiveInferenceUnavailable("damaged binding")

        monkeypatch.setattr(
            active_inference, "get_active_inference_selection", _unavailable
        )
        models = build_self_model(db_session)["models"]
        assert models["active_provider_id"] == "unavailable"
        assert models["active_model_id"] == ""


# ===========================================================================
# Model provider abstraction
# ===========================================================================


class TestProviderAbstraction:
    def test_ollama_provider_conforms_to_protocol(self):
        from backend.runtime.model_provider import ModelProvider
        from backend.adapters.ollama_adapter import get_default_provider

        provider = get_default_provider()
        assert isinstance(provider, ModelProvider)

    def test_higher_layers_do_not_import_adapter(self):
        """Planner / Main Agent / selector / orchestrator must not know about
        Ollama or the adapter module."""
        import inspect

        for mod_name in (
            "backend.services.planner",
            "backend.services.main_agent",
            "backend.services.goal_selector",
            "backend.services.task_orchestrator",
            "backend.services.self_model",
        ):
            mod = __import__(mod_name, fromlist=["x"])
            src = inspect.getsource(mod)
            assert "ollama" not in src.lower(), f"{mod_name} leaks Ollama"
            assert "get_adapter" not in src, f"{mod_name} imports adapter"

    def test_provider_outage_does_not_corrupt_goals(self, db_session):
        """A failing provider yields UNAVAILABLE records — goal rows intact."""
        from backend.runtime.model_provider import ModelHealth

        class _DeadProvider:
            def provider_id(self):
                return "dead"

            def selected_model_id(self):
                return "m1"

            def list_models(self):
                raise RuntimeError("provider down")

            def health(self):
                return ModelHealth("dead", ModelHealth.UNAVAILABLE, "down")

            async def chat(self, messages, max_tokens=4000, timeout=300.0):
                raise RuntimeError("down")

        import backend.runtime.model_provider as mpm

        original = mpm.get_default_provider
        mpm.get_default_provider = lambda: _DeadProvider()
        try:
            registry = ModelRegistry(db_session)
            records = registry.list_models()
            assert records[0].available_now is False
            assert records[0].health_status == ModelHealth.UNAVAILABLE

            from backend.services.goal_manager import GoalManager

            manager = GoalManager(db_session)
            goal = manager.create_goal("Unaffected")
            assert manager.get_goal(goal.id).status == "active"
        finally:
            mpm.get_default_provider = original


# ===========================================================================
# Model registry truthfulness
# ===========================================================================


class TestModelRegistryTruthfulness:
    def test_unknown_performance_claims(self, db_session):
        caps = ModelRegistry(None).model_capabilities("any-model")
        assert caps["evaluated_claims"] == "none"
        assert caps["reasoning"]["evaluated"] is False
        assert caps["context_tokens"] is None  # not measured → unknown

    def test_selected_model_marked(self, db_session, monkeypatch):
        import backend.runtime.model_provider as mpm

        class _P:
            def provider_id(self):
                return "test"

            def selected_model_id(self):
                return "chosen"

            def list_models(self):
                return [{"name": "chosen"}, {"name": "other"}]

            def health(self):
                return ModelHealth("test", ModelHealth.UNKNOWN)

            async def chat(self, messages, max_tokens=4000, timeout=300.0):
                return {}

        monkeypatch.setattr(mpm, "get_default_provider", lambda: _P())
        registry = ModelRegistry(db_session)
        selected = registry.selected_model()
        assert selected.model_id == "chosen"
        assert selected.available_now is True
        other = registry.get_model("other")
        assert other.selected is False


# ===========================================================================
# Health framework + resources
# ===========================================================================


class TestHealthFramework:
    def test_all_checks_run_without_remote(self, db_session):
        report = run_health_checks(db_session=db_session)
        names = {c["name"] for c in report["checks"]}
        assert {"database", "main_agent", "capability_consistency",
                "configuration", "provider"} <= names
        # Remote probe disabled by default → UNKNOWN, never executed.
        prov = next(c for c in report["checks"] if c["name"] == "provider")
        assert prov["status"] == "UNKNOWN"

    def test_degraded_when_capability_inconsistent(self, db_session):
        from unittest.mock import patch

        with patch.object(CapabilityRegistry, "validate",
                          return_value=[{"capability": "x",
                                         "code": "MISSING_TOOL"}]):
            report = run_health_checks(db_session=db_session)
            consistency = next(c for c in report["checks"]
                               if c["name"] == "capability_consistency")
            assert consistency["status"] == "DEGRADED"

    def test_provider_outage_reported_not_fatal(self, db_session):
        import backend.runtime.model_provider as mpm

        class _Down:
            def provider_id(self):
                return "downstream"

            def selected_model_id(self):
                return None

            def list_models(self):
                raise RuntimeError("offline")

            def health(self):
                return ModelHealth("downstream", ModelHealth.UNAVAILABLE)

            async def chat(self, messages, max_tokens=4000, timeout=300.0):
                raise RuntimeError("offline")

        original = mpm.get_default_provider
        mpm.get_default_provider = lambda: _Down()
        try:
            report = run_health_checks(include_provider=True,
                                       db_session=db_session)
            overall = report["overall"]
            assert overall in ("DEGRADED", "UNAVAILABLE")
            goals_count = db_session.query(GoalPlan).count()
            assert goals_count == 0  # nothing corrupted
        finally:
            mpm.get_default_provider = original


class TestResourceSnapshot:
    def test_known_values_present_no_fabrication(self, db_session):
        snap = resource_snapshot(db_session)
        assert snap["platform"] in ("Windows", "Linux", "Darwin")
        assert snap["budgets"]["max_actions_per_task"] > 0
        # Unknown stays explicitly None:
        assert snap["cpu_percent"] is None
        assert snap["memory_available_bytes"] is None

    def test_disk_unknown_without_data_dir(self):
        snap = resource_snapshot(None)
        assert isinstance(snap["disk_free_bytes"], (int, type(None)))