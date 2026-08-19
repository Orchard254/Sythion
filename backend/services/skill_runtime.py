"""
Runtime-facing declarative skill validation, storage, and execution.

This module owns the trusted runtime concerns of the skill system:

- declarative skill schema parsing and validation
- policy-filtered tool allowlisting
- skill/version persistence helpers
- pinned version execution and pause semantics

Proposal generation and growth live separately in `skill_proposals.py` so the
runtime workflow contract stays distinct from higher-order skill evolution.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field, ValidationError

try:
    from backend.models.database import Session, Skill, SkillVersion, Task
except ImportError:
    from models.database import Session, Skill, SkillVersion, Task

logger = logging.getLogger(__name__)

ID_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
INPUT_REF_RE = re.compile(r"^inputs\.([a-zA-Z_][a-zA-Z0-9_]{0,63})$")
STEP_OUTPUT_REF_RE = re.compile(
    r"^steps\.([a-zA-Z_][a-zA-Z0-9_]{0,63})\.output\.([a-zA-Z_][a-zA-Z0-9_]{0,63})$"
)

_EXECUTABLE_PATTERNS = (
    "{{", "}}", "eval(", "exec(", "__import__", "import ", "subprocess",
    "popen", "os.system", "shutil", "compile(", "lambda ", "globals(",
    "locals(", "system(", "shell", "`",
)

_COORDINATE_KEYS = frozenset({
    "x", "y", "xy", "coord", "coords", "coordinates", "position", "positions",
    "point", "points", "bounds", "region", "locations", "location", "pixel",
    "pixels", "offset", "offsets", "top", "left",
})

_COORDINATE_VALUE_RE = re.compile(r"^\s*(\[\s*)?\d+\s*,\s*\d+(\s*\])?\s*$")

_SECRET_KEY_HINTS = (
    "api_key", "apikey", "password", "passwd", "secret", "token",
    "credential", "private_key", "access_key",
)


class SkillParameter(BaseModel):
    """Declared input parameter of a skill."""

    name: str
    type: str = "string"
    required: bool = False
    description: str = ""
    default: Any = None

    model_config = {"protected_namespaces": ()}


class SkillStep(BaseModel):
    """One declarative step: a registered tool plus its inputs."""

    step_id: str
    tool: str
    inputs: Dict[str, Any] = Field(default_factory=dict)
    description: str = ""

    model_config = {"protected_namespaces": ()}


class SkillDefinition(BaseModel):
    """Versioned declarative skill definition."""

    version: str
    name: str
    description: str = ""
    category: str = "custom"
    parameters: List[SkillParameter] = Field(default_factory=list)
    steps: List[SkillStep] = Field(default_factory=list)

    model_config = {"protected_namespaces": ()}


class SkillRunOutcome(BaseModel):
    """Outcome of a declarative skill execution."""

    success: bool = False
    paused: bool = False
    error: Optional[str] = None
    outputs: Dict[str, Any] = Field(default_factory=dict)
    version: Optional[str] = None
    definition_hash: Optional[str] = None

    model_config = {"protected_namespaces": ()}


def definition_to_json(definition: SkillDefinition) -> str:
    """Serialize a definition deterministically for storage."""
    return json.dumps(definition.model_dump(), sort_keys=True)


def definition_hash(definition_json: str) -> str:
    """Return a content hash used to pin a validated skill version."""
    return hashlib.sha256(definition_json.encode("utf-8")).hexdigest()


def parse_definition(raw: str) -> SkillDefinition:
    """Parse a stored definition string, raising ValidationError on malformed input."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationError.from_exception_data(
            "SkillDefinition",
            [{"type": "value_error", "loc": ("json",), "input": raw, "ctx": {"error": exc}}],
        )
    return SkillDefinition.model_validate(data)


def _declared_param_names(definition: SkillDefinition) -> set:
    return {p.name for p in definition.parameters}


def _literal_allowed(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool))


def _value_is_reference(value: Any) -> bool:
    return isinstance(value, str) and (
        INPUT_REF_RE.match(value) or STEP_OUTPUT_REF_RE.match(value)
    )


def _is_coordinate_key(key: str) -> bool:
    key_l = key.strip().lower().replace("-", "_")
    for token in _COORDINATE_KEYS:
        if key_l == token or key_l.endswith("_" + token) or key_l.startswith(token + "_"):
            return True
    return False


def _has_secret_hint(key: str) -> bool:
    key_l = key.strip().lower().replace("-", "_")
    return any(hint in key_l for hint in _SECRET_KEY_HINTS)


def validate_definition(
    definition: SkillDefinition,
    available_tools: Dict[str, Any],
) -> List[str]:
    """Validate a declarative skill definition against the allowed tool set."""
    errors: List[str] = []

    if not VERSION_RE.match(definition.version):
        errors.append(f"version must match major.minor.patch (got {definition.version!r})")
    if not definition.name.strip():
        errors.append("name must not be empty")
    if len(definition.name) > 128:
        errors.append("name must be at most 128 characters")
    if not definition.steps:
        errors.append("a skill must define at least one step")

    param_names = _declared_param_names(definition)
    seen_ids: set = set()
    step_order: Dict[str, int] = {
        step.step_id: idx for idx, step in enumerate(definition.steps)
    }

    for p in definition.parameters:
        if not ID_RE.match(p.name):
            errors.append(f"parameter name {p.name!r} is invalid (must match {ID_RE.pattern})")
        if p.type not in {"string", "integer", "number", "boolean"}:
            errors.append(f"parameter {p.name!r} has unsupported type {p.type!r}")
        if _is_coordinate_key(p.name):
            errors.append(f"parameter {p.name!r} looks like a screen coordinate and is rejected")
        if _has_secret_hint(p.name):
            errors.append(f"parameter {p.name!r} looks like a secret and is rejected")

    for step in definition.steps:
        if not ID_RE.match(step.step_id):
            errors.append(f"step_id {step.step_id!r} is invalid (must match {ID_RE.pattern})")
        if step.step_id in seen_ids:
            errors.append(f"duplicate step_id {step.step_id!r}")
        seen_ids.add(step.step_id)

        tool = available_tools.get(step.tool)
        if tool is None:
            errors.append(f"step {step.step_id!r} references unknown or disallowed tool {step.tool!r}")
            continue
        tool_fields = set(getattr(tool.input_schema, "model_fields", {}).keys())

        for key, value in step.inputs.items():
            if key not in tool_fields:
                errors.append(
                    f"step {step.step_id!r} input {key!r} is not a parameter of tool {step.tool!r}"
                )
            if _is_coordinate_key(key):
                errors.append(f"step {step.step_id!r} input {key!r} looks like a coordinate and is rejected")
            if _has_secret_hint(key):
                errors.append(f"step {step.step_id!r} input {key!r} looks like a secret and is rejected")

            if _value_is_reference(value):
                ref = value
                if INPUT_REF_RE.match(ref):
                    pname = INPUT_REF_RE.match(ref).group(1)
                    if pname not in param_names:
                        errors.append(
                            f"step {step.step_id!r} references undeclared input parameter {pname!r}"
                        )
                else:
                    m = STEP_OUTPUT_REF_RE.match(ref)
                    dep_id = m.group(1)
                    if dep_id not in step_order:
                        errors.append(
                            f"step {step.step_id!r} references unknown step {dep_id!r}"
                        )
                    elif step_order[dep_id] >= step_order[step.step_id]:
                        errors.append(
                            f"step {step.step_id!r} references a later step {dep_id!r} (no forward refs)"
                        )
            elif _literal_allowed(value):
                if isinstance(value, str):
                    text = value
                    for token in _EXECUTABLE_PATTERNS:
                        if token in text:
                            errors.append(
                                f"step {step.step_id!r} input {key!r} contains rejected content {token!r}"
                            )
                    if _COORDINATE_VALUE_RE.match(text):
                        errors.append(
                            f"step {step.step_id!r} input {key!r} looks like a coordinate pair"
                        )
            else:
                errors.append(
                    f"step {step.step_id!r} input {key!r} must be a literal scalar or a "
                    "reference (inputs.<field> / steps.<id>.output.<field>)"
                )

    return errors


def allowed_tool_names(tool_executor: Any) -> Dict[str, Any]:
    """Return the policy-filtered read-only tool subset skills may call."""
    allowed = {}
    for name in tool_executor.list_tools():
        tool = tool_executor.get_tool(name)
        if not tool:
            continue
        reqs = tool.get_policy_requirements()
        if reqs.get("mutating") or reqs.get("destructive"):
            if not reqs.get("app_internal"):
                continue
        if reqs.get("requires_confirmation"):
            continue
        if reqs.get("risk_level") in {"medium", "high"}:
            continue
        if (
            reqs.get("privacy_sensitive")
            or reqs.get("requires_screen_access")
            or reqs.get("requires_ui_automation")
        ):
            continue
        allowed[name] = tool
    return allowed


def get_skill_version(db: Session, skill_id: str, version: str) -> Optional[SkillVersion]:
    """Fetch one exact skill version."""
    return (
        db.query(SkillVersion)
        .filter_by(skill_id=skill_id, version=version)
        .order_by(SkillVersion.id.desc())
        .first()
    )


def get_enabled_skill_version(db: Session, skill_id: str) -> Optional[Tuple[Skill, SkillVersion]]:
    """Return (skill, enabled_version) when the skill is enabled, else None."""
    skill = db.query(Skill).filter_by(id=skill_id).first()
    if not skill or skill.state != "enabled":
        return None
    if not skill.versions:
        return None
    version = max(skill.versions, key=lambda v: v.id)
    return skill, version


def get_skill(db: Session, skill_id: str) -> Optional[Skill]:
    return db.query(Skill).filter_by(id=skill_id).first()


def list_skill_summaries(db: Session) -> List[Dict[str, Any]]:
    rows = db.query(Skill).order_by(Skill.id.asc()).all()
    out = []
    for skill in rows:
        current = max(skill.versions, key=lambda v: v.id) if skill.versions else None
        out.append({
            "id": skill.id,
            "name": skill.name,
            "description": skill.description,
            "category": skill.category if hasattr(skill, "category") else "custom",
            "state": skill.state,
            "version_count": len(skill.versions),
            "current_version": current.version if current else None,
            "current_definition_hash": current.definition_hash if current else None,
            "created_at": skill.created_at.isoformat() if skill.created_at else None,
        })
    return out


def get_skill_detail(db: Session, skill_id: str) -> Optional[Dict[str, Any]]:
    skill = get_skill(db, skill_id)
    if not skill:
        return None
    versions = sorted(skill.versions, key=lambda v: v.id, reverse=True)
    return {
        "id": skill.id,
        "name": skill.name,
        "description": skill.description,
        "category": skill.category if hasattr(skill, "category") else "custom",
        "state": skill.state,
        "created_at": skill.created_at.isoformat() if skill.created_at else None,
        "versions": [
            {
                "version": v.version,
                "definition_hash": v.definition_hash,
                "definition": json.loads(v.definition) if v.definition else None,
                "validation_errors": json.loads(v.validation_result) if v.validation_result else [],
                "created_at": v.created_at.isoformat() if v.created_at else None,
            }
            for v in versions
        ],
    }


def add_skill_version(
    db: Session,
    skill: Skill,
    definition: SkillDefinition,
    validation_errors: List[str],
) -> SkillVersion:
    """Append a new validated version to a skill."""
    definition_json = definition_to_json(definition)
    version = SkillVersion(
        skill_id=skill.id,
        version=definition.version,
        definition=definition_json,
        definition_hash=definition_hash(definition_json),
        validation_result=json.dumps(validation_errors),
    )
    db.add(version)
    skill.name = definition.name
    skill.description = definition.description
    db.commit()
    db.refresh(version)
    return version


def create_skill(
    db: Session,
    skill_id: str,
    name: str,
    description: str,
    category: str = "custom",
    state: str = "enabled",
) -> Skill:
    skill = Skill(
        id=skill_id,
        name=name,
        description=description,
        category=category,
        state=state,
    )
    db.add(skill)
    db.commit()
    db.refresh(skill)
    return skill


def set_skill_state(db: Session, skill_id: str, state: str) -> Optional[Skill]:
    skill = get_skill(db, skill_id)
    if not skill:
        return None
    skill.state = state
    db.commit()
    db.refresh(skill)
    return skill


def _resolve_value(
    value: Any,
    inputs: Dict[str, Any],
    outputs: Dict[str, Dict[str, Any]],
) -> Any:
    if isinstance(value, str):
        m = INPUT_REF_RE.match(value)
        if m:
            pname = m.group(1)
            if pname not in inputs:
                raise ValueError(f"missing input parameter {pname!r}")
            return inputs[pname]
        m = STEP_OUTPUT_REF_RE.match(value)
        if m:
            dep_id, field = m.group(1), m.group(2)
            step_out = outputs.get(dep_id)
            if step_out is None:
                raise ValueError(f"step {dep_id!r} produced no output")
            if field not in step_out:
                raise ValueError(f"step {dep_id!r} output has no field {field!r}")
            return step_out[field]
    return value


def _pinned_marker(skill_id: str, version: str, definition_hash_value: str) -> str:
    return f"{skill_id}@{version}@{definition_hash_value[:12]}"


async def execute_skill(
    db: Session,
    runner: Any,
    task_id: str,
    skill_id: str,
    inputs: Dict[str, Any],
    tool_executor: Any,
) -> SkillRunOutcome:
    """Pin the validated skill version and run its steps declaratively."""
    try:
        from backend.api.schemas import TaskState
        from backend.tools.executor import PolicyContext
    except ImportError:
        from api.schemas import TaskState
        from tools.executor import PolicyContext

    enabled = get_enabled_skill_version(db, skill_id)
    if not enabled:
        return SkillRunOutcome(success=False, error=f"skill {skill_id!r} is not enabled")
    skill, version = enabled
    definition = parse_definition(version.definition)
    marker = _pinned_marker(skill_id, version.version, version.definition_hash)

    task = db.query(Task).filter_by(id=task_id).first()
    if not task:
        return SkillRunOutcome(success=False, error=f"task {task_id!r} not found")

    pinned = task.selected_skill_version
    if pinned and pinned != marker:
        return SkillRunOutcome(
            success=False,
            error=f"task already pinned to a different skill version ({pinned})",
        )
    if not pinned:
        task.selected_skill_version = marker
        db.commit()

    async def _is_cancelled() -> bool:
        current = await runner.get_task(task_id)
        return current is not None and current.state == TaskState.CANCELLED.value

    outputs: Dict[str, Dict[str, Any]] = {}

    for step in definition.steps:
        current = get_enabled_skill_version(db, skill_id)
        if not current:
            await _pause_for_disabled(runner, task_id, skill_id, step.step_id, "disabled")
            return SkillRunOutcome(success=False, paused=True, error="skill_disabled")
        cur_skill, cur_version = current
        if (
            cur_skill.state != "enabled"
            or _pinned_marker(skill_id, cur_version.version, cur_version.definition_hash) != marker
        ):
            await _pause_for_disabled(runner, task_id, skill_id, step.step_id, "changed_or_disabled")
            return SkillRunOutcome(success=False, paused=True, error="skill_disabled")

        try:
            resolved = {
                key: _resolve_value(value, inputs, outputs)
                for key, value in step.inputs.items()
            }
        except ValueError as exc:
            return SkillRunOutcome(success=False, error=str(exc))

        result = await tool_executor.execute_tool(
            tool_name=step.tool,
            input_data=resolved,
            policy_context=PolicyContext(
                direct_user_instruction=False,
                cancelled_check=_is_cancelled,
            ),
        )

        model_summary = result.model_summary
        if not model_summary and result.data is not None:
            model_summary = json.dumps(result.data, default=str)[:200]

        await runner.record_event(
            task_id=task_id,
            event_type="tool.executed",
            event_data={
                "tool_name": step.tool,
                "params": resolved,
                "success": result.success,
                "output": result.data or {},
                "model_summary": model_summary,
                "error": result.error,
                "evidence": result.evidence,
                "duration_ms": result.duration_ms,
                "mutating": False,
                "verification": "passed" if result.success else "failed",
                "via_skill": skill_id,
                "skill_version": version.version,
                "step_id": step.step_id,
            },
        )

        if not result.success:
            await runner.record_event(
                task_id=task_id,
                event_type="skill.executed",
                event_data={
                    "skill_id": skill_id,
                    "skill_name": definition.name,
                    "version": version.version,
                    "success": False,
                    "error": (result.error or {}).get("message", "step failed"),
                    "failed_step_id": step.step_id,
                },
            )
            return SkillRunOutcome(
                success=False,
                error=f"step {step.step_id!r} failed",
                version=version.version,
                definition_hash=version.definition_hash,
                outputs=outputs,
            )

        outputs[step.step_id] = result.data if isinstance(result.data, dict) else {
            "value": result.data
        }

    await runner.record_event(
        task_id=task_id,
        event_type="skill.executed",
        event_data={
            "skill_id": skill_id,
            "skill_name": definition.name,
            "version": version.version,
            "success": True,
            "steps": len(definition.steps),
        },
    )
    return SkillRunOutcome(
        success=True,
        version=version.version,
        definition_hash=version.definition_hash,
        outputs=outputs,
    )


async def _pause_for_disabled(
    runner: Any,
    task_id: str,
    skill_id: str,
    next_step_id: str,
    reason: str,
) -> None:
    try:
        from backend.api.schemas import TaskState
    except ImportError:
        from api.schemas import TaskState

    await runner.transition_state(
        task_id=task_id,
        new_state=TaskState.PAUSED,
        event_type="task.paused",
        event_data={
            "reason": reason,
            "skill_id": skill_id,
            "next_step_id": next_step_id,
            "paused_at": datetime.utcnow().isoformat(),
        },
        terminal_reason="paused_skill_disabled",
    )
    logger.info("Task %s paused (skill %s %s before step %s)", task_id, skill_id, reason, next_step_id)
