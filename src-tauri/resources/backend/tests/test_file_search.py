"""
Tests for the FileSearchTool.

Contract: searches file contents for a text pattern, returns matching lines
with file path, line number, and content. Respects approved roots, hidden
files, and file extension filters.
"""

import pytest
from pathlib import Path

from backend.tools.file_tools import FileSearchTool
from backend.api.schemas import FileSearchInput, ToolOutput


@pytest.fixture
def search_tool():
    return FileSearchTool()


@pytest.fixture
def test_files(tmp_path, monkeypatch):
    """Create test files for search and mock approved roots."""
    monkeypatch.setattr("backend.tools.file_tools._approved_roots", lambda: [tmp_path])
    (tmp_path / "hello.txt").write_text("Hello world\nFoo bar\nHello again")
    (tmp_path / "data.py").write_text("def hello():\n    return 'world'")
    (tmp_path / "empty.txt").write_text("")
    subdir = tmp_path / "subdir"
    subdir.mkdir()
    (subdir / "nested.txt").write_text("Hello from nested\nBar baz")
    hidden = tmp_path / ".hidden"
    hidden.mkdir()
    (hidden / "secret.txt").write_text("Hidden hello")
    return tmp_path


class TestFileSearchExecute:
    def test_finds_matches(self, search_tool, test_files):
        """Should find matching lines across files."""
        import asyncio
        input_data = FileSearchInput(path=str(test_files), pattern="hello")
        output = asyncio.run(search_tool.execute(input_data))

        assert output.success is True
        assert output.data["match_count"] >= 3
        assert output.data["truncated"] is False
        files_found = {m["file"] for m in output.data["matches"]}
        assert any("hello.txt" in f for f in files_found)
        assert any("data.py" in f for f in files_found)

    def test_case_insensitive(self, search_tool, test_files):
        """Search should be case-insensitive."""
        import asyncio
        input_data = FileSearchInput(path=str(test_files), pattern="HELLO")
        output = asyncio.run(search_tool.execute(input_data))

        assert output.success is True
        assert output.data["match_count"] >= 3

    def test_no_matches(self, search_tool, test_files):
        """Should return zero matches when pattern not found."""
        import asyncio
        input_data = FileSearchInput(path=str(test_files), pattern="zzzznotfound")
        output = asyncio.run(search_tool.execute(input_data))

        assert output.success is True
        assert output.data["match_count"] == 0
        assert output.data["matches"] == []

    def test_hidden_files_excluded_by_default(self, search_tool, test_files):
        """Hidden directories should be excluded by default."""
        import asyncio
        input_data = FileSearchInput(path=str(test_files), pattern="hello")
        output = asyncio.run(search_tool.execute(input_data))

        hidden_matches = [m for m in output.data["matches"] if ".hidden" in m["file"]]
        assert len(hidden_matches) == 0

    def test_hidden_files_included(self, search_tool, test_files):
        """When include_hidden=True, hidden files should be searched."""
        import asyncio
        input_data = FileSearchInput(path=str(test_files), pattern="hello", include_hidden=True)
        output = asyncio.run(search_tool.execute(input_data))

        hidden_matches = [m for m in output.data["matches"] if ".hidden" in m["file"]]
        assert len(hidden_matches) >= 1

    def test_file_extension_filter(self, search_tool, test_files):
        """Should only search files matching given extensions."""
        import asyncio
        input_data = FileSearchInput(path=str(test_files), pattern="hello", file_extensions=[".py"])
        output = asyncio.run(search_tool.execute(input_data))

        assert output.success is True
        for m in output.data["matches"]:
            assert m["file"].endswith(".py")

    def test_max_results_limit(self, search_tool, test_files):
        """Should respect max_results limit."""
        import asyncio
        input_data = FileSearchInput(path=str(test_files), pattern="hello", max_results=2)
        output = asyncio.run(search_tool.execute(input_data))

        assert output.success is True
        assert output.data["match_count"] == 2
        assert output.data["truncated"] is True

    def test_max_results_one(self, search_tool, test_files):
        """Should return exactly one result when max_results=1."""
        import asyncio
        input_data = FileSearchInput(path=str(test_files), pattern="hello", max_results=1)
        output = asyncio.run(search_tool.execute(input_data))

        assert output.success is True
        assert output.data["match_count"] == 1

    def test_path_not_found(self, search_tool):
        """Should fail gracefully for nonexistent path."""
        import asyncio
        input_data = FileSearchInput(path="/nonexistent/path/abc123", pattern="test")
        output = asyncio.run(search_tool.execute(input_data))

        assert output.success is False
        assert "TARGET_OUTSIDE_ROOTS" in output.error.get("code", "")

    def test_empty_pattern_rejected(self, search_tool, test_files):
        """Should reject empty pattern."""
        import asyncio
        input_data = FileSearchInput(path=str(test_files), pattern="")
        output = asyncio.run(search_tool.execute(input_data))

        assert output.success is False
        assert "PATTERN_REQUIRED" in output.error.get("code", "")

    @pytest.mark.asyncio
    async def test_verify_returns_true_on_success(self, search_tool, test_files):
        """Verify should return True for successful search."""
        input_data = FileSearchInput(path=str(test_files), pattern="hello")
        output = await search_tool.execute(input_data)

        assert await search_tool.verify(input_data, output) is True

    def test_get_policy_requirements(self, search_tool):
        """Should return read-only policy."""
        reqs = search_tool.get_policy_requirements()
        assert reqs["risk_level"] == "none"
        assert reqs["requires_confirmation"] is False
