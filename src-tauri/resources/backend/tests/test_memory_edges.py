"""
Tests for memory-edge lifecycle (decay + owner review surface).

Contract: edges decay in weight when untouched and surface as "stale" for
explicit owner review -- decay NEVER auto-deletes or removes. Only the review
endpoints (reinforce / remove) transition an edge, mirroring the
proposed_skills.status review pattern.
"""

import sqlite3
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect

from backend.core.db_manager import DatabaseManager
from backend.models.database import set_db_manager, MemoryEdge, MemoryEntry, get_db
from backend.services.memory_consolidation import (
    EDGE_MIN_WEIGHT,
    EDGE_STALE_WEIGHT,
    apply_edge_decay,
    get_edge,
    list_edges,
    reinforce_edge,
    remove_edge,
)


@pytest.fixture
def edge_db(tmp_path):
    db_path = str(tmp_path / "test_edges.db")
    db_mgr = DatabaseManager(db_path)
    set_db_manager(db_mgr)
    with db_mgr.get_session() as session:
        yield session


def _add_edge(session, source="user_name", relation="prefers", target="x", weight=1.0, updated_at=None):
    edge = MemoryEdge(
        source_key=source,
        relation=relation,
        target_key=target,
        weight=weight,
    )
    if updated_at is not None:
        edge.created_at = updated_at
        edge.updated_at = updated_at
    session.add(edge)
    session.commit()
    return edge


def _now():
    return datetime.utcnow()


# --- decay ------------------------------------------------------------------

def test_fresh_edge_not_decayed(edge_db):
    edge = _add_edge(edge_db, updated_at=_now())

    stats = apply_edge_decay(edge_db, now=_now())

    assert stats == {"decayed": 0, "marked_stale": 0}
    row = get_edge(edge_db, edge.id)
    assert row.weight == 1.0
    assert row.status == "active"


def test_untouched_edge_decays_and_turns_stale(edge_db):
    old = _now() - timedelta(days=14)
    edge = _add_edge(edge_db, updated_at=old)

    stats = apply_edge_decay(edge_db, now=_now())

    assert stats["decayed"] == 1
    assert stats["marked_stale"] == 1
    row = get_edge(edge_db, edge.id)
    assert row.weight == 0.25  # 14 days -> 2 intervals -> 1.0 * 0.5**2
    assert row.status == "stale"
    # The decay pass must NOT bump updated_at (that would reset the decay clock).
    assert row.updated_at == old


def test_weight_floors_at_min(edge_db):
    old = _now() - timedelta(days=60)
    edge = _add_edge(edge_db, weight=1.0, updated_at=old)

    apply_edge_decay(edge_db, now=_now())

    row = get_edge(edge_db, edge.id)
    assert row.weight == EDGE_MIN_WEIGHT
    assert row.weight > 0


def test_decay_never_deletes(edge_db):
    old = _now() - timedelta(days=90)
    _add_edge(edge_db, updated_at=old)
    _add_edge(edge_db, source="a", relation="rel", target="b", updated_at=old)

    apply_edge_decay(edge_db, now=_now())

    assert edge_db.query(MemoryEdge).count() == 2  # both still present
    assert all(e.status == "stale" for e in edge_db.query(MemoryEdge).all())


def test_removed_edge_left_untouched_by_decay(edge_db):
    old = _now() - timedelta(days=30)
    edge = _add_edge(edge_db, updated_at=old)
    edge.status = "removed"
    edge_db.commit()

    stats = apply_edge_decay(edge_db, now=_now())

    assert stats == {"decayed": 0, "marked_stale": 0}
    row = get_edge(edge_db, edge.id)
    assert row.status == "removed"
    assert row.weight == 1.0


def test_exactly_one_interval_marks_stale(edge_db):
    old = _now() - timedelta(days=7)
    edge = _add_edge(edge_db, updated_at=old)

    apply_edge_decay(edge_db, now=_now())

    row = get_edge(edge_db, edge.id)
    assert row.weight == EDGE_STALE_WEIGHT
    assert row.status == "stale"


# --- review surface ---------------------------------------------------------

def test_list_edges_filters_by_status(edge_db):
    stale = _add_edge(edge_db, source="a", relation="rel", target="b", weight=0.2)
    stale.status = "stale"
    active = _add_edge(edge_db, source="c", relation="rel", target="d")
    edge_db.commit()

    stale_list = list_edges(edge_db, status="stale")
    assert [e["id"] for e in stale_list] == [stale.id]
    assert stale_list[0]["source_key"] == "a"
    assert "updated_at" in stale_list[0]

    all_edges = list_edges(edge_db)
    assert len(all_edges) == 2
    # Ordered by weight ascending: weakest first.
    assert all_edges[0]["id"] == stale.id
    assert all_edges[1]["id"] == active.id


def test_reinforce_restores_stale_edge(edge_db):
    old = _now() - timedelta(days=14)
    edge = _add_edge(edge_db, updated_at=old)
    apply_edge_decay(edge_db, now=_now())

    result = reinforce_edge(edge_db, edge.id)

    assert result["status"] == "active"
    assert result["weight"] == 1.0
    row = get_edge(edge_db, edge.id)
    assert row.status == "active"
    assert row.updated_at > old  # reinforce is a real touch


def test_reinforce_non_stale_raises(edge_db):
    edge = _add_edge(edge_db)
    with pytest.raises(ValueError, match="only stale edges"):
        reinforce_edge(edge_db, edge.id)


def test_reinforce_missing_raises(edge_db):
    with pytest.raises(ValueError, match="not found"):
        reinforce_edge(edge_db, 999999)


def test_remove_marks_stale_edge_removed(edge_db):
    old = _now() - timedelta(days=14)
    edge = _add_edge(edge_db, updated_at=old)
    apply_edge_decay(edge_db, now=_now())

    result = remove_edge(edge_db, edge.id)

    assert result["status"] == "removed"
    # Row retained on record (like rejected proposals), not hard-deleted.
    assert edge_db.query(MemoryEdge).filter_by(id=edge.id).count() == 1


def test_remove_non_stale_raises(edge_db):
    edge = _add_edge(edge_db)
    with pytest.raises(ValueError, match="only stale edges"):
        remove_edge(edge_db, edge.id)


# --- migration --------------------------------------------------------------

def test_migration_adds_status_column_to_existing_table(tmp_path):
    db_path = str(tmp_path / "old_edge_db.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE memory_edges ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "source_key VARCHAR NOT NULL, relation VARCHAR NOT NULL, "
        "target_key VARCHAR NOT NULL, weight FLOAT, source_task_id VARCHAR, "
        "created_at DATETIME, updated_at DATETIME)"
    )
    conn.commit()
    conn.close()

    mgr = DatabaseManager(db_path)

    cols = {c["name"] for c in inspect(mgr.engine).get_columns("memory_edges")}
    assert "status" in cols


def test_migration_preserves_existing_edges(tmp_path):
    db_path = str(tmp_path / "old_edge_db2.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE memory_edges ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "source_key VARCHAR NOT NULL, relation VARCHAR NOT NULL, "
        "target_key VARCHAR NOT NULL, weight FLOAT, source_task_id VARCHAR, "
        "created_at DATETIME, updated_at DATETIME)"
    )
    conn.execute(
        "INSERT INTO memory_edges (source_key, relation, target_key, weight) "
        "VALUES ('user_name', 'prefers', 'preferred_editor', 1.0)"
    )
    conn.commit()
    conn.close()

    DatabaseManager(db_path)

    # Re-open via a fresh manager only to inspect; existing row must survive.
    db_mgr = DatabaseManager(db_path)
    with db_mgr.get_session() as session:
        row = session.query(MemoryEdge).first()
        assert row is not None
        assert row.source_key == "user_name"
        assert row.status == "active"  # default fills the migrated column


# --- tools integration ------------------------------------------------------

def test_memory_link_relink_clears_stale_status(edge_db):
    from backend.api.schemas import MemoryLinkInput
    from backend.tools.memory_tools import MemoryLinkTool

    old = _now() - timedelta(days=14)
    edge = _add_edge(edge_db, source="user_name", relation="prefers", target="preferred_editor", updated_at=old)
    apply_edge_decay(edge_db, now=_now())
    assert get_edge(edge_db, edge.id).status == "stale"

    out = edge_db_async_run(MemoryLinkTool(), MemoryLinkInput(
        source_key="user_name", relation="prefers", target_key="preferred_editor", weight=1.0,
    ))

    assert out.success
    edge_db.expire_all()  # the tool wrote via its own session; drop stale identity
    row = get_edge(edge_db, edge.id)
    assert row.status == "active"
    assert row.weight == 1.0


def test_recall_excludes_removed_edges(edge_db):
    import asyncio

    from backend.api.schemas import MemoryRecallRelatedInput
    from backend.models.database import MemoryEntry
    from backend.tools.memory_tools import MemoryRecallRelatedTool

    edge_db.add(MemoryEntry(key="user_name", value="Alice"))
    edge_db.add(MemoryEntry(key="preferred_editor", value="VS Code"))
    edge_db.commit()

    live = MemoryEdge(source_key="user_name", relation="prefers", target_key="preferred_editor")
    removed = MemoryEdge(
        source_key="user_name", relation="used_to_prefer", target_key="preferred_editor",
        status="removed",
    )
    edge_db.add(live)
    edge_db.add(removed)
    edge_db.commit()

    tool = MemoryRecallRelatedTool()
    out = asyncio.run(tool.execute(MemoryRecallRelatedInput(key="user_name", hops=1)))

    assert out.success
    links = out.data.get("links", [])
    rels = [link["relation"] for link in links]
    assert "prefers" in rels
    assert "used_to_prefer" not in rels


def edge_db_async_run(tool, input_data):
    import asyncio
    return asyncio.run(tool.execute(input_data))


class TestEdgeReviewApi:
    @pytest.fixture
    def api_client(self, edge_db):
        from backend.main import app
        import backend.main as backend_main

        def override_get_db():
            yield edge_db

        app.dependency_overrides[get_db] = override_get_db
        previous_token = backend_main.control_token
        backend_main.control_token = "edge-review-token"
        try:
            yield TestClient(
                app,
                headers={
                    "Origin": "tauri://localhost",
                    "X-Control-Token": "edge-review-token",
                },
            )
        finally:
            backend_main.control_token = previous_token
            app.dependency_overrides.clear()

    def _stale_edge(self, edge_db):
        old = _now() - timedelta(days=14)
        edge = _add_edge(edge_db, source="a", relation="rel", target="b", updated_at=old)
        apply_edge_decay(edge_db, now=_now())
        assert edge.status == "stale"
        return edge

    def test_list_stale_edges_endpoint(self, edge_db, api_client):
        edge = self._stale_edge(edge_db)
        _add_edge(edge_db, source="c", relation="rel", target="d")

        resp = api_client.get("/api/v1/memory/edges", params={"status": "stale"})

        assert resp.status_code == 200
        rows = resp.json()
        assert [r["id"] for r in rows] == [edge.id]
        assert rows[0]["status"] == "stale"

    def test_reinforce_endpoint(self, edge_db, api_client):
        edge = self._stale_edge(edge_db)

        resp = api_client.post(f"/api/v1/memory/edges/{edge.id}/reinforce")

        assert resp.status_code == 200
        assert resp.json()["status"] == "active"
        assert get_edge(edge_db, edge.id).status == "active"

    def test_reinforce_missing_edge_is_404(self, edge_db, api_client):
        resp = api_client.post("/api/v1/memory/edges/999999/reinforce")
        assert resp.status_code == 404

    def test_reinforce_active_edge_is_422(self, edge_db, api_client):
        edge = _add_edge(edge_db)
        resp = api_client.post(f"/api/v1/memory/edges/{edge.id}/reinforce")
        assert resp.status_code == 422

    def test_remove_endpoint(self, edge_db, api_client):
        edge = self._stale_edge(edge_db)

        resp = api_client.post(f"/api/v1/memory/edges/{edge.id}/remove")

        assert resp.status_code == 200
        assert resp.json()["status"] == "removed"
        # Row is retained, not hard-deleted.
        assert edge_db.query(MemoryEdge).filter_by(id=edge.id).count() == 1

    def test_remove_active_edge_is_422(self, edge_db, api_client):
        edge = _add_edge(edge_db)
        resp = api_client.post(f"/api/v1/memory/edges/{edge.id}/remove")
        assert resp.status_code == 422
