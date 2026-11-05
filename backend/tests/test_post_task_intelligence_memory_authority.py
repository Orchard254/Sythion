"""
Offline temp-DB tests for post_task_intelligence memory authority.

Covers:
- authority policy (USER_CONVERSATION conservative provenance, no verified minting,
  no model-direct mutation)
- duplicate key handling through MemoryService supersession
- contradiction detection/linking
- live-state rejection at the persistent-memory boundary
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.schemas import TaskState
from backend.core.db_manager import DatabaseManager
from backend.models.database import set_db_manager
from backend.core.session import set_current_session_id
from backend.models.database import Base, MemoryEdge, MemoryEntry, Task
from backend.services.memory_service import MemoryAuthorityError, MemoryService
from backend.services.post_task_intelligence import save_conversation_turn


@pytest.fixture
def db_session(tmp_path):
    db_path = str(tmp_path / "post_task_intelligence_authority.db")
    db_mgr = DatabaseManager(db_path)
    set_db_manager(db_mgr)
    with db_mgr.get_session() as session:
        yield session, db_mgr


def _task(task_id, request, result="done"):
    return Task(
        id=task_id,
        original_request=request,
        state=TaskState.COMPLETED.value,
        final_result=result,
    )


@pytest.mark.asyncio
async def test_auto_extraction_uses_conservative_user_conversation_provenance(db_session):
    session, _ = db_session
    set_current_session_id("auth_session")
    task = _task(
        "task_auth",
        "my name is Alice and I prefer VS Code",
    )
    session.add(task)
    session.commit()

    await save_conversation_turn(session, "task_auth", task)

    name = session.query(MemoryEntry).filter_by(key="user_name").one()
    editor = session.query(MemoryEntry).filter_by(key="preferred_editor").one()
    inst = session.query(MemoryEntry).filter_by(key="pref_use_dark_mode").first()

    for row in (name, editor, inst) if inst is not None else (name, editor):
        assert row.source_type == "USER_CONVERSATION"
        assert row.verification_status == "UNVERIFIED"
        assert row.status == "ACTIVE"

    assert name.kind == "FACT"
    assert name.value == "Alice"
    assert name.category == "user_profile"
    assert editor.kind == "PREFERENCE"
    assert editor.value == "VS Code"
    assert editor.category == "user_preference"

    # Conversation persistence itself remains owned by the conversation-turn path.
    assert session.query(MemoryEntry).filter_by(source_type="USER_CONVERSATION").count() >= 2
    from backend.models.database import ConversationTurn

    assert session.query(ConversationTurn).filter_by(task_id="task_auth").count() == 2


def test_memory_service_authority_rejects_model_direct_fact(db_session):
    session, _ = db_session
    svc = MemoryService(session)
    with pytest.raises(MemoryAuthorityError):
        svc.create(key="forbidden", value="x", kind="FACT", source_type="MODEL_INFERENCE")


def test_memory_service_authority_rejects_disallowed_kind_for_user_conversation(db_session):
    session, _ = db_session
    svc = MemoryService(session)
    with pytest.raises(MemoryAuthorityError):
        svc.create(key="not_allowed", value="x", kind="BELIEF", source_type="USER_CONVERSATION")


@pytest.mark.asyncio
async def test_auto_extraction_duplicate_key_supersedes_instead_of_overwriting(db_session):
    session, _ = db_session
    set_current_session_id("dup_session")

    first = _task("task_dup_1", "my name is Alice")
    session.add(first)
    session.commit()
    await save_conversation_turn(session, "task_dup_1", first)

    second = _task("task_dup_2", "my name is Bob")
    session.add(second)
    session.commit()
    await save_conversation_turn(session, "task_dup_2", second)

    active_rows = session.query(MemoryEntry).filter_by(key="user_name", status="ACTIVE").all()
    assert len(active_rows) == 1
    assert active_rows[0].value == "Bob"

    superseded_rows = session.query(MemoryEntry).filter(
        MemoryEntry.key.like("user_name:superseded:%")
    ).all()
    assert len(superseded_rows) == 1
    assert superseded_rows[0].value == "Alice"
    assert superseded_rows[0].status == "SUPERSEDED"


@pytest.mark.asyncio
async def test_contradiction_is_represented_as_edge_not_silent_overwrite(db_session):
    session, _ = db_session
    set_current_session_id("contra_session")
    svc = MemoryService(session)
    svc.create(
        key="editor_pref",
        value="Notepad++",
        kind="PREFERENCE",
        source_type="USER_EXPLICIT",
    )

    task = _task("task_contra", "i prefer VS Code")
    session.add(task)
    session.commit()
    await save_conversation_turn(session, "task_contra", task)

    newer = session.query(MemoryEntry).filter_by(key="preferred_editor", status="ACTIVE").one()
    assert newer.value == "VS Code"
    assert newer.source_type == "USER_CONVERSATION"

    older = session.query(MemoryEntry).filter_by(key="editor_pref", status="ACTIVE").one()
    assert older.value == "Notepad++"

    edges = session.query(MemoryEdge).filter_by(relation="contradicts").all()
    assert len(edges) >= 1


def test_live_state_rejection_boundary(db_session):
    session, _ = db_session
    from backend.tools.persistent_memory_tools import _rejects_live_state

    assert _rejects_live_state("current_time", "12:00", None) is not None
    assert _rejects_live_state("preferred_editor", "VS Code", "user_preference") is None
    assert _rejects_live_state("user_name", "Alice", "user_profile") is None
    # Absolute device paths are never persisted.
    assert _rejects_live_state("preferred_editor", r"C:\Users\alice", "user_preference") is not None