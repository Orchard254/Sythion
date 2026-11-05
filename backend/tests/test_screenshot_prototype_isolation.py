"""Focused tests for prototype screenshot path isolation."""

import base64
import os
import tempfile
from pathlib import Path

import pytest

from backend.core import secrets as secrets_module
from backend.tools.system_tools import BrowserScreenshotTool
from backend.tools.gui_tools import GUIScreenshotTool
from backend.api.schemas import BrowserScreenshotInput, GUIScreenshotInput

_ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


class FakeBridge:
    def __init__(self):
        self._active_socket = object()

    @property
    def is_connected(self):
        return True

    async def take_screenshot(self, tab_id=None):
        return {
            "success": True,
            "image_base64": base64.b64encode(_ONE_PIXEL_PNG).decode("ascii"),
            "title": "tab",
            "url": "https://example.test",
            "tab_id": tab_id or 1,
        }


@pytest.fixture(autouse=True)
def _prototype_env(monkeypatch):
    for var in (
        "SYTHION_PROTECT_BUILD",
        "SYTHION_PROTOTYPE_BUILD",
        "SYTHION_DATA_DIR",
        "Sythion (Synth)_DATA_DIR",
    ):
        monkeypatch.delenv(var, raising=False)
    for var in (
        "SYTHION_PROTECT_BUILD",
        "SYTHION_PROTOTYPE_BUILD",
        "SYTHION_DATA_DIR",
        "Sythion (Synth)_DATA_DIR",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("SYTHION_PROTECT_BUILD", "1")
    secrets_module.set_secret_store_for_testing(None)
    yield


@pytest.fixture
def isolated_profile(monkeypatch):
    d = tempfile.mkdtemp()
    profile = Path(d) / "profile"
    profile.mkdir()
    monkeypatch.setenv("SYTHION_DATA_DIR", str(profile))
    return profile


@pytest.fixture
def fake_bridge(monkeypatch):
    fake = FakeBridge()
    monkeypatch.setattr(
        "backend.services.browser_bridge_service.BrowserBridgeService.get_instance",
        staticmethod(lambda: fake),
    )
    return fake


@pytest.mark.asyncio
async def test_browser_screenshot_default_stays_inside_profile(
    isolated_profile, fake_bridge
):
    tool = BrowserScreenshotTool()
    result = await tool.execute(BrowserScreenshotInput())
    # Prototype fail-closed: host observation is refused before any bridge
    # interaction, so no artifact is produced.
    assert result.success is False
    assert result.error["code"] == "PROTOTYPE_HOST_DATA_DISABLED"


@pytest.mark.asyncio
async def test_browser_screenshot_rejects_explicit_outside_path(
    isolated_profile, fake_bridge
):
    outside = Path(tempfile.mkdtemp()) / "outside.png"
    tool = BrowserScreenshotTool()
    result = await tool.execute(BrowserScreenshotInput(save_path=str(outside)))
    assert result.success is False
    assert result.error["code"] in {"TARGET_OUTSIDE_ROOTS", "PROTOTYPE_HOST_DATA_DISABLED"}
    assert not outside.exists()


@pytest.mark.asyncio
async def test_browser_screenshot_rejects_traversal_path(
    isolated_profile, fake_bridge
):
    traversal = str(isolated_profile / "screenshots" / ".." / ".." / "escape.png")
    tool = BrowserScreenshotTool()
    result = await tool.execute(BrowserScreenshotInput(save_path=traversal))
    assert result.success is False
    assert result.error["code"] in {"TARGET_OUTSIDE_ROOTS", "PROTOTYPE_HOST_DATA_DISABLED"}


@pytest.mark.asyncio
async def test_browser_screenshot_fails_closed_when_data_dir_unavailable(
    fake_bridge, monkeypatch
):
    def _raise():
        raise RuntimeError("data dir unavailable")

    monkeypatch.setattr("backend.core.storage.data_dir", _raise)
    tool = BrowserScreenshotTool()
    result = await tool.execute(BrowserScreenshotInput())
    assert result.success is False


@pytest.mark.asyncio
async def test_gui_screenshot_rejects_explicit_outside_path(isolated_profile):
    outside = Path(tempfile.mkdtemp()) / "outside.png"
    tool = GUIScreenshotTool()
    result = await tool.execute(GUIScreenshotInput(save_path=str(outside)))
    assert result.success is False
    assert result.error["code"] in {"TARGET_OUTSIDE_ROOTS", "PROTOTYPE_HOST_DATA_DISABLED"}
    assert not outside.exists()
