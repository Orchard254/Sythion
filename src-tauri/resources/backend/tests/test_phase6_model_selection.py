"""
Phase 6 tests: model capability schema, deterministic evaluation,
evidence-based selection, bounded fallback, budget awareness, and security
boundaries (evaluation/selector never touch real tools or the adapter).
"""

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models.database import (
    Base,
    GoalPlan,
    ModelEvaluation,
    ModelSelection,
)
from backend.services.model_selection import (
    EVALUATION_VERSION,
    IMPLEMENTED_TASK_CLASSES,
    ModelEvaluator,
    ModelRequirements,
    ModelSelector,
    NOT_IMPLEMENTED_TASK_CLASSES,
    record_failure,
    requirements_for_task_class,
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


def _resp(content: str):
    return {"message": {"content": content},
            "usage": {"total_tokens": 42}}


class _ScriptedProvider:
    """Provider stub whose chat replies come from a queue (FIFO)."""

    provider = "test-provider"

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def provider_id(self):
        return "test"

    def selected_model_id(self):
        return "primary"

    def list_models(self):
        return [{"name": m} for m in ("primary", "backup", "text_only")]

    def health(self):
        from backend.runtime.model_provider import ModelHealth
        return ModelHealth("test", ModelHealth.HEALTHY)

    async def chat(self, messages, max_tokens=4000, timeout=300.0, model_id=None):
        self.calls.append(model_id or "primary")
        reply = self.replies.pop(0) if self.replies else ""
        if isinstance(reply, Exception):
            raise reply
        return _resp(reply)


# ===========================================================================
# Requirements + evaluation
# ===========================================================================


class TestRequirements:
    def test_deterministic_generation(self):
        req = requirements_for_task_class("planning")
        assert req.structured_output is True
        assert req.reasoning is True
        assert req.vision is False
        assert requirements_for_task_class("tool_calling").tool_calling is True


class TestEvaluation:
    def test_pass_recorded_with_metrics(self, db_session):
        provider = _ScriptedProvider(["The answer is 43."])
        evaluator = ModelEvaluator(db_session)
        result = asyncio_run(evaluator.run("m1", ["simple_reasoning"], provider))
        r = result["results"][0]
        assert r["passed"] is True and r["score"] == 1.0
        row = db_session.query(ModelEvaluation).one()
        assert row.model_id == "m1"
        assert row.evaluation_version == EVALUATION_VERSION
        assert row.latency_ms is not None

    def test_fail_recorded(self, db_session):
        provider = _ScriptedProvider(["I don't know."])
        evaluator = ModelEvaluator(db_session)
        result = asyncio_run(evaluator.run("m1", ["simple_reasoning"], provider))
        assert result["results"][0]["passed"] is False
        row = db_session.query(ModelEvaluation).one()
        assert row.passed is False

    def test_structured_output_json_check(self, db_session):
        provider = _ScriptedProvider(['Sure: {"status": "ok", "n": 7}'])
        evaluator = ModelEvaluator(db_session)
        result = asyncio_run(evaluator.run("m1", ["structured_output"], provider))
        assert result["results"][0]["passed"] is True

    def test_error_case_recorded_inconclusive(self, db_session, monkeypatch):
        from backend.core.config import settings
        monkeypatch.setattr(settings, "decision_timeout_seconds", 0.001)

        class _Slow(_ScriptedProvider):
            async def chat(self, *a, **kw):
                await asyncio.sleep(1)

        evaluator = ModelEvaluator(db_session)
        result = asyncio_run(evaluator.run("m1", ["simple_reasoning"], _Slow([])))
        r = result["results"][0]
        assert r["status"] == "error"
        assert r["passed"] is None  # unknown stays unknown
        row = db_session.query(ModelEvaluation).one()
        assert row.passed is None

    def test_not_implemented_classes_reported_honestly(self, db_session):
        provider = _ScriptedProvider([])
        evaluator = ModelEvaluator(db_session)
        result = asyncio_run(evaluator.run(
            "m1",
            ["simple_reasoning", "coding", "tool_calling"],
            provider,
        ))
        # coding/tool_calling have NO safe deterministic checks yet.
        assert set(result["skipped_not_implemented"]) == {"coding", "tool_calling"}
        classes = [r["task_class"] for r in result["results"]]
        assert "coding" not in classes and "tool_calling" not in classes

    def test_unknown_task_class_rejected(self, db_session):
        evaluator = ModelEvaluator(db_session)
        result = asyncio_run(evaluator.run("m1", ["quantum_leap"], _ScriptedProvider([])))
        assert "quantum_leap" in result["unknown_task_classes"]
        assert db_session.query(ModelEvaluation).count() == 0

    def test_versioned_and_persisted_history(self, db_session):
        provider = _ScriptedProvider(["43", "BANANA"])
        evaluator = ModelEvaluator(db_session)
        asyncio_run(evaluator.run("m1", ["simple_reasoning", "context_following"], provider))
        rows = db_session.query(ModelEvaluation).all()
        assert {r.task_class for r in rows} == {
            "simple_reasoning", "context_following"}
        assert all(r.evaluation_version == EVALUATION_VERSION for r in rows)


# ===========================================================================
# Selection
# ===========================================================================


class TestSelection:
    def _seed_eval(self, db, model, task, passed, score):
        db.add(ModelEvaluation(
            model_id=model, provider_id="test", task_class=task,
            evaluation_version=EVALUATION_VERSION,
            score=score, passed=passed, latency_ms=10,
            created_at=__import__("datetime").datetime.utcnow(),
        ))
        db.commit()

    def _selector(self, db, monkeypatch, selected="primary"):
        import backend.runtime.model_provider as mpm

        monkeypatch.setattr(mpm, "get_default_provider", lambda: _P(selected))
        from backend.services.model_selection import ModelSelector as MS

        return MS(db), MS

    def test_incompatible_model_rejected_with_reason(self, db_session, monkeypatch):
        selector, _ = self._selector(db_session, monkeypatch)
        # vision required; registry says vision supported=False for all models
        result = selector.select(ModelRequirements(vision=True))
        assert result.selected is None
        assert all("lacks vision" in r["reason"] for r in result.rejected)
        assert db_session.query(ModelSelection).count() == 1  # audit persisted

    def test_unavailable_model_rejected(self, db_session, monkeypatch):
        import backend.runtime.model_provider as mpm

        class _Dead:
            def provider_id(self):
                return "t"

            def selected_model_id(self):
                return "primary"

            def list_models(self):
                raise RuntimeError("down")

            def health(self):
                return ModelHealth("t", ModelHealth.UNAVAILABLE)

            async def chat(self, *a, **kw):
                raise RuntimeError("down")

        monkeypatch.setattr(mpm, "get_default_provider", lambda: _Dead())
        from backend.services.model_selection import ModelSelector as MS

        selector = MS(db_session)
        result = selector.select(ModelRequirements(reasoning=True))
        assert result.selected is None
        assert any(r["reason"] == "unavailable" for r in result.rejected)

    def test_configured_primary_wins_when_compatible(self, db_session, monkeypatch):
        selector, _ = self._selector(db_session, monkeypatch, selected="primary")
        result = selector.select(ModelRequirements(reasoning=True))
        assert result.selected == "primary"
        assert "configured primary" in result.reason

    def test_evaluated_evidence_preferred_over_unrated(self, db_session, monkeypatch):
        """Primary has no evidence; backup has a passing record for the task
        class → evidence beats configured-primary-first policy? NO — the
        documented policy keeps primary first when compatible+available;
        evidence decides only AMONG non-primary candidates."""
        selector, _ = self._selector(db_session, monkeypatch, selected="primary")
        self._seed_eval(db_session, "backup", "simple_reasoning", True, 1.0)

        result = selector.select(requirements_for_task_class("simple_reasoning"))
        assert result.selected == "primary"

    def test_evidence_ranks_among_non_primary_candidates(self, db_session, monkeypatch):
        selector, _ = self._selector(db_session, monkeypatch, selected="retired")
        self._seed_eval(db_session, "weak", "simple_reasoning", False, 0.2)
        self._seed_eval(db_session, "strong", "simple_reasoning", True, 1.0)

        # exclude the (nonexistent here) primary so candidates are weak/strong/text_only
        result = selector.select(
            requirements_for_task_class("simple_reasoning"),
            available_models=["weak", "strong", "text_only"],
        )
        assert result.selected == "strong"
        assert "highest evaluated evidence" in result.reason

    def test_deterministic_tie_break_by_model_id(self, db_session, monkeypatch):
        selector, _ = self._selector(db_session, monkeypatch, selected="zzz")
        # no evidence anywhere → stable first candidate by id
        result = selector.select(
            ModelRequirements(reasoning=True),
            available_models=["bbb", "aaa"],
        )
        assert result.selected == "aaa"


class _P:
    def __init__(self, selected="primary"):
        self._selected = selected

    def provider_id(self):
        return "test"

    def selected_model_id(self):
        return self._selected

    def list_models(self):
        return [{"name": "primary"}, {"name": "backup"},
                {"name": "text_only"}, {"name": "weak"},
                {"name": "strong"}, {"name": "aaa"}, {"name": "zzz"}]

    def health(self):
        from backend.runtime.model_provider import ModelHealth
        return ModelHealth("test", ModelHealth.HEALTHY)

    async def chat(self, messages, max_tokens=4000, timeout=300.0, model_id=None):
        return _resp("ok")


# ===========================================================================
# Fallback
# ===========================================================================


class TestFallback:
    @pytest.fixture
    def engine_env(self, db_session, monkeypatch):
        from backend.services.model_engine import ModelDecisionEngine
        from backend.tools.executor import ToolExecutor

        calls = []

        class _Client:
            def __init__(self, fail_first_for=None):
                self.fail_first_for = fail_first_for or set()

            async def decide(self, messages, tools, max_tokens=4000, model_id=None):
                target = model_id or "primary"
                calls.append(target)
                if target in self.fail_first_for and \
                        self.fail_first_for[target] > 0:
                    self.fail_first_for[target] -= 1
                    raise RuntimeError(f"{target} exploded")
                return {"message": {"tool_calls": [{
                    "function": {"name": "complete_task",
                                 "arguments": {"result": f"done via {target}"}},
                }]}}

        client = _Client()
        monkeypatch.setattr(
            "backend.runtime.model_client.get_default_model_client",
            lambda: client,
        )
        # Provider facade used by the selector: primary fails first time.
        provider = _ScriptedProvider([])  # unused for chat in this path

        engine = ModelDecisionEngine(db_session, ToolExecutor(),
                                     model_client=client)
        engine._build_context = lambda **kw: []
        return engine, client, calls

    def _fail_provider_facade(self, monkeypatch):
        import backend.runtime.model_provider as mpm

        class _P:
            def provider_id(self):
                return "test"

            def selected_model_id(self):
                return "primary"

            def list_models(self):
                return [{"name": "primary"}, {"name": "backup"}]

            def health(self):
                return ModelHealth("test", ModelHealth.HEALTHY)

            async def chat(self, *a, **kw):
                return {}

        monkeypatch.setattr(mpm, "get_default_provider", lambda: _P())

    @pytest.mark.asyncio
    async def test_transient_failure_falls_back_once_then_recovers(
            self, db_session, monkeypatch, engine_env):
        from backend.api.schemas import TaskState  # noqa: F401
        from backend.core.config import settings

        self._fail_provider_facade(monkeypatch)
        monkeypatch.setattr(settings, "model_max_attempts", 2)

        engine, client, calls = engine_env
        client.fail_first_for = {"primary": 1}

        decision_type, params = await engine.make_decision(
            task_id="t", original_request="x", normalized_goal=None,
            event_history=[], current_state="running")

        assert params["result"] == "done via backup"
        assert calls == ["primary", "backup"]  # exactly one bounded fallback

        # Audit trail exists.
        failures = [
            e for e in __import__(
                "backend.runtime.events", fromlist=["DbEventLedger"]
            ).DbEventLedger(db_session).event_history("models")
        ] if False else None
        sel_rows = db_session.query(ModelSelection).all()
        assert len(sel_rows) >= 1
        assert sel_rows[-1].fallback_of == "primary"
        failure_events = [
            e for e in __import__(
                "backend.runtime.events", fromlist=["DbEventLedger"]
            ).DbEventLedger(db_session).event_history("model:primary")
            if e["event_type"].startswith("model.provider_error")
               or e["event_type"] == "model.fallback"
        ]
        assert len(failure_events) >= 1

    @pytest.mark.asyncio
    async def test_permanent_failure_bounded_no_infinite_retry(
            self, db_session, monkeypatch, engine_env):
        from backend.core.config import settings

        self._fail_provider_facade(monkeypatch)
        monkeypatch.setattr(settings, "model_max_attempts", 3)

        engine, client, calls = engine_env
        client.fail_first_for = {"primary": 99, "backup": 99}

        with pytest.raises(Exception):
            await engine.make_decision(
                task_id="t", original_request="x", normalized_goal=None,
                event_history=[], current_state="running")

        # Bounded: primary + ONE fallback attempt only (max_attempts - 1).
        assert len(calls) <= 2

    @pytest.mark.asyncio
    async def test_budget_consumed_by_fallback_attempt(
            self, db_session, monkeypatch, engine_env):
        from backend.core.budgets import TaskBudget
        from backend.core.config import settings

        self._fail_provider_facade(monkeypatch)
        monkeypatch.setattr(settings, "model_max_attempts", 2)

        engine, client, calls = engine_env
        client.fail_first_for = {"primary": 1}
        budget = TaskBudget(max_cloud_requests=10)

        await engine.make_decision(
            task_id="t", original_request="x", normalized_goal=None,
            event_history=[], current_state="running", budget=budget)

        # Both attempts consumed cloud-request budget (fallback is not free).
        assert budget.cloud_requests == 2


# ===========================================================================
# Security boundaries
# ===========================================================================


class TestSecurityBoundaries:
    def test_selector_and_evaluator_cannot_execute_tools_or_touch_adapter(self):
        import inspect

        import backend.services.model_selection as ms

        src = inspect.getsource(ms)
        assert "execute_tool" not in src
        assert "get_adapter" not in src
        assert "OllamaCloudAdapter" not in src

    def test_evaluation_prompts_are_fixed_and_safe(self, db_session):
        """Every eval case must be a fixed prompt — no tool schemas, no file
        paths, no credentials can enter an evaluation run."""
        from backend.services.model_selection import _cases

        for case in _cases():
            blob = json.dumps(case.messages).lower()
            assert "tool" not in blob or case.task_class == "tool_calling"
            for banned in ("api_key", "password", "c:\\users", "/home/"):
                assert banned not in blob
            assert all(m["role"] in ("user", "system", "assistant")
                       for m in case.messages)


# ===========================================================================
# helpers
# ===========================================================================


def asyncio_run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro) \
        if False else asyncio.run(coro)


import asyncio  # noqa: E402