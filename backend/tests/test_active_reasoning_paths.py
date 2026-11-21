"""Regression tests for the two active-reasoning binding paths.

BUG 1: ``SpecialistReasoner`` must resolve its chat provider through the
       verified active binding, never the bare Ollama default.
BUG 2: ``ReasoningEngine`` must actually reach the resolved active decision
       client instead of swallowing an unsupported ``model_id`` keyword and
       always returning the deterministic fallback.

No real network calls: activations use synthetic credentials and clients are
replaced with fakes.
"""
import json

import pytest

from backend.agents.reasoner import SpecialistReasoner
from backend.agents.specialist import SpecialistContextPacket
from backend.cognitive.reasoning import ReasoningEngine
from backend.cognitive.state import CognitiveState, EpistemicLabel
from backend.runtime import active_inference as active


_PROVIDER_ID = "openrouter"
_MODEL_ID = "unit/cloud:free"
_SYNTHETIC_KEY = "synthetic-not-real"  # never a real credential


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #

@pytest.fixture
def isolated_profile(tmp_path, monkeypatch):
    """Point profile/secret state at a disposable store for one test."""
    from backend.core import profile, secrets

    monkeypatch.setattr(profile, "data_dir", lambda: str(tmp_path))
    secrets.set_secret_store_for_testing(secrets.MemoryStore())
    yield tmp_path
    secrets.set_secret_store_for_testing(None)


def _activate_verified_cloud(db):
    """Build a genuine credential-bound active selection (offline evidence)."""
    from backend.core import profile, secrets
    from backend.core.cloud_text_evidence import persist_text_evidence
    from backend.core.cloud_probe_evidence import persist_probe_evidence
    from backend.core.cloud_assistant_evidence import persist_assistant_contract_evidence

    profile.stage_cloud_selection(_PROVIDER_ID, _MODEL_ID)
    secrets.set_secret(secrets.OPENROUTER_API_KEY, _SYNTHETIC_KEY)
    snapshot = profile.get_staged_cloud_snapshot()
    persist_text_evidence(db, snapshot, _SYNTHETIC_KEY)
    persist_probe_evidence(db, snapshot, _SYNTHETIC_KEY)
    persist_assistant_contract_evidence(db, snapshot, _SYNTHETIC_KEY)
    return active.activate_verified_cloud_selection(db, _PROVIDER_ID, _MODEL_ID)


def _packet(role="ANALYSIS"):
    return SpecialistContextPacket(
        objective="Compare A and B",
        specialist_agent_id="s-test",
        role=role,
        required_capabilities=[],
        budget={"steps": 2, "time_seconds": 60, "model_calls": 2},
    )


class _Selection:
    def __init__(self, model):
        self.selected = model


class _SelectorWithModel:
    def __init__(self, model):
        self.model = model

    def select(self, requirements):
        return _Selection(self.model)


_VALID_SPECIALIST_JSON = json.dumps({
    "findings": ["active binding finding"],
    "analysis": "reached the active provider",
    "limitations": ["none"],
    "confidence": 0.8,
})


_VALID_ARTIFACT_JSON = json.dumps({
    "problem_statement": "Decide whether to migrate notes to Obsidian",
    "relevant_facts": [{"text": "notes currently in Notion", "epistemic": "REMEMBERED"}],
    "unknowns": ["migration tooling availability"],
    "constraints": [],
    "candidate_options": [{"id": "pilot", "description": "pilot migration first",
                           "risk": "MEDIUM", "reversible": True}],
    "tradeoffs": ["migration effort vs long-term speed"],
    "recommended_direction": "pilot migration first",
    "uncertainties": ["plugin compatibility"],
})


def _state():
    state = CognitiveState(user_input="Should I migrate my notes?")
    state.add_context("MEMORY_DATA", "notes currently in Notion",
                      EpistemicLabel.REMEMBERED)
    return state


# --------------------------------------------------------------------------- #
# BUG 1 — SpecialistReasoner provider resolution
# --------------------------------------------------------------------------- #

def test_specialist_reasoner_honors_active_cloud_binding(
        test_db, isolated_profile, monkeypatch):
    """An active cloud binding selects the active-compatible chat provider."""
    selection = _activate_verified_cloud(test_db)
    assert selection == active.ActiveInferenceSelection(_PROVIDER_ID, _MODEL_ID)

    import backend.runtime.model_provider as model_provider

    def _forbidden_default():
        raise AssertionError("bare Ollama default must not be used for an active binding")

    monkeypatch.setattr(model_provider, "get_default_provider", _forbidden_default)

    reasoner = SpecialistReasoner(test_db, _packet(), allowed_tools=[], budget=_packet().budget)
    provider = reasoner._provider()
    assert isinstance(provider, active.ActiveCompatibleChatProvider)
    assert provider.provider_id() == _PROVIDER_ID
    assert provider.selected_model_id() == _MODEL_ID


def test_specialist_reasoner_legacy_ollama_sentinel_uses_default_provider(monkeypatch):
    """The unconfigured sentinel still yields the native Ollama provider."""
    sentinel_provider = object()
    monkeypatch.setattr(
        active, "get_active_inference_selection",
        lambda: active.ActiveInferenceSelection("ollama-cloud", ""),
    )
    import backend.runtime.model_provider as model_provider
    monkeypatch.setattr(model_provider, "get_default_provider", lambda: sentinel_provider)

    pkt = _packet()
    reasoner = SpecialistReasoner(None, pkt, allowed_tools=[], budget=pkt.budget)
    assert reasoner._provider() is sentinel_provider


def test_specialist_reasoner_damaged_binding_fails_closed(test_db, isolated_profile):
    """A rotated credential raises instead of silently reverting to Ollama."""
    from backend.core import secrets

    _activate_verified_cloud(test_db)
    secrets.set_secret(secrets.OPENROUTER_API_KEY, "rotated-not-real")

    pkt = _packet()
    reasoner = SpecialistReasoner(test_db, pkt, allowed_tools=[], budget=pkt.budget)
    with pytest.raises(active.ActiveInferenceUnavailable):
        reasoner._provider()


@pytest.mark.asyncio
async def test_specialist_reasoning_reaches_active_provider(
        test_db, isolated_profile, monkeypatch):
    """The specialist's model call is served by the active chat provider."""
    _activate_verified_cloud(test_db)
    instances = []

    class _RecordingActiveChatProvider(active.ActiveCompatibleChatProvider):
        def __init__(self, selection):
            super().__init__(selection)
            instances.append(self)
            self.calls = []

        async def chat(self, messages, max_tokens=4000, timeout=60.0, temperature=None):
            self.calls.append({"max_tokens": max_tokens, "timeout": timeout})
            return {"message": {"content": _VALID_SPECIALIST_JSON}}

    monkeypatch.setattr(active, "ActiveCompatibleChatProvider", _RecordingActiveChatProvider)

    pkt = _packet()
    reasoner = SpecialistReasoner(
        test_db, pkt, allowed_tools=[], budget=pkt.budget,
        model_selector=_SelectorWithModel("selector-chosen-model"),
    )
    result = await reasoner.analyze()

    assert result["fallback"] is False
    assert result["findings"] == ["active binding finding"]
    assert len(instances) == 1
    assert len(instances[0].calls) == 1


# --------------------------------------------------------------------------- #
# BUG 2 — ReasoningEngine decision client
# --------------------------------------------------------------------------- #

class _StrictDecisionClient:
    """Active-style client: decide() has no ``model_id`` parameter."""

    def __init__(self, content=None, error=None):
        self.content = content
        self.error = error
        self.calls = []

    async def decide(self, messages, tools, max_tokens=4000):
        self.calls.append({"messages": messages, "tools": tools, "max_tokens": max_tokens})
        if self.error is not None:
            raise self.error
        return {"message": {"content": self.content}}


class _ModelIdAwareDecisionClient(_StrictDecisionClient):
    """Legacy-style client that does accept ``model_id``."""

    async def decide(self, messages, tools, max_tokens=4000, model_id=None):
        self.calls.append({"model_id": model_id})
        return {"message": {"content": self.content}}


def _engine(client):
    engine = ReasoningEngine(None, model_client=client)
    # Force a non-empty selection so an unsupported ``model_id`` would matter.
    engine._selector = lambda: _SelectorWithModel("bound-model")
    return engine


def test_reasoning_engine_reaches_decide_and_returns_parsed_artifact():
    """A client without ``model_id`` support is reached and its result parsed."""
    client = _StrictDecisionClient(content=_VALID_ARTIFACT_JSON)
    artifact = _engine(client).analyze(_state())

    assert len(client.calls) == 1
    assert "model_id" not in client.calls[0]
    assert artifact.problem_statement.startswith("Decide")
    assert artifact.recommended_direction == "pilot migration first"
    assert artifact.uncertainties == ["plugin compatibility"]
    assert "model unavailable" not in " ".join(artifact.uncertainties)


def test_reasoning_engine_genuine_model_failure_still_falls_back():
    """A real decide() failure keeps the deterministic fallback contract."""
    client = _StrictDecisionClient(error=RuntimeError("model offline"))
    artifact = _engine(client).analyze(_state())

    assert len(client.calls) == 1
    assert artifact.recommended_direction == "gather more information"
    assert any("model unavailable" in u for u in artifact.uncertainties)


def test_reasoning_engine_still_passes_model_id_when_supported():
    """Clients that accept ``model_id`` keep receiving the selected model."""
    client = _ModelIdAwareDecisionClient(content=_VALID_ARTIFACT_JSON)
    artifact = _engine(client).analyze(_state())

    assert artifact.recommended_direction == "pilot migration first"
    assert client.calls[0]["model_id"] == "bound-model"


def test_reasoning_engine_malformed_output_falls_back():
    """Malformed (but successfully fetched) output uses the parse fallback."""
    client = _StrictDecisionClient(content="I think you should just do it!")
    artifact = _engine(client).analyze(_state())

    assert len(client.calls) == 1
    assert artifact.candidate_options == []
    assert any("malformed" in u for u in artifact.uncertainties)
