"""Synthetic DB tests for MemoryAccessContext read filtering."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models.database import Base, MemoryEntry
from backend.services.memory_service import MemoryAccessContext, MemoryService


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
def svc(db_session):
    return MemoryService(db_session)


def _seed(svc):
    user = svc.create(key="user_pref", value="tea", kind="PREFERENCE",
                      source_type="USER_EXPLICIT", scope="USER")
    global_ = svc.create(key="global_fact", value="sun", kind="FACT",
                         source_type="TOOL_EVIDENCE", scope="GLOBAL")
    task_match = svc.create(key="task_note", value="task hit", kind="FACT",
                           source_type="TOOL_EVIDENCE", scope="TASK",
                           scope_ref="task-1")
    task_other = svc.create(key="task_other", value="task miss", kind="FACT",
                            source_type="TOOL_EVIDENCE", scope="TASK",
                            scope_ref="task-2")
    session_match = svc.create(key="session_note", value="session hit",
                               kind="FACT", source_type="TOOL_EVIDENCE",
                               scope="SESSION", scope_ref="sess-1")
    session_other = svc.create(key="session_other", value="session miss",
                               kind="FACT", source_type="TOOL_EVIDENCE",
                               scope="SESSION", scope_ref="sess-2")
    svc_db = svc.db
    svc_db.commit()
    return {
        "user": user, "global": global_, "task_match": task_match,
        "task_other": task_other, "session_match": session_match,
        "session_other": session_other,
    }


def test_context_none_preserves_existing_scope_filter(svc, db_session):
    _seed(svc)
    ids = {m.id for m in svc.list_items(scopes=["USER"])}
    assert ids == {svc.db.query(MemoryEntry).filter_by(key="user_pref").one().id}


def test_context_filters_exact_session_and_task(svc, db_session):
    seeded = _seed(svc)
    ctx = MemoryAccessContext(session_id="sess-1", task_id="task-1")
    rows = svc.list_items(access_context=ctx)
    keys = {r.key for r in rows}
    assert "user_pref" in keys
    assert "task_note" in keys
    assert "session_note" in keys
    assert "task_other" not in keys
    assert "session_other" not in keys


def test_context_with_context_kwarg(svc, db_session):
    _seed(svc)
    ctx = MemoryAccessContext(session_id="sess-2")
    items = svc.retrieve("", context=ctx)
    assert all(m.key != "session_note" for m in items)
    assert any(m.key == "session_other" for m in items)


def test_context_and_scope_intersect(svc, db_session):
    _seed(svc)
    ctx = MemoryAccessContext(session_id="sess-1", task_id="task-1")
    items = svc.list_items(scopes=["TASK"], access_context=ctx)
    assert {i.key for i in items} == {"task_note"}


def test_count_items_context(svc, db_session):
    _seed(svc)
    ctx = MemoryAccessContext(session_id="sess-1", task_id="task-1")
    assert svc.count_items(access_context=ctx) >= 3
    no_match = MemoryAccessContext(session_id="sess-x", task_id="task-x",
                                   include_user=False, include_global=False)
    assert svc.count_items(access_context=no_match) == 0


def test_get_active_respects_context(svc, db_session):
    _seed(svc)
    ctx = MemoryAccessContext(task_id="task-2")
    assert svc.get_active("task_note", access_context=ctx) is None
    assert svc.get_active("task_other", access_context=ctx).key == "task_other"


def test_coercion_from_dict_and_duck(svc, db_session):
    _seed(svc)
    class Duck:
        session_id = "sess-1"
        task_id = "task-1"
    for value in ({"session_id": "sess-1", "task_id": "task-1"},
                  Duck()):
        items = svc.list_items(access_context=value)
        assert any(i.key == "user_pref" for i in items)


def test_access_context_helpers(svc, db_session):
    ctx = MemoryAccessContext(session_id="sess-1", task_id="task-1")
    # Build a fake entry-like
    class E:
        scope = "TASK"
        scope_ref = "task-1"
    assert ctx.allows(E())
    other = MemoryAccessContext()
    # default keeps user at least
    assert "USER" in other.visible_scopes