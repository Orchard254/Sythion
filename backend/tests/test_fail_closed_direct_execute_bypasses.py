"""Fake-only focused tests for direct built-in consequential/sensitive effect containment.

These tests use counters and in-memory SQLite only. They do not touch filesystem,
devices, profiles, vault, network, staging, reset, or commit operations.
"""

import subprocess
import pytest
from unittest.mock import patch


@pytest.mark.asyncio
async def test_direct_app_launch_does_not_touch_registry_or_os(monkeypatch):
    from backend.tools.app_tools import AppLaunchTool, AppLaunchInput
    import backend.tools.app_tools as mod

    calls = {"registry": 0, "run": 0, "popen": 0, "focus": 0}

    def fake_apps():
        calls["registry"] += 1
        return []

    def fake_resolve(app_id):
        calls["registry"] += 1
        return None

    def fake_run(*args, **kwargs):
        calls["run"] += 1
        return None

    class FakePopen:
        def __init__(self, *args, **kwargs):
            calls["popen"] += 1

    class FakeFocusTool:
        def __init__(self, *args, **kwargs):
            pass

        async def execute(self, *args, **kwargs):
            calls["focus"] += 1
            return ToolOutput(success=True, data={})

    monkeypatch.setattr(mod, "_windows_registered_apps", fake_apps)
    monkeypatch.setattr(mod, "_resolve_registered_app", fake_resolve)
    monkeypatch.setattr(subprocess, "run", fake_run, raising=False)
    monkeypatch.setattr(subprocess, "Popen", FakePopen, raising=False)
    monkeypatch.setattr(mod, "AppFocusTool", FakeFocusTool, raising=False)

    tool = AppLaunchTool()
    result = await tool.execute(AppLaunchInput(app_id="app", new_instance=True))

    assert result.success is False
    assert calls == {"registry": 0, "run": 0, "popen": 0, "focus": 0}
from pydantic import BaseModel as PBaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.schemas import TaskState
from backend.core.importance_classifier import classify_action, Importance
from backend.models.database import Base, Task
from backend.tools.executor import (
    BaseTool,
    ToolExecutor,
    PolicyContext,
    ToolOutput,
    is_reviewed_safe_builtin,
)


class _Input(PBaseModel):
    value: str = "x"


class _UnreviewedInput(PBaseModel):
    value: str = "x"


class _FakeConsequentialTool(BaseTool):
    name = "fake_consequential"
    description = "fake consequential effect"
    input_schema = _UnreviewedInput

    def __init__(self):
        self.calls = 0

    def get_policy_requirements(self):
        # Looks safe in metadata, but identity is not a reviewed safe builtin.
        return {"risk_level": "none", "requires_confirmation": False, "read_only": True, "app_internal": True}

    async def execute(self, _input):
        self.calls += 1
        return ToolOutput(success=True, data={"called": True})

    async def verify(self, _input, output):
        return True


@pytest.mark.asyncio
async def test_unreviewed_consequential_effect_is_not_dispatched():
    tool = _FakeConsequentialTool()
    executor = ToolExecutor()
    executor.register_tool(tool)

    result = await executor.execute_tool("fake_consequential", {"value": "x"})

    assert tool.calls == 0
    assert result.success is False
    assert result.error["code"] == "UNREVIEWED_IMPLEMENTATION"


def test_app_internal_classification_is_consequential():
    policy = {"risk_level": "low", "requires_confirmation": False, "mutating": True, "app_internal": True}
    assert classify_action("memory_store", policy) is Importance.CONSEQUENTIAL


@pytest.mark.asyncio
async def test_real_memory_store_effect_requires_trusted_consent_boundary():
    from backend.tools.persistent_memory_tools import MemoryStoreTool

    tool = MemoryStoreTool()
    # Genuinely reviewed implementation, but not a reviewed safe auto-dispatch
    # builtin.
    assert is_reviewed_safe_builtin(tool) is False

    executor = ToolExecutor()
    executor.register_tool(tool)
    result = await executor.execute_tool("memory_store", {"key": "k", "value": "v"})

    assert result.success is False
    assert result.error["code"] == "DIRECT_INSTRUCTION_REQUIRED"


@pytest.mark.asyncio
async def test_real_app_focus_effect_requires_trusted_consent_boundary():
    from backend.tools.app_tools import AppFocusTool

    tool = AppFocusTool()
    assert is_reviewed_safe_builtin(tool) is False

    executor = ToolExecutor()
    executor.register_tool(tool)
    result = await executor.execute_tool("app_focus", {"app_id": "x"})

    assert result.success is False
    assert result.error["code"] == "DIRECT_INSTRUCTION_REQUIRED"
