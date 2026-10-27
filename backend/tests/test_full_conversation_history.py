import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from backend.core.db_manager import DatabaseManager
from backend.models.database import ConversationExchange, ConversationTurn
from backend.services.conversation_runtime import ConversationRuntime
from backend.services.retention import purge_turns


@pytest.mark.asyncio
async def test_full_history_survives_restart_and_concurrent_exchanges(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.services.conversation_state.update_state", lambda *args: None)
    path = str(tmp_path / "history.db")
    manager = DatabaseManager(path)

    async def exchange(n):
        with manager.get_session() as db:
            runtime = ConversationRuntime(db)
            runtime._build_context = AsyncMock(return_value="")

            async def respond(text, context):
                await asyncio.sleep(0)
                return "reply:" + text

            runtime._get_model_response = respond
            return await runtime.handle_conversation(str(n) + "x" * 2500)

    results = await asyncio.gather(*(exchange(n) for n in range(4)))
    manager.engine.dispose()
    restarted = DatabaseManager(path)
    with restarted.get_session() as db:
        rows = db.query(ConversationExchange).all()
        assert len(rows) == 4
        assert {r.id for r in rows} == {r["conversation_id"] for r in results}
        assert all(r.assistant_text == "reply:" + r.user_text for r in rows)
        assert all(len(r.user_text) > 2500 for r in rows)
    restarted.engine.dispose()


@pytest.mark.asyncio
async def test_persistence_failure_is_not_reported_as_success(test_db, monkeypatch):
    runtime = ConversationRuntime(test_db)
    runtime._build_context = AsyncMock(return_value="")
    runtime._get_model_response = AsyncMock(return_value="answer")
    monkeypatch.setattr(test_db, "commit", lambda: (_ for _ in ()).throw(RuntimeError("disk failure")))
    with pytest.raises(RuntimeError, match="Conversation persistence failed"):
        await runtime.handle_conversation("hello")
    assert test_db.query(ConversationExchange).count() == 0
    assert test_db.query(ConversationTurn).count() == 0


def test_history_api_excludes_legacy_and_requires_auth(test_client, test_db):
    test_db.add(ConversationTurn(session_id="old", role="user", summary="truncated"))
    test_db.add(ConversationExchange(id="conv-1", session_id="s", user_text="u" * 600,
                                     assistant_text="a" * 3000))
    test_db.commit()
    response = test_client.get("/api/v1/conversations")
    assert response.status_code == 200
    rows = response.json()["conversations"]
    assert len(rows) == 1 and rows[0]["id"] == "conv-1"
    assert rows[0]["user_text"] == "u" * 600
    assert rows[0]["assistant_text"] == "a" * 3000
    assert test_client.get("/api/v1/conversations", headers={"X-Control-Token": "wrong"}).status_code in (401, 403)
    assert test_client.get("/api/v1/conversations?limit=0").status_code == 422


def test_cursor_paginates_ties_and_new_insertions(test_client, test_db):
    timestamp = datetime(2020, 1, 2)
    for n in range(5):
        test_db.add(ConversationExchange(id=f"conv-{n}", session_id="s", user_text=str(n),
                                         assistant_text=str(n), created_at=timestamp))
    test_db.commit()
    first = test_client.get("/api/v1/conversations?limit=2").json()
    assert [row["id"] for row in first["conversations"]] == ["conv-4", "conv-3"]
    test_db.add(ConversationExchange(id="new", session_id="s", user_text="n", assistant_text="n",
                                     created_at=timestamp + timedelta(seconds=1)))
    test_db.commit()
    second = test_client.get("/api/v1/conversations", params={"limit": 2, "cursor": first["next_cursor"]}).json()
    third = test_client.get("/api/v1/conversations", params={"limit": 2, "cursor": second["next_cursor"]}).json()
    assert [row["id"] for row in second["conversations"] + third["conversations"]] == ["conv-2", "conv-1", "conv-0"]
    assert third["next_cursor"] is None
    assert test_client.get("/api/v1/conversations?cursor=invalid!").status_code == 400


@pytest.mark.asyncio
async def test_persistence_failure_redacts_sql_parameters(test_db, monkeypatch, caplog):
    from sqlalchemy.exc import StatementError

    runtime = ConversationRuntime(test_db)
    runtime._build_context = AsyncMock(return_value="")
    runtime._get_model_response = AsyncMock(return_value="PRIVATE_RESPONSE")
    def fail():
        raise StatementError("failure", "INSERT", {"text": "PRIVATE_INPUT PRIVATE_RESPONSE"}, RuntimeError("failure"))
    monkeypatch.setattr(test_db, "commit", fail)
    with caplog.at_level(logging.ERROR):
        with pytest.raises(RuntimeError) as error:
            await runtime.handle_conversation("PRIVATE_INPUT")
    assert "PRIVATE_INPUT" not in str(error.value) + caplog.text
    assert "PRIVATE_RESPONSE" not in str(error.value) + caplog.text


def test_http_persistence_error_does_not_disclose_parameters(test_client, test_db, monkeypatch, caplog):
    from sqlalchemy.exc import StatementError
    monkeypatch.setattr(ConversationRuntime, "_build_context", AsyncMock(return_value=""))
    monkeypatch.setattr(ConversationRuntime, "_get_model_response", AsyncMock(return_value="PRIVATE_RESPONSE"))
    def fail():
        raise StatementError("failed", "INSERT", {"text": "PRIVATE_INPUT PRIVATE_RESPONSE"}, RuntimeError())
    monkeypatch.setattr(test_db, "commit", fail)
    with caplog.at_level(logging.ERROR):
        response = test_client.post("/api/v1/tasks", json={"original_request": "Hey PRIVATE_INPUT, talk to me."})
    assert response.status_code == 500
    assert "PRIVATE_INPUT" not in response.text + caplog.text
    assert "PRIVATE_RESPONSE" not in response.text + caplog.text


def test_existing_database_adds_exchange_table(tmp_path):
    path = str(tmp_path / "existing.db")
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE conversation_turns (id INTEGER PRIMARY KEY, session_id VARCHAR NOT NULL, task_id VARCHAR, role VARCHAR, summary TEXT NOT NULL, created_at DATETIME)")
        db.execute("INSERT INTO conversation_turns VALUES (1, 'old', NULL, 'user', 'legacy summary', '2020-01-01')")
    manager = DatabaseManager(path)
    with manager.get_session() as db:
        assert db.query(ConversationTurn).one().summary == "legacy summary"
        assert db.query(ConversationExchange).count() == 0
        db.add(ConversationExchange(id="new", session_id="s", user_text="full", assistant_text="answer"))
    manager.engine.dispose()
    manager = DatabaseManager(path)
    with manager.get_session() as db:
        assert db.query(ConversationExchange).one().user_text == "full"
    manager.engine.dispose()


def test_retention_removes_full_exchange(test_db):
    now = datetime.utcnow()
    test_db.add_all([
        ConversationExchange(id="old", session_id="s", user_text="PRIVATE_USER", assistant_text="PRIVATE_ASSISTANT", created_at=now - timedelta(days=91)),
        ConversationExchange(id="new", session_id="s", user_text="new", assistant_text="new", created_at=now),
    ])
    test_db.commit()
    purge_turns(test_db, days=90, now=now)
    assert [row.id for row in test_db.query(ConversationExchange).all()] == ["new"]


def test_manual_purge_endpoint_removes_only_expired_exchanges(test_client, test_db):
    now = datetime.utcnow()
    test_db.add_all([
        ConversationExchange(id="old", session_id="s", user_text="PRIVATE_USER", assistant_text="PRIVATE_ASSISTANT", created_at=now - timedelta(days=91)),
        ConversationExchange(id="new", session_id="s", user_text="new", assistant_text="new", created_at=now),
    ])
    test_db.commit()
    response = test_client.post("/api/v1/retention/purge", json={"days": 90})
    assert response.status_code == 200
    assert response.json()["purged_turns"] == 0
    assert response.json()["purged_exchanges"] == 1
    assert response.json()["receipts"] == []
    assert "PRIVATE_USER" not in response.text
    assert "PRIVATE_ASSISTANT" not in response.text
    assert [row.id for row in test_db.query(ConversationExchange).all()] == ["new"]
    assert "PRIVATE_USER" not in test_client.get("/api/v1/retention/receipts").text
    assert "PRIVATE_ASSISTANT" not in test_client.get("/api/v1/retention/receipts").text


def test_manual_purge_reports_summary_and_exchange_counts_separately(test_client, test_db):
    old = datetime.utcnow() - timedelta(days=91)
    test_db.add(ConversationTurn(session_id="s", role="user", summary="summary", created_at=old))
    test_db.add(ConversationExchange(id="old", session_id="s", user_text="u", assistant_text="a", created_at=old))
    test_db.commit()
    result = test_client.post("/api/v1/retention/purge", json={"days": 90}).json()
    assert result["purged_turns"] == 1
    assert result["purged_exchanges"] == 1
    assert result["purged_count"] == 0
