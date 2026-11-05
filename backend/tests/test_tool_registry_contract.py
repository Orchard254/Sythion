from pydantic import BaseModel

from backend.tools.executor import ToolExecutor
from backend.tools.registry import register_all_tools


def test_registered_tools_have_usable_input_schemas():
    executor = ToolExecutor()
    register_all_tools(executor)
    names = executor.list_tools()
    assert "device_status" in names
    for name in names:
        tool = executor.get_tool(name)
        schema = getattr(tool, "input_schema", None)
        assert isinstance(schema, type) and issubclass(schema, BaseModel), name
        assert isinstance(schema.model_json_schema(), dict), name
