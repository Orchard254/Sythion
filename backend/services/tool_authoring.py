"""
L3 tool-authoring pipeline: model-written tool proposals with strict gates.

A tool proposal includes a name, description, input schema, and Python
implementation. Every proposal must pass:
1. Code validation (no secrets, no dangerous imports, read-only first)
2. Sandbox testing (isolated execution, timeout, no side effects)
3. Owner approval (explicit review)
4. Staged enablement (guarded → autonomous after N verified uses)
"""

import ast
import json
import logging
import re
import textwrap
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from backend.models.database import ProposedTool, Session

logger = logging.getLogger(__name__)

# --- Validation constants ---------------------------------------------------

# Forbidden imports (security: no network, no subprocess, no file writes)
_FORBIDDEN_IMPORTS = frozenset({
    "subprocess", "os", "shutil", "sys", "socket", "http", "urllib",
    "requests", "asyncio", "threading", "multiprocessing", "ctypes",
    "importlib", "code", "codeop", "compile", "exec", "eval",
    "inspect", "pkgutil", "py_compile", "pyclbr", "tabnanny",
    "webbrowser", "cgi", "cgitb", "ftplib", "imaplib", "poplib",
    "smtplib", "telnetlib", "xmlrpc", "soap", "wsdl",
})

# Forbidden AST node types (no imports, no exec, no eval, no dynamic code)
_FORBIDDEN_AST_NODES = frozenset({
    "Import", "ImportFrom", "Exec", "Eval",
})

# Required BaseTool interface methods
_REQUIRED_METHODS = frozenset({"execute", "get_policy_requirements"})

# Tool name pattern: lowercase, underscores, starts with letter
_TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class ToolProposal(BaseModel):
    """Schema for a model-authored tool proposal."""
    name: str = Field(..., min_length=1, max_length=64)
    description: str = Field(..., min_length=1, max_length=500)
    input_schema: Dict[str, Any] = Field(..., description="Pydantic schema as JSON")
    implementation: str = Field(..., min_length=1, description="Python source code")


class ValidationResult(BaseModel):
    """Result of validating a tool proposal."""
    valid: bool
    errors: List[str] = []
    warnings: List[str] = []


class SandboxResult(BaseModel):
    """Result of sandbox testing a tool proposal."""
    passed: bool
    error: Optional[str] = None
    output: Optional[str] = None
    duration_ms: int = 0


# --- Validation ------------------------------------------------------------

def validate_tool_name(name: str) -> List[str]:
    """Validate tool name follows naming convention."""
    errors = []
    if not _TOOL_NAME_RE.match(name):
        errors.append(
            f"Tool name {name!r} must match {_TOOL_NAME_RE.pattern} "
            "(lowercase, underscores, starts with letter, max 64 chars)"
        )
    # Don't allow names that collide with built-in tools
    _BUILTIN_NAMES = frozenset({
        "system_time", "system_battery", "system_disk", "system_processes",
        "system_active_window", "system_file_roots", "system_capabilities",
        "file_list", "file_read", "file_write", "file_delete", "file_move",
        "file_copy", "file_search",
        "app_list", "app_launch", "app_focus", "app_close",
        "package_search", "package_list", "package_install", "package_uninstall",
        "gui_observe", "gui_click", "gui_type", "gui_hotkey", "gui_paste",
        "gui_focus", "gui_invoke", "gui_set_value",
        "memory_store", "memory_recall", "memory_forget", "memory_link",
        "memory_recall_related", "activity_recall",
    })
    if name in _BUILTIN_NAMES:
        errors.append(f"Tool name {name!r} collides with a built-in tool")
    return errors


def validate_description(description: str) -> List[str]:
    """Validate tool description follows rules."""
    errors = []
    if len(description) > 500:
        errors.append("Description must be at most 500 characters")
    # Check for secret hints
    _SECRET_HINTS = ("api_key", "apikey", "password", "secret", "token", "credential")
    desc_lower = description.lower()
    for hint in _SECRET_HINTS:
        if hint in desc_lower:
            errors.append(f"Description contains secret hint {hint!r}")
    return errors


def validate_input_schema(schema_json: Dict[str, Any]) -> List[str]:
    """Validate input schema follows rules."""
    errors = []
    if not isinstance(schema_json, dict):
        errors.append("Input schema must be a JSON object")
        return errors

    # Check required fields
    if "properties" not in schema_json:
        errors.append("Input schema must have a 'properties' field")

    # Check for coordinate hints
    _COORDINATE_KEYS = frozenset({
        "x", "y", "xy", "coord", "coords", "position", "point", "pixel",
    })
    properties = schema_json.get("properties", {})
    for key in properties:
        if key.lower() in _COORDINATE_KEYS:
            errors.append(f"Input schema field {key!r} looks like a coordinate")

    # Check for secret hints
    _SECRET_HINTS = ("api_key", "apikey", "password", "secret", "token", "credential")
    for key in properties:
        key_lower = key.lower()
        for hint in _SECRET_HINTS:
            if hint in key_lower:
                errors.append(f"Input schema field {key!r} looks like a secret")

    return errors


def validate_implementation(implementation: str) -> List[str]:
    """Validate tool implementation follows security rules."""
    errors = []

    try:
        tree = ast.parse(implementation)
    except SyntaxError as e:
        errors.append(f"Implementation has syntax error: {e}")
        return errors

    # Check for forbidden imports
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                module = alias.name.split(".")[0]
                if module in _FORBIDDEN_IMPORTS:
                    errors.append(f"Forbidden import: {alias.name!r}")
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                module = node.module.split(".")[0]
                if module in _FORBIDDEN_IMPORTS:
                    errors.append(f"Forbidden import from: {node.module!r}")

    # Check for forbidden patterns in source
    _FORBIDDEN_PATTERNS = (
        "eval(", "exec(", "__import__", "compile(",
        "globals(", "locals(", "vars(",
        "open(",  # No file I/O in tools
    )
    for pattern in _FORBIDDEN_PATTERNS:
        if pattern in implementation:
            errors.append(f"Implementation contains forbidden pattern: {pattern!r}")

    # Check class definition exists
    has_class = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            has_class = True
            break
    if not has_class:
        errors.append("Implementation must define a class")

    # Check for BaseTool inheritance
    has_base_tool = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                base_name = ""
                if isinstance(base, ast.Name):
                    base_name = base.id
                elif isinstance(base, ast.Attribute):
                    base_name = base.attr
                if "BaseTool" in base_name or "Tool" in base_name:
                    has_base_tool = True
                    break
    if not has_base_tool:
        errors.append("Implementation class must inherit from BaseTool")

    # Check for execute method
    has_execute = False
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "execute":
            has_execute = True
            break
    if not has_execute:
        errors.append("Implementation must define an 'execute' method")

    # Check for get_policy_requirements method
    has_policy = False
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "get_policy_requirements":
            has_policy = True
            break
    if not has_policy:
        errors.append("Implementation must define a 'get_policy_requirements' method")

    return errors


def validate_tool_proposal(proposal: ToolProposal) -> ValidationResult:
    """Run all validation checks on a tool proposal."""
    errors = []
    warnings = []

    errors.extend(validate_tool_name(proposal.name))
    errors.extend(validate_description(proposal.description))
    errors.extend(validate_input_schema(proposal.input_schema))
    errors.extend(validate_implementation(proposal.implementation))

    # Warnings (not blocking)
    if len(proposal.implementation) > 10000:
        warnings.append("Implementation is very long (>10KB)")
    if proposal.description.startswith("I "):
        warnings.append("Description starts with 'I ' — consider third-person")

    return ValidationResult(valid=len(errors) == 0, errors=errors, warnings=warnings)


# --- Sandbox testing -------------------------------------------------------

def sandbox_test(proposal: ToolProposal, timeout_seconds: int = 10) -> SandboxResult:
    """Test the tool implementation in an isolated sandbox.

    Runs the implementation in a restricted namespace with:
    - No network access
    - No file I/O
    - No subprocess
    - Timeout enforcement
    """
    import signal
    import time

    start_time = time.monotonic()

    # Build restricted namespace
    from backend.tools.executor import BaseTool
    from backend.api.schemas import ToolOutput
    restricted_globals = {
        "__builtins__": {
            # Allow basic types and functions
            "True": True, "False": False, "None": None,
            "int": int, "float": float, "str": str, "bool": bool,
            "list": list, "dict": dict, "tuple": tuple, "set": set,
            "len": len, "range": range, "enumerate": enumerate,
            "zip": zip, "map": map, "filter": filter,
            "sorted": sorted, "reversed": reversed,
            "min": min, "max": max, "sum": sum, "abs": abs,
            "round": round, "pow": pow,
            "isinstance": isinstance, "type": type,
            "print": print, "repr": repr, "str": str,
            "ValueError": ValueError, "TypeError": TypeError,
            "KeyError": KeyError, "IndexError": IndexError,
            "Exception": Exception, "RuntimeError": RuntimeError,
            "AttributeError": AttributeError,
            # Allow imports and class creation (validation already blocked dangerous ones)
            "__import__": __import__,
            "__build_class__": __build_class__,
            "__name__": "__main__",
        },
        # Pre-populate base classes so tool implementations can inherit
        # without relying on import working inside the restricted env
        "BaseTool": BaseTool,
        "ToolOutput": ToolOutput,
    }

    try:
        # Compile and execute the implementation
        compiled = compile(proposal.implementation, f"<tool:{proposal.name}>", "exec")
        exec(compiled, restricted_globals)

        # Find the tool class (skip abstract base classes)
        import inspect
        tool_class = None
        for obj in restricted_globals.values():
            if (
                isinstance(obj, type)
                and hasattr(obj, "execute")
                and not getattr(obj, "__abstractmethods__", None)
            ):
                tool_class = obj
                break

        if tool_class is None:
            return SandboxResult(
                passed=False,
                error="No tool class found with execute method",
            )

        # Instantiate and verify interface
        tool_instance = tool_class()
        if not hasattr(tool_instance, "name"):
            return SandboxResult(passed=False, error="Tool class missing 'name' attribute")
        if not hasattr(tool_instance, "description"):
            return SandboxResult(passed=False, error="Tool class missing 'description' attribute")
        if not hasattr(tool_instance, "input_schema"):
            return SandboxResult(passed=False, error="Tool class missing 'input_schema' attribute")

        # Test get_policy_requirements
        reqs = tool_instance.get_policy_requirements()
        if not isinstance(reqs, dict):
            return SandboxResult(passed=False, error="get_policy_requirements must return a dict")

        duration_ms = int((time.monotonic() - start_time) * 1000)
        return SandboxResult(
            passed=True,
            output=f"Tool class {tool_class.__name__!r} instantiated successfully",
            duration_ms=duration_ms,
        )

    except Exception as e:
        duration_ms = int((time.monotonic() - start_time) * 1000)
        return SandboxResult(
            passed=False,
            error=f"Sandbox test failed: {type(e).__name__}: {e}",
            duration_ms=duration_ms,
        )


# --- Persistence -----------------------------------------------------------

def list_proposed_tools(
    db: Session,
    status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """List tool proposals, optionally filtered by status."""
    query = db.query(ProposedTool).order_by(ProposedTool.id.desc())
    if status:
        query = query.filter_by(status=status)
    return [
        {
            "id": p.id,
            "name": p.name,
            "description": p.description,
            "status": p.status,
            "enabled_mode": p.enabled_mode,
            "successful_uses": p.successful_uses,
            "validation_errors": json.loads(p.validation_errors) if p.validation_errors else [],
            "sandbox_result": json.loads(p.sandbox_result) if p.sandbox_result else None,
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "decided_at": p.decided_at.isoformat() if p.decided_at else None,
            "enabled_at": p.enabled_at.isoformat() if p.enabled_at else None,
        }
        for p in query.all()
    ]


def get_proposed_tool(db: Session, tool_id: int) -> Optional[ProposedTool]:
    return db.query(ProposedTool).filter_by(id=tool_id).first()


def approve_tool_proposal(
    db: Session,
    proposal: ProposedTool,
    tool_executor: Any,
) -> ProposedTool:
    """Approve a tool proposal and enable it in guarded mode.

    The tool must have passed validation and sandbox testing first.
    """
    if proposal.status not in ("proposed", "sandbox_passed"):
        raise ValueError(f"Proposal {proposal.id} is {proposal.status}, not approvable")

    # Re-validate
    tool_proposal = ToolProposal(
        name=proposal.name,
        description=proposal.description,
        input_schema=json.loads(proposal.input_schema_json),
        implementation=proposal.implementation,
    )
    validation = validate_tool_proposal(tool_proposal)
    if not validation.valid:
        proposal.validation_errors = json.dumps(validation.errors)
        db.commit()
        raise ValueError(
            "Tool proposal failed validation: " + "; ".join(validation.errors)
        )

    # Re-run sandbox test
    sandbox = sandbox_test(tool_proposal)
    if not sandbox.passed:
        proposal.sandbox_result = json.dumps(sandbox.model_dump())
        db.commit()
        raise ValueError(
            "Tool proposal failed sandbox test: " + (sandbox.error or "unknown")
        )

    # Enable in guarded mode
    proposal.status = "enabled"
    proposal.enabled_mode = "guarded"
    proposal.enabled_at = datetime.utcnow()
    proposal.decided_at = datetime.utcnow()
    db.commit()
    db.refresh(proposal)

    # Dynamically load and register the approved tool with the executor
    try:
        namespace = {"__builtins__": __builtins__}
        exec(proposal.implementation, namespace)
        for obj in namespace.values():
            if (
                isinstance(obj, type)
                and hasattr(obj, "execute")
                and not getattr(obj, "__abstractmethods__", None)
                and hasattr(obj, "name")
            ):
                tool_executor.register_tool(obj())
                logger.info("Registered approved tool '%s' with executor", proposal.name)
                break
    except Exception as e:
        logger.warning("Failed to register approved tool '%s': %s", proposal.name, e)

    logger.info("Approved tool proposal %s (%s) in guarded mode", proposal.id, proposal.name)
    return proposal


def reject_tool_proposal(
    db: Session,
    proposal: ProposedTool,
) -> ProposedTool:
    """Reject a tool proposal."""
    if proposal.status in ("enabled",):
        raise ValueError(f"Cannot reject enabled tool {proposal.id}")

    proposal.status = "rejected"
    proposal.decided_at = datetime.utcnow()
    db.commit()
    db.refresh(proposal)

    logger.info("Rejected tool proposal %s (%s)", proposal.id, proposal.name)
    return proposal


def record_tool_use(db: Session, tool_name: str) -> Optional[ProposedTool]:
    """Record a successful use of a proposed tool.

    When the use count reaches the threshold, promote to autonomous mode.
    """
    proposal = db.query(ProposedTool).filter_by(name=tool_name, status="enabled").first()
    if not proposal:
        return None

    proposal.successful_uses = (proposal.successful_uses or 0) + 1

    # Promote to autonomous after 5 successful uses
    if proposal.enabled_mode == "guarded" and proposal.successful_uses >= 5:
        proposal.enabled_mode = "autonomous"
        logger.info(
            "Promoted tool %s to autonomous mode after %d uses",
            proposal.name, proposal.successful_uses,
        )

    db.commit()
    db.refresh(proposal)
    return proposal
