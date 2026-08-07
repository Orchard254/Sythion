"""
Unit and integration tests for Sythion's real-time memory tools.
"""

import pytest
from backend.models.database import set_db_manager, MemoryEntry, MemoryEdge
from backend.core.db_manager import DatabaseManager
from backend.api.schemas import (
    MemoryStoreInput,
    MemoryRecallInput,
    MemoryLinkInput,
    MemoryRecallRelatedInput,
)
from backend.tools.memory_tools import (
    MemoryStoreTool,
    MemoryRecallTool,
    MemoryLinkTool,
    MemoryRecallRelatedTool,
)


@pytest.fixture
def memory_db_setup(tmp_path):
    db_path = str(tmp_path / "test_memory.db")
    db_mgr = DatabaseManager(db_path)
    set_db_manager(db_mgr)
    with db_mgr.get_session() as session:
        yield session


@pytest.mark.asyncio
async def test_memory_store_and_recall_lifecycle(memory_db_setup):
    store_tool = MemoryStoreTool()

    # 1. Store memory entry
    store_input = MemoryStoreInput(
        key="preferred_editor",
        value="Notepad++",
        category="user_preference"
    )
    out1 = await store_tool.execute(store_input)
    assert out1.success is True
    assert out1.data["action"] == "created"

    # 2. Update memory entry
    update_input = MemoryStoreInput(
        key="preferred_editor",
        value="VS Code",
        category="user_preference"
    )
    out2 = await store_tool.execute(update_input)
    assert out2.success is True
    assert out2.data["action"] == "updated"

    # 3. Recall memory entry
    recall_tool = MemoryRecallTool()
    recall_input = MemoryRecallInput(query="editor")
    out3 = await recall_tool.execute(recall_input)
    assert out3.success is True
    assert out3.data["count"] == 1
    assert out3.data["memories"][0]["key"] == "preferred_editor"
    assert out3.data["memories"][0]["value"] == "VS Code"


@pytest.mark.asyncio
async def test_recall_surfaces_memories_for_natural_language_query(memory_db_setup):
    store_tool = MemoryStoreTool()
    for key, value, category in [
        ("user_name", "orchard", "profile"),
        ("conversation_style_preference", "normal, casual conversation", "preference"),
        ("address_preference", 'Address the user as "Sir"', "preference"),
    ]:
        await store_tool.execute(MemoryStoreInput(key=key, value=value, category=category))

    recall_tool = MemoryRecallTool()
    out = await recall_tool.execute(MemoryRecallInput(query="user profile preferences"))
    assert out.success is True
    assert out.data["count"] == 3
    keys = {m["key"] for m in out.data["memories"]}
    assert keys == {"user_name", "conversation_style_preference", "address_preference"}


@pytest.mark.asyncio
async def test_recall_matches_stemmed_plural_queries(memory_db_setup):
    store_tool = MemoryStoreTool()
    await store_tool.execute(
        MemoryStoreInput(key="preferred_editor", value="Notepad++", category="user_preference")
    )

    recall_tool = MemoryRecallTool()
    out = await recall_tool.execute(MemoryRecallInput(query="preferences"))
    assert out.success is True
    assert out.data["count"] == 1
    assert out.data["memories"][0]["key"] == "preferred_editor"


@pytest.mark.asyncio
async def test_recall_empty_query_returns_all_memories(memory_db_setup):
    store_tool = MemoryStoreTool()
    await store_tool.execute(
        MemoryStoreInput(key="first", value="one", category="profile")
    )
    await store_tool.execute(
        MemoryStoreInput(key="second", value="two", category="preference")
    )

    recall_tool = MemoryRecallTool()
    out = await recall_tool.execute(MemoryRecallInput(query=""))
    assert out.success is True
    assert out.data["count"] == 2


@pytest.mark.asyncio
async def test_recall_no_match_returns_zero(memory_db_setup):
    store_tool = MemoryStoreTool()
    await store_tool.execute(
        MemoryStoreInput(key="preferred_editor", value="Notepad++", category="user_preference")
    )

    recall_tool = MemoryRecallTool()
    out = await recall_tool.execute(MemoryRecallInput(query="onboarding"))
    assert out.success is True
    assert out.data["count"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("key,category", [
    ("approved_roots", "system"),
    ("file_roots", "system"),
    ("system_root_path", "system"),
    ("user_desktop_path", "system"),
    ("downloads_path", "system"),
    ("env_home_dir", "profile"),
    ("home_dir", "profile"),
    ("hostname", "system"),
    ("windows_user", "system"),
])
async def test_memory_store_rejects_device_identity(memory_db_setup, key, category):
    store_tool = MemoryStoreTool()
    out = await store_tool.execute(
        MemoryStoreInput(key=key, value="C:\\Users\\Administrator", category=category)
    )
    assert out.success is False
    assert out.error["code"] == "MEMORY_STATE_STORAGE_REJECTED"

    recall_tool = MemoryRecallTool()
    res = await recall_tool.execute(MemoryRecallInput(query=""))
    assert res.data["count"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("key,category", [
    ("current_time", "environment"),
    ("active_window", "environment"),
    ("installed_apps", "environment"),
    ("disk_usage_documents", "environment"),
    ("summary", "environment"),
    ("disk_usage", "system"),
    ("processes", "system"),
])
async def test_memory_store_rejects_ephemeral_state(memory_db_setup, key, category):
    store_tool = MemoryStoreTool()
    out = await store_tool.execute(
        MemoryStoreInput(key=key, value="some snapshot value", category=category)
    )
    assert out.success is False
    assert out.error["code"] == "MEMORY_STATE_STORAGE_REJECTED"


@pytest.mark.asyncio
async def test_memory_store_rejects_environment_category_arbitrary_key(memory_db_setup):
    store_tool = MemoryStoreTool()
    out = await store_tool.execute(
        MemoryStoreInput(key="anything", value="x", category="environment")
    )
    assert out.success is False
    assert out.error["code"] == "MEMORY_STATE_STORAGE_REJECTED"


@pytest.mark.asyncio
async def test_memory_store_rejects_absolute_path_values(memory_db_setup):
    store_tool = MemoryStoreTool()
    out = await store_tool.execute(
        MemoryStoreInput(key="favorite_folder", value="C:\\Users\\Administrator\\Desktop", category="user_preference")
    )
    assert out.success is False
    assert out.error["code"] == "MEMORY_STATE_STORAGE_REJECTED"


@pytest.mark.asyncio
async def test_memory_store_still_accepts_durable_preferences(memory_db_setup):
    store_tool = MemoryStoreTool()
    for key, value, category in [
        ("preferred_editor", "Notepad++", "user_preference"),
        ("address_preference", 'Address the user as "Sir"', "preference"),
    ]:
        out = await store_tool.execute(MemoryStoreInput(key=key, value=value, category=category))
        assert out.success is True

    recall_tool = MemoryRecallTool()
    res = await recall_tool.execute(MemoryRecallInput(query=""))
    assert res.data["count"] == 2


# --- memory_link -------------------------------------------------------------

@pytest.mark.asyncio
async def test_memory_link_creates_and_updates(memory_db_setup):
    link_tool = MemoryLinkTool()

    out1 = await link_tool.execute(MemoryLinkInput(
        source_key="user_name", relation="prefers", target_key="preferred_editor"
    ))
    assert out1.success is True
    assert out1.data["action"] == "created"

    out2 = await link_tool.execute(MemoryLinkInput(
        source_key="user_name", relation="prefers", target_key="preferred_editor", weight=2.0
    ))
    assert out2.success is True
    assert out2.data["action"] == "updated"
    assert out2.data["weight"] == 2.0

    session = memory_db_setup
    edges = session.query(MemoryEdge).all()
    assert len(edges) == 1
    assert edges[0].relation == "prefers"
    assert edges[0].weight == 2.0


@pytest.mark.asyncio
@pytest.mark.parametrize("key", [
    "system_root_path", "downloads_path", "active_window", "current_time", "approved_roots",
])
async def test_memory_link_rejects_live_state_endpoints(memory_db_setup, key):
    link_tool = MemoryLinkTool()

    out_src = await link_tool.execute(MemoryLinkInput(source_key=key, relation="relates_to", target_key="other"))
    assert out_src.success is False
    assert out_src.error["code"] == "MEMORY_LINK_STATE_REJECTED"

    out_tgt = await link_tool.execute(MemoryLinkInput(source_key="other", relation="relates_to", target_key=key))
    assert out_tgt.success is False
    assert out_tgt.error["code"] == "MEMORY_LINK_STATE_REJECTED"

    assert memory_db_setup.query(MemoryEdge).count() == 0


@pytest.mark.asyncio
async def test_memory_link_rejects_absolute_path_value(memory_db_setup):
    link_tool = MemoryLinkTool()
    out = await link_tool.execute(MemoryLinkInput(
        source_key="favorite_folder", relation="points_to", target_key="C:\\Users\\Administrator\\Desktop"
    ))
    assert out.success is False
    assert out.error["code"] == "MEMORY_LINK_STATE_REJECTED"


@pytest.mark.asyncio
async def test_memory_link_rejects_invalid_relation(memory_db_setup):
    link_tool = MemoryLinkTool()
    for bad in ["", "Uppercase", "has space", "a-b", "99start"]:
        out = await link_tool.execute(MemoryLinkInput(source_key="a", relation=bad, target_key="b"))
        assert out.success is False
        assert out.error["code"] == "MEMORY_LINK_INVALID_RELATION"
    assert memory_db_setup.query(MemoryEdge).count() == 0


@pytest.mark.asyncio
async def test_memory_link_rejects_self_loop(memory_db_setup):
    out = await MemoryLinkTool().execute(MemoryLinkInput(
        source_key="a", relation="relates_to", target_key="a"
    ))
    assert out.success is False
    assert out.error["code"] == "MEMORY_LINK_SELF_LOOP"


@pytest.mark.asyncio
async def test_memory_link_accepts_free_text_target(memory_db_setup):
    out = await MemoryLinkTool().execute(MemoryLinkInput(
        source_key="user_name", relation="works_at", target_key="acme_corp"
    ))
    assert out.success is True
    assert memory_db_setup.query(MemoryEdge).count() == 1
    assert memory_db_setup.query(MemoryEntry).filter_by(key="acme_corp").count() == 0


# --- memory_recall_related ---------------------------------------------------

def _seed_neighborhood(session):
    session.add(MemoryEntry(key="user_name", value="orchard", category="profile"))
    session.add(MemoryEntry(key="preferred_editor", value="VS Code", category="preference"))
    session.add(MemoryEntry(key="python_work", value="backend dev", category="knowledge"))
    session.add(MemoryEdge(source_key="user_name", relation="prefers", target_key="preferred_editor"))
    session.add(MemoryEdge(source_key="preferred_editor", relation="used_for", target_key="python_work"))
    session.commit()


@pytest.mark.asyncio
async def test_memory_recall_related_one_hop(memory_db_setup):
    _seed_neighborhood(memory_db_setup)

    tool = MemoryRecallRelatedTool()
    out = await tool.execute(MemoryRecallRelatedInput(key="user_name", hops=1))
    assert out.success is True
    assert out.data["anchor_key"] == "user_name"
    keys = {m["key"] for m in out.data["memories"]}
    assert "user_name" in keys
    assert "preferred_editor" in keys
    # The 2-hop neighbor is NOT reached at hops=1.
    assert "python_work" not in keys


@pytest.mark.asyncio
async def test_memory_recall_related_two_hops(memory_db_setup):
    _seed_neighborhood(memory_db_setup)

    tool = MemoryRecallRelatedTool()
    out = await tool.execute(MemoryRecallRelatedInput(key="user_name", hops=2))
    assert out.success is True
    keys = {m["key"] for m in out.data["memories"]}
    assert "python_work" in keys


@pytest.mark.asyncio
async def test_memory_recall_related_hops_capped_at_two(memory_db_setup):
    _seed_neighborhood(memory_db_setup)

    tool = MemoryRecallRelatedTool()
    out = await tool.execute(MemoryRecallRelatedInput(key="user_name", hops=9))
    assert out.success is True
    keys = {m["key"] for m in out.data["memories"]}
    assert "python_work" in keys


@pytest.mark.asyncio
async def test_memory_recall_related_fuzzy_key_fallback(memory_db_setup):
    session = memory_db_setup
    session.add(MemoryEntry(key="preferred_editor", value="VS Code", category="preference"))
    session.add(MemoryEntry(key="other_thing", value="x", category="knowledge"))
    session.add(MemoryEdge(source_key="preferred_editor", relation="used_for", target_key="python_work"))
    session.commit()

    tool = MemoryRecallRelatedTool()
    out = await tool.execute(MemoryRecallRelatedInput(key="editor"))
    assert out.success is True
    assert out.data["anchor_key"] == "preferred_editor"


@pytest.mark.asyncio
async def test_memory_recall_related_key_not_found(memory_db_setup):
    session = memory_db_setup
    session.add(MemoryEntry(key="preferred_editor", value="VS Code", category="preference"))
    session.commit()

    out = await MemoryRecallRelatedTool().execute(MemoryRecallRelatedInput(key="zzzzzz"))
    assert out.success is False
    assert out.error["code"] == "MEMORY_NOT_FOUND"


@pytest.mark.asyncio
async def test_memory_recall_related_surfaces_unlinked_facts(memory_db_setup):
    session = memory_db_setup
    session.add(MemoryEntry(key="user_name", value="orchard", category="profile"))
    session.add(MemoryEdge(source_key="user_name", relation="works_at", target_key="acme_corp"))
    session.commit()

    out = await MemoryRecallRelatedTool().execute(MemoryRecallRelatedInput(key="user_name"))
    assert out.success is True
    assert out.data["unlinked_facts"] == [
        {"target_key": "acme_corp", "relation": "works_at", "from": "user_name", "hop": 1}
    ]
