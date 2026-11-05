"""Regression tests for bounded, junction-safe file search traversal."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from backend.core import secrets as secrets_module
from backend.core.config import settings
from backend.tools import file_tools
from backend.tools.file_tools import FileSearchTool
from backend.api.schemas import FileSearchInput


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in (
        "SYTHION_PROTECT_BUILD",
        "SYTHION_PROTOTYPE_BUILD",
        "SYTHION_IS_PROTECT",
        "SYTHION_IS_PROTOTYPE",
        "SYTHION_DATA_DIR",
        "Sythion (Synth)_DATA_DIR",
    ):
        monkeypatch.delenv(var, raising=False)
    secrets_module.set_secret_store_for_testing(None)
    yield


@pytest.fixture
def approved_root(tmp_path):
    from backend.core.db_manager import DatabaseManager
    from backend.models.database import set_db_manager

    root = tmp_path / "root"
    root.mkdir()
    db_mgr = DatabaseManager(str(tmp_path / "junction.db"))
    set_db_manager(db_mgr)
    settings.file_roots = [str(root)]
    yield root
    set_db_manager(None)


def _junction_supported() -> bool:
    if os.name != "nt":
        return False
    try:
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "src"
            dst = Path(d) / "dst"
            src.mkdir()
            dst.mkdir()
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(src / "j"), str(dst)],
                check=True,
                capture_output=True,
            )
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _junction_supported(), reason="Windows junctions unavailable")
def test_file_search_skips_junction_and_cyclic_junction(approved_root, tmp_path):
    root = approved_root
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "leak.txt").write_text("LEAKED_JUNCTION_CONTENT", encoding="utf-8")
    (root / "normal.txt").write_text("NORMAL_ROOT_CONTENT", encoding="utf-8")

    # Junction to an outside directory plus a cyclic junction back to root.
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(root / "escape"), str(outside)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(root / "cycle"), str(root)],
        check=True,
        capture_output=True,
    )

    import asyncio

    async def run():
        tool = FileSearchTool()
        return await tool.execute(
            FileSearchInput(path=str(root), pattern="CONTENT", max_results=100)
        )

    result = asyncio.run(run())
    assert result.success is True
    matches = result.data["matches"]
    texts = {m["content"] for m in matches}
    assert "NORMAL_ROOT_CONTENT" in texts
    assert "LEAKED_JUNCTION_CONTENT" not in texts
    # Ensure traversal terminated (process reaching here proves bounded).
    assert result.data["match_count"] >= 1
