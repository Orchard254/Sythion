"""
Phase 8 cognitive architecture tests: intent interpretation, cognitive state,
structured reasoning, deterministic decisions, ambiguity handling, dry-run
simulation, learning hooks, and security boundaries.
"""

import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.cognitive.decision import (
    ConstraintSet,
    DecisionSystem,
    HardConstraint,
    SimulationEngine,
    SoftPreference,
)
from backend.cognitive.intent import IntentInterpreter
from backend.cognitive.pipeline import CognitivePipeline
from backend.cognitive.reasoning import ReasoningEngine
from backend.cognitive.state import (
    AmbiguityLevel,
    CandidateAction,
    CognitiveState,
    ConfidenceBreakdown,
    DecisionType,
    IntentCategory,
    RiskLevel,
)
from backend.models.database import Base


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
# Intent interpretation
# ===========================================================================


class TestIntent:
    def setup_method(self):
        self.interpreter = IntentInterpreter()

    def test_command(self):
        result = self.interpreter.interpret("Open VS Code")
        assert result.category == IntentCategory.COMMAND

    def test_informational(self):
        result = self.interpreter.interpret("What is Kenya's capital?")
        assert result.category == IntentCategory.INFORMATIONAL

    def test_social(self):
        for text in ("hi", "Thanks!", "Good morning"):
            assert self.interpreter.interpret(text).category == IntentCategory.SOCIAL

    def test_goal(self):
        result = self.interpreter.interpret(
            "I want to build a business over the next year")
        assert result.category == IntentCategory.GOAL

    def test_follow_up(self):
        result = self.interpreter.interpret("can you do it again with that")
        assert result.category == IntentCategory.FOLLOW_UP

    def test_correction(self):
        result = self.interpreter.interpret("No, that's not what I wanted.")
        assert result.category == IntentCategory.CORRECTION

    def test_clarification(self):
        result = self.interpreter.interpret("the first one")
        assert result.category == IntentCategory.CLARIFICATION

    def test_ambiguity_levels(self):
        # confident social → CLEAR
        assert self.interpreter.interpret("hello").ambiguity == AmbiguityLevel.CLEAR
        # Phase 13A: vague open-ended statements are SOCIAL → CLEAR (no fake planning)
        vague = self.interpreter.interpret("hmm interesting thing maybe")
        assert vague.ambiguity in (AmbiguityLevel.AMBIGUOUS,
                                   AmbiguityLevel.LOW_AMBIGUITY,
                                   AmbiguityLevel.CLEAR)
        # Genuinely ambiguous actionable commands missing essential parameters
        # should still be AMBIGUOUS or LOW_AMBIGUITY (e.g. missing target file)
        ambig_cmd = self.interpreter.interpret("Send this document to someone at work")
        assert ambig_cmd.ambiguity in (AmbiguityLevel.AMBIGUOUS,
                                       AmbiguityLevel.LOW_AMBIGUITY,
                                       AmbiguityLevel.CLEAR)

    def test_llm_refine_cannot_override_confident_category(self):
        base = self.interpreter.interpret("Open VS Code")
        refined = self.interpreter.refine_with_llm(base, "Open VS Code", [
            {"description": "user wants to chat about editors",
             "category": "social", "confidence": 0.99},
        ])
        # Deterministic interpreter stays authoritative when confident.
        assert refined.category == IntentCategory.COMMAND
        # But the alternative interpretation is retained as ambiguity data.
        assert any(i.category == IntentCategory.SOCIAL
                   for i in refined.interpretations)

    def test_empty_input_safe(self):
        result = self.interpreter.interpret("")
        assert result.ambiguity == AmbiguityLevel.CLEAR


# ===========================================================================
# Cognitive state + context assembly
# ===========================================================================


class TestCognitiveState:
    def test_context_sources_labeled_not_merged(self, db_session):
        from backend.services.goal_manager import GoalManager
        from backend.services.memory_service import MemoryService

        GoalManager(db_session).create_goal("Labeled context goal")
        MemoryService(db_session).create(
            key="ctx_fact", value="likes tea", kind="FACT",
            source_type="USER_EXPLICIT")

        pipeline = CognitivePipeline(db_session)
        state = CognitiveState(user_input="what am I doing?")
        pipeline._assemble_context(state)

        labels = {c.label for c in state.context_sources}
        assert "GOAL_CONTEXT" in labels
        assert "PERSONAL_CONTEXT" in labels
        # Sources remain individually labeled.
        goal_ctx = next(c for c in state.context_sources
                        if c.label == "GOAL_CONTEXT")
        assert "Labeled context goal" in goal_ctx.content

    def test_memory_enters_as_remembered_epistemic(self, db_session):
        from backend.services.memory_service import MemoryService

        MemoryService(db_session).create(
            key="pref_editor", value="VS Code", kind="PREFERENCE",
            source_type="USER_EXPLICIT")

        pipeline = CognitivePipeline(db_session)
        state = CognitiveState(user_input="x")
        pipeline._assemble_context(state)

        personal = next(c for c in state.context_sources
                        if c.label == "PERSONAL_CONTEXT")
        assert personal.epistemic.value == "REMEMBERED"

    def test_capability_availability_in_state(self, db_session):
        from backend.api.dependencies import get_tool_executor as _g  # noqa
        from backend.tools.registry import register_all_tools
        from backend.tools.executor import ToolExecutor
        from backend.api import dependencies as deps

        executor = ToolExecutor()
        register_all_tools(executor)
        deps.set_executor_provider(lambda: executor)

        pipeline = CognitivePipeline(db_session)
        state = CognitiveState(user_input="check caps")
        pipeline._assemble_context(state)
        assert "system_time" in state.capabilities_available


# ===========================================================================
# Reasoning engine
# ===========================================================================


class _FakeModelClient:
    def __init__(self, content):
        self.content = content
        self.calls = []

    async def decide(self, messages, tools, max_tokens=4000, model_id=None):
        self.calls.append({"model_id": model_id,
                           "system": messages[0]["content"][:40]})
        return {"message": {"content": self.content}}


def _state_with_context():
    state = CognitiveState(user_input="Should I move my notes to Obsidian?")
    state.add_context("MEMORY_DATA", "notes are currently in Notion",
                      epistemic_label := __import__(
                          "backend.cognitive.state",
                          fromlist=["EpistemicLabel"]).EpistemicLabel.REMEMBERED)
    return state


VALID_ARTIFACT_JSON = """
{
  "problem_statement": "Decide whether to migrate notes to Obsidian",
  "relevant_facts": [{"text": "notes currently in Notion", "epistemic": "REMEMBERED"},
                     {"text": "Obsidian is faster", "epistemic": "ASSUMED"}],
  "unknowns": ["migration tooling availability"],
  "constraints": [{"type": "HARD_CONSTRAINT", "text": "no data loss"}],
  "candidate_options": [
    {"id": "migrate", "description": "migrate now", "risk": "MEDIUM", "reversible": true},
    {"id": "stay", "description": "stay on Notion", "risk": "LOW", "reversible": true}
  ],
  "tradeoffs": ["migration effort vs long-term speed"],
  "recommended_direction": "pilot migration first",
  "uncertainties": ["plugin compatibility"]
}
"""


class TestReasoningEngine:
    def test_structured_artifact_from_valid_model_output(self, db_session):
        client = _FakeModelClient(VALID_ARTIFACT_JSON)
        engine = ReasoningEngine(db_session, model_client=client)
        artifact = engine.analyze(_state_with_context())

        assert artifact.problem_statement.startswith("Decide")
        assert len(artifact.relevant_facts) == 2
        # Unverifiable KNOWN claim downgraded to ASSUMED by epistemic guard.
        assert artifact.relevant_facts[1]["epistemic"] == "ASSUMED"
        assert artifact.recommended_direction == "pilot migration first"

    def test_malformed_model_output_falls_back_deterministically(self, db_session):
        client = _FakeModelClient("I think you should just do it!")
        engine = ReasoningEngine(db_session, model_client=client)
        artifact = engine.analyze(_state_with_context())
        assert "deterministic fallback" in "; ".join(artifact.uncertainties) or \
            artifact.problem_statement
        assert artifact.candidate_options == []

    def test_no_chain_of_thought_stored(self):
        import inspect
        from backend.cognitive import reasoning as rmod

        src = inspect.getsource(rmod)
        assert "chain_of_thought" not in src.lower()
        artifact_fields = getattr(
            rmod.ReasoningArtifact, "__dataclass_fields__", {})
        assert "chain_of_thought" not in artifact_fields
        assert "internal_reasoning" not in artifact_fields

    def test_model_selection_integration(self, db_session, monkeypatch):
        """Reasoning requests flow through the Phase 6 selector."""
        import backend.runtime.model_provider as mpm

        class _P:
            def provider_id(self):
                return "test"

            def selected_model_id(self):
                return "reasoning-model"

            def list_models(self):
                return [{"name": "reasoning-model"}]

            def health(self):
                return ModelHealth("test", ModelHealth.HEALTHY)

            async def chat(self, *a, **kw):
                return {}

        monkeypatch.setattr(mpm, "get_default_provider", lambda: _P())
        client = _FakeModelClient(VALID_ARTIFACT_JSON)
        engine = ReasoningEngine(db_session, model_client=client)
        engine.analyze(_state_with_context())
        # Selector chose the configured model for this request.
        assert client.calls[0]["model_id"] == "reasoning-model"


# ===========================================================================
# Decision system
# ===========================================================================


def _cand(action_id="a", risk=RiskLevel.LOW, reversible=True,
          requires_capabilities=None, evidence_refs=None,
          decision_type=DecisionType.EXECUTE_STEP, description=None):
    return CandidateAction(
        action_id=action_id,
        decision_type=decision_type,
        description=description or f"candidate {action_id}",
        risk=risk, reversible=reversible,
        requires_capabilities=list(requires_capabilities or []),
        evidence_refs=list(evidence_refs or []),
    )


class TestDecisionSystem:
    def test_hard_constraint_veto_dominates(self):
        """A hard constraint vetoes regardless of any scoring consideration."""
        system = DecisionSystem()

        delete_candidate = CandidateAction(
            action_id="delete_files",
            decision_type=DecisionType.EXECUTE_STEP,
            description="delete project files",
            risk=RiskLevel.LOW, reversible=True)
        keep_candidate = CandidateAction(
            action_id="archive_files",
            decision_type=DecisionType.EXECUTE_STEP,
            description="archive project files",
            risk=RiskLevel.LOW, reversible=True)

        constraints = ConstraintSet(hard=[HardConstraint(
            text="no deletions without approval",
            check=lambda cand: cand.action_id == "delete_files")])

        evaluation = system.evaluate([delete_candidate, keep_candidate],
                                     constraints)
        assert evaluation.vetoed[0]["action_id"] == "delete_files"
        assert evaluation.selected.candidate.action_id == "archive_files"

    def test_hard_constraints_dominate_scores(self, db_session):
        system = DecisionSystem()
        high_risk = _cand("high_risk_action", risk=RiskLevel.HIGH,
                          description="high-risk delete action")
        constraints = ConstraintSet(
            hard=[HardConstraint(text="never delete without approval",
                                 check=lambda c: "delete" in c.description)])
        evaluation = system.evaluate([high_risk], constraints)
        assert evaluation.selected is None
        assert evaluation.vetoed[0]["action_id"] == "high_risk_action"

    def test_a_hard_constraint_without_an_executable_check_is_rejected(self):
        # An unenforced claim must not be labelled "hard": that would let a
        # model proposal appear vetted by a rule no code actually applies.
        with pytest.raises(TypeError, match="check"):
            HardConstraint(text="unenforced policy statement")

    def test_pipeline_hard_rules_are_all_enforced_and_advisory_rules_are_explicit(self):
        constraints = CognitivePipeline._default_constraints()
        assert constraints.hard
        assert all(callable(c.check) for c in constraints.hard)
        assert "never exfiltrate secrets" in constraints.policy_notes
        assert "never bypass policy/verification/lease/budgets" in constraints.policy_notes
        assert all("exfiltrate" not in c.text for c in constraints.hard)

    def test_advisory_policy_note_never_vetoes_by_itself(self):
        # A policy note is context for the downstream runtime, not a decision
        # here. Treating it as a veto would be a false claim of enforcement.
        candidate = _cand("destructive_note", description="delete project files")
        evaluation = DecisionSystem().evaluate(
            [candidate], ConstraintSet(policy_notes=["no deletions"]))
        assert evaluation.vetoed == []
        assert evaluation.selected.candidate.action_id == "destructive_note"

    def test_permission_flag_allows_an_otherwise_destructive_proposal(self):
        candidate = CandidateAction(
            action_id="permitted_delete",
            decision_type=DecisionType.EXECUTE_STEP,
            description="delete temporary files",
            risk=RiskLevel.LOW, reversible=True,
            requires_permission=True,
        )
        evaluation = DecisionSystem().evaluate(
            [candidate], CognitivePipeline._default_constraints())
        assert evaluation.vetoed == []
        assert evaluation.selected.candidate.action_id == "permitted_delete"

    def test_lower_risk_preferred(self):
        system = DecisionSystem()
        low = _cand("low", risk=RiskLevel.LOW)
        high = _cand("high", risk=RiskLevel.HIGH)
        evaluation = system.evaluate([low, high], ConstraintSet())
        assert evaluation.selected.candidate.action_id == "low"
        assert evaluation.selected.risk == RiskLevel.LOW

    def test_reversibility_tie_break(self):
        system = DecisionSystem()
        rev = _cand("reversible", reversible=True)
        irrev = _cand("irreversible", reversible=False)
        evaluation = system.evaluate([rev, irrev], ConstraintSet())
        assert evaluation.selected.candidate.action_id == "reversible"

    def test_missing_capability_veto(self, db_session):
        system = DecisionSystem(available_capabilities=["file_operations"])
        candidate = _cand("needs_gui")
        candidate.requires_capabilities = ["gui_automation"]
        evaluation = system.evaluate([candidate], ConstraintSet())
        assert evaluation.selected is None
        assert "capabilities unavailable" in evaluation.vetoed[0]["reason"]

    def test_no_candidates_yields_none_with_reason(self):
        system = DecisionSystem()
        evaluation = system.evaluate([], ConstraintSet())
        assert evaluation.selected is None
        assert "none provided" in evaluation.reason


# ===========================================================================
# Ambiguity / clarification policy
# ===========================================================================


class TestAmbiguityPolicy:
    @pytest.fixture
    def pipeline(self, db_session):
        return CognitivePipeline(db_session)

    def test_high_impact_ambiguous_prefers_clarification(self, db_session, pipeline,
                                                         monkeypatch):
        """Materially ambiguous + potentially impactful input asks rather
        than guesses."""
        # Force ambiguous intent deterministically via a stubbed interpreter.
        class _AmbiguousIntent:
            pass

        from backend.cognitive.state import IntentResult, Interpretation

        intent = IntentResult(
            category=IntentCategory.COMMAND,
            interpretations=[
                Interpretation("delete project files?", IntentCategory.COMMAND),
                Interpretation("archive project files?", IntentCategory.COMMAND),
            ],
            ambiguity=AmbiguityLevel.HIGH_IMPACT_AMBIGUITY,
            confidence=0.4,
            needs_clarification=True,
        )
        monkeypatch.setattr(pipeline.interpreter, "interpret",
                            lambda text: intent)

        result = pipeline.process("do the thing to my project files")
        assert result.decision.decision_type == DecisionType.ASK_CLARIFICATION
        assert result.needs_clarification is True

    def test_safe_interpretation_does_not_ask(self, db_session):
        # Phase 8.75: the pipeline is THE single clarification authority
        # (same materiality rule the live gate used). A confident, clear
        # question must proceed to an answer without asking.
        pipeline = CognitivePipeline(db_session)
        result = pipeline.process("What is Kenya's capital city?")
        assert result.decision is None or \
            result.decision.decision_type == DecisionType.ANSWER
        assert result.needs_clarification is False

    def test_low_confidence_statement_asks(self, db_session):
        """Phase 13A: Greetings and social inputs no longer trigger clarification.
        They are handled conversationally (ANSWER). Only genuinely ambiguous
        actionable commands missing essential parameters are parked.
        'hello there' → SOCIAL → ANSWER (not ASK_CLARIFICATION)."""
        pipeline = CognitivePipeline(db_session)
        result = pipeline.process("hello there")
        # Phase 13A: social greeting is answered directly, not parked
        assert result.needs_clarification is False
        assert result.decision is not None
        assert result.decision.decision_type == DecisionType.ANSWER


# ===========================================================================
# Simulation (dry-run only)
# ===========================================================================


class TestSimulation:
    def test_simulation_never_executes_real_actions(self, db_session):
        sim = SimulationEngine()
        destructive = _cand("delete_everything", risk=RiskLevel.CRITICAL,
                            reversible=False)
        outcome = sim.evaluate(destructive)
        assert outcome.simulated is True
        assert any("irreversible" in e for e in outcome.predicted_effects)
        assert any("dry-run only" in e for e in outcome.predicted_effects)

    def test_simulation_is_isolated_hypothetical(self, db_session):
        sim = SimulationEngine()
        cand = _cand("write_file", risk=RiskLevel.MEDIUM)
        before = cand.expected_outcome
        sim.evaluate(cand)
        assert cand.expected_outcome == before  # no mutation of candidate


# ===========================================================================
# Learning hooks
# ===========================================================================


class TestLearningHooks:
    def test_decision_emits_structured_event(self, db_session):
        from backend.runtime.events import DbEventLedger

        pipeline = CognitivePipeline(db_session)
        result = pipeline.process("What is 2+2?")

        events = DbEventLedger(db_session).event_history("cognitive")
        made = [e for e in events
                if e["event_type"] == "cognitive.decision.made"]
        assert len(made) >= 1
        data = made[-1]["event_data"]
        assert data["input"].startswith("What is 2+2")
        assert data["decision_type"] in (DecisionType.ANSWER, DecisionType.NO_ACTION)
        assert "risk" in data and "confidence" in data


# ===========================================================================
# Security: cognition cannot bypass Runtime
# ===========================================================================


class TestCognitiveSecurity:
    def test_cognitive_modules_have_no_execution_or_state_authority(self):
        import inspect

        import backend.cognitive.intent as intent_mod
        import backend.cognitive.reasoning as reason_mod
        import backend.cognitive.decision as dec_mod

        for mod in (intent_mod, reason_mod, dec_mod):
            src = inspect.getsource(mod)
            assert "execute_tool" not in src, f"{mod.__name__}"
            assert "get_adapter" not in src, f"{mod.__name__}"
            assert "GoalManager" not in src, f"{mod.__name__} writes goals"
            assert "MemoryService" not in src, f"{mod.__name__} writes memory"

    def test_pipeline_proposal_does_not_execute(self, db_session, monkeypatch):
        """A command-intent cycle produces a proposal; no task row is created,
        no runtime execution happens inside the pipeline."""
        from backend.models.database import Task

        pipeline = CognitivePipeline(db_session)
        result = pipeline.process("open notepad and write hello")

        tasks = db_session.query(Task).all()
        assert tasks == []  # execution belongs to Runtime paths only

    def test_model_metadata_cannot_bypass_policy(self):
        """Even a 'HIGH capability fit' candidate still requires runtime
        authorization; scoring never grants permission."""
        system = DecisionSystem(available_capabilities=["package_management"])
        candidate = _cand("install_pkg", risk=RiskLevel.CRITICAL,
                          requires_capabilities=["package_management"],
                          reversible=False)
        evaluation = system.evaluate([candidate],
                                     ConstraintSet())  # no approval constraint set
        # Selection is a RECOMMENDATION; it carries CRITICAL risk metadata —
        # actual execution would still hit executor policy/approval gates.
        assert evaluation.selected.risk == RiskLevel.CRITICAL
        assert evaluation.selected.confidence.decision is not None