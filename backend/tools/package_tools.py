"""
Package Management Tools for JARVIS

v1 is Windows-only and restricts package actions to fixed-argument `winget`
operations with exact package ids. No other package manager is invoked, no
free-form flags are accepted, agreements are never auto-accepted, and no
elevation is ever requested (the policy gate also blocks these tools with
ELEVATION_NOT_ALLOWED for defense in depth).
"""
import subprocess
import platform
import json
import re
from typing import Dict, Any, List, Optional

from tools.executor import BaseTool
from api.schemas import (
    ToolOutput,
    PackageSearchInput,
    PackageListInput,
    PackageInstallInput,
    PackageUninstallInput,
)

# Package ids are passed as argument-list entries (never through a shell), but a
# value starting with `-`/`--` could be parsed as a tool flag (e.g. winget).
# Restrict ids to sane package-name characters.
_PACKAGE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+\-]*$")

# The only accepted install source; everything else is rejected.
_APPROVED_SOURCES = {None, "", "winget"}


def _validate_package_id(package_id: str) -> None:
    if not package_id or not _PACKAGE_ID_PATTERN.match(package_id):
        raise ValueError(f"Invalid package_id: {package_id!r}")


def _package_target_error(package_id: str) -> Optional[Dict[str, str]]:
    """Deterministic, side-effect-free validation of a package target."""
    try:
        _validate_package_id(package_id)
    except ValueError as e:
        return {"code": "INVALID_PACKAGE_ID", "message": str(e)}
    return None


def _wingset_json(data):
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return None


class PackageSearchTool(BaseTool):
    """Search for packages in package managers."""

    name = "package_search"
    description = "Search for packages in system package managers"
    input_schema = PackageSearchInput

    async def execute(self, input_data: PackageSearchInput) -> ToolOutput:
        """Execute the package search tool."""
        try:
            if platform.system() != "Windows":
                return ToolOutput(
                    success=False,
                    error={"code": "NON_WINDOWS_UNSUPPORTED", "message": "package tools are Windows-only in v1"},
                    duration_ms=0
                )

            results = []
            try:
                result = subprocess.run(
                    ["winget", "search", input_data.query, "--format", "json"],
                    capture_output=True,
                    text=True,
                    timeout=60
                )
                if result.returncode == 0:
                    data = _wingset_json(result.stdout)
                    if data:
                        for pkg in data.get('Packages', [])[:input_data.limit]:
                            results.append({
                                "id": pkg.get('Id', ''),
                                "name": pkg.get('Name', ''),
                                "version": pkg.get('Version', ''),
                                "source": pkg.get('Source', 'winget'),
                                "platform": "windows"
                            })
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass

            return ToolOutput(
                success=True,
                data={
                    "query": input_data.query,
                    "count": len(results),
                    "limit": input_data.limit,
                    "packages": results,
                    "platform": "windows"
                },
                evidence={"source": "package_manager_search"}
            )
        except Exception as e:
            return ToolOutput(
                success=False,
                error={"code": "PACKAGE_SEARCH_ERROR", "message": str(e)},
                duration_ms=0
            )

    async def verify(self, input_data: PackageSearchInput, output: ToolOutput) -> bool:
        """Verify the tool execution result."""
        return output.success and "packages" in (output.data or {})

    def get_policy_requirements(self) -> Dict[str, Any]:
        """Get policy requirements for this tool."""
        return {"risk_level": "none", "requires_confirmation": False}


class PackageListTool(BaseTool):
    """List installed packages."""

    name = "package_list"
    description = "List installed packages on the system"
    input_schema = PackageListInput

    async def execute(self, input_data: PackageListInput) -> ToolOutput:
        """Execute the package list tool."""
        try:
            if platform.system() != "Windows":
                return ToolOutput(
                    success=False,
                    error={"code": "NON_WINDOWS_UNSUPPORTED", "message": "package tools are Windows-only in v1"},
                    duration_ms=0
                )

            packages = []
            try:
                result = subprocess.run(
                    ["winget", "list", "--format", "json"],
                    capture_output=True,
                    text=True,
                    timeout=120
                )
                if result.returncode == 0:
                    data = _wingset_json(result.stdout)
                    if data:
                        for pkg in data:
                            name = pkg.get('Name', '')
                            if input_data.search_term and input_data.search_term.lower() not in name.lower():
                                continue
                            packages.append({
                                "id": pkg.get('Id', ''),
                                "name": name,
                                "version": pkg.get('Version', ''),
                                "source": "winget",
                                "platform": "windows"
                            })
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass

            return ToolOutput(
                success=True,
                data={
                    "count": len(packages),
                    "search_term": input_data.search_term,
                    "packages": packages[:200],
                    "platform": "windows"
                },
                evidence={"source": "package_manager_list"}
            )
        except Exception as e:
            return ToolOutput(
                success=False,
                error={"code": "PACKAGE_LIST_ERROR", "message": str(e)},
                duration_ms=0
            )

    async def verify(self, input_data: PackageListInput, output: ToolOutput) -> bool:
        """Verify the tool execution result."""
        return output.success and "packages" in (output.data or {})

    def get_policy_requirements(self) -> Dict[str, Any]:
        """Get policy requirements for this tool."""
        return {"risk_level": "none", "requires_confirmation": False}


class PackageInstallTool(BaseTool):
    """Install a package via winget."""

    name = "package_install"
    description = "Install a package using winget"
    input_schema = PackageInstallInput

    async def execute(self, input_data: PackageInstallInput) -> ToolOutput:
        """Execute the package install tool.

        Fixed-argument winget only. Agreement/elevation prompts are never
        auto-accepted: when winget requires them it fails and the result is
        surfaced to the user rather than silently accepted.
        """
        try:
            if platform.system() != "Windows":
                return ToolOutput(
                    success=False,
                    error={"code": "NON_WINDOWS_UNSUPPORTED", "message": "package tools are Windows-only in v1"},
                    duration_ms=0
                )

            package_id = input_data.package_id
            _validate_package_id(package_id)

            if input_data.source not in _APPROVED_SOURCES:
                return ToolOutput(
                    success=False,
                    error={"code": "INVALID_PACKAGE_SOURCE",
                           "message": f"Package source '{input_data.source}' is not approved; only winget is supported"},
                    duration_ms=0
                )

            try:
                result = subprocess.run(
                    ["winget", "install", "--id", package_id, "--silent"],
                    capture_output=True,
                    text=True,
                    timeout=300
                )
            except (FileNotFoundError, subprocess.TimeoutExpired) as e:
                return ToolOutput(
                    success=False,
                    error={"code": "INSTALL_ERROR", "message": f"Winget not available: {str(e)}"},
                    duration_ms=0
                )

            success = result.returncode == 0
            return ToolOutput(
                success=success,
                data={"package_id": package_id, "method": "winget", "platform": "windows", "source": "winget"}
                if success else None,
                error=None if success else {
                    "code": "INSTALL_FAILED",
                    "message": f"Failed to install {package_id} (agreements or elevation may be required; JARVIS does not auto-accept)",
                    "detail": (result.stderr or result.stdout).strip()[:2000]
                },
                evidence={"source": "package_manager_install"}
            )
        except Exception as e:
            return ToolOutput(
                success=False,
                error={"code": "INSTALL_ERROR", "message": str(e)},
                duration_ms=0
            )

    async def verify(self, input_data: PackageInstallInput, output: ToolOutput) -> bool:
        """Verify the tool execution result."""
        return output.success

    def get_policy_requirements(self) -> Dict[str, Any]:
        """Get policy requirements for this tool."""
        return {"risk_level": "high", "requires_confirmation": True, "requires_admin": True}

    def validate_targets(self, input_data: PackageInstallInput) -> Optional[Dict[str, str]]:
        return _package_target_error(input_data.package_id)


class PackageUninstallTool(BaseTool):
    """Uninstall a package via winget."""

    name = "package_uninstall"
    description = "Uninstall a package using winget"
    input_schema = PackageUninstallInput

    async def execute(self, input_data: PackageUninstallInput) -> ToolOutput:
        """Execute the package uninstall tool (fixed-argument winget only)."""
        try:
            if platform.system() != "Windows":
                return ToolOutput(
                    success=False,
                    error={"code": "NON_WINDOWS_UNSUPPORTED", "message": "package tools are Windows-only in v1"},
                    duration_ms=0
                )

            package_id = input_data.package_id
            _validate_package_id(package_id)

            try:
                result = subprocess.run(
                    ["winget", "uninstall", "--id", package_id, "--silent"],
                    capture_output=True,
                    text=True,
                    timeout=300
                )
            except (FileNotFoundError, subprocess.TimeoutExpired) as e:
                return ToolOutput(
                    success=False,
                    error={"code": "UNINSTALL_ERROR", "message": f"Winget not available: {str(e)}"},
                    duration_ms=0
                )

            success = result.returncode == 0
            return ToolOutput(
                success=success,
                data={"package_id": package_id, "method": "winget", "platform": "windows"}
                if success else None,
                error=None if success else {
                    "code": "UNINSTALL_FAILED",
                    "message": f"Failed to uninstall {package_id}",
                    "detail": (result.stderr or result.stdout).strip()[:2000]
                },
                evidence={"source": "package_manager_uninstall"}
            )
        except Exception as e:
            return ToolOutput(
                success=False,
                error={"code": "UNINSTALL_ERROR", "message": str(e)},
                duration_ms=0
            )

    async def verify(self, input_data: PackageUninstallInput, output: ToolOutput) -> bool:
        """Verify the tool execution result."""
        return output.success

    def get_policy_requirements(self) -> Dict[str, Any]:
        """Get policy requirements for this tool."""
        return {"risk_level": "high", "requires_confirmation": True, "destructive": True, "requires_admin": True}

    def validate_targets(self, input_data: PackageUninstallInput) -> Optional[Dict[str, str]]:
        return _package_target_error(input_data.package_id)


def create_package_tools() -> List[BaseTool]:
    """Create instances of all package tools."""
    return [
        PackageSearchTool(),
        PackageListTool(),
        PackageInstallTool(),
        PackageUninstallTool(),
    ]
