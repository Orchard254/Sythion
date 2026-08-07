"""
Tests for background memory consolidation (PREMem-style pre-storage reasoning).
"""

import pytest
from unittest.mock import AsyncMock

from backend.core.db_manager import DatabaseManager
from backend.models.database import set_db_manager, MemoryEntry, MemoryEdge
from backend.services.memory_consolidation import (
    consolidate_memory,
    parse_ops,
    parse_edge_ops,
    sanitize_ops,
    sanitize_edge_ops,
)


@pytest.fixture
def memory_db_setup(tmp_path):
    db_path = str(tmp_path / "test_memory.db")
    db_mgr = DatabaseManager(db_path)
    set_db_manager(db_mgr)
    with db_mgr.get_session() as session:
        yield session


class _Turn:
    def __init__(self, role, summary):
        self.role = role
        self.summary = summary


def _fake_adapter(content: str):
    adapter = AsyncMock()
    adapter.chat.return_value = {"message": {"content": content}}
    return adapter


def _store_entry(session, key, value, category="preference"):
    session.add(MemoryEntry(key=key, value=value, category=category))
    session.commit()


# --- parsing / sanitizing ---------------------------------------------------

def test_parse_ops_plain_json():
    content = '{"ops": [{"op": "add", "key": "a", "value": "b"}]}'
    assert parse_ops(content) == [{"op": "add", "key": "a", "value": "b"}]


def test_parse_ops_tolerates_markdown_fences():
    content = '```json\n{"ops": []}\n```'
    assert parse_ops(content) == []


def test_parse_ops_tolerates_prose_wrapper():
    content = 'Here you go:\n{"ops": [{"op": "noop"}]}\nDone.'
    assert parse_ops(content) == [{"op": "noop"}]


def test_parse_ops_missing_json_raises():
    with pytest.raises(ValueError):
        parse_ops("no json here at all")


def test_parse_edge_ops_plain_json():
    content = ('{"ops": [{"op": "noop"}], "edge_ops": [{"op": "add_edge", '
               '"source_key": "a", "relation": "prefers", "target_key": "b"}]}')
    assert parse_edge_ops(content) == [
        {"op": "add_edge", "source_key": "a", "relation": "prefers", "target_key": "b"}
    ]


def test_parse_edge_ops_missing_returns_empty():
    assert parse_edge_ops('{"ops": [{"op": "noop"}]}') == []


def test_parse_edge_ops_tolerates_markdown_fences():
    content = '```json\n{"ops": [], "edge_ops": []}\n```'
    assert parse_edge_ops(content) == []


def test_sanitize_edge_ops_drops_malformed():
    ops = [
        {"op": "add_edge", "source_key": "a", "relation": "prefers", "target_key": "b", "confidence": "high"},
        {"op": "unknown_op", "source_key": "a", "relation": "x", "target_key": "b"},
        "garbage",
        {"op": "add_edge", "source_key": "", "relation": "x", "target_key": "b"},
        {"op": "add_edge", "source_key": "a", "relation": "Bad Relation", "target_key": "b"},
        {"op": "add_edge", "source_key": "a", "relation": "prefers", "target_key": "toolong" * 20},
        {"op": "remove_edge", "source_key": "a", "relation": "prefers", "target_key": "b", "confidence": "high"},
    ]
    cleaned = sanitize_edge_ops(ops)
    assert len(cleaned) == 2
    assert cleaned[0]["op"] == "add_edge"
    assert cleaned[0]["relation"] == "prefers"
    assert cleaned[1]["op"] == "remove_edge"


def test_sanitize_edge_ops_normalizes_bad_source():
    cleaned = sanitize_edge_ops([
        {"op": "add_edge", "source_key": "a", "relation": "prefers", "target_key": "b",
         "confidence": "High", "source": "made_up"}
    ])
    assert cleaned[0]["confidence"] == "high"
    assert cleaned[0]["source"] == "inference"


def test_build_user_message_handles_dicts_and_orms():
    from backend.services.memory_consolidation import _build_user_message
    msg = _build_user_message(
        [{"category": "profile", "key": "user_name", "value": "orchard"}],
        [_Turn("user", "hi"), _Turn("assistant", "hello")],
    )
    assert "[profile] user_name: orchard" in msg
    assert "User: hi" in msg
    assert "Synth: hello" in msg
    empty_msg = _build_user_message([], [])
    assert "CURRENT PERSISTENT MEMORY:" in empty_msg
    assert "(empty)" in empty_msg


def test_sanitize_ops_drops_malformed_and_noop():
    ops = [
        {"op": "add", "key": "a", "value": "b", "category": "profile", "source": "user_statement", "confidence": "high"},
        {"op": "noop"},
        "garbage",
        {"op": "add", "key": "", "value": "b"},
        {"op": "add", "key": "toolong" * 20, "value": "b"},
    ]
    cleaned = sanitize_ops(ops)
    assert len(cleaned) == 1
    assert cleaned[0]["key"] == "a"


def test_sanitize_ops_normalizes_bad_category_source():
    cleaned = sanitize_ops([
        {"op": "add", "key": "a", "value": "b", "category": "weird", "source": "made_up", "confidence": "High"}
    ])
    assert cleaned[0]["category"] == "knowledge"
    assert cleaned[0]["source"] == "inference"
    assert cleaned[0]["confidence"] == "high"


# --- gating / applying ------------------------------------------------------

def test_consolidate_requires_turns(memory_db_setup):
    applied = asyncio_run(consolidate_memory(memory_db_setup, [], [], _fake_adapter("{}")))
    assert applied == []


def test_consolidate_applies_high_confidence_ops(memory_db_setup):
    adapter = _fake_adapter(
        '{"ops": [{"op": "add", "key": "work_location", "value": "home office in Nairobi", '
        '"category": "profile", "confidence": "high", "source": "user_statement", '
        '"reason": "user stated it"}]}'
    )
    turns = [_Turn("user", "Hi, I'm orchard, I work from home in Nairobi."), _Turn("assistant", "Noted.")]
    applied = asyncio_run(consolidate_memory(memory_db_setup, turns, [], adapter))

    assert len(applied) == 1
    assert applied[0]["key"] == "work_location"
    entry = memory_db_setup.query(MemoryEntry).filter_by(key="work_location").first()
    assert entry is not None
    assert entry.value == "home office in Nairobi"
    assert entry.category == "profile"


def test_consolidate_skips_low_confidence_ops(memory_db_setup):
    adapter = _fake_adapter(
        '{"ops": [{"op": "add", "key": "guessed_fact", "value": "x", "category": "profile", '
        '"confidence": "medium", "source": "inference", "reason": "maybe"}]}'
    )
    turns = [_Turn("user", "hello"), _Turn("assistant", "hi")]
    applied = asyncio_run(consolidate_memory(memory_db_setup, turns, [], adapter))

    assert applied == []
    assert memory_db_setup.query(MemoryEntry).filter_by(key="guessed_fact").first() is None


def test_consolidate_never_auto_deletes(memory_db_setup):
    _store_entry(memory_db_setup, "user_name", "orchard", "profile")
    adapter = _fake_adapter(
        '{"ops": [{"op": "delete", "key": "user_name", "category": "profile", '
        '"confidence": "high", "source": "user_statement", "reason": "overridden"}]}'
    )
    turns = [_Turn("user", "never mind my name"), _Turn("assistant", "ok")]
    applied = asyncio_run(consolidate_memory(memory_db_setup, turns, [], adapter))

    assert applied == []
    assert memory_db_setup.query(MemoryEntry).filter_by(key="user_name").first() is not None


def test_consolidate_updates_existing_entry(memory_db_setup):
    _store_entry(memory_db_setup, "preferred_editor", "Notepad++", "user_preference")
    adapter = _fake_adapter(
        '{"ops": [{"op": "update", "key": "preferred_editor", "value": "VS Code", '
        '"category": "preference", "confidence": "high", "source": "user_statement", '
        '"reason": "user switched editors"}]}'
    )
    turns = [_Turn("user", "I switched to VS Code"), _Turn("assistant", "noted")]
    asyncio_run(consolidate_memory(memory_db_setup, turns, [], adapter))

    entry = memory_db_setup.query(MemoryEntry).filter_by(key="preferred_editor").first()
    assert entry.value == "VS Code"


def test_build_user_message_includes_links():
    from backend.services.memory_consolidation import _build_user_message
    msg = _build_user_message(
        [{"category": "profile", "key": "user_name", "value": "orchard"}],
        [_Turn("user", "hi")],
        [{"source_key": "user_name", "relation": "prefers", "target_key": "preferred_editor"}],
    )
    assert "CURRENT MEMORY LINKS:" in msg
    assert "user_name --prefers--> preferred_editor" in msg


def test_consolidate_applies_high_confidence_add_edge(memory_db_setup):
    adapter = _fake_adapter(
        '{"ops": [{"op": "noop"}], "edge_ops": [{"op": "add_edge", "source_key": "user_name", '
        '"relation": "prefers", "target_key": "preferred_editor", "confidence": "high", '
        '"source": "user_statement", "reason": "user stated it"}]}'
    )
    turns = [_Turn("user", "call me Alex and I prefer VS Code"), _Turn("assistant", "noted")]
    applied = asyncio_run(consolidate_memory(memory_db_setup, turns, [], adapter))

    assert len(applied) == 1
    assert applied[0]["op"] == "add_edge"
    edge = memory_db_setup.query(MemoryEdge).filter_by(source_key="user_name").first()
    assert edge is not None
    assert edge.relation == "prefers"
    assert edge.target_key == "preferred_editor"


def test_consolidate_memory_and_edge_ops_both_apply(memory_db_setup):
    adapter = _fake_adapter(
        '{"ops": [{"op": "add", "key": "user_name", "value": "Alex", "category": "profile", '
        '"confidence": "high", "source": "user_statement", "reason": "user stated it"}], '
        '"edge_ops": [{"op": "add_edge", "source_key": "user_name", "relation": "prefers", '
        '"target_key": "preferred_editor", "confidence": "high", "source": "user_statement", '
        '"reason": "user stated it"}]}'
    )
    turns = [_Turn("user", "I'm Alex and prefer VS Code"), _Turn("assistant", "noted")]
    applied = asyncio_run(consolidate_memory(memory_db_setup, turns, [], adapter))

    assert len(applied) == 2
    assert memory_db_setup.query(MemoryEntry).filter_by(key="user_name").first() is not None
    assert memory_db_setup.query(MemoryEdge).filter_by(source_key="user_name").first() is not None


def test_consolidate_skips_low_confidence_edge_ops(memory_db_setup):
    adapter = _fake_adapter(
        '{"ops": [{"op": "noop"}], "edge_ops": [{"op": "add_edge", "source_key": "a", '
        '"relation": "prefers", "target_key": "b", "confidence": "medium", '
        '"source": "inference", "reason": "maybe"}]}'
    )
    turns = [_Turn("user", "hi"), _Turn("assistant", "hello")]
    applied = asyncio_run(consolidate_memory(memory_db_setup, turns, [], adapter))

    assert applied == []
    assert memory_db_setup.query(MemoryEdge).count() == 0


def test_consolidate_never_auto_removes_edges(memory_db_setup):
    memory_db_setup.add(MemoryEdge(source_key="a", relation="prefers", target_key="b"))
    memory_db_setup.commit()
    adapter = _fake_adapter(
        '{"ops": [{"op": "noop"}], "edge_ops": [{"op": "remove_edge", "source_key": "a", '
        '"relation": "prefers", "target_key": "b", "confidence": "high", '
        '"source": "user_statement", "reason": "overridden"}]}'
    )
    turns = [_Turn("user", "never mind"), _Turn("assistant", "ok")]
    applied = asyncio_run(consolidate_memory(memory_db_setup, turns, [], adapter))

    assert applied == []
    assert memory_db_setup.query(MemoryEdge).count() == 1


def test_consolidate_edge_ops_respect_live_state_gate(memory_db_setup):
    adapter = _fake_adapter(
        '{"ops": [{"op": "noop"}], "edge_ops": [{"op": "add_edge", "source_key": "user_name", '
        '"relation": "prefers", "target_key": "system_root_path", "confidence": "high", '
        '"source": "user_statement", "reason": "user stated it"}]}'
    )
    turns = [_Turn("user", "hi"), _Turn("assistant", "hello")]
    applied = asyncio_run(consolidate_memory(memory_db_setup, turns, [], adapter))

    assert applied == []
    assert memory_db_setup.query(MemoryEdge).count() == 0


def test_consolidate_edge_ops_passed_into_prompt(memory_db_setup):
    memory_db_setup.add(MemoryEdge(source_key="user_name", relation="prefers", target_key="preferred_editor"))
    memory_db_setup.commit()
    captured = {}

    adapter = AsyncMock()

    async def _fake_chat(messages, **kwargs):
        captured["user_content"] = messages[1]["content"]
        return {"message": {"content": '{"ops": [{"op": "noop"}], "edge_ops": []}'}}

    adapter.chat.side_effect = _fake_chat
    turns = [_Turn("user", "hi"), _Turn("assistant", "hello")]
    asyncio_run(consolidate_memory(memory_db_setup, turns, [], memory_edges=[
        memory_db_setup.query(MemoryEdge).first()
    ], adapter=adapter))

    assert "user_name --prefers--> preferred_editor" in captured["user_content"]


def test_consolidate_unparseable_response_returns_empty(memory_db_setup):
    adapter = _fake_adapter("I cannot do that today.")
    turns = [_Turn("user", "hi"), _Turn("assistant", "hello")]
    applied = asyncio_run(consolidate_memory(memory_db_setup, turns, [], adapter))
    assert applied == []


def test_consolidate_adapter_failure_returns_empty(memory_db_setup):
    adapter = AsyncMock()
    adapter.chat.side_effect = RuntimeError("network down")
    turns = [_Turn("user", "hi"), _Turn("assistant", "hello")]
    applied = asyncio_run(consolidate_memory(memory_db_setup, turns, [], adapter))
    assert applied == []


def asyncio_run(coro):
    import asyncio
    return asyncio.run(coro)
