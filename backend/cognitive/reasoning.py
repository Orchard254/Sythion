"""ReasoningEngine (Phase 8): structured reasoning artifacts — no CoT.

Produces concise, structured reasoning RESULTS (problem/facts/unknowns/
options/tradeoffs/recommendation/uncertainties). Uses the Phase 6
ModelSelector + ModelClient; never executes tools or writes state. Malformed
model output falls back to a deterministic artifact rather than failing.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from backend.cognitive.state import (
    CognitiveState,
    EpistemicLabel,
    RiskLevel,
)
from backend.services.model_selection import (
    ModelRequirements,
    ModelSelector,
)

logger = logging.getLogger(__name__)

MAX_REASONING_CALLS = 2  # cognitive-loop bound


@dataclass
class ReasoningArtifact:
    problem_statement: str
    relevant_facts: List[Dict[str, str]]      # [{text, epistemic}]
    unknowns: List[str]
    constraints: List[Dict[str, str]]          # [{type: HARD|SOFT, text}]
    candidate_options: List[Dict[str, Any]]
    tradeoffs: List[str]
    recommended_direction: str
    uncertainties: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "problem_statement": self.problem_statement,
            "relevant_facts": self.relevant_facts,
            "unknowns": self.unknowns,
            "constraints": self.constraints,
            "candidate_options": self.candidate_options,
            "tradeoffs": self.tradeoffs,
            "recommended_direction": self.recommended_direction,
            "uncertainties": self.uncertainties,
        }


FALLBACK_FACT_LIMIT = 5
MAX_FACT_CHARS = 200
MAX_PROBLEM_CHARS = 300


def _fallback_artifact(state: CognitiveState,
                       reason: str = "deterministic fallback") -> ReasoningArtifact:
    """Build a deterministic reasoning artifact when model reasoning is unavailable.

    Truncates the problem statement and context facts to bounded lengths and
    records ``reason`` in the uncertainties so callers can surface why the
    fallback was used.
    """
    facts = [
        {"text": c.content[:MAX_FACT_CHARS],
         "epistemic": c.epistemic.value}
        for c in state.context_sources[:FALLBACK_FACT_LIMIT]
    ]
    return ReasoningArtifact(
        problem_statement=state.user_input[:MAX_PROBLEM_CHARS] or "(empty input)",
        relevant_facts=facts,
        unknowns=["insufficient context for deeper analysis"],
        constraints=[],
        candidate_options=[],
        tradeoffs=[],
        recommended_direction="gather more information",
        uncertainties=[reason],
    )


_REASONING_SYSTEM_PROMPT = """You are the reasoning module of Sythion's \
cognitive layer. Given a structured problem, respond with STRICT JSON only:
{
  "problem_statement": "...",
  "relevant_facts": [{"text": "...", "epistemic": "KNOWN|OBSERVED|REMEMBERED|INFERRED|ASSUMED|UNKNOWN"}],
  "unknowns": ["..."],
  "constraints": [{"type": "HARD_CONSTRAINT|SOFT_PREFERENCE", "text": "..."}],
  "candidate_options": [{"id": "opt_a", "description": "...", "risk": "LOW|MEDIUM|HIGH|CRITICAL", "reversible": true}],
  "tradeoffs": ["..."],
  "recommended_direction": "...",
  "uncertainties": ["..."]
}
Rules: be concise; never invent capabilities or tools; mark anything not
directly evidenced as INFERRED/ASSUMED/UNKNOWN; JSON only, no prose."""


class ReasoningEngine:
    """Provider-neutral structured reasoning over a CognitiveState."""

    def __init__(self, session=None, model_client=None):
        self.db = session
        self._model_client = model_client
        self.calls_made = 0  # cognitive-loop bound

    def _client(self):
        if self._model_client is not None:
            return self._model_client
        from backend.runtime.model_client import get_default_model_client

        return get_default_model_client()

    def _selector(self):
        from backend.services.model_selection import ModelSelector

        return ModelSelector(self.db) if self.db is not None else None

    def analyze(self, state: CognitiveState,
                requirements: Optional[ModelRequirements] = None) -> ReasoningArtifact:
        if self.calls_made >= MAX_REASONING_CALLS:
            logger.info("Cognitive loop bound reached; deterministic artifact")
            return _fallback_artifact(state, "cognitive loop bound reached")
        self.calls_made += 1

        requirements = requirements or ModelRequirements(
            reasoning=True, structured_output=True)

        # Phase 6 integration: selector picks the model for THIS reasoning
        # request; no provider/model names are hard-coded here.
        selected_model = None
        selector = self._selector()
        if selector is not None:
            try:
                selection = selector.select(requirements)
                selected_model = selection.selected
            except Exception as exc:
                logger.warning("Model selection failed: %s", exc)

        prompt = self._build_prompt(state, requirements)
        client = self._client()
        try:
            import asyncio

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                response = asyncio.run(client.decide(
                    messages=prompt, tools=[], max_tokens=4000,
                    model_id=selected_model))
            else:
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    response = pool.submit(
                        asyncio.run,
                        client.decide(messages=prompt, tools=[],
                                      max_tokens=4000, model_id=selected_model),
                    ).result(timeout=120)
        except Exception as exc:
            logger.warning("Reasoning call failed (%s); deterministic artifact",
                           exc)
            return _fallback_artifact(state, f"model unavailable: {exc}"[:120])

        text = self._extract(response)
        artifact = self._parse_artifact(text)
        if artifact is None:
            return _fallback_artifact(state, "malformed reasoning output")

        # Epistemic sanity: any fact the model labels KNOWN that we cannot
        # ground in provided context is downgraded to ASSUMED.
        known_texts = {c.content[:100].lower() for c in state.context_sources}
        for fact in artifact.relevant_facts:
            if fact.get("epistemic") == EpistemicLabel.KNOWN.value:
                anchor = str(fact.get("text", ""))[:60].lower()
                if anchor and not any(anchor[:30] in k for k in known_texts):
                    fact["epistemic"] = EpistemicLabel.ASSUMED.value
        return artifact

    @staticmethod
    def _build_prompt(state: CognitiveState,
                      requirements: ModelRequirements) -> List[Dict[str, str]]:
        sections = [f"USER_INPUT:\n{state.user_input}"]
        for src in state.context_sources:
            sections.append(f"{src.label} ({src.epistemic.value}):\n{src.content}")
        if state.capabilities_available:
            sections.append("AVAILABLE CAPABILITIES:\n"
                            + ", ".join(state.capabilities_available))
        messages = [{"role": "system", "content": _REASONING_SYSTEM_PROMPT}]
        messages.append({"role": "user", "content": "\n\n".join(sections)})
        return messages

    @staticmethod
    def _extract(response: Dict[str, Any]) -> str:
        message = response.get("message") or {}
        content = message.get("content") or ""
        if not content and response.get("choices"):
            content = (response["choices"][0].get("message") or {}).get("content", "")
        return (content or "").strip()

    def _parse_artifact(self, text: str) -> Optional[ReasoningArtifact]:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
        try:
            return ReasoningArtifact(
                problem_statement=str(data.get("problem_statement", ""))[:500],
                relevant_facts=[
                    {"text": str(f.get("text", ""))[:300],
                     "epistemic": str(f.get("epistemic", "UNKNOWN"))}
                    for f in data.get("relevant_facts", [])[:10]
                ],
                unknowns=[str(u)[:200] for u in data.get("unknowns", [])[:8]],
                constraints=[
                    {"type": str(c.get("type", "SOFT_PREFERENCE")),
                     "text": str(c.get("text", ""))[:200]}
                    for c in data.get("constraints", [])[:8]
                ],
                candidate_options=list(data.get("candidate_options", []))[:6],
                tradeoffs=[str(t)[:200] for t in data.get("tradeoffs", [])[:8]],
                recommended_direction=str(data.get("recommended_direction", ""))[:300],
                uncertainties=[str(u)[:200] for u in data.get("uncertainties", [])[:8]],
            )
        except Exception:  # pragma: no cover - defensive shape guard
            return None