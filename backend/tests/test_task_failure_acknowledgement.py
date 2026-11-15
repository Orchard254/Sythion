"""Owner dismissal hides failed-task attention without destroying execution evidence."""
import json

from backend.core.settings_store import get_setting, set_setting
from backend.models.database import Task


def failed_task(db, task_id="failed-task"):
    db.add(Task(id=task_id, original_request="Open Notepad",
                state="failed", terminal_reason="INVALID_DECISION_TYPE"))
    db.commit()


def test_acknowledgement_requires_terminal_failure(test_client, test_db):
    no_task = test_client.post("/api/v1/tasks/missing/acknowledge-failure")
    assert no_task.status_code == 404
    test_db.add(Task(id="still-running", original_request="Busy", state="running"))
    test_db.commit()
    response = test_client.post("/api/v1/tasks/still-running/acknowledge-failure")
    assert response.status_code == 409
    assert test_client.get("/api/v1/tasks/acknowledged-failures").json()["task_ids"] == []


def test_failed_task_dismissal_is_idempotent_durable_and_does_not_delete_task(test_client, test_db):
    failed_task(test_db)
    url = "/api/v1/tasks/failed-task/acknowledge-failure"
    first = test_client.post(url)
    assert first.status_code == 200, first.text
    assert first.json() == {
        "task_id": "failed-task", "acknowledged": True,
        "audit_preserved": True,
    }
    assert test_client.post(url).status_code == 200
    assert test_client.get("/api/v1/tasks/acknowledged-failures").json() == {
        "task_ids": ["failed-task"],
    }
    task = test_client.get("/api/v1/tasks/failed-task")
    assert task.status_code == 200
    assert task.json()["state"] == "failed"
    assert task.json()["terminal_reason"] == "INVALID_DECISION_TYPE"
    # Task list and its execution records remain queryable.
    assert any(item["id"] == "failed-task"
               for item in test_client.get("/api/v1/tasks").json()["tasks"])


def test_invalid_acknowledgement_store_fails_closed(test_client, test_db):
    set_setting(test_db, "acknowledged_failed_task_ids_v1", '{"bad":true}')
    test_db.commit()
    assert test_client.get("/api/v1/tasks/acknowledged-failures").status_code == 503
    failed_task(test_db)
    assert test_client.post("/api/v1/tasks/failed-task/acknowledge-failure").status_code == 503
    assert test_client.get("/api/v1/tasks/failed-task").json()["state"] == "failed"


def test_failed_task_acknowledgement_rejects_unconfirmed_control_token(test_client, test_db):
    failed_task(test_db)
    saved = test_client.headers.pop("X-Control-Token", None)
    try:
        response = test_client.post("/api/v1/tasks/failed-task/acknowledge-failure")
        assert response.status_code == 401
    finally:
        if saved: test_client.headers["X-Control-Token"] = saved
    assert test_client.get("/api/v1/tasks/acknowledged-failures").json()["task_ids"] == []
