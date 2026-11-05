"""
Tool executor framework for Sythion (Synth).
Executes typed tools with policy evaluation and verification.
"""

import functools
import logging
import os
import sys
import time
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Generic, Optional, Protocol, TypeVar

from pydantic import BaseModel

_EXECUTOR_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_EXECUTOR_DIR))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

try:
    from backend.api.schemas import ToolOutput
except ImportError:
    from sythion.backend.api.schemas import ToolOutput


def handle_tool_errors(default_code: str, fallback_message: str | None = None):
    """Decorator for tool execute() methods.

    Catches unhandled Exceptions, logs exc type, message, and traceback via
    logger.warning(..., exc_info=True), and returns ToolOutput with the
    designated error code and message.
    """
    def decorator(fn: Callable[..., Awaitable[ToolOutput]]) -> Callable[..., Awaitable[ToolOutput]]:
        @functools.wraps(fn)
        async def wrapper(self: Any, input_data: Any, *args: Any, **kwargs: Any) -> ToolOutput:
            try:
                return await fn(self, input_data, *args, **kwargs)
            except Exception as exc:
                code = getattr(exc, "code", default_code)
                msg = getattr(exc, "message", None) or (fallback_message if fallback_message is not None else str(exc))
                tool_name = getattr(self, "name", self.__class__.__name__)
                log = getattr(self, "logger", None) or logging.getLogger(self.__class__.__module__)
                log.warning(
                    "Tool '%s' execute failed with %s: %s",
                    tool_name,
                    type(exc).__name__,
                    exc,
                    exc_info=True,
                )
                return ToolOutput(
                    success=False,
                    error={"code": code, "message": msg},
                    duration_ms=0,
                )
        return wrapper
    return decorator


# =============================================================================
# Tool Input Protocol — explicit contract for tool input schemas
# =============================================================================

class ToolInputSchema(Protocol):
    """Protocol for tool input schemas.

    All tool input schemas must implement this protocol. This replaces the
    implicit `type[BaseModel] | dict[str, Any]` with an explicit contract.

    Implementations can be:
    - A Pydantic BaseModel subclass (preferred)
    - A dict with JSON Schema structure (for dynamic/loaded tools)
    """

    # Schema metadata
    model_fields: dict[str, Any]
    model_config: dict[str, Any]

    def __init__(self, **data: Any) -> None: ...

    def model_dump(self, **kwargs: Any) -> dict[str, Any]: ...

    @classmethod
    def model_validate(cls, obj: Any) -> "ToolInputSchema": ...


# Type variable for tool input data
T_Input = TypeVar("T_Input", bound=ToolInputSchema)


# =============================================================================
# BaseTool — explicit contract for all tools
# =============================================================================

class BaseTool(ABC, Generic[T_Input]):
    """Base class for all tools.

    Generic over the input schema type T_Input, which must implement
    ToolInputSchema. This provides:
    - Type-safe input validation
    - IDE autocomplete for tool parameters
    - Static analysis of tool contracts

    CANCELLATION (Phase 1): tools are shared singletons; per-execution state
    (cancellation, task identity) lives in an execution-scoped
    ``ToolExecutionContext`` carried by a ContextVar, never on the instance.
    One task's cancellation state can therefore never affect another task,
    even under concurrent workers.
    """

    name: str
    description: str
    input_schema: type[T_Input]

    @abstractmethod
    async def execute(self, input_data: T_Input) -> ToolOutput:
        """Execute the tool with validated input data."""

    def validate_targets(self, input_data: T_Input) -> dict[str, str] | None:
        """Deterministic, side-effect-free target validation.

        Runs before `execute` so an ambiguous, malformed, or out-of-scope
        target is rejected before any OS action occurs. Returns an error dict
        with `code`/`message` when the target is invalid, or None when valid.
        """
        return None

    async def _is_cancelled(self) -> bool:
        """Return True when the calling execution was cancelled mid-flight.

        Reads the execution-scoped context installed by the executor for the
        duration of `execute`. Multi-step tools consult this before a final,
        irreversible OS swap (e.g. the atomic replace in file writes).
        """
        ctx = current_execution_context()
        if ctx is None or ctx.cancelled_check is None:
            return False
        try:
            return bool(await ctx.cancelled_check())
        except Exception:
            return False

    async def verify(self, input_data: T_Input, output: ToolOutput) -> bool:
        """Verify the tool execution result."""
        return bool(output.success)

    def get_policy_requirements(self) -> dict[str, Any]:
        """Fail-closed default: an unreviewed/underspecified tool is NOT
        read-only, IS confirmation-gated, and is state-changing. Trusted tools
        must override this explicitly."""
        return {
            "risk_level": "high",
            "requires_confirmation": True,
            "read_only": False,
            "mutating": True,
            "destructive": True,
        }


@dataclass(frozen=True)
class PolicyContext:
    """Trusted execution context supplied by the task runner, never the model.

    This is the execution-scoped runtime context: one instance per tool
    execution, carrying everything a tool may consult about *its own*
    execution and nothing about any other task.
    """

    direct_user_instruction: bool = False
    task_id: str = ""
    """Identity of the owning task (execution scoping / diagnostics)."""

    cancelled_check: Callable[[], Awaitable[bool]] | None = None
    """Async predicate the executor calls immediately before an OS action.

    Returns True when the caller's task has been cancelled, so the executor
    can refuse the action at the last deterministic boundary.
    """


# --- Execution-scoped context (replaces class-level mutable state) ----------

_EXECUTION_CONTEXT: ContextVar[Optional["PolicyContext"]] = ContextVar(
    "sythion_tool_execution_context", default=None
)


def current_execution_context() -> Optional[PolicyContext]:
    """The execution-scoped context for the currently running tool call.

    Each asyncio execution context gets its own value: concurrent workers
    cannot observe each other's cancellation state or task identity.
    """
    return _EXECUTION_CONTEXT.get()


@contextmanager
def execution_context(ctx: PolicyContext):
    """Install an execution-scoped context for the duration of a tool call."""
    token = _EXECUTION_CONTEXT.set(ctx)
    try:
        yield ctx
    finally:
        _EXECUTION_CONTEXT.reset(token)


# Backwards-compatible alias: the execution context IS the policy context.
ToolExecutionContext = PolicyContext


# =============================================================================
# ToolExecutor — type-safe tool execution
# =============================================================================

class ToolExecutor:
    """Executes tools with timing and error handling.

    The registry is shared across concurrent specialist executions. Reads
    (get/list/execute) are concurrent, writes (register) are serialized
    and must not happen during active execution — callers must hold the
    external composition lock (see backend/tools/registry.py). This lock
    guarantees snapshot consistency for list_tools().
    """

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool[Any]] = {}
        import threading as _thr
        self._registry_lock = _thr.RLock()

    def register_tool(self, tool: BaseTool[Any]) -> None:
        """Register a tool for execution.

        Serialized — must not be called concurrently with execute_tool.
        """
        with self._registry_lock:
            self._tools[tool.name] = tool

    def get_tool(self, name: str) -> BaseTool[Any] | None:
        """Get a registered tool by name."""
        with self._registry_lock:
            return self._tools.get(name)

    def list_tools(self) -> list[str]:
        """List all registered tools — snapshot under lock."""
        with self._registry_lock:
            return list(self._tools.keys())

    async def execute_tool(
        self,
        tool_name: str,
        input_data: dict[str, Any],
        policy_context: PolicyContext | None = None,
    ) -> ToolOutput:
        """Execute a tool with validated input.

        Args:
            tool_name: Name of the tool to execute
            input_data: Input data dictionary

        Returns:
            ToolOutput with execution results

        Raises:
            ValueError: If tool not found or input validation fails
        """
        tool = self.get_tool(tool_name)
        if not tool:
            return ToolOutput(
                success=False,
                error={"code": "TOOL_NOT_FOUND", "message": f"Tool '{tool_name}' not found"},
                duration_ms=0,
            )

        policy_error = self._policy_error(tool, policy_context)
        if policy_error:
            return ToolOutput(
                success=False,
                error=policy_error,
                duration_ms=0,
            )

        # Validate input using the tool's schema
        try:
            # Pydantic model class
            if isinstance(tool.input_schema, type) and issubclass(tool.input_schema, BaseModel):
                validated_input = tool.input_schema(**input_data)
            else:
                # Raw dict schema (dynamic tools) - use SimpleNamespace
                from types import SimpleNamespace
                validated_input = SimpleNamespace(**input_data)
        except Exception as e:
            return ToolOutput(
                success=False,
                error={"code": "INVALID_INPUT", "message": str(e)},
                duration_ms=0,
            )

        # Install the execution-scoped context (task identity + cancellation)
        # for validation, target checks, and execution alike. ContextVar
        # semantics guarantee isolation between concurrent executions.
        exec_ctx = PolicyContext(
            direct_user_instruction=(
                policy_context.direct_user_instruction if policy_context else False
            ),
            task_id=policy_context.task_id if policy_context else "",
            cancelled_check=policy_context.cancelled_check if policy_context else None,
        )

        with execution_context(exec_ctx):
            # Deterministic target validation before any OS action.
            target_error = tool.validate_targets(validated_input)
            if target_error:
                return ToolOutput(
                    success=False,
                    error=target_error,
                    duration_ms=0,
                )

            # Final cancellation check immediately before the OS action: a
            # cancel that arrived during input/target validation must stop
            # the action.
            if exec_ctx.cancelled_check is not None:
                try:
                    if await exec_ctx.cancelled_check():
                        return ToolOutput(
                            success=False,
                            error={
                                "code": "CANCELLED",
                                "message": "Task was cancelled before the action could run",
                            },
                            duration_ms=0,
                        )
                except Exception as e:
                    return ToolOutput(
                        success=False,
                        error={"code": "CANCELLATION_CHECK_ERROR", "message": str(e)},
                        duration_ms=0,
                    )

            # Execute with timing
            start_time = time.time()
            try:
                output = await tool.execute(validated_input)
                duration_ms = int((time.time() - start_time) * 1000)
                output.duration_ms = duration_ms

                # Verify if successful
                if output.success:
                    verified = await tool.verify(validated_input, output)
                    if not verified:
                        output.success = False
                        output.error = {
                            "code": "VERIFICATION_FAILED",
                            "message": "Post-execution verification failed",
                        }

                return output
            except Exception as e:
                duration_ms = int((time.time() - start_time) * 1000)
                return ToolOutput(
                    success=False,
                    error={"code": "EXECUTION_ERROR", "message": str(e)},
                    duration_ms=duration_ms,
                )

    @staticmethod
    def _policy_error(
        tool: BaseTool[Any],
        policy_context: PolicyContext | None,
    ) -> dict[str, str] | None:
        """Apply non-bypassable, deterministic policy before a tool executes."""
        # Prototype-only fail-closed exclusion for host-data observation
        # tools. Handled before the generic authorization messages so the
        # refusal is accurately attributable. This does not weaken policy:
        # it always blocks in prototype builds.
        try:
            from backend.core.secrets import is_prototype_build as _proto
        except Exception:
            try:
                from core.secrets import is_prototype_build as _proto
            except Exception:
                _proto = None
        _is_prototype = False
        if _proto is not None:
            try:
                _is_prototype = bool(_proto())
            except Exception:
                _is_prototype = False
        if (
            _is_prototype
            and getattr(tool, "name", None) in _PROTOTYPE_HOST_DATA_TOOL_NAMES
        ):
            return {
                "code": "PROTOTYPE_HOST_DATA_DISABLED",
                "message": "Host data observation is disabled in prototype builds.",
            }

        requirements = tool.get_policy_requirements()
        if requirements.get("requires_admin"):
            return {
                "code": "ELEVATION_NOT_ALLOWED",
                "message": "Sythion (Synth) only performs standard-user actions",
            }
        # Never trust caller-supplied consent flags: a confirmation-gated tool
        # requires a real authenticated boundary, which is absent here, so it is
        # always blocked fail-closed.
        if requirements.get("requires_confirmation"):
            return {
                "code": "DIRECT_INSTRUCTION_REQUIRED",
                "message": "A mutating or sensitive action requires a trusted consent boundary; a direct user instruction alone is not authorization",
            }

        # Automatic dispatch is allowed only for an exact reviewed builtin that is
        # genuinely safe: read-only, non-mutating, non-destructive, non-privacy,
        # non-screen/UI, non-admin, low-risk, and never app_internal.  A reviewed
        # canonical built-in that is state-changing (or self-reports unsafe
        # metadata) is NOT auto-dispatchable even when requires_confirmation
        # is False, app_internal is True, configuration is permissive, or the
        # caller asserts direct_user_instruction.  It must go through the trusted
        # consent/action boundary instead.
        if not is_reviewed_safe_builtin(tool) or not _safe_automatic_dispatch_metadata(requirements):
            if is_reviewed_implementation(tool):
                return {
                    "code": "DIRECT_INSTRUCTION_REQUIRED",
                    "message": (
                        "A state-changing or sensitive built-in action requires a "
                        "trusted consent boundary, not identity, metadata, "
                        "app_internal status, configuration, or a caller flag"
                    ),
                }
            return {
                "code": "UNREVIEWED_IMPLEMENTATION",
                "message": (
                    "An unreviewed implementation cannot be dispatched by "
                    "trusted identity"
                ),
            }
        return None

    def is_reviewed_safe_builtin(self, tool: Any) -> bool:
        """Whether ``tool`` is a genuinely reviewed safe built-in implementation."""
        return is_reviewed_safe_builtin(tool)


# =============================================================================
# Reviewed safe built-in implementation identity (skill-fencing hardening)
# =============================================================================

_REVIEWED_SAFE_TOOL_CLASSES_CACHE: Optional[dict[type, str]] = None


def _reviewed_safe_tool_classes() -> dict[type, str]:
    global _REVIEWED_SAFE_TOOL_CLASSES_CACHE
    if _REVIEWED_SAFE_TOOL_CLASSES_CACHE is not None:
        return _REVIEWED_SAFE_TOOL_CLASSES_CACHE
    mapping: dict[type, str] = {}
    try:
        from backend.tools.activity_recall_tool import ActivityRecallTool
        from backend.tools.app_tools import AppListTool
        from backend.tools.file_tools import (
            FileHashTool,
            FileListTool,
            FileReadTool,
            FileSearchTool,
        )
        from backend.tools.objective_planning_tool import ObjectiveGetPlanTool
        from backend.tools.package_tools import PackageListTool, PackageSearchTool
        from backend.tools.persistent_memory_tools import (
            MemoryExportTool,
            MemoryRecallRelatedTool,
            MemoryRecallTool,
            MemoryStatsTool,
        )
        from backend.tools.system_tools import (
            BrowserReadPageTool,
            BrowserTabsTool,
            DeviceStatusTool,
            NetworkStatusTool,
            SystemActiveWindowTool,
            SystemBatteryTool,
            SystemCapabilitiesTool,
            SystemDiskTool,
            SystemFileRootsTool,
            SystemPowerTool,
            SystemProcessesTool,
            SystemTimeTool,
            ToolProposeTool,
            WindowListTool,
        )
    except Exception:
        _REVIEWED_SAFE_TOOL_CLASSES_CACHE = {}
        return _REVIEWED_SAFE_TOOL_CLASSES_CACHE

    mapping.update(
        {
            ActivityRecallTool: "activity_recall",
            AppListTool: "app_list",
            BrowserReadPageTool: "browser_read_page",
            BrowserTabsTool: "browser_tabs",
            DeviceStatusTool: "device_status",
            FileHashTool: "file_hash",
            FileListTool: "file_list",
            FileReadTool: "file_read",
            FileSearchTool: "file_search",
            MemoryExportTool: "memory_export",
            MemoryRecallTool: "memory_recall",
            MemoryRecallRelatedTool: "memory_recall_related",
            MemoryStatsTool: "memory_stats",
            NetworkStatusTool: "network_status",
            ObjectiveGetPlanTool: "objective_get_plan",
            PackageListTool: "package_list",
            PackageSearchTool: "package_search",
            SystemActiveWindowTool: "system_active_window",
            SystemBatteryTool: "system_battery",
            SystemCapabilitiesTool: "system_capabilities",
            SystemDiskTool: "system_disk",
            SystemFileRootsTool: "system_file_roots",
            SystemPowerTool: "system_power",
            SystemProcessesTool: "system_processes",
            SystemTimeTool: "system_time",
            ToolProposeTool: "tool_propose",
            WindowListTool: "window_list",
        }
    )
    _REVIEWED_SAFE_TOOL_CLASSES_CACHE = mapping
    return _REVIEWED_SAFE_TOOL_CLASSES_CACHE


def is_reviewed_safe_builtin(tool: Any) -> bool:
    """Return True only for an exact reviewed safe built-in implementation."""
    mapping = _reviewed_safe_tool_classes()
    if not mapping:
        return False
    cls = type(tool)
    expected_name = mapping.get(cls)
    if expected_name is None:
        return False
    try:
        if getattr(tool, "name", None) != expected_name:
            return False
    except Exception:
        return False
    return True


_AUTOMATIC_DISPATCH_BOOL_KEYS = (
    "mutating",
    "destructive",
    "read_only",
    "app_internal",
    "requires_confirmation",
    "privacy_sensitive",
    "requires_screen_access",
    "requires_ui_automation",
    "requires_admin",
)


def _safe_automatic_dispatch_metadata(requirements: Any) -> bool:
    """Metadata may never authorize an effect.  Only a strictly read-only,
    non-mutating, non-sensitive, low-risk requirement set can be dispatched
    without a separate trusted consent/action boundary."""
    if not isinstance(requirements, dict):
        return False
    for key in _AUTOMATIC_DISPATCH_BOOL_KEYS:
        value = requirements.get(key)
        if value is not None and not isinstance(value, bool):
            return False
    if requirements.get("requires_admin") is True:
        return False
    if requirements.get("requires_confirmation") is True:
        return False
    if requirements.get("app_internal") is True:
        return False
    if requirements.get("mutating") is True or requirements.get("destructive") is True:
        return False
    if requirements.get("read_only") is False:
        return False
    risk_level = requirements.get("risk_level", "none")
    if risk_level not in ("none", "low", None):
        return False
    if (
        requirements.get("privacy_sensitive") is True
        or requirements.get("requires_screen_access") is True
        or requirements.get("requires_ui_automation") is True
    ):
        return False
    return True


_REVIEWED_IMPLEMENTATION_CLASSES_CACHE = None


def _reviewed_implementation_classes() -> dict[type, str]:
    global _REVIEWED_IMPLEMENTATION_CLASSES_CACHE
    if _REVIEWED_IMPLEMENTATION_CLASSES_CACHE is not None:
        return _REVIEWED_IMPLEMENTATION_CLASSES_CACHE
    mapping: dict[type, str] = {}
    try:
        from backend.tools.registry import create_all_tools
    except Exception:
        _REVIEWED_IMPLEMENTATION_CLASSES_CACHE = {}
        return _REVIEWED_IMPLEMENTATION_CLASSES_CACHE
    try:
        for tool in create_all_tools():
            cls = type(tool)
            name = getattr(tool, "name", None)
            if isinstance(name, str) and name:
                mapping[cls] = name
    except Exception:
        _REVIEWED_IMPLEMENTATION_CLASSES_CACHE = {}
        return _REVIEWED_IMPLEMENTATION_CLASSES_CACHE
    _REVIEWED_IMPLEMENTATION_CLASSES_CACHE = mapping
    return _REVIEWED_IMPLEMENTATION_CLASSES_CACHE


def get_reviewed_safe_tool_classes() -> frozenset[type]:
    return frozenset(_reviewed_safe_tool_classes().keys())


reviewed_safe_tool_classes = get_reviewed_safe_tool_classes


def is_reviewed_implementation(tool: Any) -> bool:
    mapping = _reviewed_implementation_classes()
    if not mapping:
        return False
    cls = type(tool)
    expected_name = mapping.get(cls)
    if expected_name is None:
        return False
    try:
        return getattr(tool, "name", None) == expected_name
    except Exception:
        return False


reviewed_implementation = is_reviewed_implementation


# Prototype-only host-data observation surfaces. Keep this enumeration in sync
# with the guard installed in system_tools.py / gui_tools.py.
_PROTOTYPE_HOST_DATA_TOOL_NAMES = frozenset({
    "system_processes",
    "system_active_window",
    "window_list",
    "browser_tabs",
    "browser_read_page",
    "browser_screenshot",
    "clipboard_read",
    "gui_observe",
    "gui_screenshot",
})


def __getattr__(name: str):
    if name == "REVIEWED_SAFE_TOOL_CLASSES":
        return get_reviewed_safe_tool_classes()
    raise AttributeError(name)