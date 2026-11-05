"""Real HTTP/offline lifecycle regression for post-persistence semantic indexing."""

import json
import os
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.core.session import set_current_session_id
from backend.core.storage import data_dir as _ignored_data_dir  # noqa
from backend.main import app


class ScriptedHttpModelClient:
    _candidate_id = None
    _index_calls = 0

    def __init__(self, *a, **k):
        pass

    async def chat(self, *a, **k):
        messages = a[0] if a else k.get("messages", [])
        text = " ".join(m.get("content", "") for m in (messages or []))
        if text.lower().startswith("you are sythion's separately budgeted semantic discussion indexer"):
            ScriptedHttpModelClient._index_calls += 1
            return json.dumps(
                {
                    "status": "insufficient",
                    "proposals": [],
                    "clarification": "Need more",
                    "reason": "sparse",
                }
            )
        if text.lower().startswith("you identify which grounded discussions"):
            return json.dumps(
                {
                    "outcome": "selected",
                    "selected_discussion_ids": [],
                    "clarification": None,
                }
            )
        return json.dumps(
            {
                "assistant_text": "Normal HTTP completion.",
                "commands": [],
            }
        )


@pytest.fixture()
def http_session_environment(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("SYTHION_DATA_DIR", str(data_dir))
    monkeypatch.delenv("Sythion (Synth)_DATA_DIR", raising=False)
    # The real application lifespan attempts a legacy-profile migration.  This
    # test must never inspect or copy the user's profile into its temporary DB.
    monkeypatch.setattr("backend.core.storage.migrate_legacy_data_dir", lambda: False)
    from backend.core.config import settings

    monkeypatch.setattr(settings, "database_path", str(data_dir / "sythion.db"))
    sid = f"http-index-session-{uuid.uuid4().hex[:8]}"
    set_current_session_id(sid)

    import backend.services.conversation_runtime as crt

    monkeypatch.setattr(crt, "ModelClient", ScriptedHttpModelClient)

    from backend.api.dependencies import set_grounded_discussion_provider
    from backend.services.partial_discussion_recall_linkage import (
        PartialDiscussionRecallLinkageService,
    )

    set_grounded_discussion_provider(
        lambda db: PartialDiscussionRecallLinkageService(db)
    )
    try:
        yield sid, data_dir
    finally:
        from backend.api.dependencies import reset_grounded_discussion_provider

        reset_grounded_discussion_provider()


def test_http_empty_session_bootstrap_indexes_after_durable_receipt(http_session_environment):
    sid, data_dir = http_session_environment
    ScriptedHttpModelClient._index_calls = 0

    from backend.models.database import get_db, get_db_manager
    from backend.services.grounded_discussions import Base as GroundedBase

    with TestClient(app) as client:
        db_manager = get_db_manager()
        engine = db_manager.engine if hasattr(db_manager, "engine") else None
        if engine is None:
            # Fall back to the first connection's bound engine.
            with db_manager.get_session() as _s:
                engine = _s.get_bind()
        assert Path(engine.url.database).resolve().is_relative_to(data_dir.resolve())
        GroundedBase.metadata.create_all(engine)
        from backend.services.grounded_discussion_index import Base as IndexBase

        IndexBase.metadata.create_all(engine)
        print("HTTP,engine_url=", engine.url)
        print("HTTP,grounded_tables=", sorted(GroundedBase.metadata.tables.keys()))

        client_request_id = f"http-bootstrap-{uuid.uuid4()}"
        from backend.main import control_token as current_control_token

        headers = {"X-Control-Token": current_control_token}
        response = client.post(
            "/api/v1/assistant/turns",
            headers=headers,
            json={"client_request_id": client_request_id, "text": "bootstrap turn"},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["status"] == "completed"
        print("HTTP,index_calls=", ScriptedHttpModelClient._index_calls)
        print("HTTP,response_payload=", payload)

        with db_manager.get_session() as db:
            from backend.models.database import ConversationExchange, ConversationTurn
            from backend.services.grounded_discussion_index import (
                DiscussionIndexStateRow,
            )

            exchanges = db.query(ConversationExchange).all()
            print("HTTP,all_exchanges=", [(e.id, e.session_id) for e in exchanges])
            assert exchanges, "no persisted canonical exchange"
            index_rows = db.query(DiscussionIndexStateRow).filter_by(
                session_id=sid
            ).all()
            print("HTTP,exchanges=", [(e.id, e.user_text[:20]) for e in exchanges])
            print("HTTP,index_rows=", [(r.status, r.exchange_id, r.user_turn_id, r.assistant_turn_id) for r in index_rows])
            print("HTTP,index_calls_final=", ScriptedHttpModelClient._index_calls)

            assert ScriptedHttpModelClient._index_calls == 1, (
                "background index did not run after durable receipt; runtime ordering "
                "skips uncommitted receipt reads and no retry was performed"
            )
            assert len(index_rows) == 1, "expected one canonical index state row"
            assert index_rows[0].status == "insufficient"
