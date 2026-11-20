"""Owner-only read visibility for remote-action lifecycle state."""

from backend.runtime.remote_action_lifecycle import (
    PLANNED,
    UNCERTAIN,
    UNDERSTANDING,
    RemoteActionManager,
)


def _manager(test_db):
    return RemoteActionManager(test_db)


def _create(test_db, key="v-1", device="dev_1"):
    return _manager(test_db).create(
        idempotency_key=key, device_id=device, task_id="task-1")


def test_list_is_read_only_and_bounded(test_client, test_db):
    first = _create(test_db)
    second = _create(test_db, key="v-2", device="dev_2")
    response = test_client.get("/api/v1/remote-actions")
    assert response.status_code == 200
    actions = response.json()["actions"]
    assert {a["action_id"] for a in actions} == {
        first["action_id"], second["action_id"]}
    assert all(a["authorized"] is False for a in actions)
    assert all(a["approval_issued"] is False for a in actions)

    bad = test_client.get("/api/v1/remote-actions?limit=0")
    assert bad.status_code == 400


def test_detail_returns_events(test_client, test_db):
    action = _create(test_db)
    manager = _manager(test_db)
    manager.transition(action["action_id"], UNDERSTANDING)
    manager.transition(action["action_id"], PLANNED)

    response = test_client.get(f"/api/v1/remote-actions/{action['action_id']}")
    assert response.status_code == 200
    body = response.json()
    assert body["action"]["state"] == PLANNED
    assert [e["to_state"] for e in body["events"]] == [
        "received", UNDERSTANDING, PLANNED]
    assert [e["sequence"] for e in body["events"]] == [1, 2, 3]


def test_uncertain_endpoint_lists_only_uncertain(test_client, test_db):
    uncertain = _create(test_db, key="v-uncertain")
    other = _create(test_db, key="v-other")
    manager = _manager(test_db)
    manager.transition(uncertain["action_id"], UNCERTAIN, reason="lost connection")

    response = test_client.get("/api/v1/remote-actions/uncertain")
    assert response.status_code == 200
    actions = response.json()["actions"]
    assert [a["action_id"] for a in actions] == [uncertain["action_id"]]
    assert other["action_id"] not in [a["action_id"] for a in actions]
    assert all(a["uncertain"] is True for a in actions)


def test_detail_unknown_action_returns_404(test_client, test_db):
    assert test_client.get("/api/v1/remote-actions/ra_missing").status_code == 404


def test_device_channel_cannot_read_owner_visibility(test_client, test_db):
    action = _create(test_db)
    headers = {
        "X-Sythion-Device-Id": "dev_1",
        "X-Sythion-Device-Token": "token",
    }
    assert test_client.get(
        "/api/v1/remote-actions", headers=headers).status_code == 403
    assert test_client.get(
        f"/api/v1/remote-actions/{action['action_id']}", headers=headers).status_code == 403


def test_owner_boundary_still_required(test_client, test_db):
    saved = test_client.headers.pop("X-Control-Token", None)
    try:
        assert test_client.get("/api/v1/remote-actions").status_code == 401
    finally:
        if saved is not None:
            test_client.headers["X-Control-Token"] = saved
