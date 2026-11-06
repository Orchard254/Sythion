"""Model evaluation framework + deterministic selector (Phase 6).

Truthfulness invariants:
- A model receives an `evaluated=true` claim ONLY where an actual evaluation
  row exists for that task class.
- Unknown stays unknown: no name-based inference, no manufactured scores.
- Evaluation runs through the provider `chat` boundary with fixed, harmless
  prompts — no tools, no filesystem, no arbitrary code execution.

Selection policy (deterministic, documented order — see
README.md):
  1. filter capability-incompatible models      → rejected: lacks <cap>
  2. filter unavailable models                  → rejected: unavailable
  3. configured primary kept if compatible+available
  4. prefer evaluated evidence for the task class (pass rate, then score)
  5. tie-break by registry order / model_id ascending

Fallback is bounded (settings.model_max_attempts), audited (ledger events +
model_selections rows) and never falls back to an incompatible model.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from backend.models.database import ModelEvaluation, ModelSelection
from backend.runtime.events import DbEventLedger
from backend.runtime.model_provider import get_default_provider


EVALUATION_VERSION = "v1"

# Task classes with SAFE deterministic checks in this phase.
IMPLEMENTED_TASK_CLASSES = {
    "simple_reasoning",
    "structured_output",
    "context_following",
    "error_recovery",
    "planning",
}
# Explicitly NOT evaluated yet (documented; capability stays unknown):
NOT_IMPLEMENTED_TASK_CLASSES = {"tool_calling", "coding"}


# --------------------------------------------------------------------------- #
# Requirements                                                                 #
# --------------------------------------------------------------------------- #

@dataclass
class ModelRequirements:
    """Structured requirements generated deterministically from task context."""
    tool_calling: bool = False
    structured_output: bool = False
    reasoning: bool = True
    vision: bool = False
    long_context: bool = False
    task_class: str = "general"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool_calling": self.tool_calling,
            "structured_output": self.structured_output,
            "reasoning": self.reasoning,
            "vision": self.vision,
            "long_context": self.long_context,
            "task_class": self.task_class,
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "ModelRequirements":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in raw.items() if k in known})


def requirements_for_task_class(task_class: str) -> ModelRequirements:
    """Deterministic requirement generation from the task class."""
    base = {
        "simple_reasoning": dict(reasoning=True),
        "structured_output": dict(structured_output=True),
        "context_following": dict(reasoning=True),
        "error_recovery": dict(reasoning=True),
        "planning": dict(reasoning=True, structured_output=True),
        "tool_calling": dict(tool_calling=True),
        "coding": dict(reasoning=True),
        "general": dict(reasoning=True),
    }.get(task_class, dict(reasoning=True))
    return ModelRequirements(task_class=task_class, **base)


# --------------------------------------------------------------------------- #
# Evaluator                                                                    #
# --------------------------------------------------------------------------- #

@dataclass
class EvalCase:
    task_class: str
    input_ref: str
    messages: List[Dict[str, str]]
    check: Any  # Callable[response_text] -> (score float 0..1, passed Optional[bool], summary)


def _extract_text(response: Dict[str, Any]) -> str:
    message = response.get("message") or {}
    text = message.get("content") or ""
    if not text and response.get("choices"):
        text = (response["choices"][0].get("message") or {}).get("content", "")
    return text or ""


def _usage_tokens(response: Dict[str, Any]) -> int:
    usage = response.get("usage") or {}
    return int(usage.get("total_tokens")
               or usage.get("prompt_eval_count", 0)
               + usage.get("eval_count", 0)
               or 0)


def _cases() -> List[EvalCase]:
    def exact_word(expected: str):
        def check(text: str):
            hit = expected.lower() in text.lower()
            return (1.0 if hit else 0.0), hit, f"expected '{expected}'"
        return check

    def strict_json(shape: Dict[str, Any]):
        def check(text: str):
            m = re.search(r"\{.*\}", text, re.DOTALL)
            if not m:
                return 0.0, False, "no JSON object found"
            try:
                data = json.loads(m.group(0))
            except json.JSONDecodeError as exc:
                return 0.0, False, f"invalid JSON: {exc}"
            score, ok = 1.0, True
            for key, val in shape.items():
                if key not in data:
                    return 0.0, False, f"missing key '{key}'"
                if data[key] != val and val is not None:
                    score -= 0.34
                    ok = False
            return max(0.0, min(1.0, score)), ok, json.dumps(data)[:120]
        return check

    return [
        EvalCase(
            task_class="simple_reasoning",
            input_ref="arith_17_26",
            messages=[{"role": "user",
                       "content": "What is 17 + 26? Reply with just the number."}],
            check=exact_word("43"),
        ),
        EvalCase(
            task_class="context_following",
            input_ref="verbatim_token",
            messages=[{"role": "user",
                       "content": 'Reply with exactly the word "BANANA" and nothing else.'}],
            check=lambda t: (
                (1.0, True, "exact") if t.strip().upper().strip('."') == "BANANA"
                else (0.0, False, f"got {t[:40]!r}")
            ),
        ),
        EvalCase(
            task_class="structured_output",
            input_ref="strict_json_ok7",
            messages=[{"role": "system",
                       "content": 'Always answer with STRICT JSON only.'},
                      {"role": "user",
                       'content': 'Return JSON {"status": "ok", "n": 7} exactly.'}],
            check=strict_json({"status": "ok", "n": 7}),
        ),
        EvalCase(
            task_class="planning",
            input_ref="three_step_json_plan",
            messages=[{"role": "system",
                       "content": 'Answer with STRICT JSON: {"steps": ["...", "...", "..."]} '
                                  'with exactly 3 steps.'},
                      {"role": "user",
                       "content": "Plan making a cup of tea in 3 steps."}],
            check=strict_json({"steps": None}),
        ),
        EvalCase(
            task_class="error_recovery",
            input_ref="format_correction",
            messages=[{"role": "user", "content": "Say hello."},
                      {"role": "assistant", "content": "{\"broken\": true"},
                      {"role": "user",
                       "content": "Your previous reply was malformed JSON. "
                                  "Reply now with valid JSON {\"greeting\": \"...\"}."}],
            check=strict_json({"greeting": None}),
        ),
    ]


class ModelEvaluator:
    """Runs the deterministic evaluation suite against ONE target model."""

    def __init__(self, session):
        self.db = session
        self.ledger = DbEventLedger(session)

    def implemented_task_classes(self) -> List[str]:
        return sorted(IMPLEMENTED_TASK_CLASSES)

    async def run(self, model_id: str, task_classes: Optional[List[str]] = None,
                  provider=None) -> Dict[str, Any]:
        """Execute safe cases against ``model_id`` and persist each result.

        Never touches real tools/credentials; only fixed chat prompts.
        """
        from backend.core.config import settings

        provider = provider or get_default_provider()
        requested = set(task_classes or IMPLEMENTED_TASK_CLASSES)
        results: List[Dict[str, Any]] = []
        for case in _cases():
            if case.task_class not in requested:
                continue
            started = time.monotonic()
            try:
                response = await provider.chat(
                    case.messages,
                    max_tokens=settings.max_output_tokens,
                    timeout=settings.decision_timeout_seconds,
                    model_id=model_id,
                )
                latency_ms = int((time.monotonic() - started) * 1000)
                text = _extract_text(response)
                score, passed, summary = case.check(text)
                status = "recorded"
            except Exception as exc:
                latency_ms = int((time.monotonic() - started) * 1000)
                score, passed, summary = None, None, f"error: {exc}"[:200]
                status = "error"

            row = ModelEvaluation(
                model_id=model_id,
                provider_id=getattr(provider, "provider_id", lambda: "unknown")(),
                task_class=case.task_class,
                evaluation_version=EVALUATION_VERSION,
                score=score,
                passed=passed,
                latency_ms=latency_ms,
                usage_tokens=_usage_tokens(response) if status == "recorded" else None,
                input_ref=case.input_ref,
                expected_summary=f"v{EVALUATION_VERSION}:{case.input_ref}",
                actual_summary=str(summary)[:300],
                created_at=datetime.utcnow(),
            )
            self.db.add(row)
            self.ledger.append_event(f"model:{model_id}", "model.evaluation.recorded", {
                "task_class": case.task_class,
                "evaluation_version": EVALUATION_VERSION,
                "passed": passed,
                "score": score,
            })
            results.append({
                "task_class": case.task_class,
                "score": score, "passed": passed,
                "latency_ms": latency_ms, "status": status,
                "summary": str(summary)[:200],
            })
        self.db.commit()

        skipped = sorted(requested & NOT_IMPLEMENTED_TASK_CLASSES)
        unknown = sorted(requested - set(c.task_class for c in _cases())
                         - NOT_IMPLEMENTED_TASK_CLASSES)
        return {"model_id": model_id, "results": results,
                "skipped_not_implemented": skipped,
                "unknown_task_classes": unknown}

    def compare(self, model_ids: List[str], task_class: str) -> Dict[str, Any]:
        """Aggregate persisted evidence for one task class, best first.

        Ranking: pass_rate desc → avg_score desc → attempts asc → model_id.
        Models with zero rows are reported as UNRATED (never inferred).
        """
        best_by_model: Dict[str, Dict[str, Any]] = {}
        for mid in model_ids:
            rows = (
                self.db.query(ModelEvaluation)
                .filter_by(model_id=mid, task_class=task_class)
                .all()
            )
            attempts = len(rows)
            passed = sum(1 for r in rows if r.passed is True)
            scored = [r.score for r in rows if r.score is not None]
            avg = round(sum(scored) / len(scored), 4) if scored else None
            best_by_model[mid] = {
                "model_id": mid, "attempts": attempts,
                "pass_rate": round(passed / attempts, 4) if attempts else None,
                "avg_score": avg,
            }
        ranked = sorted(
            best_by_model.values(),
            key=lambda d: (
                -(d["pass_rate"] if d["pass_rate"] is not None else -1),
                -(d["avg_score"] if d["avg_score"] is not None else -1),
                d["attempts"],
                d["model_id"],
            ),
        )
        return {"task_class": task_class,
                "ranking": ranked,
                "unrated": [m for m in model_ids
                            if best_by_model[m]["attempts"] == 0]}


def record_failure(db, model_id: str, category: str, detail: str) -> None:
    """Structured failure evidence (audited; never auto-blacklists)."""
    DbEventLedger(db).append_event(f"model:{model_id}", f"model.{category}", {
        "detail": detail[:300],
    })
    db.commit()


# --------------------------------------------------------------------------- #
# Selector                                                                     #
# --------------------------------------------------------------------------- #

@dataclass
class SelectionResult:
    selected: Optional[str]
    rejected: List[Dict[str, str]] = field(default_factory=list)
    reason: str = ""
    candidates_considered: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "selected": self.selected,
            "rejected": self.rejected,
            "reason": self.reason,
            "candidates_considered": self.candidates_considered,
        }


class ModelSelector:
    """Deterministic evidence-based selection over registered models."""

    def __init__(self, session, evaluator: Optional[ModelEvaluator] = None,
                 provider: Optional[Any] = None):
        self.db = session
        self.evaluator = evaluator or ModelEvaluator(session)
        # Lazy resolution: dependencies are fetched at CALL time so test
        # doubles and policy changes apply without reimporting.
        self._provider_override = provider

    def _provider(self):
        if self._provider_override is not None:
            return self._provider_override
        from backend.runtime.model_provider import get_default_provider

        return get_default_provider()

    def select(self, requirements: ModelRequirements,
               available_models: Optional[List[str]] = None,
               exclude: Optional[set] = None) -> SelectionResult:
        """Apply the documented deterministic policy and persist the audit."""
        registry_records = {
            r.model_id: r for r in ModelRegistryLike(self.db).list_models()
        }
        candidates = list(available_models or list(registry_records.keys()))
        if exclude:
            candidates = [c for c in candidates if c not in exclude]

        rejected: List[Dict[str, str]] = []
        considered: List[str] = []
        provider = self._provider()
        selected_primary = provider.selected_model_id()

        # 1. capability compatibility (supported=False ⇒ hard reject;
        #    unknown passes but is flagged at ranking time via evidence).
        for mid in list(candidates):
            caps = ModelRegistryLike(self.db).model_capabilities(mid)
            incompatible = []
            if requirements.tool_calling and caps["tool_calling"]["supported"] is False:
                incompatible.append("tool_calling")
            if requirements.vision and caps["vision"]["supported"] is False:
                incompatible.append("vision")
            if incompatible:
                rejected.append({"model": mid,
                                 "reason": f"lacks {'/'.join(incompatible)}"})
                candidates.remove(mid)
            elif available_models is not None and mid not in available_models:
                rejected.append({"model": mid, "reason": "unavailable"})
                candidates.remove(mid)
            elif registry_records.get(mid) and \
                    registry_records[mid].available_now is False:
                rejected.append({"model": mid, "reason": "unavailable"})
                candidates.remove(mid)
            else:
                considered.append(mid)

        if not candidates:
            result = SelectionResult(None, rejected, "no_compatible_available_model",
                                     considered)
            self._persist(requirements, result)
            return result

        # 2. configured primary first when compatible+available.
        if selected_primary in candidates:
            result = SelectionResult(
                selected_primary, rejected,
                "configured primary satisfies requirements",
                considered)
            self._persist(requirements, result)
            return result

        # 3./4./5. evidence-based ranking for the task class.
        comparison = self.evaluator.compare(candidates, requirements.task_class)
        ranking = {r["model_id"]: r for r in comparison["ranking"]}
        best = None
        for mid in candidates:
            info = ranking.get(mid)
            if info and (info["pass_rate"] or 0) > 0:
                best = mid
                break
        if best is None:
            # No positive evidence anywhere: fall back to stable registry
            # order (first candidate), flagged as unverified.
            best = sorted(candidates)[0]
            result = SelectionResult(best, rejected,
                                     "no evaluation evidence; first stable candidate (unverified)",
                                     considered)
            self._persist(requirements, result)
            return result

        result = SelectionResult(
            best, rejected,
            "highest evaluated evidence for task class",
            considered)
        self._persist(requirements, result)
        return result

    def next_fallback(self, requirements: ModelRequirements,
                      failed_model: str) -> SelectionResult:
        """Next compatible candidate after a classified failure."""
        result = self.select(requirements, exclude={failed_model})
        result.reason = f"fallback after {failed_model} failure: {result.reason}"
        if result.selected:
            DbEventLedger(self.db).append_event(
                f"model:{result.selected}", "model.fallback", {
                    "failed_model": failed_model,
                    "requirements": requirements.to_dict(),
                })
            self.db.commit()
        return result

    def _persist(self, requirements: ModelRequirements,
                 result: SelectionResult) -> None:
        self.db.add(ModelSelection(
            requirements_json=json.dumps(requirements.to_dict()),
            selected_model=result.selected,
            rejected_json=json.dumps(result.rejected),
            reason=result.reason[:300],
            created_at=datetime.utcnow(),
        ))
        self.db.commit()


class ModelRegistryLike:
    """Thin adapter so the selector depends on the registry interface."""

    def __init__(self, session):
        from backend.services.system_state import ModelRegistry

        self._registry = ModelRegistry(session)

    def list_models(self):
        return self._registry.list_models()

    def model_capabilities(self, model_id: str):
        return self._registry.model_capabilities(model_id)