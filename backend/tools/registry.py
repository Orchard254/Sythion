"""
Central tool registration.

The executor used by the API routes is the one passed to the task runner and
therefore the source of the model-visible tool subset. Registering tools in
two places (routes + main lifespan) caused drift, so every entrypoint builds
the same executor through this single factory.
"""

from typing import Iterable

from backend.tools.executor import ToolExecutor, BaseTool


def create_all_tools() -> Iterable[BaseTool]:
    """Yield every registered tool exactly once, in a stable order."""
    from backend.tools.system_tools import create_system_tools
    from backend.tools.file_tools import create_file_tools
    from backend.tools.app_tools import create_app_tools
    from backend.tools.package_tools import create_package_tools
    from backend.tools.gui_tools import create_gui_tools
    from backend.tools.memory_tools import (
        MemoryStoreTool,
        MemoryRecallTool,
        MemoryForgetTool,
        MemoryLinkTool,
        MemoryRecallRelatedTool,
        ActivityRecallTool,
    )
    from backend.tools.web_tools import WebSearchTool, WebFetchTool

    yield from create_system_tools()
    yield from create_file_tools()
    yield from create_app_tools()
    yield from create_package_tools()
    yield from create_gui_tools()
    yield MemoryStoreTool()
    yield MemoryRecallTool()
    yield MemoryForgetTool()
    yield MemoryLinkTool()
    yield MemoryRecallRelatedTool()
    yield ActivityRecallTool()
    yield WebSearchTool()
    yield WebFetchTool()


def register_all_tools(executor: ToolExecutor) -> None:
    """Populate an executor with the full registered tool set."""
    for tool in create_all_tools():
        executor.register_tool(tool)
