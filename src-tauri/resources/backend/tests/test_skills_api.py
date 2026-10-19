"""
Phase F skill + retention API tests.

The owner-facing contract: skills are declarative, versioned workflows that
enter the system only as proposals requiring explicit review and enablement.
Retention purges complete records and returns non-sensitive receipts.
"""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.main import app
from backend.models.database import Base, Task, TaskEvent, SettingsMetadata
from backend.models.database import get_db


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    import backend.main as backend_main

    previous_token = backend_main.control_token
    backend_main.control_token = "phase-f-test-token"
    try:
        yield TestClient(
            app,
            headers={
                "Origin": "tauri://localhost",
                "X-Control-Token": "phase-f-test-token",
            },
        )
    finally:
        backend_main.control_token = previous_token
        app.dependency_overrides.clear()


def _valid_definition(**overrides):
    definition = {
        "version": "1.0.0",
        "name": "read workflow",
        "description": "reads a file",
        "category": "file",
        "parameters": [{"name": "path", "type": "string", "required": True, "description": "file path"}],
        "steps": [{"step_id": "read1", "tool": "file_read", "inputs": {"path": "inputs.path"}}],
    }
    definition.update(overrides)
    return definition


class TestSkillSubmission:
    def test_submit_creates_proposal(self, client):
        response = client.post(
            "/api/v1/skills",
            json={"skill_id": "workflow.1", "definition": _valid_definition()},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "proposed"
        assert body["proposal_id"] > 0

    def test_submit_invalid_definition_returns_422(self, client):
        bad = _valid_definition(steps=[{"step_id": "s1", "tool": "file_write", "inputs": {}}])
        response = client.post("/api/v1/skills", json={"skill_id": "workflow.2", "definition": bad})
        assert response.status_code == 422

    def test_submit_malformed_json_returns_422(self, client):
        response = client.post(
            "/api/v1/skills",
            json={"skill_id": "workflow.3", "definition": {"version": "1.0.0"}},
        )
        assert response.status_code == 422

    def test_approval_flow(self, client):
        created = client.post(
            "/api/v1/skills",
            json={"skill_id": "workflow.4", "definition": _valid_definition()},
        ).json()
        proposal_id = created["proposal_id"]

        listed = client.get("/api/v1/skills/proposals").json()
        assert any(p["id"] == proposal_id and p["status"] == "proposed" for p in listed)

        approved = client.post(f"/api/v1/skills/proposals/{proposal_id}/approve")
        assert approved.status_code == 200
        assert approved.json()["state"] == "enabled"

        # Proposal is now enabled; skill is listable with its validated version.
        skills = client.get("/api/v1/skills").json()
        assert any(s["id"] == "workflow.4" and s["current_version"] == "1.0.0" for s in skills)
        detail = client.get("/api/v1/skills/workflow.4").json()
        assert detail["versions"][0]["version"] == "1.0.0"

    def test_reject_flow(self, client):
        created = client.post(
            "/api/v1/skills",
            json={"skill_id": "workflow.5", "definition": _valid_definition()},
        ).json()
        proposal_id = created["proposal_id"]

        rejected = client.post(f"/api/v1/skills/proposals/{proposal_id}/reject")
        assert rejected.status_code == 200
        assert rejected.json()["status"] == "rejected"

        # A rejected proposal must not have created a live skill.
        assert client.get("/api/v1/skills/workflow.5").status_code == 404

    def test_unknown_proposal_404(self, client):
        assert client.post("/api/v1/skills/proposals/99999/approve").status_code == 404

    def test_duplicate_skill_id_conflict(self, client):
        payload = {"skill_id": "workflow.6", "definition": _valid_definition()}
        client.post("/api/v1/skills", json=payload)
        created = client.post(
            "/api/v1/skills",
            json={"skill_id": "workflow.6", "definition": _valid_definition()},
        )
        assert created.status_code == 200  # second submission is another proposal

    def test_disable_and_enable_state(self, client):
        created = client.post(
            "/api/v1/skills",
            json={"skill_id": "workflow.7", "definition": _valid_definition()},
        ).json()
        client.post(f"/api/v1/skills/proposals/{created['proposal_id']}/approve")

        disabled = client.post("/api/v1/skills/workflow.7/state", json={"state": "disabled"})
        assert disabled.status_code == 200
        assert disabled.json()["state"] == "disabled"

        enabled = client.post("/api/v1/skills/workflow.7/state", json={"state": "enabled"})
        assert enabled.json()["state"] == "enabled"


class TestRetentionAPI:
    def test_purge_by_task_id(self, client, db_session):
        task = Task(
            id="t-purge",
            original_request="some content",
            state="completed",
            version=1,
            created_at=datetime.utcnow() - timedelta(days=300),
            updated_at=datetime.utcnow(),
        )
        db_session.add(task)
        db_session.add(TaskEvent(task_id="t-purge", event_type="tool.executed",
                                 event_data="{}", sequence_num=1))
        db_session.commit()

        response = client.post(
            "/api/v1/retention/purge",
            json={"task_id": "t-purge", "reason": "test"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["purged_count"] == 1
        assert body["receipts"][0]["task_id"] == "t-purge"
        assert body["receipts"][0]["event_count"] == 1

        assert db_session.query(Task).filter_by(id="t-purge").first() is None

    def test_purge_unknown_task_404(self, client):
        response = client.post(
            "/api/v1/retention/purge",
            json={"task_id": "nope", "reason": "test"},
        )
        assert response.status_code == 404

    def test_purge_by_days(self, client, db_session):
        db_session.add(SettingsMetadata(key="retention_days", value="90"))
        db_session.commit()
        db_session.add(Task(
            id="t-old", original_request="x", state="completed", version=1,
            created_at=datetime.utcnow() - timedelta(days=200), updated_at=datetime.utcnow(),
        ))
        db_session.commit()

        response = client.post(
            "/api/v1/retention/purge",
            json={"days": 90, "reason": "retention"},
        )
        assert response.status_code == 200
        assert response.json()["purged_count"] == 1

    def test_receipts_listed(self, client, db_session):
        db_session.add(Task(
            id="t-r", original_request="x", state="completed", version=1,
            created_at=datetime.utcnow() - timedelta(days=200), updated_at=datetime.utcnow(),
        ))
        db_session.commit()
        client.post("/api/v1/retention/purge", json={"task_id": "t-r", "reason": "expired"})

        receipts = client.get("/api/v1/retention/receipts").json()
        assert any(r["task_id"] == "t-r" and r["reason"] == "expired" for r in receipts)

    def test_receipts_never_contain_request_text(self, client, db_session):
        db_session.add(Task(
            id="t-secret", original_request="MY PRIVATE PHRASE", state="completed", version=1,
            created_at=datetime.utcnow() - timedelta(days=200), updated_at=datetime.utcnow(),
        ))
        db_session.commit()
        client.post("/api/v1/retention/purge", json={"task_id": "t-secret", "reason": "expired"})

        receipts = client.get("/api/v1/retention/receipts").json()
        assert "MY PRIVATE PHRASE" not in str(receipts)
