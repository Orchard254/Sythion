"""
Acceptance gate: Package actions restricted to fixed-argument winget
(Phase C #68/#69).

Package install/uninstall call winget only, with exact ids and no
agreement/elevation auto-accept flags; other package managers and free-form
flags are gone. The executor policy gate additionally blocks these tools with
ELEVATION_NOT_ALLOWED (standard-user actions only).
"""

import platform

import pytest

from backend.tools.executor import ToolExecutor, PolicyContext
from backend.tools.package_tools import create_package_tools


@pytest.fixture
def pkg_executor(monkeypatch):
    ex = ToolExecutor()
    for tool in create_package_tools():
        ex.register_tool(tool)
    return ex


class _FakeResult:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.mark.asyncio
async def test_policy_blocks_install_with_elevation_error(pkg_executor):
    out = await pkg_executor.execute_tool(
        "package_install",
        {"package_id": "Notepad++"},
        policy_context=PolicyContext(direct_user_instruction=True),
    )
    assert not out.success
    assert out.error["code"] == "ELEVATION_NOT_ALLOWED"


@pytest.mark.asyncio
async def test_policy_blocks_uninstall_with_elevation_error(pkg_executor):
    out = await pkg_executor.execute_tool(
        "package_uninstall",
        {"package_id": "Notepad++"},
        policy_context=PolicyContext(direct_user_instruction=True),
    )
    assert not out.success
    assert out.error["code"] == "ELEVATION_NOT_ALLOWED"


@pytest.mark.asyncio
async def test_install_uses_fixed_winget_args_no_agreement_flags(pkg_executor, monkeypatch):
    """Executed directly (bypassing policy): only fixed winget args, never accept flags."""
    calls = []

    def _run(cmd, **kwargs):
        calls.append(list(cmd))
        return _FakeResult(returncode=0)

    monkeypatch.setattr("backend.tools.package_tools.subprocess.run", _run)
    tool = pkg_executor.get_tool("package_install")
    out = await tool.execute(tool.input_schema(package_id="Notepad++"))
    assert out.success
    assert calls == [["winget", "install", "--id", "Notepad++", "--silent"]]


@pytest.mark.asyncio
async def test_uninstall_uses_fixed_winget_args(pkg_executor, monkeypatch):
    calls = []

    def _run(cmd, **kwargs):
        calls.append(list(cmd))
        return _FakeResult(returncode=0)

    monkeypatch.setattr("backend.tools.package_tools.subprocess.run", _run)
    tool = pkg_executor.get_tool("package_uninstall")
    out = await tool.execute(tool.input_schema(package_id="Notepad++"))
    assert out.success
    assert calls == [["winget", "uninstall", "--id", "Notepad++", "--silent"]]


@pytest.mark.asyncio
async def test_install_rejects_unapproved_source(pkg_executor, monkeypatch):
    called = {"run": False}

    def _run(*args, **kwargs):
        called["run"] = True
        return _FakeResult(returncode=0)

    monkeypatch.setattr("backend.tools.package_tools.subprocess.run", _run)
    tool = pkg_executor.get_tool("package_install")
    out = await tool.execute(tool.input_schema(package_id="Notepad++", source="chocolatey"))
    assert not out.success
    assert out.error["code"] == "INVALID_PACKAGE_SOURCE"
    assert not called["run"]


@pytest.mark.asyncio
async def test_install_non_windows_rejected(pkg_executor, monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    tool = pkg_executor.get_tool("package_install")
    out = await tool.execute(tool.input_schema(package_id="Notepad++"))
    assert not out.success
    assert out.error["code"] == "NON_WINDOWS_UNSUPPORTED"


@pytest.mark.asyncio
async def test_install_failure_surfaces_agreement_needed(pkg_executor, monkeypatch):
    """When winget needs agreements, the failure is surfaced, not auto-accepted."""
    monkeypatch.setattr(
        "backend.tools.package_tools.subprocess.run",
        lambda *a, **k: _FakeResult(returncode=1, stderr="This source requires accepting agreements"),
    )
    tool = pkg_executor.get_tool("package_install")
    out = await tool.execute(tool.input_schema(package_id="SomePkg"))
    assert not out.success
    assert out.error["code"] == "INSTALL_FAILED"
    assert "does not auto-accept" in out.error["message"]


@pytest.mark.asyncio
async def test_search_parses_winget_json(pkg_executor, monkeypatch):
    import json
    payload = json.dumps({"Packages": [{"Id": "Foo.Bar", "Name": "Foo", "Version": "1.0", "Source": "winget"}]})
    monkeypatch.setattr(
        "backend.tools.package_tools.subprocess.run",
        lambda *a, **k: _FakeResult(returncode=0, stdout=payload),
    )
    out = await pkg_executor.execute_tool(
        "package_search",
        {"query": "foo"},
        policy_context=PolicyContext(direct_user_instruction=True),
    )
    assert out.success
    assert out.data["packages"][0]["id"] == "Foo.Bar"
