"""Mocked prototype fail-closed tests for host-data observation tools.

No filesystem, browser, window, process, clipboard, UI, profile, network, vault,
mirror, or OS interactions occur: every tool is exercised under the prototype
build identity and must refuse before touching any external surface.
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import patch

import pytest

from backend.core import secrets as secrets_module


@pytest.fixture(autouse=True)
def _prototype_env(monkeypatch):
    for var in (
        "SYTHION_PROTECT_BUILD",
        "SYTHION_PROTOTYPE_BUILD",
        "SYTHION_IS_PROTECT",
        "SYTHION_IS_PROTOTYPE",
        "SYTHION_PROTECT",
        "SYTHION_PROTOTYPE",
        "SYTHION_BUILD_IDENTITY",
        "SYTHION_PRODUCT_NAME",
        "SYTHION_APP_IDENTITY",
        "SYTHION_IDENTIFIER",
        "SYTHION_PRODUCT_IDENTIFIER",
        "SYTHION_SECRET_NAMESPACE",
        "SYTHION_SECRET_TARGET_PREFIX",
        "SYTHION_SECRET_SERVICE",
        "SYTHION_DATA_DIR",
        "Sythion (Synth)_DATA_DIR",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("SYTHION_PROTECT_BUILD", "1")
    secrets_module.set_secret_store_for_testing(None)
    yield
    secrets_module.set_secret_store_for_testing(None)


@pytest.mark.asyncio
async def test_system_processes_prototype_disabled(monkeypatch):
    from backend.tools.system_tools import SystemProcessesTool, SystemProcessesInput

    calls = {"process_iter": 0}

    def fake_process_iter(*args, **kwargs):
        calls["process_iter"] += 1
        return []

    monkeypatch.setattr("psutil.process_iter", fake_process_iter, raising=False)

    tool = SystemProcessesTool()
    result = await tool.execute(SystemProcessesInput())
    assert result.success is False
    assert result.error["code"] == "PROTOTYPE_HOST_DATA_DISABLED"
    assert calls["process_iter"] == 0


@pytest.mark.asyncio
async def test_system_active_window_prototype_disabled(monkeypatch):
    from backend.tools.system_tools import SystemActiveWindowTool, SystemActiveWindowInput

    calls = {"get_foreground": 0}

    def fake_get_foreground():
        calls["get_foreground"] += 1
        return 1

    monkeypatch.setattr("win32gui.GetForegroundWindow", fake_get_foreground, raising=False)

    tool = SystemActiveWindowTool()
    result = await tool.execute(SystemActiveWindowInput())
    assert result.success is False
    assert result.error["code"] == "PROTOTYPE_HOST_DATA_DISABLED"
    assert calls["get_foreground"] == 0


@pytest.mark.asyncio
async def test_window_list_prototype_disabled(monkeypatch):
    from backend.tools.system_tools import WindowListTool, WindowListInput

    calls = {"enum_windows": 0}

    def fake_enum_windows(*args, **kwargs):
        calls["enum_windows"] += 1

    monkeypatch.setattr("win32gui.EnumWindows", fake_enum_windows, raising=False)

    tool = WindowListTool()
    result = await tool.execute(WindowListInput())
    assert result.success is False
    assert result.error["code"] == "PROTOTYPE_HOST_DATA_DISABLED"
    assert calls["enum_windows"] == 0


@pytest.mark.asyncio
async def test_browser_tabs_prototype_disabled(monkeypatch):
    from backend.tools.system_tools import BrowserTabsTool, BrowserTabsInput

    calls = {"get_instance": 0}

    class FakeBridge:
        is_connected = True

        async def get_tabs(self):
            calls["get_instance"] += 1
            return []

    monkeypatch.setattr(
        "backend.services.browser_bridge_service.BrowserBridgeService.get_instance",
        staticmethod(lambda: FakeBridge()),
        raising=False,
    )

    tool = BrowserTabsTool()
    result = await tool.execute(BrowserTabsInput())
    assert result.success is False
    assert result.error["code"] == "PROTOTYPE_HOST_DATA_DISABLED"
    assert calls["get_instance"] == 0


@pytest.mark.asyncio
async def test_browser_read_page_prototype_disabled(monkeypatch):
    from backend.tools.system_tools import BrowserReadPageTool, BrowserReadPageInput

    calls = {"get_instance": 0}

    class FakeBridge:
        is_connected = True

        async def read_page(self, tab_id=None):
            calls["get_instance"] += 1
            return {}

    monkeypatch.setattr(
        "backend.services.browser_bridge_service.BrowserBridgeService.get_instance",
        staticmethod(lambda: FakeBridge()),
        raising=False,
    )

    tool = BrowserReadPageTool()
    result = await tool.execute(BrowserReadPageInput())
    assert result.success is False
    assert result.error["code"] == "PROTOTYPE_HOST_DATA_DISABLED"
    assert calls["get_instance"] == 0


@pytest.mark.asyncio
async def test_browser_screenshot_prototype_disabled(monkeypatch):
    from backend.tools.system_tools import BrowserScreenshotTool, BrowserScreenshotInput

    calls = {"get_instance": 0}

    class FakeBridge:
        is_connected = True

        async def take_screenshot(self, tab_id=None):
            calls["get_instance"] += 1
            return {"success": True, "image_base64": "", "title": "t", "url": "u", "tab_id": 1}

    monkeypatch.setattr(
        "backend.services.browser_bridge_service.BrowserBridgeService.get_instance",
        staticmethod(lambda: FakeBridge()),
        raising=False,
    )

    tool = BrowserScreenshotTool()
    result = await tool.execute(BrowserScreenshotInput())
    assert result.success is False
    assert result.error["code"] == "PROTOTYPE_HOST_DATA_DISABLED"
    assert calls["get_instance"] == 0


@pytest.mark.asyncio
async def test_clipboard_read_prototype_disabled(monkeypatch):
    from backend.tools.system_tools import ClipboardReadTool, ClipboardReadInput

    calls = {"open_clipboard": 0}

    class FakeUser32:
        def OpenClipboard(self, *args, **kwargs):
            calls["open_clipboard"] += 1
            return 1

    monkeypatch.setattr("ctypes.windll.user32", FakeUser32(), raising=False)

    tool = ClipboardReadTool()
    result = await tool.execute(ClipboardReadInput())
    assert result.success is False
    assert result.error["code"] == "PROTOTYPE_HOST_DATA_DISABLED"
    assert calls["open_clipboard"] == 0


@pytest.mark.asyncio
async def test_gui_observe_prototype_disabled(monkeypatch):
    from backend.tools.gui_tools import GUIObserveTool, GUIObserveInput

    calls = {"connect": 0}

    class FakeDriver:
        def _session(self):
            calls["connect"] += 1

    monkeypatch.setattr("backend.tools.gui_tools._UIADriver", lambda: FakeDriver(), raising=False)

    tool = GUIObserveTool()
    result = await tool.execute(GUIObserveInput())
    assert result.success is False
    assert result.error["code"] == "PROTOTYPE_HOST_DATA_DISABLED"
    assert calls["connect"] == 0


@pytest.mark.asyncio
async def test_gui_screenshot_prototype_disabled(monkeypatch):
    from backend.tools.gui_tools import GUIScreenshotTool, GUIScreenshotInput

    calls = {"capture": 0}

    tool = GUIScreenshotTool()
    result = await tool.execute(GUIScreenshotInput())
    assert result.success is False
    assert result.error["code"] == "PROTOTYPE_HOST_DATA_DISABLED"
    assert calls["capture"] == 0
