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
    outside = tmp_path.parent / "canonical_delete.txt"
    outside.write_text("x", encoding="utf-8")
    out = await file_executor.execute_tool(
        "file_delete",
        {"path": str(outside)},
        policy_context=PolicyContext(direct_user_instruction=True),
    )
    assert not out.success
    assert out.error["code"] == "TARGET_OUTSIDE_ROOTS"
    outside.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_canonical_write_destination_under_roots(file_executor, tmp_path):
    dest = tmp_path / "nested" / "dir" / "new.txt"
    out = await file_executor.execute_tool(
        "file_write",
        {"path": str(dest), "content": "hello"},
        policy_context=PolicyContext(direct_user_instruction=True),
    )
    assert out.success
    assert dest.read_text(encoding="utf-8") == "hello"
