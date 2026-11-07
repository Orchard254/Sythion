"""
Acceptance gate: Controlled file mutations (Phase C).

File writes are atomic (temp file + os.replace) and verify committed content by
reading it back; move/copy/delete record fresh post-mutation verification. A
cancellation arriving while the temp file is written stops before the final
replace, leaving the previous content intact.

Under the deny-by-default contract, all mutating tools are denied by policy
before any OS action (DIRECT_INSTRUCTION_REQUIRED); therefore the executor-level
mutation assertions here verify policy-first denial and non-mutation. Primitive
execution semantics remain covered by test_file_create_atomic_no_replace_race.py
and backend acceptance canonical file checks.
"""

import pytest

from backend.tools.executor import ToolExecutor, PolicyContext
from backend.tools.file_tools import create_file_tools


@pytest.fixture
def file_executor(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.tools.file_tools._approved_roots", lambda: [tmp_path])
    ex = ToolExecutor()
    for tool in create_file_tools():
        ex.register_tool(tool)
    return ex


@pytest.mark.asyncio
async def test_write_is_atomic_and_verified(file_executor, tmp_path):
    target = tmp_path / "atomic.txt"
    tool = file_executor.get_tool("file_write")
    assert tool is not None
    called = []

    async def _bomb(input_data):
        called.append(True)
        raise AssertionError(f"execute called despite policy denial: {input_data}")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(tool, "execute", _bomb)
    try:
        out = await file_executor.execute_tool(
            "file_write",
            {"path": str(target), "content": "first"},
            policy_context=PolicyContext(direct_user_instruction=True),
        )
        assert not out.success
        assert out.error["code"] == "DIRECT_INSTRUCTION_REQUIRED"
        assert called == []
        assert not target.exists()
    finally:
        monkeypatch.undo()


@pytest.mark.asyncio
async def test_append_is_atomic_and_verified(file_executor, tmp_path):
    target = tmp_path / "append.txt"
    target.write_text("start|", encoding="utf-8")
    tool = file_executor.get_tool("file_write")
    assert tool is not None
    called = []

    async def _bomb(input_data):
        called.append(True)
        raise AssertionError(f"execute called despite policy denial: {input_data}")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(tool, "execute", _bomb)
    try:
        out = await file_executor.execute_tool(
            "file_write",
            {"path": str(target), "content": "end", "append": True},
            policy_context=PolicyContext(direct_user_instruction=True),
        )
        assert not out.success
        assert out.error["code"] == "DIRECT_INSTRUCTION_REQUIRED"
        assert called == []
        assert target.read_text(encoding="utf-8") == "start|"
    finally:
        monkeypatch.undo()


@pytest.mark.asyncio
async def test_create_refuses_overwrite_without_flag(file_executor, tmp_path):
    target = tmp_path / "exists.txt"
    target.write_text("old", encoding="utf-8")
    tool = file_executor.get_tool("file_create")
    assert tool is not None
    called = []

    async def _bomb(input_data):
        called.append(True)
        raise AssertionError(f"execute called despite policy denial: {input_data}")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(tool, "execute", _bomb)
    try:
        out = await file_executor.execute_tool(
            "file_create",
            {"path": str(target), "content": "new"},
            policy_context=PolicyContext(direct_user_instruction=True),
        )
        assert not out.success
        assert out.error["code"] == "DIRECT_INSTRUCTION_REQUIRED"
        assert called == []
        assert target.read_text(encoding="utf-8") == "old"
    finally:
        monkeypatch.undo()


@pytest.mark.asyncio
async def test_create_overwrite_is_verified(file_executor, tmp_path):
    target = tmp_path / "overwrite.txt"
    target.write_text("old", encoding="utf-8")
    tool = file_executor.get_tool("file_create")
    assert tool is not None
    called = []

    async def _bomb(input_data):
        called.append(True)
        raise AssertionError(f"execute called despite policy denial: {input_data}")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(tool, "execute", _bomb)
    try:
        out = await file_executor.execute_tool(
            "file_create",
            {"path": str(target), "content": "new", "overwrite": True},
            policy_context=PolicyContext(direct_user_instruction=True),
        )
        assert not out.success
        assert out.error["code"] == "DIRECT_INSTRUCTION_REQUIRED"
        assert called == []
        assert target.read_text(encoding="utf-8") == "old"
    finally:
        monkeypatch.undo()


@pytest.mark.asyncio
async def test_cancelled_write_leaves_previous_content(file_executor, tmp_path):
    target = tmp_path / "guarded.txt"
    target.write_text("original", encoding="utf-8")
    tool = file_executor.get_tool("file_write")
    assert tool is not None
    called = []

    async def _bomb(input_data):
        called.append(True)
        raise AssertionError(f"execute called despite policy denial: {input_data}")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(tool, "execute", _bomb)
    try:
        out = await file_executor.execute_tool(
            "file_write",
            {"path": str(target), "content": "replacement"},
            policy_context=PolicyContext(direct_user_instruction=True),
        )
        assert not out.success
        assert out.error["code"] == "DIRECT_INSTRUCTION_REQUIRED"
        assert called == []
        assert target.read_text(encoding="utf-8") == "original"
        assert not (tmp_path / "guarded.txt.sythion-tmp").exists()
    finally:
        monkeypatch.undo()


@pytest.mark.asyncio
async def test_move_verified_and_no_source_remaining(file_executor, tmp_path):
    src = tmp_path / "move_src.txt"
    src.write_text("payload", encoding="utf-8")
    dest = tmp_path / "sub" / "move_dest.txt"
    tool = file_executor.get_tool("file_move")
    assert tool is not None
    called = []

    async def _bomb(input_data):
        called.append(True)
        raise AssertionError(f"execute called despite policy denial: {input_data}")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(tool, "execute", _bomb)
    try:
        out = await file_executor.execute_tool(
            "file_move",
            {"source_path": str(src), "destination_path": str(dest)},
            policy_context=PolicyContext(direct_user_instruction=True),
        )
        assert not out.success
        assert out.error["code"] == "DIRECT_INSTRUCTION_REQUIRED"
        assert called == []
        assert src.exists()
        assert not dest.exists()
    finally:
        monkeypatch.undo()


@pytest.mark.asyncio
async def test_copy_verified(file_executor, tmp_path):
    src = tmp_path / "copy_src.txt"
    src.write_text("copyme", encoding="utf-8")
    dest = tmp_path / "copy_dest.txt"
    tool = file_executor.get_tool("file_copy")
    assert tool is not None
    called = []

    async def _bomb(input_data):
        called.append(True)
        raise AssertionError(f"execute called despite policy denial: {input_data}")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(tool, "execute", _bomb)
    try:
        out = await file_executor.execute_tool(
            "file_copy",
            {"source_path": str(src), "destination_path": str(dest)},
            policy_context=PolicyContext(direct_user_instruction=True),
        )
        assert not out.success
        assert out.error["code"] == "DIRECT_INSTRUCTION_REQUIRED"
        assert called == []
        assert not dest.exists()
    finally:
        monkeypatch.undo()


@pytest.mark.asyncio
async def test_delete_verified_permanently(file_executor, tmp_path):
    target = tmp_path / "delete_me.txt"
    target.write_text("bye", encoding="utf-8")
    tool = file_executor.get_tool("file_delete")
    assert tool is not None
    called = []

    async def _bomb(input_data):
        called.append(True)
        raise AssertionError(f"execute called despite policy denial: {input_data}")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(tool, "execute", _bomb)
    try:
        out = await file_executor.execute_tool(
            "file_delete",
            {"path": str(target), "use_recycle_bin": False},
            policy_context=PolicyContext(direct_user_instruction=True),
        )
        assert not out.success
        assert out.error["code"] == "DIRECT_INSTRUCTION_REQUIRED"
        assert called == []
        assert target.exists()
    finally:
        monkeypatch.undo()
