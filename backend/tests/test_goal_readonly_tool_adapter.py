"""Real read-only file tool adapter checks in a disposable workspace.

These tests use the actual ToolExecutor + reviewed FileRead/FileHash tools,
not a fake tool or model claim. They do NOT grant file-write authorization,
invoke an LLM, or run the installed Windows application. Run in full checkout.
"""

import hashlib
import pytest

from backend.tools import file_tools
from backend.tools.executor import PolicyContext, ToolExecutor
from backend.tools.file_tools import FileCreateTool, FileHashTool, FileReadTool


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    approved = tmp_path / "approved"
    approved.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setattr(file_tools, "_approved_roots", lambda: [approved.resolve()])
    executor = ToolExecutor()
    for tool in (FileReadTool(), FileHashTool(), FileCreateTool()):
        executor.register_tool(tool)
    return approved, outside, executor


@pytest.mark.asyncio
async def test_real_read_and_sha256_tools_agree_with_independent_file_bytes(workspace):
    approved, _, executor = workspace
    path = approved / "numbers.txt"
    path.write_text("2\n3\n5\n", encoding="utf-8")
    raw = path.read_bytes()
    read = await executor.execute_tool("file_read", {"path": str(path)})
    hashed = await executor.execute_tool(
        "file_hash", {"path": str(path), "algorithm": "sha256"}
    )
    assert read.success, read.error
    assert hashed.success, hashed.error
    # file_read uses text-mode universal newline translation, so on Windows
    # Path.write_text may persist CRLF while the returned text contains LF.
    # SHA-256 and bytes_read must still describe the exact on-disk bytes.
    assert read.data["content"] == path.read_text(encoding="utf-8")
    assert [int(x) for x in read.data["content"].splitlines()] == [2, 3, 5]
    assert read.data["bytes_read"] == len(raw)
    assert hashed.data["file_size"] == len(raw)
    assert hashed.data["algorithm"] == "sha256"
    assert hashed.data["hash"] == hashlib.sha256(raw).hexdigest()
    assert path.read_bytes() == raw


@pytest.mark.parametrize(
    "separator", [b"\n", b"\r\n", b"\r"],
    ids=["lf", "crlf", "cr"],
)
@pytest.mark.asyncio
async def test_file_read_normalizes_line_endings_but_hashes_actual_bytes(
    workspace, separator,
):
    """Distinct raw line endings produce the same text, not the same digest.

    Byte-level evidence remains independent of text newline translation and
    file_read must not modify the original content.
    """
    approved, _, executor = workspace
    path = approved / "numbers.txt"
    raw = separator.join((b"2", b"3", b"5")) + separator
    path.write_bytes(raw)
    read = await executor.execute_tool("file_read", {"path": str(path)})
    hashed = await executor.execute_tool(
        "file_hash", {"path": str(path), "algorithm": "sha256"}
    )
    assert read.success, read.error
    assert hashed.success, hashed.error
    assert read.data["content"] == "2\n3\n5\n"
    assert read.data["bytes_read"] == len(raw)
    assert read.data["size"] == len(read.data["content"].encode("utf-8"))
    assert hashed.data["file_size"] == len(raw)
    assert hashed.data["hash"] == hashlib.sha256(raw).hexdigest()
    assert path.read_bytes() == raw


@pytest.mark.asyncio
async def test_file_hash_changes_after_real_file_tampering(workspace):
    approved, _, executor = workspace
    path = approved / "numbers.txt"
    path.write_text("2\n3\n5\n", encoding="utf-8")
    original = await executor.execute_tool("file_hash", {"path": str(path)})
    assert original.success
    path.write_text("2\n3\n8\n", encoding="utf-8")
    current = await executor.execute_tool("file_hash", {"path": str(path)})
    assert current.success
    assert current.data["hash"] != original.data["hash"]
    assert current.data["hash"] == hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.asyncio
async def test_read_and_hash_outside_approved_workspace_are_refused(workspace):
    _, outside, executor = workspace
    source = outside / "private.txt"
    source.write_text("outside allowed scope", encoding="utf-8")
    for name in ("file_read", "file_hash"):
        result = await executor.execute_tool(name, {"path": str(source)})
        assert result.success is False
        assert result.error["code"] == "TARGET_OUTSIDE_ROOTS"
    assert source.read_text(encoding="utf-8") == "outside allowed scope"


@pytest.mark.asyncio
async def test_mutating_file_create_requires_trusted_consent_not_a_claim(workspace):
    approved, _, executor = workspace
    target = approved / "must_not_be_created.txt"
    result = await executor.execute_tool(
        "file_create",
        {"path": str(target), "content": "not authorized"},
        policy_context=PolicyContext(direct_user_instruction=True, task_id="test-goal"),
    )
    assert result.success is False
    assert result.error["code"] == "DIRECT_INSTRUCTION_REQUIRED"
    assert not target.exists()


@pytest.mark.asyncio
async def test_invalid_or_missing_file_cannot_supply_successful_hash_evidence(workspace):
    approved, _, executor = workspace
    missing = approved / "nonexistent.txt"
    result = await executor.execute_tool("file_hash", {"path": str(missing)})
    assert result.success is False
    assert not missing.exists()
    assert not (result.data or {}).get("hash")
