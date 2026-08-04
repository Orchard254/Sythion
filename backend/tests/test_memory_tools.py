"""
Unit and integration tests for Sythion's real-time memory tools.
"""

import pytest
from backend.models.database import set_db_manager, MemoryEntry
from backend.core.db_manager import DatabaseManager
from backend.api.schemas import MemoryStoreInput, MemoryRecallInput
from backend.tools.memory_tools import MemoryStoreTool, MemoryRecallTool


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
