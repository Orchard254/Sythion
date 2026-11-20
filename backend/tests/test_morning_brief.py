"""Stage 1 morning brief: deterministic, read-only, once per local date."""

from datetime import datetime

from backend.models.database import MorningBrief, Objective, Task
from backend.services.morning_brief import build_morning_brief, ensure_daily_brief


NOW = datetime(2026, 11, 20, 9, 30, 0)


def test_default_config_is_off(test_db):
    from backend.core.config import Settings

    config = Settings(_env_file=None)
    assert config.morning_brief_enabled is False
    assert config.morning_brief_local_hour == 8


def test_invalid_hour_rejected_only_when_enabled(monkeypatch):
    from backend.core import config
    from backend.core.config import validate_startup_config

    monkeypatch.setattr(config.settings, "ollama_api_key", "k")
    monkeypatch.setattr(config.settings, "selected_model", "m")
    monkeypatch.setattr(config.settings, "morning_brief_enabled", True)
    monkeypatch.setattr(config.settings, "morning_brief_local_hour", 24)
    assert "morning_brief_local_hour" in " ".join(
        msg for _, msg in validate_startup_config())

    monkeypatch.setattr(config.settings, "morning_brief_enabled", False)
    assert "morning_brief_" not in " ".join(
        msg for _, msg in validate_startup_config())


def test_brief_is_non_authorizing_and_structured(test_db):
    brief = build_morning_brief(test_db, now=NOW)
    assert brief["date"] == "2026-11-20"
    assert brief["headline"].startswith("Good morning")
    assert [s["key"] for s in brief["sections"]] == [
        "goals", "tasks", "blockers", "reconciliation", "agent", "devices", "device_actions", "receipts", "system",
    ]
    assert brief["authorized"] is False
    assert brief["approval_issued"] is False
    assert brief["execution_started"] is False
    assert "not an approval" in brief["note"]
    assert "not an approval" in brief["text"]
    assert test_db.query(MorningBrief).count() == 0  # build does not persist


def test_brief_reports_recent_work_and_blockers(test_db):
    test_db.add(Objective(id="g1", title="Ship the report", status="completed",
                          updated_at=NOW))
    test_db.add(Task(id="t1", original_request="draft the report",
                     state="completed", updated_at=NOW))
    test_db.add(Task(id="t2", original_request="waiting task",
                     state="waiting_for_user", pending_question="Which file?"))
    test_db.commit()

    brief = build_morning_brief(test_db, now=NOW)
    sections = {s["key"]: s["lines"] for s in brief["sections"]}
    assert any("Ship the report" in line for line in sections["goals"])
    assert any("draft the report" in line for line in sections["tasks"])
    assert any("Which file?" in line for line in sections["blockers"])


def test_brief_reports_registered_devices(test_db):
    from backend.services.device_registry import (
        register_local_device, set_trust_level,
    )

    device = register_local_device(test_db, now=NOW)
    set_trust_level(test_db, device["device_id"], "trusted")
    test_db.commit()

    brief = build_morning_brief(test_db, now=NOW)
    devices = next(s for s in brief["sections"] if s["key"] == "devices")
    assert len(devices["lines"]) == 1
    line = devices["lines"][0]
    assert device["device_id"] in line
    assert "trust=trusted" in line
    assert "last_seen=" in line


def test_brief_reports_recent_device_actions(test_db):
    from backend.runtime.remote_action_lifecycle import RemoteActionManager

    manager = RemoteActionManager(test_db)
    first = manager.create(idempotency_key="mb-recent-1", device_id="dev_1")
    second = manager.create(idempotency_key="mb-recent-2", device_id="dev_2")
    test_db.commit()

    brief = build_morning_brief(test_db, now=NOW)
    section = next(s for s in brief["sections"] if s["key"] == "device_actions")
    joined = " ".join(section["lines"])
    assert first["action_id"] in joined
    assert second["action_id"] in joined
    assert "received" in joined
    assert "dev_1" in joined
    assert "dev_2" in joined


def test_brief_recent_device_actions_empty_is_explicit(test_db):
    brief = build_morning_brief(test_db, now=NOW)
    section = next(s for s in brief["sections"] if s["key"] == "device_actions")
    assert section["lines"] == ["No device actions recorded."]


def test_brief_reports_durable_delivery_receipts(test_db):
    from backend.services.device_registry import register_local_device
    from backend.services.relay import open_session, post_message
    from backend.services.relay_acks import record_ack

    device = register_local_device(test_db, now=NOW)
    device_id = device["device_id"]
    session = open_session(test_db, device_id)
    msg = post_message(test_db, session["session_id"], "hello")
    test_db.commit()
    record_ack(test_db, session_id=session["session_id"],
               device_id=device_id, sequence=msg["id"])
    test_db.commit()

    brief = build_morning_brief(test_db, now=NOW)
    section = next(s for s in brief["sections"] if s["key"] == "receipts")
    joined = " ".join(section["lines"])
    assert session["session_id"] in joined
    assert f"acked through #{msg['id']}" in joined


def test_brief_receipts_flag_never_acking_session(test_db):
    from backend.services.device_registry import register_local_device
    from backend.services.relay import open_session

    device = register_local_device(test_db, now=NOW)
    session = open_session(test_db, device["device_id"], transport="wss")
    test_db.commit()

    brief = build_morning_brief(test_db, now=NOW)
    section = next(s for s in brief["sections"] if s["key"] == "receipts")
    joined = " ".join(section["lines"])
    # A WSS session with messages but no acks is a silent device -> flagged.
    assert session["session_id"] in joined
    assert "acked through #0" in joined
    assert "unacked" in joined


def test_brief_receipts_do_not_flag_http_fallback(test_db):
    from backend.services.device_registry import register_local_device
    from backend.services.relay import open_session

    device = register_local_device(test_db, now=NOW)
    session = open_session(test_db, device["device_id"], transport="http")
    test_db.commit()

    brief = build_morning_brief(test_db, now=NOW)
    section = next(s for s in brief["sections"] if s["key"] == "receipts")
    joined = " ".join(section["lines"])
    assert session["session_id"] in joined
    assert "HTTP fallback (acks unavailable)" in joined
    assert "unacked" not in joined


def test_brief_receipts_report_unacked_lag(test_db):
    from backend.services.device_registry import register_local_device
    from backend.services.relay import open_session, post_message
    from backend.services.relay_acks import record_ack

    device = register_local_device(test_db, now=NOW)
    device_id = device["device_id"]
    session = open_session(test_db, device_id)
    m1 = post_message(test_db, session["session_id"], "one")
    post_message(test_db, session["session_id"], "two")
    post_message(test_db, session["session_id"], "three")
    test_db.commit()
    record_ack(test_db, session_id=session["session_id"],
               device_id=device_id, sequence=m1["id"])
    test_db.commit()

    brief = build_morning_brief(test_db, now=NOW)
    section = next(s for s in brief["sections"] if s["key"] == "receipts")
    joined = " ".join(section["lines"])
    assert "2 unacked" in joined


def test_brief_delivery_receipts_empty_is_explicit(test_db):
    brief = build_morning_brief(test_db, now=NOW)
    section = next(s for s in brief["sections"] if s["key"] == "receipts")
    assert section["lines"] == ["No delivery receipts recorded."]


def test_brief_reports_uncertain_actions_and_pending_approvals(test_db):
    from backend.models.database import PermissionProposalRow, Task
    from backend.runtime.remote_action_lifecycle import UNCERTAIN, RemoteActionManager

    test_db.add(Task(id="task-1", original_request="request",
                     state="running", version=3))
    test_db.commit()
    action = RemoteActionManager(test_db).create(
        idempotency_key="mb-unc", device_id="dev_1", task_id="task-1")
    RemoteActionManager(test_db).transition(
        action["action_id"], UNCERTAIN, reason="outcome unknown")
    test_db.add(PermissionProposalRow(
        proposal_id="p-mb", task_id="task-1", action_id="a-mb",
        tool_name="file_write", task_version=3, schema_version=1,
        params_digest="d", proposal_digest="d", target_canonical="x",
        content_sha256="h", content_bytes=0, target_must_not_exist=True,
        status="awaiting", created_at=NOW,
    ))
    test_db.commit()

    brief = build_morning_brief(test_db, now=NOW)
    section = next(s for s in brief["sections"] if s["key"] == "reconciliation")
    joined = " ".join(section["lines"])
    assert action["action_id"] in joined
    assert "outcome unknown" in joined
    assert "file_write" in joined
    assert "awaiting decision" in joined


def test_brief_reconciliation_empty_is_explicit(test_db):
    brief = build_morning_brief(test_db, now=NOW)
    section = next(s for s in brief["sections"] if s["key"] == "reconciliation")
    assert section["lines"] == ["Nothing needs reconciliation."]


def test_brief_devices_section_handles_empty_registry(test_db):
    brief = build_morning_brief(test_db, now=NOW)
    devices = next(s for s in brief["sections"] if s["key"] == "devices")
    assert devices["lines"] == ["No devices have registered."]


def test_build_makes_no_state_changes(test_db):
    test_db.add(Task(id="t1", original_request="r", state="waiting_for_user",
                     pending_question="Which file?"))
    test_db.commit()
    before = test_db.query(Task).get("t1").state
    build_morning_brief(test_db, now=NOW)
    assert test_db.query(Task).get("t1").state == before


def test_ensure_daily_brief_is_idempotent(test_db):
    first, created1 = ensure_daily_brief(test_db, now=NOW)
    test_db.commit()
    second, created2 = ensure_daily_brief(test_db, now=NOW)

    assert created1 is True
    assert created2 is False
    assert second == first
    assert test_db.query(MorningBrief).count() == 1


def test_next_day_creates_a_new_brief(test_db):
    ensure_daily_brief(test_db, now=NOW)
    test_db.commit()
    _, created = ensure_daily_brief(
        test_db, now=datetime(2026, 11, 21, 9, 0, 0))
    assert created is True
    assert test_db.query(MorningBrief).count() == 2


def test_read_morning_brief_returns_stored_without_writing(test_db):
    from backend.services.morning_brief import read_morning_brief

    stored, _ = ensure_daily_brief(test_db, now=NOW)
    test_db.commit()
    result = read_morning_brief(test_db, now=NOW)
    assert result == stored
    assert test_db.query(MorningBrief).count() == 1


def test_morning_brief_endpoint_is_read_only(test_client, test_db):
    response = test_client.get("/api/v1/briefing/morning")
    assert response.status_code == 200
    body = response.json()
    assert "headline" in body
    assert [s["key"] for s in body["sections"]] == [
        "goals", "tasks", "blockers", "reconciliation", "agent", "devices", "device_actions", "receipts", "system",
    ]
    assert body["authorized"] is False
    assert body["approval_issued"] is False
    assert body["execution_started"] is False
    assert test_db.query(MorningBrief).count() == 0  # endpoint does not persist
