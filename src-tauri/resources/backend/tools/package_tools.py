"""
Package Management Tools for Sythion (Synth)

v1 is Windows-only and restricts package actions to fixed-argument `winget`
operations with exact package ids. No other package manager is invoked, no
free-form flags are accepted, agreements are never auto-accepted, and no
elevation is ever requested (the policy gate also blocks these tools with
ELEVATION_NOT_ALLOWED for defense in depth).
"""
import logging
import subprocess
import platform
import json
import re
from typing import Dict, Any, List, Optional, Generic, TypeVar

logger = logging.getLogger(__name__)

try:
    from backend.tools.executor import BaseTool, handle_tool_errors
    from backend.api.schemas import (
        ToolOutput,
        PackageSearchInput,
        PackageListInput,
        PackageInstallInput,
        PackageUninstallInput,
    )
except ImportError:
    from tools.executor import BaseTool, handle_tool_errors
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


def parse_winget_table(stdout: str) -> List[Dict[str, Any]]:
    """Parse winget's columnar tabular output using dash separator positions.

    Handles package names with embedded spaces (e.g. "NoteTab Light", "Microsoft GameInput")
    by slicing on column offset boundaries rather than splitting on whitespace.
    """
    if not stdout or not stdout.strip():
        return []

    lines = stdout.splitlines()
    header_idx = -1
    sep_idx = -1

    # Find the separator line (line consisting of dashes and optional spaces, >= 10 chars)
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped and re.match(r"^[-\s]{10,}$", stripped) and "-" in stripped:
            sep_idx = idx
            header_idx = idx - 1
            break

    if header_idx < 0 or header_idx >= len(lines):
        return []

    header_line = lines[header_idx]

    # Find column start positions from header_line
    cols: List[Tuple[int, str]] = []
    for col_name in ["Name", "Id", "Version", "Available", "Match", "Source"]:
        pos = header_line.find(col_name)
        if pos != -1:
            cols.append((pos, col_name))

    cols.sort(key=lambda x: x[0])
    if not cols or cols[0][1] != "Name":
        return []

    col_map: Dict[str, Tuple[int, Optional[int]]] = {
        name: (pos, cols[i + 1][0] if i + 1 < len(cols) else None)
        for i, (pos, name) in enumerate(cols)
    }

    id_bounds = col_map.get("Id")
    ver_bounds = col_map.get("Version")
    if not id_bounds or not ver_bounds:
        return []

    name_end = id_bounds[0]
    id_start, id_end = id_bounds
    ver_start, ver_end = ver_bounds

    src_bounds = col_map.get("Source")
    src_start, src_end = src_bounds if src_bounds else (None, None)

    results: List[Dict[str, Any]] = []
    for line in lines[sep_idx + 1:]:
        stripped = line.strip()
        if not stripped or stripped.startswith("<") or stripped.startswith("-"):
            continue
        if len(line) < id_start:
            continue

        pkg_name = line[:name_end].strip()
        pkg_id = line[id_start:id_end].strip() if id_end else line[id_start:].strip()
        pkg_ver = (
            line[ver_start:ver_end].strip()
            if ver_end and len(line) >= ver_start
            else (line[ver_start:].strip() if len(line) >= ver_start else "")
        )
        pkg_src = (
            line[src_start:src_end].strip()
            if src_start and len(line) >= src_start
            else "winget"
        )

        if pkg_id:
            results.append({
                "id": pkg_id,
                "name": pkg_name,
                "version": pkg_ver,
                "source": pkg_src or "winget",
                "platform": "windows",
            })

    return results


class PackageSearchTool(BaseTool[PackageSearchInput]):
    """Search for packages in package managers."""

    name = "package_search"
    description = "Search for packages in system package managers"
    input_schema = PackageSearchInput

    @handle_tool_errors("PACKAGE_SEARCH_ERROR")
    async def execute(self, input_data: PackageSearchInput) -> ToolOutput:
        """Execute the package search tool."""
        if platform.system() != "Windows":
            return ToolOutput(
                success=False,
                error={"code": "NON_WINDOWS_UNSUPPORTED", "message": "package tools are Windows-only in v1"},
                duration_ms=0,
            )

        cmd = [
            "winget",
            "search",
            input_data.query,
            "-s",
            "winget",
            "-n",
            str(min(input_data.limit, 100)),
            "--disable-interactivity",
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except FileNotFoundError:
            return ToolOutput(
                success=False,
                error={"code": "WINGET_NOT_FOUND", "message": "winget executable not found in PATH"},
                duration_ms=0,
            )
        except subprocess.TimeoutExpired:
            return ToolOutput(
                success=False,
                error={"code": "SEARCH_TIMEOUT", "message": "winget search timed out after 60s"},
                duration_ms=0,
            )

        combined_out = (result.stdout or "") + "\n" + (result.stderr or "")

        if result.returncode != 0:
            if "no package found" in combined_out.lower():
                return ToolOutput(
                    success=True,
                    data={
                        "query": input_data.query,
                        "count": 0,
                        "limit": input_data.limit,
                        "packages": [],
                        "platform": "windows",
                    },
                    evidence={"source": "package_manager_search"},
                )
            logger.warning("winget search failed (exit %s): %s", result.returncode, combined_out.strip())
            return ToolOutput(
                success=False,
                error={
                    "code": "PACKAGE_SEARCH_ERROR",
                    "message": f"winget search failed (code {result.returncode})",
                    "detail": combined_out.strip()[:2000],
                },
                duration_ms=0,
            )

        packages = parse_winget_table(result.stdout)[:input_data.limit]
        return ToolOutput(
            success=True,
            data={
                "query": input_data.query,
                "count": len(packages),
                "limit": input_data.limit,
                "packages": packages,
                "platform": "windows",
            },
            evidence={"source": "package_manager_search"},
        )

    async def verify(self, input_data: PackageSearchInput, output: ToolOutput) -> bool:
        """Verify the tool execution result."""
        return output.success and "packages" in (output.data or {})

    def get_policy_requirements(self) -> Dict[str, Any]:
        """Get policy requirements for this tool."""
        return {"risk_level": "none", "requires_confirmation": False}


class PackageListTool(BaseTool[PackageListInput]):
    """List installed packages."""

    name = "package_list"
    description = "List installed packages on the system"
    input_schema = PackageListInput

    @handle_tool_errors("PACKAGE_LIST_ERROR")
    async def execute(self, input_data: PackageListInput) -> ToolOutput:
        """Execute the package list tool."""
        if platform.system() != "Windows":
            return ToolOutput(
                success=False,
                error={"code": "NON_WINDOWS_UNSUPPORTED", "message": "package tools are Windows-only in v1"},
                duration_ms=0,
            )

        cmd = [
            "winget",
            "list",
            "--disable-interactivity",
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except FileNotFoundError:
            return ToolOutput(
                success=False,
                error={"code": "WINGET_NOT_FOUND", "message": "winget executable not found in PATH"},
                duration_ms=0,
            )
        except subprocess.TimeoutExpired:
            return ToolOutput(
                success=False,
                error={"code": "LIST_TIMEOUT", "message": "winget list timed out after 120s"},
                duration_ms=0,
            )

        combined_out = (result.stdout or "") + "\n" + (result.stderr or "")

        if result.returncode != 0:
            if "no installed package found" in combined_out.lower() or "no package found" in combined_out.lower():
                return ToolOutput(
                    success=True,
                    data={
                        "count": 0,
                        "search_term": input_data.search_term,
                        "packages": [],
                        "platform": "windows",
                    },
                    evidence={"source": "package_manager_list"},
                )
            logger.warning("winget list failed (exit %s): %s", result.returncode, combined_out.strip())
            return ToolOutput(
                success=False,
                error={
                    "code": "PACKAGE_LIST_ERROR",
                    "message": f"winget list failed (code {result.returncode})",
                    "detail": combined_out.strip()[:2000],
                },
                duration_ms=0,
            )

        all_packages = parse_winget_table(result.stdout)
        if input_data.search_term:
            term = input_data.search_term.lower()
            filtered = [
                p for p in all_packages
                if term in p.get("name", "").lower() or term in p.get("id", "").lower()
            ]
        else:
            filtered = all_packages

        return ToolOutput(
            success=True,
            data={
                "count": len(filtered),
                "search_term": input_data.search_term,
                "packages": filtered[:200],
                "platform": "windows",
            },
            evidence={"source": "package_manager_list"},
        )

    async def verify(self, input_data: PackageListInput, output: ToolOutput) -> bool:
        """Verify the tool execution result."""
        return output.success and "packages" in (output.data or {})

    def get_policy_requirements(self) -> Dict[str, Any]:
        """Get policy requirements for this tool."""
        return {"risk_level": "none", "requires_confirmation": False}


class PackageInstallTool(BaseTool[PackageInstallInput]):
    """Install a package via winget."""

    name = "package_install"
    description = "Install a package using winget"
    input_schema = PackageInstallInput

    @handle_tool_errors("INSTALL_ERROR")
    async def execute(self, input_data: PackageInstallInput) -> ToolOutput:
        """Execute the package install tool.

        Fixed-argument winget only. Agreement/elevation prompts are never
        auto-accepted: when winget requires them it fails and the result is
        surfaced to the user rather than silently accepted.
        """
        if platform.system() != "Windows":
            return ToolOutput(
                success=False,
                error={"code": "NON_WINDOWS_UNSUPPORTED", "message": "package tools are Windows-only in v1"},
                duration_ms=0,
            )

        package_id = input_data.package_id
        _validate_package_id(package_id)

        if input_data.source not in _APPROVED_SOURCES:
            return ToolOutput(
                success=False,
                error={
                    "code": "INVALID_PACKAGE_SOURCE",
                    "message": f"Package source '{input_data.source}' is not approved; only winget is supported",
                },
                duration_ms=0,
            )

        cmd = ["winget", "install", "--id", package_id, "--silent", "--disable-interactivity"]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
            )
        except FileNotFoundError:
            return ToolOutput(
                success=False,
                error={"code": "WINGET_NOT_FOUND", "message": "winget executable not found in PATH"},
                duration_ms=0,
            )
        except subprocess.TimeoutExpired:
            return ToolOutput(
                success=False,
                error={"code": "INSTALL_TIMEOUT", "message": f"Installation of {package_id} timed out after 300s"},
                duration_ms=0,
            )

        success = result.returncode == 0
        if not success:
            logger.warning("winget install %s failed (exit %s): %s", package_id, result.returncode, (result.stderr or result.stdout).strip())

        return ToolOutput(
            success=success,
            data={"package_id": package_id, "method": "winget", "platform": "windows", "source": "winget"}
            if success else None,
            error=None if success else {
                "code": "INSTALL_FAILED",
                "message": f"Failed to install {package_id} (code {result.returncode}; agreements or elevation may be required)",
                "detail": (result.stderr or result.stdout).strip()[:2000],
            },
            evidence={"source": "package_manager_install"},
        )

    async def verify(self, input_data: PackageInstallInput, output: ToolOutput) -> bool:
        """Verify the tool execution result."""
        return output.success

    def get_policy_requirements(self) -> Dict[str, Any]:
        """Get policy requirements for this tool."""
        return {"risk_level": "high", "requires_confirmation": True, "requires_admin": True}

    def validate_targets(self, input_data: PackageInstallInput) -> Optional[Dict[str, str]]:
        return _package_target_error(input_data.package_id)


class PackageUninstallTool(BaseTool[PackageUninstallInput]):
    """Uninstall a package via winget."""

    name = "package_uninstall"
    description = "Uninstall a package using winget"
    input_schema = PackageUninstallInput

    @handle_tool_errors("UNINSTALL_ERROR")
    async def execute(self, input_data: PackageUninstallInput) -> ToolOutput:
        """Execute the package uninstall tool (fixed-argument winget only)."""
        if platform.system() != "Windows":
            return ToolOutput(
                success=False,
                error={"code": "NON_WINDOWS_UNSUPPORTED", "message": "package tools are Windows-only in v1"},
                duration_ms=0,
            )

        package_id = input_data.package_id
        _validate_package_id(package_id)

        cmd = ["winget", "uninstall", "--id", package_id, "--silent", "--disable-interactivity"]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
            )
        except FileNotFoundError:
            return ToolOutput(
                success=False,
                error={"code": "WINGET_NOT_FOUND", "message": "winget executable not found in PATH"},
                duration_ms=0,
            )
        except subprocess.TimeoutExpired:
            return ToolOutput(
                success=False,
                error={"code": "UNINSTALL_TIMEOUT", "message": f"Uninstall of {package_id} timed out after 300s"},
                duration_ms=0,
            )

        success = result.returncode == 0
        if not success:
            logger.warning("winget uninstall %s failed (exit %s): %s", package_id, result.returncode, (result.stderr or result.stdout).strip())

        return ToolOutput(
            success=success,
            data={"package_id": package_id, "method": "winget", "platform": "windows"}
            if success else None,
            error=None if success else {
                "code": "UNINSTALL_FAILED",
                "message": f"Failed to uninstall {package_id} (code {result.returncode})",
                "detail": (result.stderr or result.stdout).strip()[:2000],
            },
            evidence={"source": "package_manager_uninstall"},
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
