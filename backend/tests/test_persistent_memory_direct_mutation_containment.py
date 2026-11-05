"""Focused fake/temp-DB tests: direct persistent-memory mutation containment.

These tests prove ``MemoryStoreTool.execute``, ``MemoryForgetTool.execute``,
and ``MemoryLinkTool.execute`` fail closed before any input interpretation,
DB/session lookup, ORM insert/update/delete, commit, or other effect.
Read-only retrieval behavior is not altered.
"""

import pytest
from types import SimpleNamespace

from backend.api.schemas import MemoryStoreInput, MemoryForgetInput, MemoryLinkInput, ToolOutput
from backend.core.db_manager import DatabaseManager
from backend.models.database import MemoryEntry, MemoryEdge, set_db_manager, get_db_manager
from backend.tools import persistent_memory_tools as pmt


_DENIAL_CODE = "DIRECT_INSTRUCTION_REQUIRED"


@pytest.fixture
def seeded_memory_db(tmp_path):
    try:
        previous_manager = get_db_manager()
    except RuntimeError:
        previous_manager = None
    db_path = str(tmp_path / "containment.db")
    db_mgr = DatabaseManager(db_path)
    set_db_manager(db_mgr)
    try:
        with db_mgr.get_session() as session:
            session.add(MemoryEntry(key="preferred_editor", value="VS Code", category="user_preference", status="ACTIVE"))
            session.commit()
            rows_before = session.query(MemoryEntry).all()
            assert len(rows_before) == 1
        yield db_mgr
    finally:
        set_db_manager(previous_manager)


@pytest.fixture
def seeded_memory_edge_db(tmp_path):
    try:
        previous_manager = get_db_manager()
    except RuntimeError:
        previous_manager = None
    db_path = str(tmp_path / "containment_edges.db")
    db_mgr = DatabaseManager(db_path)
    set_db_manager(db_mgr)
    try:
        with db_mgr.get_session() as session:
            session.add(MemoryEntry(key="preferred_editor", value="VS Code", category="user_preference", status="ACTIVE"))
            session.add(MemoryEntry(key="user_name", value="Ada", category="profile", status="ACTIVE"))
            session.add(MemoryEdge(source_key="user_name", relation="prefers", target_key="preferred_editor", weight=1.0))
            session.commit()
            edges_before = session.query(MemoryEdge).all()
            assert len(edges_before) == 1
        yield db_mgr
    finally:
        set_db_manager(previous_manager)


@pytest.mark.asyncio
async def test_memory_store_fails_closed_before_db_access(monkeypatch):
    def forbidden_get_db_manager():
        raise AssertionError("memory_store accessed get_db_manager")

    monkeypatch.setattr(pmt, "get_db_manager", forbidden_get_db_manager)

    tool = pmt.MemoryStoreTool()
    # Adversarial flags/labels must not authorize mutation.
    result = await tool.execute(
        SimpleNamespace(
            key="preferred_editor",
            value="EVIL",
            category="user_preference",
            direct_user_instruction=True,
            approved_by_user=True,
            admin=True,
            source_type="TOOL_EVIDENCE",
        )
    )

    assert result.success is False
    assert result.error["code"] == _DENIAL_CODE


@pytest.mark.asyncio
async def test_memory_forget_fails_closed_before_db_access(monkeypatch):
    def forbidden_get_db_manager():
        raise AssertionError("memory_forget accessed get_db_manager")

    monkeypatch.setattr(pmt, "get_db_manager", forbidden_get_db_manager)

    tool = pmt.MemoryForgetTool()
    result = await tool.execute(
        SimpleNamespace(
            key="preferred_editor",
            direct_user_instruction=True,
            approved_by_user=True,
            admin=True,
            source_type="TOOL_EVIDENCE",
        )
    )

    assert result.success is False
    assert result.error["code"] == _DENIAL_CODE


@pytest.mark.asyncio
async def test_memory_link_fails_closed_before_db_access(monkeypatch):
    def forbidden_get_db_manager():
        raise AssertionError("memory_link accessed get_db_manager")

    monkeypatch.setattr(pmt, "get_db_manager", forbidden_get_db_manager)

    tool = pmt.MemoryLinkTool()
    # Adversarial flags/labels must not authorize mutation.
    result = await tool.execute(
        SimpleNamespace(
            source_key="user_name",
            relation="prefers",
            target_key="preferred_editor",
            weight=2.5,
            direct_user_instruction=True,
            approved_by_user=True,
            admin=True,
            source_type="TOOL_EVIDENCE",
        )
    )

    assert result.success is False
    assert result.error["code"] == _DENIAL_CODE


@pytest.mark.asyncio
async def test_memory_link_denial_precedes_input_interpretation(monkeypatch):
    # Malformed input must not reach the link validation/DB paths.
    def forbidden_get_db_manager():
        raise AssertionError("memory_link accessed get_db_manager")

    monkeypatch.setattr(pmt, "get_db_manager", forbidden_get_db_manager)

    tool = pmt.MemoryLinkTool()
    malformed_inputs = [
        # Empty endpoints (would be MEMORY_LINK_EMPTY_ENDPOINT)
        SimpleNamespace(source_key="", relation="prefers", target_key="preferred_editor", weight=1.0),
        SimpleNamespace(source_key="user_name", relation="prefers", target_key="  ", weight=1.0),
        # Invalid relation (would be MEMORY_LINK_INVALID_RELATION)
        SimpleNamespace(source_key="user_name", relation="PREFERS!!", target_key="preferred_editor", weight=1.0),
        SimpleNamespace(source_key="user_name", relation="", target_key="preferred_editor", weight=1.0),
        # Self loop (would be MEMORY_LINK_SELF_LOOP)
        SimpleNamespace(source_key="user_name", relation="prefers", target_key="user_name", weight=1.0),
        # Live-state-looking keys (would be MEMORY_LINK_STATE_REJECTED)
        SimpleNamespace(source_key="current_time", relation="prefers", target_key="preferred_editor", weight=1.0),
    ]
    for bad in malformed_inputs:
        result = await tool.execute(bad)
        assert result.success is False
        assert result.error["code"] == _DENIAL_CODE


@pytest.mark.asyncio
async def test_memory_link_denial_preserves_seeded_edge_history(seeded_memory_edge_db):
    tool = pmt.MemoryLinkTool()

    with seeded_memory_edge_db.get_session() as session:
        before = [
            (e.source_key, e.relation, e.target_key, e.weight, e.status)
            for e in session.query(MemoryEdge).all()
        ]

    # This input would UPDATE the seeded edge (same source/relation/target).
    update_attempt = await tool.execute(
        MemoryLinkInput(source_key="user_name", relation="prefers", target_key="preferred_editor", weight=9.0)
    )
    assert update_attempt.success is False
    assert update_attempt.error["code"] == _DENIAL_CODE

    # This input would INSERT a new edge.
    insert_attempt = await tool.execute(
        MemoryLinkInput(source_key="user_name", relation="mentions", target_key="preferred_editor", weight=1.0)
    )
    assert insert_attempt.success is False
    assert insert_attempt.error["code"] == _DENIAL_CODE

    with seeded_memory_edge_db.get_session() as session:
        after = [
            (e.source_key, e.relation, e.target_key, e.weight, e.status)
            for e in session.query(MemoryEdge).all()
        ]
    assert after == before
    assert len(after) == 1
    assert after[0][:3] == ("user_name", "prefers", "preferred_editor")
    assert after[0][3] == 1.0
    assert after[0][4] == "active"


@pytest.mark.asyncio
async def test_memory_store_denial_preserves_existing_history(seeded_memory_db):
    tool = pmt.MemoryStoreTool()

    with seeded_memory_db.get_session() as session:
        before = [(r.key, r.value, r.category, r.status) for r in session.query(MemoryEntry).all()]

    result = await tool.execute(
        MemoryStoreInput(key="preferred_editor", value="OVERWRITE", category="user_preference")
    )

    assert result.success is False
    assert result.error["code"] == _DENIAL_CODE

    with seeded_memory_db.get_session() as session:
        after = [(r.key, r.value, r.category, r.status) for r in session.query(MemoryEntry).all()]
    assert after == before
    assert len(after) == 1
    assert after[0][1] == "VS Code"


@pytest.mark.asyncio
async def test_memory_forget_denial_preserves_existing_history(seeded_memory_db):
    tool = pmt.MemoryForgetTool()

    with seeded_memory_db.get_session() as session:
        before = [(r.key, r.value, r.category, r.status) for r in session.query(MemoryEntry).all()]

    result = await tool.execute(
        MemoryForgetInput(key="preferred_editor")
    )

    assert result.success is False
    assert result.error["code"] == _DENIAL_CODE

    with seeded_memory_db.get_session() as session:
        after = [(r.key, r.value, r.category, r.status) for r in session.query(MemoryEntry).all()]
    assert after == before
    assert len(after) == 1
    assert after[0][0] == "preferred_editor"


@pytest.mark.asyncio
async def test_both_memory_mutation_tools_share_same_reason():
    store = pmt.MemoryStoreTool()
    forget = pmt.MemoryForgetTool()

    store_result = await store.execute(MemoryStoreInput(key="k", value="v"))
    forget_result = await forget.execute(MemoryForgetInput(key="k"))

    assert store_result.success is False
    assert forget_result.success is False
    assert store_result.error == forget_result.error
    assert store_result.error["code"] == _DENIAL_CODE


@pytest.mark.asyncio
async def test_all_three_memory_mutation_tools_share_same_reason():
    store = pmt.MemoryStoreTool()
    forget = pmt.MemoryForgetTool()
    link = pmt.MemoryLinkTool()

    store_result = await store.execute(MemoryStoreInput(key="k", value="v"))
    forget_result = await forget.execute(MemoryForgetInput(key="k"))
    link_result = await link.execute(MemoryLinkInput(source_key="a", relation="prefers", target_key="b"))

    assert store_result.success is False
    assert forget_result.success is False
    assert link_result.success is False
    assert store_result.error == forget_result.error == link_result.error
    assert link_result.error["code"] == _DENIAL_CODE


@pytest.mark.asyncio
async def test_verify_on_denied_output_makes_no_db_reads_for_all_three(monkeypatch):
    def forbidden_get_db_manager():
        raise AssertionError("verify accessed get_db_manager on a denied output")

    monkeypatch.setattr(pmt, "get_db_manager", forbidden_get_db_manager)

    store = pmt.MemoryStoreTool()
    forget = pmt.MemoryForgetTool()
    link = pmt.MemoryLinkTool()

    store_input = MemoryStoreInput(key="k", value="v")
    forget_input = MemoryForgetInput(key="k")
    link_input = MemoryLinkInput(source_key="a", relation="prefers", target_key="b")

    denied_outputs = [
        (store, store_input, await store.execute(store_input)),
        (forget, forget_input, await forget.execute(forget_input)),
        (link, link_input, await link.execute(link_input)),
    ]
    for tool, tool_input, output in denied_outputs:
        assert output.success is False
        assert await tool.verify(tool_input, output) is False

    # Explicitly denied output (not produced by execute) must also verify False.
    crafted_denial = ToolOutput(
        success=False,
        error={"code": _DENIAL_CODE, "message": "denied"},
        duration_ms=0,
    )
    for tool, tool_input in ((store, store_input), (forget, forget_input), (link, link_input)):
        assert await tool.verify(tool_input, crafted_denial) is False