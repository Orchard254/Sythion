"""Phase 9 — live end-to-end delegation through the cognitive layer."""

import asyncio

import pytest


@pytest.fixture
def seeded(test_db):
    from backend.agents.service import AgentService

    service = AgentService(test_db)
    service.ensure_definitions()
    return test_db


ANALYSIS_REQUEST = (
    "Which of these three approaches is best for our rollout when we "
    "analyze scalability, cost, and maintainability trade-offs? "
    "Please compare thoroughly and recommend one with evidence."
)


class TestLiveDelegationFlow:
    def test_pipeline_selects_delegate_for_analysis_request(self, seeded):
        from backend.cognitive.pipeline import CognitivePipeline
        from backend.cognitive.state import DecisionType

        db = seeded
        result = CognitivePipeline(db).process(ANALYSIS_REQUEST)
        assert result.decision is not None
        assert result.decision.decision_type == DecisionType.DELEGATE

    def test_no_delegate_candidate_without_definitions(self, test_db,
                                                       monkeypatch):
        from backend.cognitive.pipeline import CognitivePipeline
        from backend.cognitive.state import DecisionType

        monkeypatch.setattr(
            "backend.core.config.settings.specialists_enabled", True)
        # No definitions seeded anywhere in this fresh DB...
        result = CognitivePipeline(test_db).process(ANALYSIS_REQUEST)
        candidates = [c.action_id for c in result.state.candidates]
        assert "delegate_specialist" not in candidates
        assert result.decision.decision_type != DecisionType.DELEGATE

    def test_disabled_specialists_remove_delegate_candidate(self, test_db,
                                                            monkeypatch):
        from backend.agents.service import AgentService
        from backend.cognitive.pipeline import CognitivePipeline
        from backend.cognitive.state import DecisionType

        monkeypatch.setattr(
            "backend.core.config.settings.specialists_enabled", False)
        AgentService(test_db).ensure_definitions()
        result = CognitivePipeline(test_db).process(ANALYSIS_REQUEST)
        assert result.decision.decision_type != DecisionType.DELEGATE

    def test_route_new_input_bridges_to_delegate_mode(self, seeded):
        from backend.services.cognitive_runtime import CognitiveDecisionAdapter
        from backend.services.task_runner import TaskRunner

        db = seeded
        runner = TaskRunner(db)
        assert asyncio.run(runner.acquire_lease())
        try:
            task = asyncio.run(runner.create_task("live delegate probe"))
            adapter = CognitiveDecisionAdapter(runner, db)
            routing = asyncio.run(adapter.route_new_input(
                task.id, ANALYSIS_REQUEST))
            assert routing.mode == "delegate"
        finally:
            asyncio.run(runner.release_lease())

    def test_full_delegation_completes_parent_task_with_verified_result(
            self, seeded):
        """The Phase 9 success flow:

        user input → cognitive DELEGATE → bounded specialist created →
        scoped context → work through Runtime → verification → Main Agent
        integration → parent task COMPLETED with integrated result.
        """
        from backend.api.schemas import TaskState
        from backend.models.database import Delegation
        from backend.services.cognitive_runtime import (
            CognitiveDecisionAdapter,
        )
        from backend.services.task_runner import TaskRunner

        db = seeded
        runner = TaskRunner(db)
        assert asyncio.run(runner.acquire_lease())
        try:
            task = asyncio.run(runner.create_task(ANALYSIS_REQUEST))
            adapter = CognitiveDecisionAdapter(runner, db)
            outcome = asyncio.run(adapter.run_delegate_decision(
                task.id, ANALYSIS_REQUEST))
            assert outcome["integrated"] is True

            delegation = db.query(Delegation).filter_by(
                status="INTEGRATED").first()
            assert delegation is not None
            assert delegation.integration_outcome == "ACCEPT"

            completed = asyncio.run(runner.get_task(task.id))
            assert completed.state == TaskState.COMPLETED.value
            assert "[delegated:" in completed.final_result
            assert "specialist cost" in completed.final_result

            # Specialist expired lifecycle hygiene happens via expire_due;
            # here it must at least be COMPLETED, never RUNNING forever.
            agent_row = delegation.specialist_agent_id
            from backend.agents.service import AgentService

            specialist = AgentService(db).get_agent(agent_row)
            assert specialist.state in ("COMPLETED", "EXPIRED")
        finally:
            asyncio.run(runner.release_lease())

    def test_end_to_end_via_post_tasks(self, test_client, test_db):
        """Full HTTP path: POST /tasks with an analysis request comes back
        COMPLETED with a delegated, verified, integrated final result."""
        from backend.agents.service import AgentService

        AgentService(test_db).ensure_definitions()
        response = test_client.post(
            "/api/v1/tasks", json={"original_request": ANALYSIS_REQUEST})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["state"] == "completed"
        assert "[delegated:" in (body.get("final_result") or "")

        listing = test_client.get("/api/v1/agents/delegations")
        assert listing.status_code == 200
        delegations = listing.json()["delegations"]
        assert any(d["status"] == "INTEGRATED" for d in delegations)

    def test_specialist_failure_parks_parent_not_forges_completion(
            self, seeded, monkeypatch):
        """Failure invariant: unverified specialist work never completes the
        parent task; control returns to the user instead."""
        from backend.api.schemas import TaskState
        from backend.services.cognitive_runtime import (
            CognitiveDecisionAdapter,
        )
        from backend.services.task_runner import TaskRunner

        monkeypatch.setattr(
            "backend.core.config.settings.specialists_enabled", False)

        db = seeded
        runner = TaskRunner(db)
        assert asyncio.run(runner.acquire_lease())
        try:
            task = asyncio.run(runner.create_task(ANALYSIS_REQUEST))
            adapter = CognitiveDecisionAdapter(runner, db)
            outcome = asyncio.run(adapter.run_delegate_decision(
                task.id, ANALYSIS_REQUEST))
            # specialists disabled ⇒ park path (never completes the parent)
            assert outcome.get("integrated") is not True
            # The park metadata is returned; the authoritative check is
            # the durable task state.
            current = asyncio.run(runner.get_task(task.id))
            assert current.state == TaskState.WAITING_FOR_USER.value
        finally:
            asyncio.run(runner.release_lease())
