"""
L3 tool-authoring pipeline: model-written tool proposals with strict gates.

A tool proposal includes a name, description, input schema, and Python
implementation. Every proposal must pass:
1. Code validation (no secrets, no dangerous imports, read-only first)
2. Static interface checking (AST-only; the implementation is NEVER executed)
3. Owner approval (explicit review)

PROTECTED RUNTIME CONTRACT (Phase 0): proposal implementations are treated as
untrusted data everywhere in this process. They are stored verbatim for review
but are never compiled, executed, imported, or registered as live tools.
Executable enablement of custom tools requires a genuine out-of-process
isolation boundary and is deferred to a later phase. See
README.md ("Process isolation").
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


class StaticCheckResult(BaseModel):
    """Result of statically checking a tool proposal.

    Static analysis only — the implementation source is parsed with ``ast``
    but never compiled or executed, so no proposal code can run inside the
    core process at any stage of the authoring pipeline.
    """
    passed: bool
    errors: List[str] = []
    detail: Optional[str] = None


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


# --- Static interface checking (no execution) -------------------------------

def static_interface_check(proposal: ToolProposal) -> StaticCheckResult:
    """Statically check the proposal's declared interface without executing it.

    PROTECTED RUNTIME CONTRACT: this check parses the implementation with
    ``ast`` only. It never compiles, exec()s, imports, or instantiates any
    code from the proposal, so untrusted implementation source can never run
    inside the core process at proposal time. (The previous "sandbox test"
    executed the implementation in a restricted namespace; that was removed
    because restricted builtins are not a security boundary.)

    Checks performed statically:
    - The source parses (syntax validity).
    - Exactly-shaped tool class exists: inherits from BaseTool/Tool, defines
      name/description/input_schema class attributes and execute +
      get_policy_requirements methods.
    """
    errors: List[str] = []

    try:
        tree = ast.parse(proposal.implementation)
    except SyntaxError as e:
        return StaticCheckResult(
            passed=False,
            errors=[f"Implementation has syntax error: {e}"],
        )

    candidate_classes = [
        node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
    ]
    if not candidate_classes:
        return StaticCheckResult(passed=False, errors=["No class definition found"])

    def _base_names(node: ast.ClassDef) -> List[str]:
        names = []
        for base in node.bases:
            if isinstance(base, ast.Name):
                names.append(base.id)
            elif isinstance(base, ast.Attribute):
                names.append(base.attr)
        return names

    def _method_names(node: ast.ClassDef) -> set:
        return {
            n.name
            for n in node.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

    def _assigned_attrs(node: ast.ClassDef) -> set:
        attrs = set()
        for stmt in node.body:
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Name):
                        attrs.add(target.id)
            elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                attrs.add(stmt.target.id)
        return attrs

    conforming: Optional[ast.ClassDef] = None
    for node in candidate_classes:
        if not any("BaseTool" in b or "Tool" in b for b in _base_names(node)):
            continue
        methods = _method_names(node)
        attrs = _assigned_attrs(node)
        missing = []
        if "execute" not in methods:
            missing.append("execute method")
        if "get_policy_requirements" not in methods:
            missing.append("get_policy_requirements method")
        for attr in ("name", "description", "input_schema"):
            if attr not in attrs and attr not in methods:
                missing.append(f"{attr!r} attribute")
        if missing:
            errors.append(
                f"Class {node.name!r} is missing: {', '.join(missing)}"
            )
        else:
            conforming = node

    if conforming is None and not any(
        any("BaseTool" in b or "Tool" in b for b in _base_names(c))
        for c in candidate_classes
    ):
        errors.append("No class inheriting from BaseTool found")

    if errors:
        return StaticCheckResult(passed=False, errors=errors)

    detail = (
        "Static interface check passed; implementation was NOT executed "
        "(no in-process execution of proposal code)."
    )
    if conforming is not None:
        detail += f" Tool class {conforming.name!r} declares the required interface."
    return StaticCheckResult(passed=True, detail=detail)


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
    """Approve a tool proposal after validation and static interface checks.

    PROTECTED RUNTIME CONTRACT (Phase 0): approval records the owner review
    decision on the proposal artifact only. The implementation is NEVER
    executed, imported, or registered with the executor — the core process
    must not run database-stored or model-authored Python. ``tool_executor``
    is accepted for interface compatibility but is intentionally unused.
    Executable enablement of custom tools requires a genuine out-of-process
    isolation boundary (future phase).
    """
    del tool_executor  # unused by contract; kept for signature stability

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

    # Re-run static interface check (no execution)
    check = static_interface_check(tool_proposal)
    if not check.passed:
        proposal.sandbox_result = json.dumps(check.model_dump())
        db.commit()
        raise ValueError(
            "Tool proposal failed static interface check: "
            + "; ".join(check.errors or ["unknown"])
        )

    # Record the approval decision on the proposal artifact. The stored
    # implementation remains inert data: it is never loaded as a live tool.
    proposal.status = "enabled"
    proposal.enabled_mode = "guarded"
    proposal.enabled_at = datetime.utcnow()
    proposal.decided_at = datetime.utcnow()
    db.commit()
    db.refresh(proposal)

    logger.info(
        "Approved tool proposal %s (%s); stored as an inert reviewed artifact "
        "(in-process execution of proposed tools is disabled)",
        proposal.id,
        proposal.name,
    )
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
    """Record a successful use of a proposed tool (metadata only).

    Phase 0 contract: proposed tools are inert artifacts and are never
    executed, so this counter does not advance under normal operation.
    Retained for lifecycle compatibility; mode promotion is metadata only
    and confers no execution capability.
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
