"""Scale (>500) coverage for the read-only USER memory list.

Covers temp-DB page boundaries beyond the historical 500-row cap, that older
ACTIVE rows remain reachable, and the stable database-level listing path.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.core.auth import LocalControlBoundaryMiddleware, control_origins
from backend.models.database import Base, MemoryEntry, get_db
from backend.services.memory_service import MemoryService
from fastapi import FastAPI
from fastapi.testclient import TestClient


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
        token_provider=lambda: "memory-scale-token",
        allowed_origins=control_origins(False),
    )
    test_app.include_router(router, prefix="/api/v1")

    def override_get_db():
        yield db_session

    test_app.dependency_overrides[get_db] = override_get_db
    return TestClient(test_app, headers={"X-Control-Token": "memory-scale-token"})


def _seed_many(db_session, n=600):
    rows = []
    for i in range(n):
        rows.append(MemoryEntry(
            key=f"bulk_{i:04d}",
            value=f"value {i}",
            kind="FACT",
            scope="USER",
            category="user_profile",
            source_type="USER_EXPLICIT",
            verification_status="UNVERIFIED",
            status="ACTIVE",
        ))
    # Include one distinctive older entry beyond the 500 boundary.
    rows.append(MemoryEntry(
        key="old_needle",
        value="older matching memory past 500",
        kind="PREFERENCE",
        scope="USER",
        category="user_preference",
        source_type="USER_EXPLICIT",
        verification_status="UNVERIFIED",
        status="ACTIVE",
    ))
    db_session.add_all(rows)
    db_session.commit()


def test_service_list_beyond_500_page_boundaries(db_session):
    _seed_many(db_session, n=600)
    svc = MemoryService(db_session)

    page_first = svc.list_items(limit=100, offset=0)
    page_edge = svc.list_items(limit=50, offset=500)
    page_last = svc.list_items(limit=100, offset=550)

    assert len(page_first) == 100
    assert len(page_edge) == 50
    # 601 total rows: offset 550 leaves 51.
    assert len(page_last) == 51

    first_ids = {m.id for m in page_first}
    edge_ids = {m.id for m in page_edge}
    last_ids = {m.id for m in page_last}
    assert first_ids.isdisjoint(edge_ids)
    assert first_ids.isdisjoint(last_ids)
    assert edge_ids.isdisjoint(last_ids)

    total = svc.count_items()
    assert total == 601
    # Older distinctive row is present somewhere in the full listing.
    all_items = []
    off = 0
    while True:
        chunk = svc.list_items(limit=200, offset=off)
        if not chunk:
            break
        all_items.extend(chunk)
        off += len(chunk)
    assert any(m.key == "old_needle" for m in all_items)


def test_search_finds_old_matching_entry(db_session):
    _seed_many(db_session, n=600)
    svc = MemoryService(db_session)

    found = svc.list_items(query="needle", limit=25, offset=0)
    assert any(m.key == "old_needle" for m in found)


def test_route_pagination_beyond_five_hundred(client, db_session):
    _seed_many(db_session, n=600)

    r1 = client.get("/api/v1/memory/items", params={"limit": 100, "offset": 0})
    r2 = client.get("/api/v1/memory/items", params={"limit": 50, "offset": 500})
    r3 = client.get("/api/v1/memory/items", params={"limit": 100, "offset": 550})

    assert r1.status_code == 200 and len(r1.json()["items"]) == 100
    assert r2.status_code == 200 and len(r2.json()["items"]) == 50
    assert r3.status_code == 200 and len(r3.json()["items"]) == 51

    ids1 = {x["id"] for x in r1.json()["items"]}
    ids2 = {x["id"] for x in r2.json()["items"]}
    ids3 = {x["id"] for x in r3.json()["items"]}
    assert ids1.isdisjoint(ids2)
    assert ids1.isdisjoint(ids3)
    assert ids2.isdisjoint(ids3)


def test_old_entry_found_via_query_route(client, db_session):
    _seed_many(db_session, n=600)
    resp = client.get("/api/v1/memory/items", params={"query": "needle", "limit": 25, "offset": 0})
    assert resp.status_code == 200
    assert any(r["key"] == "old_needle" for r in resp.json()["items"])


def test_retrieve_ranking_still_runs_with_null_confidence(db_session):
    from backend.models.database import MemoryEntry
    from backend.services.memory_service import MemoryService

    svc = MemoryService(db_session)
    a = svc.create(key="verified_fact", value="matches query", kind="FACT",
                   source_type="TOOL_EVIDENCE")
    b = svc.create(key="unassessed_fact", value="matches query", kind="FACT",
                   source_type="USER_CONVERSATION")
    for row_id in (a.id, b.id):
        row = db_session.query(MemoryEntry).filter_by(id=row_id).one()
        row.confidence = None
    db_session.commit()

    results = svc.retrieve("query")
    assert len(results) >= 1
    assert a.id in {r.id for r in results}


def _seed_count(db_session, n):
    rows = [
        MemoryEntry(
            key=f"contract_{i:04d}",
            value=f"value {i}",
            kind="FACT",
            scope="USER",
            category="user_profile",
            source_type="USER_EXPLICIT",
            verification_status="UNVERIFIED",
            status="ACTIVE",
        )
        for i in range(n)
    ]
    db_session.add_all(rows)
    db_session.commit()


@pytest.mark.parametrize("total,expected_has_more", [(99, False), (100, False), (101, True)])
def test_route_has_more_contract_totals(client, db_session, total, expected_has_more):
    _seed_count(db_session, total)

    resp = client.get("/api/v1/memory/items", params={"limit": 100, "offset": 0})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == min(total, 100)
    assert body["has_more"] is expected_has_more


def test_route_final_page_contract_601(client, db_session):
    _seed_count(db_session, 601)

    # Final single-row page.
    resp = client.get("/api/v1/memory/items", params={"limit": 100, "offset": 600})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["has_more"] is False

    # One item remains hidden behind a 100-row page at offset 500.
    resp = client.get("/api/v1/memory/items", params={"limit": 100, "offset": 500})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 100
    assert body["has_more"] is True
