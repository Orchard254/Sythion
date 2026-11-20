"""Durable relay delivery acks: recorded once, validated, owner-readable."""

from datetime import datetime

from backend.models.database import RelayAck
from backend.services.device_registry import register_local_device
from backend.services.relay import open_session, post_message
from backend.services.relay_acks import AckError, ack_summary, record_ack

NOW = datetime(2026, 11, 20, 8, 0, 0)


def _device_and_session(test_db):
    device = register_local_device(test_db, now=NOW)
    device_id = device["device_id"]
    session = open_session(test_db, device_id)
    test_db.commit()
    return device_id, session["session_id"]


def test_record_ack_is_durable_and_idempotent(test_db):
    device_id, sid = _device_and_session(test_db)
    msg = post_message(test_db, sid, "hello")
    test_db.commit()

    first = record_ack(test_db, session_id=sid, device_id=device_id, sequence=msg["id"])
    test_db.commit()
    assert first["duplicate"] is False
    assert first["authorized"] is False

    again = record_ack(test_db, session_id=sid, device_id=device_id, sequence=msg["id"])
    assert again["duplicate"] is True
    assert test_db.query(RelayAck).count() == 1


def test_record_ack_validates_sequence_session_and_message(test_db):
    device_id, sid = _device_and_session(test_db)
    msg = post_message(test_db, sid, "hello")
    test_db.commit()

    for bad_seq in (-1, True, "3"):
        try:
            record_ack(test_db, session_id=sid, device_id=device_id, sequence=bad_seq)
            raise AssertionError(f"bad sequence accepted: {bad_seq!r}")
        except AckError as exc:
            assert exc.code == "INVALID_SEQUENCE"

    # Session belonging to another device is refused.
    try:
        record_ack(test_db, session_id=sid, device_id="other-device", sequence=msg["id"])
        raise AssertionError("foreign device ack accepted")
    except AckError as exc:
        assert exc.code == "SESSION_MISMATCH"

    # A sequence that is not a message in this session is refused.
    try:
        record_ack(test_db, session_id=sid, device_id=device_id, sequence=999999)
        raise AssertionError("unknown message ack accepted")
    except AckError as exc:
        assert exc.code == "UNKNOWN_MESSAGE"


def test_ack_summary_reports_high_water_and_is_non_authorizing(test_db):
    device_id, sid = _device_and_session(test_db)
    m1 = post_message(test_db, sid, "one")
    m2 = post_message(test_db, sid, "two")
    test_db.commit()
    record_ack(test_db, session_id=sid, device_id=device_id, sequence=m1["id"])
    record_ack(test_db, session_id=sid, device_id=device_id, sequence=m2["id"])
    test_db.commit()

    summary = ack_summary(test_db, session_id=sid)
    assert summary["authorized"] is False
    assert summary["approval_issued"] is False
    assert "does not grant authority" in summary["note"]
    assert any(s["session_id"] == sid and s["highest_ack"] == m2["id"]
               for s in summary["sessions"])
    assert {a["sequence"] for a in summary["acks"]} == {m1["id"], m2["id"]}


def test_ack_summary_rejects_bad_limit(test_db):
    try:
        ack_summary(test_db, limit=0)
        raise AssertionError("bad limit accepted")
    except ValueError:
        pass


def test_owner_api_reads_acks(test_client, test_db):
    device_id, sid = _device_and_session(test_db)
    msg = post_message(test_db, sid, "hello")
    test_db.commit()
    record_ack(test_db, session_id=sid, device_id=device_id, sequence=msg["id"])
    test_db.commit()

    response = test_client.get("/api/v1/relay-acks")
    assert response.status_code == 200
    body = response.json()
    assert body["authorized"] is False
    assert any(a["sequence"] == msg["id"] for a in body["acks"])

    filtered = test_client.get(f"/api/v1/relay-acks?session_id={sid}")
    assert filtered.status_code == 200
    assert all(a["session_id"] == sid for a in filtered.json()["acks"])


def test_owner_api_rejects_device_channels_and_bad_limit(test_client, test_db):
    headers = {"X-Sythion-Device-Id": "dev_1", "X-Sythion-Device-Token": "token"}
    assert test_client.get("/api/v1/relay-acks", headers=headers).status_code == 403
    assert test_client.get("/api/v1/relay-acks?limit=0").status_code == 400


def test_owner_api_requires_control_boundary(test_client, test_db):
    saved = test_client.headers.pop("X-Control-Token", None)
    try:
        assert test_client.get("/api/v1/relay-acks").status_code == 401
    finally:
        if saved is not None:
            test_client.headers["X-Control-Token"] = saved
