"""Acceptance: create-only writes must not clobber a concurrently created target.

Regression guard for the overwrite=False race in
backend/tools/file_tools.py.  Publication is now an atomic no-replace link of a
uniquely/exclusively created temp file; it must not replace, and a failed
publication must clean up only its own temp file.
"""

import asyncio
import os
from pathlib import Path

import pytest

from backend.tools.executor import ToolExecutor, PolicyContext
from backend.tools.file_tools import (
    _atomic_write_text,
    _CancelledError,
    _ExistsError,
    create_file_tools,
)


@pytest.fixture
def file_executor(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.tools.file_tools._approved_roots", lambda: [tmp_path])
    ex = ToolExecutor()
    for tool in create_file_tools():
        ex.register_tool(tool)
    return ex


@pytest.mark.asyncio
async def test_create_new_file_is_atomic_and_verified(file_executor, tmp_path):
    target = tmp_path / "created.txt"
    out = await file_executor.execute_tool(
        "file_create",
        {"path": str(target), "content": "first", "overwrite": False},
        policy_context=PolicyContext(direct_user_instruction=True),
    )
    assert out.success is True
    assert out.data["verified"] is True
    assert target.read_text(encoding="utf-8") == "first"
    assert not any(p.suffix == ".sythion-tmp" for p in tmp_path.iterdir())


@pytest.mark.asyncio
async def test_create_refuses_existing_without_clobber(file_executor, tmp_path):
    target = tmp_path / "exists.txt"
    target.write_text("old", encoding="utf-8")
    out = await file_executor.execute_tool(
        "file_create",
        {"path": str(target), "content": "new", "overwrite": False},
        policy_context=PolicyContext(direct_user_instruction=True),
    )
    assert out.success is False
    assert out.error["code"] == "FILE_EXISTS"
    assert target.read_text(encoding="utf-8") == "old"


@pytest.mark.asyncio
async def test_create_overwrite_replaces_explicitly(file_executor, tmp_path):
    target = tmp_path / "overwrite.txt"
    target.write_text("old", encoding="utf-8")
    out = await file_executor.execute_tool(
        "file_create",
        {"path": str(target), "content": "new", "overwrite": True},
        policy_context=PolicyContext(direct_user_instruction=True),
    )
    assert out.success is True
    assert out.data["verified"] is True
    assert out.data["overwritten"] is True
    assert target.read_text(encoding="utf-8") == "new"


@pytest.mark.asyncio
async def test_create_only_race_does_not_clobber(tmp_path, monkeypatch):
    """A target created between checks/publication must survive untouched."""
    target = tmp_path / "race.txt"
    interloper = "INTERLOPER-WAS-CREATED-DURING-RACE"
    real_link = os.link

    def racing_link(src, dst):
        Path(dst).write_text(interloper, encoding="utf-8")
        return real_link(src, dst)

    monkeypatch.setattr(os, "link", racing_link)

    with pytest.raises(_ExistsError):
        await _atomic_write_text(
            target,
            "NEW-PAYLOAD-FROM-CREATE",
            append=False,
            overwrite=False,
        )

    assert target.read_text(encoding="utf-8") == interloper
    # Unique temp files from this call are cleaned (race made publication fail).
    assert not any(p.name.endswith(".sythion-tmp") for p in tmp_path.iterdir())


@pytest.mark.asyncio
async def test_publication_failure_cleans_only_own_temp(tmp_path, monkeypatch):
    """A failed create-only publication leaves no temp file (even stale siblings)."""
    target = tmp_path / "fail.txt"
    stale = tmp_path / "legacy.sythion-tmp"
    stale.write_text("stale", encoding="utf-8")

    def failing_link(src, dst):
        raise OSError("boom")

    monkeypatch.setattr(os, "link", failing_link)

    with pytest.raises(OSError):
        await _atomic_write_text(
            target,
            "payload",
            append=False,
            overwrite=False,
        )

    assert target.exists() is False
    # Own unique temp is gone; stale sibling remains untouched.
    assert not any(
        p.name != stale.name and p.name.endswith(".sythion-tmp")
        for p in tmp_path.iterdir()
    )
    assert stale.read_text(encoding="utf-8") == "stale"


@pytest.mark.asyncio
async def test_direct_create_only_no_replace(tmp_path):
    target = tmp_path / "direct.txt"
    target.write_text("old", encoding="utf-8")
    with pytest.raises(_ExistsError):
        await _atomic_write_text(
            target,
            "new",
            append=False,
            overwrite=False,
        )
    assert target.read_text(encoding="utf-8") == "old"


@pytest.mark.asyncio
async def test_concurrent_same_target_writers(tmp_path):
    """Real concurrent create-only writers: exactly one wins, others FILE-ish failure."""
    target = tmp_path / "contested.txt"
    payloads = [f"payload-{i}" for i in range(8)]
    results = []

    async def worker(payload):
        try:
            await _atomic_write_text(
                target, payload, append=False, overwrite=False
            )
            return ("ok", payload)
        except _ExistsError:
            return ("exists", payload)

    gathered = await asyncio.gather(*(worker(p) for p in payloads))
    results.extend(gathered)

    winners = [r for r in results if r[0] == "ok"]
    losers = [r for r in results if r[0] == "exists"]
    assert len(winners) == 1
    assert len(losers) == len(payloads) - 1
    assert target.read_text(encoding="utf-8") == winners[0][1]
    # No unique temp files remain.
    assert not any(p.name.endswith(".sythion-tmp") for p in tmp_path.iterdir())


@pytest.mark.asyncio
async def test_cancelled_create_cleans_temp(tmp_path):
    target = tmp_path / "guarded.txt"

    async def cancelled():
        return True

    with pytest.raises(_CancelledError):
        await _atomic_write_text(
            target,
            "payload",
            append=False,
            overwrite=False,
            cancelled_check=cancelled,
        )

    assert target.exists() is False
    assert not any(p.name.endswith(".sythion-tmp") for p in tmp_path.iterdir())

    # Stable targets (no stale temp collissions) remain.
    stale = tmp_path / ".sythion-tmp"
    stale.write_text("stale", encoding="utf-8")
    assert stale.read_text(encoding="utf-8") == "stale"


@pytest.mark.asyncio
async def test_append_preserves_combine_behavior(file_executor, tmp_path):
    target = tmp_path / "append.txt"
    target.write_text("start|", encoding="utf-8")
    out = await file_executor.execute_tool(
        "file_write",
        {"path": str(target), "content": "end", "append": True},
        policy_context=PolicyContext(direct_user_instruction=True),
    )
    assert out.success is True
    assert out.data["verified"] is True
    assert target.read_text(encoding="utf-8") == "start|end"


@pytest.mark.asyncio
async def test_platform_link_is_atomic_no_replace():
    assert hasattr(os, "link")


@pytest.mark.asyncio
async def test_append_create_mode_replaces_without_exists_race(tmp_path):
    target = tmp_path / "append-new.txt"
    # Append to a non-existing target creates it (replace publication allowed).
    final = await _atomic_write_text(
        target, "hello", append=True, overwrite=False
    )
    assert final == "hello"
    assert target.read_text(encoding="utf-8") == "hello"
