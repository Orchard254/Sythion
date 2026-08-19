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
from dataclasses import dataclass
from typing import Any, Generic, Protocol, TypeVar

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
    """

    name: str
    description: str
    input_schema: type[T_Input]
    _active_cancelled_check: Callable[[], Awaitable[bool]] | None = None

    def set_cancelled_check(self, check: Callable[[], Awaitable[bool]] | None) -> None:
        """Set the cancellation check for this tool execution."""
        self._active_cancelled_check = check

    def clear_cancelled_check(self) -> None:
        """Clear the cancellation check after execution."""
        self._active_cancelled_check = None

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
        """Return True when the caller's task was cancelled mid-execution.

        The executor sets `_active_cancelled_check` for the duration of
        `execute`; multi-step tools consult this before a final, irreversible
        OS swap (e.g. the atomic replace in file writes).
        """
        check = self._active_cancelled_check
        if check is None:
            return False
        try:
            return bool(await check())
        except Exception:
            return False

    async def verify(self, input_data: T_Input, output: ToolOutput) -> bool:
        """Verify the tool execution result."""
        return bool(output.success)

    def get_policy_requirements(self) -> dict[str, Any]:
        """Get policy requirements for this tool."""
        return {"risk_level": "none", "requires_confirmation": False, "read_only": True}


@dataclass(frozen=True)
class PolicyContext:
    """Trusted execution context supplied by the task runner, never the model."""

    direct_user_instruction: bool = False

    cancelled_check: Callable[[], Awaitable[bool]] | None = None
    """Async predicate the executor calls immediately before an OS action.

    Returns True when the caller's task has been cancelled, so the executor can
    refuse the action at the last deterministic boundary.
    """


# =============================================================================
# ToolExecutor — type-safe tool execution
# =============================================================================

class ToolExecutor:
    """Executes tools with timing and error handling."""

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool[Any]] = {}

    def register_tool(self, tool: BaseTool[Any]) -> None:
        """Register a tool for execution.

        Args:
            tool: Tool instance to register
        """
        self._tools[tool.name] = tool

    def get_tool(self, name: str) -> BaseTool[Any] | None:
        """Get a registered tool by name.

        Args:
            name: Tool name

        Returns:
            Tool instance or None if not found
        """
        return self._tools.get(name)

    def list_tools(self) -> list[str]:
        """List all registered tools.

        Returns:
            List of tool names
        """
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

        # Deterministic target validation before any OS action.
        target_error = tool.validate_targets(validated_input)
        if target_error:
            return ToolOutput(
                success=False,
                error=target_error,
                duration_ms=0,
            )

        # Final cancellation check immediately before the OS action: a cancel
        # that arrived during input/target validation must stop the action.
        if policy_context is not None and policy_context.cancelled_check is not None:
            try:
                if await policy_context.cancelled_check():
                    return ToolOutput(
                        success=False,
                        error={"code": "CANCELLED", "message": "Task was cancelled before the action could run"},
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
        # Expose the active cancellation check to tools so multi-step mutations
        # (e.g. write-temp then atomic replace) can stop before the final swap.
        tool.set_cancelled_check(
            policy_context.cancelled_check
            if policy_context is not None
            else None
        )
        try:
            output = await tool.execute(validated_input)
            duration_ms = int((time.time() - start_time) * 1000)
            output.duration_ms = duration_ms

            # Verify if successful
            if output.success:
                verified = await tool.verify(validated_input, output)
                if not verified:
                    output.success = False
                    output.error = {"code": "VERIFICATION_FAILED", "message": "Post-execution verification failed"}

            return output
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            return ToolOutput(
                success=False,
                error={"code": "EXECUTION_ERROR", "message": str(e)},
                duration_ms=duration_ms,
            )
        finally:
            tool.clear_cancelled_check()

    @staticmethod
    def _policy_error(
        tool: BaseTool[Any],
        policy_context: PolicyContext | None,
    ) -> dict[str, str] | None:
        """Apply non-bypassable, deterministic policy before a tool executes."""
        requirements = tool.get_policy_requirements()
        if requirements.get("requires_admin"):
            return {
                "code": "ELEVATION_NOT_ALLOWED",
                "message": "Sythion (Synth) only performs standard-user actions",
            }
        if requirements.get("requires_confirmation") and not (
            policy_context and policy_context.direct_user_instruction
        ):
            return {
                "code": "DIRECT_INSTRUCTION_REQUIRED",
                "message": "A mutating action requires a direct user instruction",
            }
        return None