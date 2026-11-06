"""Synthetic coverage for lifecycle visibility through real tool dispatch."""

from contextlib import contextmanager
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models.database import MemoryEdge, MemoryEntry
from backend.tools import persistent_memory_tools as memory
from backend.tools.executor import ToolExecutor


@pytest.fixture
def executor(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'memory.db').as_posix()}")
    MemoryEntry.__table__.create(engine)
    MemoryEdge.__table__.create(engine)
    sessions = sessionmaker(bind=engine)

    class Manager:
        @contextmanager
        def get_session(self):
            with sessions() as session:
                yield session

    with sessions.begin() as session:
        session.add_all([
            MemoryEntry(id=11, key="anchor", value="visible apple", category="profile", status="ACTIVE", source_task_id="prior-task"),
            MemoryEntry(id=12, key="neighbor", value="visible pear", category="other", status="ACTIVE"),
            MemoryEntry(id=13, key="hiddenforget", value="forgottenvalue", category="profile", status="FORGOTTEN"),
            MemoryEntry(id=14, key="hiddenold", value="supersededvalue", category="retired", status="SUPERSEDED"),
            MemoryEntry(id=15, key="taskprivate", value="tasksecretvalue", category="profile", status="ACTIVE", scope="TASK", scope_ref="some-task"),
            MemoryEntry(id=16, key="sessionprivate", value="sessionsecretvalue", category="retired", status="ACTIVE", scope="SESSION", scope_ref="some-session"),
            MemoryEntry(id=17, key="globalprivate", value="globalsecretvalue", category="retired", status="ACTIVE", scope="GLOBAL"),
        ])
        session.add_all([
            MemoryEdge(source_key="anchor", target_key="neighbor", relation="knows", status="active"),
            MemoryEdge(source_key="anchor", target_key="free fact", relation="knows", status="stale"),
            MemoryEdge(source_key="anchor", target_key="hiddenforget", relation="knows", status="active"),
            MemoryEdge(source_key="anchor", target_key="14", relation="knows", status="active"),
            MemoryEdge(source_key="anchor", target_key="taskprivate", relation="knows", status="active"),
            MemoryEdge(source_key="16", target_key="neighbor", relation="knows", status="active"),
            MemoryEdge(source_key="taskprivate", target_key="neighbor", relation="knows", status="active"),
            MemoryEdge(source_key="13", target_key="neighbor", relation="knows", status="active"),
            MemoryEdge(source_key="anchor", target_key="fact forgottenvalue here", relation="knows", status="active"),
            MemoryEdge(source_key="11", target_key="12", relation="knows", status="active"),
            MemoryEdge(source_key="anchor", target_key="removed fact", relation="knows", status="removed"),
        ])
    monkeypatch.setattr(memory, "get_db_manager", lambda: Manager())
    executor = ToolExecutor()
    for tool in (memory.MemoryRecallTool(), memory.MemoryRecallRelatedTool(),
                 memory.MemoryStatsTool(), memory.MemoryExportTool()):
        executor.register_tool(tool)
    yield executor
    engine.dispose()


async def call(executor, name, **kwargs):
    result = await executor.execute_tool(name, kwargs)
    assert result.success, result.error
    return result


@pytest.mark.asyncio
async def test_recall_filters_inactive_with_empty_query_and_categories(executor):
    for query, category, keys in [
        ("", None, {"anchor", "neighbor"}),
        ("", "profile", {"anchor"}),
        ("", "retired", set()),
        ("forgottenvalue", None, set()),
        ("supersededvalue", None, set()),
        ("tasksecretvalue", None, set()),
        ("sessionsecretvalue", None, set()),
        ("globalsecretvalue", None, set()),
        ("visible", None, {"anchor", "neighbor"}),
    ]:
        result = await call(executor, "memory_recall", query=query, category=category)
        assert {row["key"] for row in result.data["memories"]} == keys


@pytest.mark.asyncio
async def test_related_excludes_inactive_anchors_neighbors_and_edges(executor):
    for key in ("hiddenforget", "supersededvalue", "taskprivate", "sessionsecretvalue", "globalprivate"):
        result = await executor.execute_tool("memory_recall_related", {"key": key})
        assert not result.success
        assert result.error["code"] == "MEMORY_NOT_FOUND"
    result = await call(executor, "memory_recall_related", key="anchor", hops=2)
    assert {m["key"] for m in result.data["memories"]} == {"anchor", "neighbor"}
    assert {edge["target_key"] for edge in result.data["links"]} == {"neighbor", "free fact"}
    assert result.data["unlinked_facts"][0]["target_key"] == "free fact"
    fuzzy = await call(executor, "memory_recall_related", key="visible apple")
    assert fuzzy.data["anchor_key"] == "anchor"


@pytest.mark.asyncio
@pytest.mark.parametrize("fmt", ["json", "markdown"])
@pytest.mark.parametrize("category,entries,edges", [(None, 2, 3), ("profile", 1, 3), ("retired", 0, 0)])
async def test_export_and_stats_hide_inactive(executor, fmt, category, entries, edges):
    result = await call(executor, "memory_export", format=fmt, category=category)
    assert (result.data["total_entries"], result.data["total_edges"]) == (entries, edges)
    text = result.data["content"]
    for forbidden in ("hiddenforget", "hiddenold", "forgottenvalue", "supersededvalue", "taskprivate", "sessionprivate", "globalprivate", "tasksecretvalue", "sessionsecretvalue", "globalsecretvalue"):
        assert forbidden not in text
    if fmt == "json":
        assert len(json.loads(text)["edges"]) == edges
    stats = await call(executor, "memory_stats")
    assert stats.data["total_entries"] == 2
    assert stats.data["total_edges"] == 3
    assert stats.data["category_counts"] == {"profile": 1, "other": 1}
    assert {row["key"] for row in stats.data["recent_entries"]} == {"anchor", "neighbor"}


@pytest.mark.asyncio
async def test_export_can_omit_edges(executor):
    result = await call(executor, "memory_export", include_edges=False)
    assert result.data["total_edges"] == 0
    assert json.loads(result.data["content"])["edges"] == []
