"""
Tests for the 3 new tools added in the tool audit:
- clipboard_read (system tool)
- file_hash (file tool)
- gui_screenshot (gui tool)
"""
import pytest
import asyncio
import os
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

try:
    from backend.tools.executor import ToolOutput
except ImportError:
    from tools.executor import ToolOutput


class TestFileHashTool:
    """Test file hash tool"""

    @pytest.fixture
    def temp_dir(self, monkeypatch):
        """Create a temporary directory for tests and approve it as a file root."""
        dirpath = tempfile.mkdtemp()
        monkeypatch.setattr(
            "backend.tools.file_tools._approved_roots",
            lambda: [Path(dirpath).resolve()],
        )
        yield dirpath
        shutil.rmtree(dirpath)

    @pytest.mark.asyncio
    async def test_file_hash_sha256(self, temp_dir):
        """Test SHA256 hash computation"""
        from backend.tools.file_tools import FileHashTool
        from backend.api.schemas import FileHashInput

        tool = FileHashTool()
        test_content = "Hello, World!"
        file_path = os.path.join(temp_dir, "test.txt")

        with open(file_path, "w") as f:
            f.write(test_content)

        result = await tool.execute(FileHashInput(path=file_path, algorithm="sha256"))

        assert result.success is True
        assert "hash" in result.data
        assert result.data["algorithm"] == "sha256"
        assert len(result.data["hash"]) == 64  # SHA256 hex digest length
        assert await tool.verify(FileHashInput(path=file_path), result) is True

    @pytest.mark.asyncio
    async def test_file_hash_md5(self, temp_dir):
        """Test MD5 hash computation"""
        from backend.tools.file_tools import FileHashTool
        from backend.api.schemas import FileHashInput

        tool = FileHashTool()
        test_content = "Test content for MD5"
        file_path = os.path.join(temp_dir, "test_md5.txt")

        with open(file_path, "w") as f:
            f.write(test_content)

        result = await tool.execute(FileHashInput(path=file_path, algorithm="md5"))

        assert result.success is True
        assert result.data["algorithm"] == "md5"
        assert len(result.data["hash"]) == 32  # MD5 hex digest length

    @pytest.mark.asyncio
    async def test_file_hash_sha1(self, temp_dir):
        """Test SHA1 hash computation"""
        from backend.tools.file_tools import FileHashTool
        from backend.api.schemas import FileHashInput

        tool = FileHashTool()
        test_content = "Test content for SHA1"
        file_path = os.path.join(temp_dir, "test_sha1.txt")

        with open(file_path, "w") as f:
            f.write(test_content)

        result = await tool.execute(FileHashInput(path=file_path, algorithm="sha1"))

        assert result.success is True
        assert result.data["algorithm"] == "sha1"
        assert len(result.data["hash"]) == 40  # SHA1 hex digest length

    @pytest.mark.asyncio
    async def test_file_hash_sha512(self, temp_dir):
        """Test SHA512 hash computation"""
        from backend.tools.file_tools import FileHashTool
        from backend.api.schemas import FileHashInput

        tool = FileHashTool()
        test_content = "Test content for SHA512"
        file_path = os.path.join(temp_dir, "test_sha512.txt")

        with open(file_path, "w") as f:
            f.write(test_content)

        result = await tool.execute(FileHashInput(path=file_path, algorithm="sha512"))

        assert result.success is True
        assert result.data["algorithm"] == "sha512"
        assert len(result.data["hash"]) == 128  # SHA512 hex digest length

    @pytest.mark.asyncio
    async def test_file_hash_invalid_algorithm(self, temp_dir):
        """Test invalid algorithm is rejected"""
        from backend.tools.file_tools import FileHashTool
        from backend.api.schemas import FileHashInput

        tool = FileHashTool()
        file_path = os.path.join(temp_dir, "test.txt")
        with open(file_path, "w") as f:
            f.write("test")

        result = await tool.execute(FileHashInput(path=file_path, algorithm="invalid"))

        assert result.success is False
        assert result.error["code"] == "INVALID_ALGORITHM"

    @pytest.mark.asyncio
    async def test_file_hash_nonexistent_file(self, temp_dir):
        """Test hash of nonexistent file"""
        from backend.tools.file_tools import FileHashTool
        from backend.api.schemas import FileHashInput

        tool = FileHashTool()
        result = await tool.execute(FileHashInput(path=os.path.join(temp_dir, "missing.txt")))

        assert result.success is False
        assert result.error["code"] == "FILE_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_file_hash_directory_rejected(self, temp_dir):
        """Test hash of directory is rejected"""
        from backend.tools.file_tools import FileHashTool
        from backend.api.schemas import FileHashInput

        tool = FileHashTool()
        result = await tool.execute(FileHashInput(path=temp_dir))

        assert result.success is False
        assert result.error["code"] == "NOT_A_FILE"

    def test_file_hash_policy_requirements(self):
        """Test policy requirements are set correctly"""
        from backend.tools.file_tools import FileHashTool

        tool = FileHashTool()
        reqs = tool.get_policy_requirements()

        assert reqs["risk_level"] == "none"
        assert reqs["requires_confirmation"] is False

    @pytest.mark.asyncio
    async def test_file_hash_deterministic(self, temp_dir):
        """Test same file produces same hash"""
        from backend.tools.file_tools import FileHashTool
        from backend.api.schemas import FileHashInput

        tool = FileHashTool()
        file_path = os.path.join(temp_dir, "deterministic.txt")
        with open(file_path, "w") as f:
            f.write("deterministic content")

        result1 = await tool.execute(FileHashInput(path=file_path))
        result2 = await tool.execute(FileHashInput(path=file_path))

        assert result1.data["hash"] == result2.data["hash"]


class TestClipboardReadTool:
    """Test clipboard read tool"""

    @pytest.mark.asyncio
    async def test_clipboard_read_returns_output(self):
        """Test clipboard read returns a ToolOutput"""
        from backend.tools.system_tools import ClipboardReadTool
        from backend.api.schemas import ClipboardReadInput

        tool = ClipboardReadTool()
        result = await tool.execute(ClipboardReadInput())

        assert isinstance(result, ToolOutput)
        # On CI/headless, clipboard may not be available
        if result.success:
            assert "content" in result.data or "format" in result.data
        else:
            assert result.error["code"] in ("CLIPBOARD_ERROR",)

    @pytest.mark.asyncio
    async def test_clipboard_read_verify(self):
        """Test verify method"""
        from backend.tools.system_tools import ClipboardReadTool
        from backend.api.schemas import ClipboardReadInput

        tool = ClipboardReadTool()
        result = await tool.execute(ClipboardReadInput())

        if result.success:
            assert await tool.verify(ClipboardReadInput(), result) is True

    def test_clipboard_read_policy_requirements(self):
        """Test policy requirements are set correctly"""
        from backend.tools.system_tools import ClipboardReadTool

        tool = ClipboardReadTool()
        reqs = tool.get_policy_requirements()

        assert reqs["risk_level"] == "low"
        assert reqs["requires_confirmation"] is False
        assert reqs["privacy_sensitive"] is True


class TestGUIScreenshotTool:
    """Test GUI screenshot tool"""

    @pytest.mark.asyncio
    async def test_gui_screenshot_returns_output(self):
        """Test gui screenshot returns a ToolOutput"""
        from backend.tools.gui_tools import GUIScreenshotTool
        from backend.api.schemas import GUIScreenshotInput

        tool = GUIScreenshotTool()
        result = await tool.execute(GUIScreenshotInput())

        assert isinstance(result, ToolOutput)
        # On headless/CI, screenshot may fail but should return proper error
        if not result.success:
            assert result.error["code"] in ("NOT_SUPPORTED", "DC_FAILED", "SCREENSHOT_ERROR", "MISSING_DEPENDENCY")

    @pytest.mark.asyncio
    async def test_gui_screenshot_policy_requirements(self):
        """Test policy requirements are set correctly"""
        from backend.tools.gui_tools import GUIScreenshotTool

        tool = GUIScreenshotTool()
        reqs = tool.get_policy_requirements()

        assert reqs["risk_level"] == "low"
        assert reqs["requires_confirmation"] is False
        assert reqs["requires_screen_access"] is True
        assert reqs["privacy_sensitive"] is True

    @pytest.mark.asyncio
    async def test_gui_screenshot_with_window_title(self):
        """Test gui screenshot with window title"""
        from backend.tools.gui_tools import GUIScreenshotTool
        from backend.api.schemas import GUIScreenshotInput

        tool = GUIScreenshotTool()
        result = await tool.execute(GUIScreenshotInput(window_title="NonExistentWindow12345"))

        assert isinstance(result, ToolOutput)
        if not result.success:
            assert result.error["code"] in ("NOT_SUPPORTED", "WINDOW_NOT_FOUND", "SCREENSHOT_ERROR", "MISSING_DEPENDENCY")

    @pytest.mark.asyncio
    async def test_gui_screenshot_verify_method(self):
        """Test verify method"""
        from backend.tools.gui_tools import GUIScreenshotTool
        from backend.api.schemas import GUIScreenshotInput
        from backend.tools.executor import ToolOutput

        tool = GUIScreenshotTool()
        output = ToolOutput(success=True, data={"path": "/test/screenshot.png", "width": 100, "height": 100})
        assert await tool.verify(GUIScreenshotInput(), output) is True

        output_fail = ToolOutput(success=False, error={"code": "ERROR"})
        assert await tool.verify(GUIScreenshotInput(), output_fail) is False


class TestGUIBatchFormTool:
    """Test GUI batch form tool"""

    @pytest.mark.asyncio
    async def test_gui_batch_form_returns_output(self):
        """Test gui batch form returns a ToolOutput"""
        from backend.tools.gui_tools import GUIBatchFormTool
        from backend.api.schemas import GUIBatchFormInput

        tool = GUIBatchFormTool()
        result = await tool.execute(GUIBatchFormInput(
            window_title="NonExistentWindow12345",
            fields=[{"element": "test", "value": "value"}]
        ))

        assert isinstance(result, ToolOutput)
        # On headless/CI, batch form may fail but should return proper structure
        if not result.success:
            # Error can be in data.results or in error field
            if result.data and "results" in result.data:
                assert result.data["failed_count"] > 0
            elif result.error:
                assert result.error["code"] in ("NOT_SUPPORTED", "WINDOW_NOT_FOUND", "UIA_UNAVAILABLE")

    @pytest.mark.asyncio
    async def test_gui_batch_form_no_fields(self):
        """Test gui batch form with no fields"""
        from backend.tools.gui_tools import GUIBatchFormTool
        from backend.api.schemas import GUIBatchFormInput

        tool = GUIBatchFormTool()
        result = await tool.execute(GUIBatchFormInput(
            window_title="TestWindow",
            fields=[]
        ))

        assert result.success is False
        assert result.error["code"] == "NO_FIELDS"

    @pytest.mark.asyncio
    async def test_gui_batch_form_too_many_fields(self):
        """Test gui batch form with too many fields"""
        from backend.tools.gui_tools import GUIBatchFormTool
        from backend.api.schemas import GUIBatchFormInput

        tool = GUIBatchFormTool()
        fields = [{"element": f"field_{i}", "value": f"value_{i}"} for i in range(21)]
        result = await tool.execute(GUIBatchFormInput(
            window_title="TestWindow",
            fields=fields
        ))

        assert result.success is False
        assert result.error["code"] == "TOO_MANY_FIELDS"

    @pytest.mark.asyncio
    async def test_gui_batch_form_policy_requirements(self):
        """Test policy requirements are set correctly"""
        from backend.tools.gui_tools import GUIBatchFormTool

        tool = GUIBatchFormTool()
        reqs = tool.get_policy_requirements()

        assert reqs["risk_level"] == "high"
        assert reqs["requires_confirmation"] is True
        assert reqs["requires_ui_automation"] is True


class TestToolRegistry:
    """Test that new tools are properly registered"""

    def test_all_62_tools_registered(self):
        """Test that all 62 tools are registered"""
        from backend.tools.registry import create_all_tools

        tools = list(create_all_tools())
        tool_names = [t.name for t in tools]

        assert len(tools) == 62
        assert "clipboard_read" in tool_names
        assert "file_hash" in tool_names
        assert "gui_screenshot" in tool_names
        assert "gui_batch_form" in tool_names
        assert "memory_stats" in tool_names
        assert "memory_export" in tool_names

    def test_new_tools_have_correct_policy(self):
        """Test new tools have correct policy requirements"""
        from backend.tools.registry import create_all_tools

        tools = list(create_all_tools())
        tools_by_name = {t.name: t for t in tools}

        # clipboard_read
        cr = tools_by_name["clipboard_read"]
        assert cr.get_policy_requirements()["risk_level"] == "low"
        assert cr.get_policy_requirements()["privacy_sensitive"] is True

        # file_hash
        fh = tools_by_name["file_hash"]
        assert fh.get_policy_requirements()["risk_level"] == "none"

        # gui_screenshot
        gs = tools_by_name["gui_screenshot"]
        assert gs.get_policy_requirements()["risk_level"] == "low"
        assert gs.get_policy_requirements()["requires_screen_access"] is True

        # gui_batch_form
        gb = tools_by_name["gui_batch_form"]
        assert gb.get_policy_requirements()["risk_level"] == "high"
        assert gb.get_policy_requirements()["requires_confirmation"] is True
        assert gb.get_policy_requirements()["requires_ui_automation"] is True

        # memory_stats
        ms = tools_by_name["memory_stats"]
        assert ms.get_policy_requirements()["risk_level"] == "none"
        assert ms.get_policy_requirements()["mutating"] is False

        # memory_export
        me = tools_by_name["memory_export"]
        assert me.get_policy_requirements()["risk_level"] == "none"
        assert me.get_policy_requirements()["mutating"] is False
