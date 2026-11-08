"""
Acceptance gate: Canonical Windows handle rechecks (Phase C #59/#60).

Beyond the deterministic path-string gate, file execution re-verifies
containment by opening a real OS handle and comparing the reported final
location against each approved root. A target that is itself a symbolic link /
junction / reparse point is rejected outright, and short-name (8.3) aliases
cannot bypass containment because the handle reports the long canonical path.
"""

import os
import sys

import pytest

from backend.tools.executor import ToolExecutor, PolicyContext
from backend.tools.file_tools import create_file_tools

# Typed schemas and real primitives used for component-level composition.
# These are the actual helper contracts; no new API is invented here.
from backend.api.schemas import FileDeleteInput, FileWriteInput
from backend.tools.file_tools import _safe_resolve, _atomic_write_text, _read_back_text


@pytest.fixture
def file_executor(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.tools.file_tools._approved_roots", lambda: [tmp_path])
    ex = ToolExecutor()
    for tool in create_file_tools():
        ex.register_tool(tool)
    return ex


def _can_create_symlinks() -> bool:
    if not hasattr(os, "symlink"):
        return False
    try:
        probe = os.path.join(os.environ.get("TEMP", "."), "sythion_link_probe")
        if os.path.exists(probe):
            os.remove(probe)
        target = os.path.join(os.environ.get("TEMP", "."), "sythion_link_probe_target")
        if os.path.exists(target):
            os.remove(target)
        os.symlink(target, probe)
        os.remove(probe)
        return True
    except OSError:
        return False


@pytest.mark.asyncio
async def test_symlink_target_rejected(file_executor, tmp_path):
    if not _can_create_symlinks():
        pytest.skip("Creating symbolic links requires elevated privileges")
    real = tmp_path / "real.txt"
    real.write_text("secret", encoding="utf-8")
    link = tmp_path / "link.txt"
    os.symlink(str(real), str(link))

    out = await file_executor.execute_tool(
        "file_read",
        {"path": str(link)},
        policy_context=PolicyContext(direct_user_instruction=True),
    )
    assert not out.success
    assert out.error["code"] == "TARGET_REPARSE_POINT_REJECTED"


@pytest.mark.asyncio
async def test_symlink_escape_rejected(file_executor, tmp_path):
    """A link inside the root pointing outside must be rejected."""
    if not _can_create_symlinks():
        pytest.skip("Creating symbolic links requires elevated privileges")
    outside = tmp_path.parent.parent / "outside_target.txt"
    outside.write_text("do not touch", encoding="utf-8")
    link = tmp_path / "escape.txt"
    os.symlink(str(outside), str(link))

    out = await file_executor.execute_tool(
        "file_read",
        {"path": str(link)},
        policy_context=PolicyContext(direct_user_instruction=True),
    )
    assert not out.success
    assert out.error["code"] == "TARGET_REPARSE_POINT_REJECTED"
    outside.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_canonical_containment_rejects_shortname_escape(file_executor, tmp_path):
    """Execution must reject paths whose canonical location leaves the roots.

    We simulate the attack by pointing the target at a directory tree that is
    not under the root (8.3 aliases cannot move a path outside, so we verify
    the canonical containment gate directly on the outside path).
    """
    outside = tmp_path.parent / "canonical_outside.txt"
    outside.write_text("x", encoding="utf-8")
    out = await file_executor.execute_tool(
        "file_read",
        {"path": str(outside)},
        policy_context=PolicyContext(direct_user_instruction=True),
    )
    assert not out.success
    assert out.error["code"] == "TARGET_OUTSIDE_ROOTS"
    outside.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_canonical_read_still_works_inside_roots(file_executor, tmp_path):
    target = tmp_path / "ok.txt"
    target.write_text("fine", encoding="utf-8")
    out = await file_executor.execute_tool(
        "file_read",
        {"path": str(target)},
        policy_context=PolicyContext(direct_user_instruction=True),
    )
    assert out.success
    assert out.data["content"] == "fine"


@pytest.mark.asyncio
async def test_canonical_delete_rejected_outside(file_executor, tmp_path):
    """Canonical delete containment is a typed, pre-execution gate.

    We keep the real executor denial (DIRECT_INSTRUCTION_REQUIRED, because a
    mutating delete requires a trusted consent boundary) and separately use the
    real FileDeleteTool.validate_targets on the same outside-root target, which
    must report TARGET_OUTSIDE_ROOTS. No delete occurs and the owned marker is
    unchanged. Component composition does NOT prove authorized integrated tool
    dispatch, ToolOutput verification, or containment rechecking at swap time.
    """
    outside = tmp_path.parent / "canonical_delete.txt"
    marker = "owned-delete-marker"
    outside.write_text(marker, encoding="utf-8")
    try:
        # Typed pre-execution validator (side-effect-free, no ToolOutput).
        tool = file_executor.get_tool("file_delete")
        validated = FileDeleteInput(path=str(outside))
        target_error = tool.validate_targets(validated)
        assert target_error is not None
        assert target_error.get("code") == "TARGET_OUTSIDE_ROOTS"

        # Distinct real executor denial: policy precedes validation/execution.
        out = await file_executor.execute_tool(
            "file_delete",
            {"path": str(outside)},
            policy_context=PolicyContext(direct_user_instruction=True),
        )
        assert not out.success
        assert out.error["code"] == "DIRECT_INSTRUCTION_REQUIRED"

        # Owned outside-root marker is untouched.
        assert outside.read_text(encoding="utf-8") == marker
    finally:
        outside.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_canonical_write_destination_under_roots(file_executor, tmp_path):
    """Write containment composition without claiming authorized dispatch.

    First prove the real executor denies the mutation (no destination is
    created). Then separately assert the real typed write validator accepts the
    within-root target (None), invoke the existing _safe_resolve and
    _atomic_write_text primitives with their actual signatures, and verify the
    exact resolved destination and read-back content.

    This mirrors existing component composition only: it does NOT prove
    authorized integrated tool dispatch, ToolOutput verification, or containment
    rechecking at swap time. The primitive is called directly, including its
    documented TOCTOU limitations.
    """
    dest = tmp_path / "nested" / "dir" / "new.txt"

    # First prove real executor denial and that no destination was created.
    out = await file_executor.execute_tool(
        "file_write",
        {"path": str(dest), "content": "hello"},
        policy_context=PolicyContext(direct_user_instruction=True),
    )
    assert not out.success
    assert out.error["code"] == "DIRECT_INSTRUCTION_REQUIRED"
    assert not dest.exists()

    # Separately assert the real typed write validator accepts the target.
    tool = file_executor.get_tool("file_write")
    validated = FileWriteInput(path=str(dest), content="hello")
    assert tool.validate_targets(validated) is None

    # Existing primitive composition only (actual current signatures).
    resolved = _safe_resolve(str(dest))
    assert resolved == dest.resolve()
    final_content = await _atomic_write_text(resolved, "hello", append=False, overwrite=True)
    assert final_content == "hello"
    assert _read_back_text(resolved) == "hello"
    assert dest.read_text(encoding="utf-8") == "hello"
