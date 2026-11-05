"""Focused offline tests for the read-only user memory list endpoint.

Contract:
- GET /api/v1/memory/items reuses MemoryService retrieval semantics: only
  ACTIVE USER-scoped memories are returned, so FORGOTTEN, SUPERSEDED,
  INVALIDATED and ARCHIVED entries are excluded. Aliases were intentionally
  removed.
- The endpoint is read-only: no POST/PUT/PATCH/DELETE route exists.
- Pagination is bounded and applied server-side.
- Task/session-scoped observations are not leaked through the default
  (USER scope) list.
"""

import pytest
from fastapi import FastAPI
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.core.auth import LocalControlBoundaryMiddleware, control_origins
from backend.models.database import Base, MemoryEntry, get_db
from backend.services.memory_service import MemoryService


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


@pytest.fixture
def client(db_session):
    from backend.api.routes import router

    test_app = FastAPI()
    test_app.add_middleware(
        LocalControlBoundaryMiddleware,
        token_provider=lambda: "memory-items-token",
        allowed_origins=control_origins(False),
    )
    test_app.include_router(router, prefix="/api/v1")

    def override_get_db():
        yield db_session

    test_app.dependency_overrides[get_db] = override_get_db
    return TestClient(test_app, headers={"X-Control-Token": "memory-items-token"})


def _seed_user_memories(db_session):
    svc = MemoryService(db_session)
    a = svc.create(key="pref_color", value="blue", kind="PREFERENCE",
                   source_type="USER_EXPLICIT", scope="USER")
    b = svc.create(key="fact_city", value="Nairobi", kind="FACT",
                   source_type="TOOL_EVIDENCE", scope="USER")
    # Forgotten entry must never surface.
    dead = svc.create(key="dead_pref", value="coffee", kind="PREFERENCE",
                      source_type="USER_EXPLICIT", scope="USER")
    svc.forget(dead.id)
    # Superseded entry must never surface.
    old = svc.create(key="editor_old", value="Notepad++", kind="FACT",
                     source_type="USER_CONVERSATION", scope="USER")
    svc.update(old.id, new_value="VS Code", source_type="USER_EXPLICIT")
    # Archived entry (manually) must not surface.
    archived = MemoryEntry(key="archived_one", value="old", status="ARCHIVED",
                          kind="FACT", scope="USER", category="fact",
                          source_type="USER_EXPLICIT",
                          verification_status="UNVERIFIED")
    db_session.add(archived)
    # Task-scoped observation must not leak by default.
    svc.create(key="task_tmp", value="task-only", kind="EPISODIC",
               source_type="SYSTEM_OBSERVATION", scope="TASK",
               scope_ref="task-1")
    db_session.commit()
    return a, b


class TestReadOnlyMemoryList:
    def test_excludes_forgotten_superseded_archived(self, client, db_session):
        _seed_user_memories(db_session)

        resp = client.get("/api/v1/memory/items")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, dict)
        assert "items" in body and "has_more" in body
        rows = body["items"]
        assert isinstance(rows, list)
        keys = {r["key"] for r in rows}
        assert "pref_color" in keys
        assert "fact_city" in keys
        assert "dead_pref" not in keys
        assert "Notepad++" not in {r["value"] for r in rows}
        assert "archived_one" not in keys
        assert "task_tmp" not in keys
        for r in rows:
            assert "id" in r and "key" in r and "value" in r

    def test_preserves_active_entries_with_status_active(self, client, db_session):
        from backend.models.database import MemoryEntry
        _seed_user_memories(db_session)
        body = client.get("/api/v1/memory/items").json()
        rows = body["items"]
        assert isinstance(body["has_more"], bool)
        assert rows
        for r in rows:
            assert "key" in r and r["scope"] == "USER"

    def test_pagination_limit_offset(self, client, db_session):
        svc = MemoryService(db_session)
        for i in range(5):
            svc.create(key=f"bulk_{i}", value=f"val {i}", kind="FACT",
                       source_type="USER_EXPLICIT", scope="USER")
        db_session.commit()

        page1 = client.get("/api/v1/memory/items", params={"limit": 2, "offset": 0}).json()["items"]
        page2 = client.get("/api/v1/memory/items", params={"limit": 2, "offset": 2}).json()["items"]
        assert len(page1) == 2
        assert len(page2) == 2
        assert {r["id"] for r in page1}.isdisjoint({r["id"] for r in page2})

    def test_invalid_bounds_rejected(self, client, db_session):
        resp = client.get("/api/v1/memory/items", params={"limit": 0})
        assert resp.status_code == 422
        resp = client.get("/api/v1/memory/items", params={"limit": 101})
        assert resp.status_code == 422
        resp = client.get("/api/v1/memory/items", params={"offset": -1})
        assert resp.status_code == 422
        resp = client.get("/api/v1/memory/items", params={"offset": 10001})
        assert resp.status_code == 422

    def test_invalid_kind_rejected(self, client, db_session):
        resp = client.get("/api/v1/memory/items", params={"kind": "NOPE"})
        assert resp.status_code == 422
        resp = client.get("/api/v1/memory/items", params={"kinds": ["NOPE"]})
        assert resp.status_code == 422

    def test_removed_aliases_are_not_routes(self, client, db_session):
        for path in ("/api/v1/memory/entries", "/api/v1/memories"):
            resp = client.get(path)
            assert resp.status_code == 404

    def test_query_and_kind_filters(self, client, db_session):
        svc = MemoryService(db_session)
        svc.create(key="pref_tea", value="likes tea", kind="PREFERENCE",
                   source_type="USER_EXPLICIT", scope="USER")
        svc.create(key="fact_sun", value="sun is hot", kind="FACT",
                   source_type="TOOL_EVIDENCE", scope="USER")
        db_session.commit()

        by_kind = client.get("/api/v1/memory/items", params={"kind": "PREFERENCE"}).json()["items"]
        assert all(r["kind"] == "PREFERENCE" for r in by_kind)
        by_query = client.get("/api/v1/memory/items", params={"query": "tea"}).json()["items"]
        assert any("tea" in r["value"] for r in by_query)

    def test_no_mutation_routes(self, client, db_session):
        for method, path in (
            ("post", "/api/v1/memory/items"),
            ("put", "/api/v1/memory/items"),
            ("patch", "/api/v1/memory/items"),
            ("delete", "/api/v1/memory/items"),
        ):
            fn = getattr(client, method)
            resp = fn(path)
            assert resp.status_code in (404, 405), (method, resp.status_code)


def test_memory_service_retrieval_excludes_non_active():
    """Direct service-level check: retrieval never serves forgotten entries."""
    import sqlite3
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        svc = MemoryService(session)
        e = svc.create(key="k", value="v", kind="FACT", source_type="USER_EXPLICIT")
        svc.forget(e.id)
        assert svc.retrieve("") == []
        assert svc.retrieve("", scopes=["USER"]) == []
    finally:
        session.close()
