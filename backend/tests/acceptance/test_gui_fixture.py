"""
Acceptance gate: Phase D fixture-application test (TODO #82).

Runs the REAL comtypes-backed UIA driver over the REAL MTA worker against a
dedicated native Win32 fixture application (`scripts/gui_fixture_app.py`),
proving observe / focus / invoke / value actually reach a live window and have
real effects before GUI actions are enabled generally.

The fixture is a plain Win32 window with standard controls so the built-in UIA
providers expose Window, Invoke (button), and Value (edit) patterns.

The test is skipped when there is no interactive Windows desktop available
(e.g. a locked session or headless CI), because the desktop gate legitimately
denies GUI actions there.
"""
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time

import pytest

from backend.tools.gui_tools import (
    GUIObserveTool,
    GUIFocusTool,
    GUIInvokeTool,
    GUISetValueTool,
    _desktop_gate,
)
from backend.api.schemas import (
    GUIObserveInput,
    GUIFocusInput,
    GUIInvokeInput,
    GUISetValueInput,
)

# This module needs a live interactive Windows desktop and the real UI
# Automation stack, so it is the one module in the repository that carries
# the `gui` marker. The marker restates the module docstring above; it adds
# no new claim.
pytestmark = pytest.mark.gui

FIXTURE_TITLE = "Sythion (Synth) GUI Fixture"


def _fixture_script() -> str:
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    return os.path.join(repo_root, "scripts", "gui_fixture_app.py")


def _read_edit_text(hwnd: int) -> str:
    """Read an EDIT control's text cross-process via WM_GETTEXT."""
    buf = ctypes.create_unicode_buffer(1024)
    ctypes.windll.user32.SendMessageW(hwnd, 0x000D, 1024, buf)
    return buf.value


def _read_status_text(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    ctypes.windll.user32.GetWindowTextW(hwnd, buf, 256)
    return buf.value


@pytest.fixture
def fixture_window():
    """Launch the dedicated fixture app and yield its child HWNDs."""
    if os.name != "nt":
        pytest.skip("fixture test is Windows-only")
    gate = _desktop_gate()
    if gate:
        pytest.skip(f"no interactive desktop available: {gate['code']}")

    tmpdir = tempfile.mkdtemp(prefix="sythion-fixture-")
    hwnds_path = os.path.join(tmpdir, "hwnds.json")
    proc = subprocess.Popen(
        [sys.executable, _fixture_script(), "--hwnds-path", hwnds_path, "--title", FIXTURE_TITLE],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(50):
            if os.path.exists(hwnds_path):
                break
            time.sleep(0.1)
        with open(hwnds_path, encoding="utf-8") as fh:
            hwnds = {k: int(v) for k, v in json.load(fh).items()}
        yield hwnds
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.mark.asyncio
async def test_observe_finds_fixture_window(fixture_window):
    tool = GUIObserveTool()
    out = await tool.execute(GUIObserveInput(window_title=FIXTURE_TITLE))
    assert out.success is True
    assert out.data["window"]["title"] == FIXTURE_TITLE
    assert out.data["window"]["hwnd"] == fixture_window["window"]
    assert out.data["window"]["pid"] is not None
    assert out.data["bounded"] is True
    assert out.data["ui_elements"] is not None
    assert "Fixture Button" in str(out.data["ui_elements"])


@pytest.mark.asyncio
async def test_invoke_flips_fixture_status(fixture_window):
    tool = GUIInvokeTool()
    assert _read_status_text(fixture_window["status"]) == "idle"
    out = await tool.execute(
        GUIInvokeInput(window_title=FIXTURE_TITLE, element_description="Fixture Button"))
    assert out.success is True
    assert out.data["method"] == "uia_invoke"
    time.sleep(0.2)
    assert _read_status_text(fixture_window["status"]) == "clicked"


@pytest.mark.asyncio
async def test_set_value_writes_fixture_edit(fixture_window):
    tool = GUISetValueTool()
    out = await tool.execute(
        GUISetValueInput(window_title=FIXTURE_TITLE, element_description="102", value="hello-fixture"))
    assert out.success is True
    assert out.data["method"] == "uia_value"
    time.sleep(0.2)
    assert _read_edit_text(fixture_window["edit"]) == "hello-fixture"


@pytest.mark.asyncio
async def test_focus_brings_fixture_window_foreground(fixture_window):
    tool = GUIFocusTool()
    out = await tool.execute(GUIFocusInput(window_title=FIXTURE_TITLE))
    assert out.success is True
    assert out.data["focused_window"] == FIXTURE_TITLE
