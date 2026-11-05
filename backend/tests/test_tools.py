"""
Comprehensive unit tests for Sythion (Synth) tool framework
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import os
import tempfile
import shutil

try:
    from backend.tools.executor import PolicyContext, ToolExecutor, BaseTool, ToolOutput
except ImportError:
    from backend.tools.executor import PolicyContext, ToolExecutor, BaseTool, ToolOutput
from pydantic import BaseModel


class MockInput(BaseModel):
    """Mock input schema for testing"""
    value: str = ""


class MockTool(BaseTool):
    """Mock tool for testing"""
    name = "mock_tool"
    description = "A mock tool for testing"
    input_schema = MockInput
    
    async def execute(self, input_data: BaseModel) -> ToolOutput:
        return ToolOutput(success=True, data={"result": "success"})
    
    async def verify(self, input_data: BaseModel, output: ToolOutput) -> bool:
        return True
    
    def get_policy_requirements(self) -> dict:
        return {"risk_level": "low", "requires_confirmation": False}


class MutatingMockTool(MockTool):
    name = "mutating_mock_tool"

    def get_policy_requirements(self) -> dict:
        return {"risk_level": "medium", "requires_confirmation": True}


class TestToolExecutor:
    """Test the tool executor framework"""
    
    @pytest.fixture
    def executor(self):
        return ToolExecutor()
    
    @pytest.mark.asyncio
    async def test_register_and_execute_tool(self, executor):
        """Test registering and executing a tool"""
        from backend.tools.system_tools import SystemTimeTool

        tool = SystemTimeTool()
        executor.register_tool(tool)

        result = await executor.execute_tool("system_time", {})

        assert result.success is True
        assert "datetime" in result.data
    
    @pytest.mark.asyncio
    async def test_execute_nonexistent_tool(self, executor):
        """Test executing a non-existent tool"""
        result = await executor.execute_tool("nonexistent_tool", {})
        
        assert result.success is False
        assert "not found" in result.error["message"].lower()
    
    @pytest.mark.asyncio
    async def test_execute_with_invalid_input(self, executor):
        """Test tool execution with invalid input"""
        from backend.tools.system_tools import SystemTimeTool

        tool = SystemTimeTool()
        executor.register_tool(tool)

        # Empty input should work since fields have defaults
        result = await executor.execute_tool("system_time", {})

        assert result.success is True  # Verified safe read-only tool succeeds

    @pytest.mark.asyncio
    async def test_mutating_tool_requires_trusted_direct_instruction(self, executor):
        tool = MutatingMockTool()
        executor.register_tool(tool)

        denied = await executor.execute_tool("mutating_mock_tool", {"value": "test"})
        also_denied = await executor.execute_tool(
            "mutating_mock_tool",
            {"value": "test"},
            policy_context=PolicyContext(direct_user_instruction=True),
        )

        assert denied.success is False
        assert denied.error["code"] == "DIRECT_INSTRUCTION_REQUIRED"
        # Caller-supplied direct-user consent is not an authenticated boundary.
        assert also_denied.success is False
        assert also_denied.error["code"] == "DIRECT_INSTRUCTION_REQUIRED"
    
    @pytest.mark.asyncio
    async def test_list_tools(self, executor):
        """Test listing available tools"""
        tool = MockTool()
        executor.register_tool(tool)
        
        tools = executor.list_tools()
        
        assert len(tools) == 1
        assert "mock_tool" in tools
    
    @pytest.mark.asyncio
    async def test_get_tool(self, executor):
        """Test getting a tool by name"""
        tool = MockTool()
        executor.register_tool(tool)
        
        retrieved = executor.get_tool("mock_tool")
        
        assert retrieved is not None
        assert retrieved.name == "mock_tool"
        
        # Non-existent tool
        assert executor.get_tool("nonexistent") is None


class TestSystemTools:
    """Test system-related tools"""
    
    @pytest.fixture
    def system_time_tool(self):
        from backend.tools.system_tools import SystemTimeTool
        return SystemTimeTool()
    
    @pytest.fixture
    def system_battery_tool(self):
        from backend.tools.system_tools import SystemBatteryTool
        return SystemBatteryTool()
    
    @pytest.fixture
    def system_disk_tool(self):
        from backend.tools.system_tools import SystemDiskTool
        return SystemDiskTool()
    
    @pytest.fixture
    def system_processes_tool(self):
        from backend.tools.system_tools import SystemProcessesTool
        return SystemProcessesTool()
    
    @pytest.fixture
    def system_active_window_tool(self):
        from backend.tools.system_tools import SystemActiveWindowTool
        return SystemActiveWindowTool()
    
    @pytest.fixture
    def system_file_roots_tool(self):
        from backend.tools.system_tools import SystemFileRootsTool
        return SystemFileRootsTool()
    
    @pytest.mark.asyncio
    async def test_get_time(self, system_time_tool):
        """Test getting current time"""
        from backend.api.schemas import SystemTimeInput
        result = await system_time_tool.execute(SystemTimeInput())
        
        assert result.success is True
        assert "datetime" in result.data or "time" in result.data
        assert "timezone" in result.data
    
    @pytest.mark.asyncio
    async def test_get_battery_status_no_battery(self, system_battery_tool):
        """Test battery status when no battery is present"""
        from backend.api.schemas import SystemBatteryInput
        with patch('psutil.sensors_battery', return_value=None):
            result = await system_battery_tool.execute(SystemBatteryInput())
            
            assert result.success is True
            assert result.data.get("available") is False
    
    @pytest.mark.asyncio
    async def test_get_disk_usage(self, system_disk_tool):
        """Test disk usage retrieval"""
        from backend.api.schemas import SystemDiskInput
        result = await system_disk_tool.execute(SystemDiskInput(path="/"))
        
        assert result.success is True
        assert "total_gb" in result.data or "total" in result.data
        assert "used_gb" in result.data or "used" in result.data
        assert "free_gb" in result.data or "free" in result.data
    
    @pytest.mark.asyncio
    async def test_list_processes(self, system_processes_tool):
        """Test listing processes"""
        from backend.api.schemas import SystemProcessesInput
        result = await system_processes_tool.execute(SystemProcessesInput(limit=5))
        
        assert result.success is True
        assert isinstance(result.data, dict)
        assert "processes" in result.data
        assert isinstance(result.data["processes"], list)
        assert len(result.data["processes"]) <= 5
        if result.data["processes"]:
            assert "pid" in result.data["processes"][0]
            assert "name" in result.data["processes"][0]

    @pytest.mark.asyncio
    async def test_list_processes_with_cpu(self, system_processes_tool):
        """Test listing processes with CPU stats"""
        from backend.api.schemas import SystemProcessesInput
        result = await system_processes_tool.execute(
            SystemProcessesInput(limit=5, include_cpu=True)
        )
        
        assert result.success is True
        assert result.data["include_cpu"] is True
        if result.data["processes"]:
            assert "cpu_percent" in result.data["processes"][0]

    @pytest.mark.asyncio
    async def test_list_processes_with_memory(self, system_processes_tool):
        """Test listing processes with memory stats"""
        from backend.api.schemas import SystemProcessesInput
        result = await system_processes_tool.execute(
            SystemProcessesInput(limit=5, include_memory=True)
        )
        
        assert result.success is True
        assert result.data["include_memory"] is True
        if result.data["processes"]:
            assert "memory_mb" in result.data["processes"][0]

    @pytest.mark.asyncio
    async def test_list_processes_with_children(self, system_processes_tool):
        """Test listing processes with child count"""
        from backend.api.schemas import SystemProcessesInput
        result = await system_processes_tool.execute(
            SystemProcessesInput(limit=5, include_children=True)
        )
        
        assert result.success is True
        if result.data["processes"]:
            assert "child_count" in result.data["processes"][0]

    @pytest.mark.asyncio
    async def test_list_processes_sort_by_cpu(self, system_processes_tool):
        """Test sorting processes by CPU"""
        from backend.api.schemas import SystemProcessesInput
        result = await system_processes_tool.execute(
            SystemProcessesInput(limit=10, include_cpu=True, sort_by="cpu")
        )
        
        assert result.success is True
        if len(result.data["processes"]) > 1:
            cpus = [p.get("cpu_percent", 0) for p in result.data["processes"]]
            assert cpus == sorted(cpus, reverse=True)

    @pytest.mark.asyncio
    async def test_list_processes_sort_by_memory(self, system_processes_tool):
        """Test sorting processes by memory"""
        from backend.api.schemas import SystemProcessesInput
        result = await system_processes_tool.execute(
            SystemProcessesInput(limit=10, include_memory=True, sort_by="memory")
        )
        
        assert result.success is True
        if len(result.data["processes"]) > 1:
            mems = [p.get("memory_mb", 0) for p in result.data["processes"]]
            assert mems == sorted(mems, reverse=True)
    
    @pytest.mark.asyncio
    async def test_get_active_window_linux(self, system_active_window_tool):
        """Test getting active window on Linux"""
        from backend.api.schemas import SystemActiveWindowInput
        result = await system_active_window_tool.execute(SystemActiveWindowInput())
        
        # On Linux without GUI, should return unavailable or succeed with limited info
        assert result.success is True  # Tool should not fail, just report unavailability

    @pytest.mark.asyncio
    async def test_file_roots_reports_approved_roots(self, system_file_roots_tool, monkeypatch, tmp_path):
        """The introspection tool reports exactly the enforced approved roots."""
        from backend.api.schemas import SystemFileRootsInput
        one = tmp_path / "one"
        two = tmp_path / "two"
        monkeypatch.setattr(
            "backend.tools.file_tools._approved_roots",
            lambda: [one.resolve(), two.resolve()],
        )
        result = await system_file_roots_tool.execute(SystemFileRootsInput())

        assert result.success is True
        assert result.data["approved_roots"] == [str(one.resolve()), str(two.resolve())]
        assert result.data["home"]
        assert await system_file_roots_tool.verify(SystemFileRootsInput(), result) is True

    @pytest.mark.asyncio
    async def test_capabilities_reports_inventory_and_gaps(self):
        """The capability-introspection tool reflects the registered toolset,
        the per-mode model-visible subsets, and the known gaps."""
        from backend.api.schemas import SystemCapabilitiesInput
        from backend.tools.system_tools import SystemCapabilitiesTool

        result = await SystemCapabilitiesTool().execute(SystemCapabilitiesInput())

        assert result.success is True
        data = result.data
        assert data["registered_count"] >= 34
        names = {t["name"] for t in data["tools"]}
        assert "system_file_roots" in names
        assert "system_capabilities" in names
        assert "file_write" in names
        assert len(data["known_gaps"]) > 0

        # Model-visible subsets are consistent with policy filtering.
        assert "system_capabilities" in data["model_visible_guarded"]
        assert "file_write" not in data["model_visible_guarded"]
        assert "package_install" not in data["model_visible_guarded"]

        # Every tool row carries its policy flags and visibility.
        for t in data["tools"]:
            assert t["name"] and t["domain"]
            assert "flags" in t and "risk_level" in t

    @pytest.mark.skip(reason="Skipped: volume tests can affect system audio during test runs")
    @pytest.mark.asyncio
    async def test_volume_tool_reads_current_level(self, monkeypatch):
        """The volume tool reads the master volume via Core Audio COM."""
        from backend.tools.system_tools import SystemVolumeTool
        from backend.api.schemas import SystemVolumeInput

        tool = SystemVolumeTool()
        result = await tool.execute(SystemVolumeInput())

        # On a real Windows machine with audio, this should succeed.
        # On CI/headless, COM may fail — treat that as a known limitation.
        if result.success:
            assert "volume_percentage" in result.data
            assert 0 <= result.data["volume_percentage"] <= 100
            assert "is_muted" in result.data
            assert isinstance(result.data["is_muted"], bool)
            assert await tool.verify(SystemVolumeInput(), result) is True
        else:
            # COM failure on headless/CI — verify it fails closed
            assert result.error["code"] in ("VOLUME_ERROR", "COMTYPES_MISSING")

    @pytest.mark.skip(reason="Skipped: volume tests can affect system audio during test runs")
    @pytest.mark.asyncio
    async def test_volume_tool_set_volume(self, monkeypatch):
        """The volume tool can set volume level."""
        from backend.tools.system_tools import SystemVolumeTool
        from backend.api.schemas import SystemVolumeInput

        tool = SystemVolumeTool()
        result = await tool.execute(SystemVolumeInput(volume_percentage=50.0))

        if result.success:
            assert abs(result.data["volume_percentage"] - 50.0) < 2.0
            assert await tool.verify(SystemVolumeInput(volume_percentage=50.0), result) is True
        else:
            assert result.error["code"] in ("VOLUME_ERROR", "COMTYPES_MISSING")

    @pytest.mark.skip(reason="Skipped: volume tests can affect system audio during test runs")
    @pytest.mark.asyncio
    async def test_volume_tool_set_mute(self, monkeypatch):
        """The volume tool can toggle mute state."""
        from backend.tools.system_tools import SystemVolumeTool
        from backend.api.schemas import SystemVolumeInput

        tool = SystemVolumeTool()
        result = await tool.execute(SystemVolumeInput(is_mute=True))

        if result.success:
            assert result.data["is_muted"] is True
            assert await tool.verify(SystemVolumeInput(is_mute=True), result) is True
        else:
            assert result.error["code"] in ("VOLUME_ERROR", "COMTYPES_MISSING")


class TestFileTools:
    """Test file system tools"""
    
    @pytest.fixture
    def temp_dir(self, monkeypatch):
        """Create a temporary directory for tests and approve it as a file root."""
        from pathlib import Path
        dirpath = tempfile.mkdtemp()
        monkeypatch.setattr(
            "backend.tools.file_tools._approved_roots",
            lambda: [Path(dirpath).resolve()],
        )
        yield dirpath
        shutil.rmtree(dirpath)
    
    @pytest.mark.asyncio
    async def test_list_directory(self, temp_dir):
        """Test listing directory contents"""
        from backend.tools.file_tools import FileListTool
        from backend.api.schemas import FileListInput
        
        tool = FileListTool()
        # Create test files
        os.makedirs(os.path.join(temp_dir, "subdir"))
        with open(os.path.join(temp_dir, "test.txt"), "w") as f:
            f.write("test content")
        
        result = await tool.execute(FileListInput(path=temp_dir))
        
        assert result.success is True
        assert "items" in result.data or "entries" in result.data
        items = result.data.get("items", result.data.get("entries", []))
        names = [e["name"] for e in items]
        assert "test.txt" in names
        assert "subdir" in names
    
    @pytest.mark.asyncio
    async def test_read_file(self, temp_dir):
        """Test reading a file"""
        from backend.tools.file_tools import FileReadTool
        from backend.api.schemas import FileReadInput
        
        tool = FileReadTool()
        test_content = "Hello, World!"
        file_path = os.path.join(temp_dir, "test.txt")
        
        with open(file_path, "w") as f:
            f.write(test_content)
        
        result = await tool.execute(FileReadInput(path=file_path))
        
        assert result.success is True
        assert result.data["content"] == test_content
        assert result.data["size"] == len(test_content)
    
    @pytest.mark.asyncio
    async def test_read_nonexistent_file(self, temp_dir):
        """Test reading a non-existent file"""
        from backend.tools.file_tools import FileReadTool
        from backend.api.schemas import FileReadInput
        
        tool = FileReadTool()
        missing = os.path.join(temp_dir, "does_not_exist.txt")
        result = await tool.execute(FileReadInput(path=missing))
        
        assert result.success is False
        assert "not found" in result.error.get("message", "").lower()

    @pytest.mark.asyncio
    async def test_path_outside_roots_rejected(self):
        """Test that paths outside approved roots are rejected"""
        from backend.tools.file_tools import FileReadTool
        from backend.api.schemas import FileReadInput
        
        tool = FileReadTool()
        # Paths outside the approved roots must be refused (e.g. system dirs)
        result = await tool.execute(FileReadInput(path="C:\\Windows\\win.ini"))
        
        assert result.success is False
        assert "outside approved roots" in result.error.get("message", "").lower()
    
    @pytest.mark.asyncio
    async def test_create_file(self, temp_dir):
        """Test creating a new file"""
        from backend.tools.file_tools import FileCreateTool
        from backend.api.schemas import FileCreateInput
        
        tool = FileCreateTool()
        file_path = os.path.join(temp_dir, "newfile.txt")
        result = await tool.execute(FileCreateInput(path=file_path, content="initial content"))
        
        assert result.success is True
        assert os.path.exists(file_path)
    
    @pytest.mark.asyncio
    async def test_create_existing_file(self, temp_dir):
        """Test creating a file that already exists"""
        from backend.tools.file_tools import FileCreateTool
        from backend.api.schemas import FileCreateInput
        
        tool = FileCreateTool()
        # Create the file first
        file_path = os.path.join(temp_dir, "existing.txt")
        with open(file_path, "w") as f:
            f.write("existing")
        
        result = await tool.execute(FileCreateInput(path=file_path, content="new content"))
        
        assert result.success is False
        assert "exists" in result.error.get("message", "").lower()
    
    @pytest.mark.asyncio
    async def test_write_file(self, temp_dir):
        """Test writing to a file"""
        from backend.tools.file_tools import FileWriteTool
        from backend.api.schemas import FileWriteInput
        
        tool = FileWriteTool()
        file_path = os.path.join(temp_dir, "write_test.txt")
        
        # Write initially
        result1 = await tool.execute(FileWriteInput(path=file_path, content="first line\n"))
        assert result1.success is True
        
        # Append
        result2 = await tool.execute(FileWriteInput(path=file_path, content="second line\n", append=True))
        assert result2.success is True
        
        # Read back
        with open(file_path, "r") as f:
            content = f.read()
        
        assert "first line" in content
        assert "second line" in content
    
    @pytest.mark.asyncio
    async def test_delete_file(self, temp_dir):
        """Test deleting a file"""
        from backend.tools.file_tools import FileDeleteTool
        from backend.api.schemas import FileDeleteInput
        
        tool = FileDeleteTool()
        file_path = os.path.join(temp_dir, "to_delete.txt")
        
        # Create the file
        with open(file_path, "w") as f:
            f.write("to delete")
        
        result = await tool.execute(FileDeleteInput(path=file_path))
        
        assert result.success is True
        assert not os.path.exists(file_path)


class TestToolMetadata:
    """Test tool metadata and validation"""
    
    def test_tool_metadata_structure(self):
        """Verify all tools have required metadata"""
        from backend.tools.executor import ToolExecutor
        
        executor = ToolExecutor()
        
        for tool_name in executor.list_tools():
            tool = executor.get_tool(tool_name)
            if tool:
                policy_reqs = tool.get_policy_requirements()
                assert policy_reqs is not None, f"Tool {tool_name} missing policy requirements"
                assert "risk_level" in policy_reqs
                assert policy_reqs["risk_level"] in ["low", "medium", "high", "critical"]
                assert "requires_confirmation" in policy_reqs


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
