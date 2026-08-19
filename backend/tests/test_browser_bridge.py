"""
Tests for BrowserBridgeService and Browser Bridge WebSocket endpoints.
"""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from backend.services.browser_bridge_service import BrowserBridgeService
from backend.api.schemas import (
    BrowserTabsInput,
    BrowserReadPageInput,
    BrowserInteractInput,
    BrowserScreenshotInput,
    ToolOutput,
)
from backend.tools.system_tools import (
    BrowserTabsTool,
    BrowserReadPageTool,
    BrowserInteractTool,
    BrowserScreenshotTool,
)




@pytest.fixture
def bridge_service():
    service = BrowserBridgeService()
    # Reset singleton state
    service._active_socket = None
    service._pending_requests.clear()
    service._browser_info.clear()
    return service


@pytest.mark.asyncio
async def test_bridge_service_initial_state(bridge_service):
    """Bridge should start disconnected."""
    assert bridge_service.is_connected is False
    assert bridge_service.browser_info == {}


@pytest.mark.asyncio
async def test_bridge_service_connect_and_disconnect(bridge_service):
    """Bridge should connect and clean up properly."""
    mock_ws = AsyncMock()
    await bridge_service.connect(mock_ws, {"browser": "Chrome 120"})

    assert bridge_service.is_connected is True
    assert bridge_service.browser_info["browser"] == "Chrome 120"

    await bridge_service.disconnect()
    assert bridge_service.is_connected is False


@pytest.mark.asyncio
async def test_bridge_service_send_command_and_response(bridge_service):
    """Bridge should correlate request ID and resolve RPC futures."""
    mock_ws = AsyncMock()
    await bridge_service.connect(mock_ws)

    # Launch send_command task
    cmd_task = asyncio.create_task(bridge_service.get_tabs())

    # Yield to let send_command send payload
    await asyncio.sleep(0.01)
    assert mock_ws.send_text.called

    # Extract sent message
    sent_payload = json.loads(mock_ws.send_text.call_args[0][0])
    req_id = sent_payload["id"]
    assert sent_payload["action"] == "get_tabs"

    # Simulate extension response
    fake_response = {
        "id": req_id,
        "result": {
            "count": 3,
            "tabs": [
                {"id": 1, "title": "Google Antigravity Auth Success", "url": "https://google.com", "active": True},
                {"id": 2, "title": "YouTube", "url": "https://youtube.com", "active": False},
                {"id": 3, "title": "GitHub", "url": "https://github.com", "active": False},
            ],
        },
    }
    bridge_service.handle_incoming_message(json.dumps(fake_response))

    result_tabs = await cmd_task
    assert len(result_tabs) == 3
    assert result_tabs[0]["title"] == "Google Antigravity Auth Success"
    assert result_tabs[1]["title"] == "YouTube"
    assert result_tabs[2]["title"] == "GitHub"


@pytest.mark.asyncio
async def test_browser_tabs_tool_with_bridge_connected(monkeypatch):
    """BrowserTabsTool should query BrowserBridgeService first when connected."""
    service = BrowserBridgeService.get_instance()
    mock_ws = AsyncMock()
    await service.connect(mock_ws)

    tool = BrowserTabsTool()

    # Intercept send_command to return canned tabs
    async def fake_send(action, params=None, timeout=8.0):
        return {
            "count": 7,
            "tabs": [
                {"id": 1, "title": "Google Antigravity Auth Success", "active": True},
                {"id": 2, "title": "YouTube", "active": False},
                {"id": 3, "title": "GitHub", "active": False},
                {"id": 4, "title": "Gmail", "active": False},
                {"id": 5, "title": "Discord", "active": False},
                {"id": 6, "title": "Reddit", "active": False},
                {"id": 7, "title": "StackOverflow", "active": False},
            ],
        }

    monkeypatch.setattr(service, "send_command", fake_send)

    output = await tool.execute(BrowserTabsInput())

    assert output.success is True
    assert output.data["count"] == 7
    assert len(output.data["tabs"]) == 7
    assert output.data["source"] == "browser_extension"
    assert "Discovered 7 open Chrome tab(s)" in output.model_summary
    assert "ALL TABS" in output.model_summary
    assert "Google Antigravity Auth Success" in output.model_summary
    assert "StackOverflow" in output.model_summary

    await service.disconnect()


@pytest.mark.asyncio
async def test_browser_read_page_tool(monkeypatch):
    """BrowserReadPageTool should extract structured content and interactive elements."""
    service = BrowserBridgeService.get_instance()
    mock_ws = AsyncMock()
    await service.connect(mock_ws)

    tool = BrowserReadPageTool()

    async def fake_send(action, params=None, timeout=8.0):
        return {
            "title": "YouTube - Focus Music",
            "url": "https://www.youtube.com/watch?v=123",
            "headings": [{"level": "h1", "text": "Deep Work Ambient Mix"}],
            "text_snippet": "Continuous concentration soundtrack for coding and thinking.",
            "interactive_elements": [
                {"type": "input", "selector": "input[name=search_query]", "label": "Search"},
                {"type": "button", "selector": "button.play", "label": "Play"},
            ],
        }

    monkeypatch.setattr(service, "send_command", fake_send)

    output = await tool.execute(BrowserReadPageInput())

    assert output.success is True
    assert output.data["title"] == "YouTube - Focus Music"
    assert "YouTube - Focus Music" in output.model_summary
    assert "Deep Work Ambient Mix" in output.model_summary
    assert "input[name=search_query]" in output.model_summary

    await service.disconnect()


@pytest.mark.asyncio
async def test_browser_interact_tool(monkeypatch):
    """BrowserInteractTool should dispatch click/type/scroll commands."""
    service = BrowserBridgeService.get_instance()
    mock_ws = AsyncMock()
    await service.connect(mock_ws)

    tool = BrowserInteractTool()

    async def fake_send(action, params=None, timeout=8.0):
        return {
            "success": True,
            "action": params.get("action_type"),
            "target": params.get("selector"),
            "typed_text": params.get("text"),
        }

    monkeypatch.setattr(service, "send_command", fake_send)

    # 1. Type into search box
    output = await tool.execute(BrowserInteractInput(
        action="type",
        selector="input[name=search_query]",
        text="Amapiano 2026",
        key="Enter",
    ))

    assert output.success is True
    assert "Typed 'Amapiano 2026'" in output.model_summary
    assert "input[name=search_query]" in output.model_summary

    # 2. Click button
    output_click = await tool.execute(BrowserInteractInput(
        action="click",
        selector="button.play",
    ))
    assert output_click.success is True
    assert "Clicked element 'button.play'" in output_click.model_summary

    await service.disconnect()


@pytest.mark.asyncio
async def test_browser_screenshot_tool(monkeypatch, tmp_path):
    """BrowserScreenshotTool should capture base64 image and save to file."""
    import base64
    service = BrowserBridgeService.get_instance()
    mock_ws = AsyncMock()
    await service.connect(mock_ws)

    tool = BrowserScreenshotTool()
    assert tool.get_policy_requirements()["mutating"] is False
    assert tool.get_policy_requirements()["read_only"] is True

    # Create dummy 1x1 transparent PNG base64
    dummy_png_base64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="

    async def fake_send(action, params=None, timeout=8.0):
        return {
            "success": True,
            "tab_id": 123,
            "title": "TradingView Chart",
            "url": "https://www.tradingview.com",
            "image_base64": dummy_png_base64,
        }

    monkeypatch.setattr(service, "send_command", fake_send)

    custom_save = str(tmp_path / "chart_snapshot.png")
    output = await tool.execute(BrowserScreenshotInput(save_path=custom_save))

    assert output.success is True
    assert output.data["file_path"] == custom_save
    assert output.data["title"] == "TradingView Chart"
    assert (tmp_path / "chart_snapshot.png").exists()
    assert (tmp_path / "chart_snapshot.png").stat().st_size > 0
    assert "Captured screenshot of 'TradingView Chart'" in output.model_summary

    await service.disconnect()

