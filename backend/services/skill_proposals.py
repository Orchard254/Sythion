"""
Higher-order skill proposal generation, evidence growth, and promotion.

This module owns the non-runtime part of the skill system:

- extracting verified traces from completed tasks
- grouping materially similar traces
- generating declarative proposals
- accumulating evidence across runs
- explicit approval / rejection
- optional auto-promotion once evidence reaches threshold

Execution and pinning stay in `skill_runtime.py` so trusted runtime behavior
remains separate from growth-oriented intelligence features.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

try:
    from backend.models.database import ProposedSkill, Session, Skill, Task, TaskEvent
except ImportError:
    from models.database import ProposedSkill, Session, Skill, Task, TaskEvent

try:
    from backend.services.skill_runtime import (
        SkillDefinition,
        SkillParameter,
        SkillStep,
        add_skill_version,
        allowed_tool_names,
        create_skill,
        definition_to_json,
        get_skill,
        parse_definition,
        validate_definition,
    )
except ImportError:
    from services.skill_runtime import (
        SkillDefinition,
        SkillParameter,
        SkillStep,
        add_skill_version,
        allowed_tool_names,
        create_skill,
        definition_to_json,
        get_skill,
        parse_definition,
        validate_definition,
    )

logger = logging.getLogger(__name__)

PROPOSAL_MIN_SIMILAR_TRACES = 2


def _task_trace(db: Session, task: Task, tool_executor: Any = None) -> Optional[List[Dict[str, Any]]]:
    """Return the ordered successful tool-execution trace of a completed task."""
    events = (
        db.query(TaskEvent)
        .filter_by(task_id=task.id)
        .order_by(TaskEvent.sequence_num.asc())
        .all()
    )
    trace: List[Dict[str, Any]] = []
    for ev in events:
        if ev.event_type != "tool.executed":
            continue
        try:
            data = json.loads(ev.event_data)
        except (json.JSONDecodeError, TypeError):
            continue
        if not data.get("success", False):
            return None
        tool_name = data.get("tool_name")
        if not tool_name:
            continue
        params = data.get("params")
        if not isinstance(params, dict):
            params = data.get("input_data") or data.get("input") or {}
        if not isinstance(params, dict):
            params = {}

        if tool_executor:
            tool = tool_executor.get_tool(tool_name)
            if tool:
                allowed_fields = set(getattr(tool.input_schema, "model_fields", {}).keys())
                params = {k: v for k, v in params.items() if k in allowed_fields}

        trace.append({
            "tool_name": tool_name,
            "params": params,
            "event": data,
        })
    if not trace:
        return None
    return trace


def _trace_signature(trace: List[Dict[str, Any]]) -> Tuple[Tuple[str, Tuple[str, ...]], ...]:
    return tuple(
        (ev["tool_name"], tuple(sorted(ev["params"].keys())))
        for ev in trace
    )


def _traces_materially_similar(a: List[Dict[str, Any]], b: List[Dict[str, Any]]) -> bool:
    return _trace_signature(a) == _trace_signature(b)


def _proposal_definition(
    name: str,
    category: str,
    traces: List[List[Dict[str, Any]]],
    tool_executor: Any = None,
) -> SkillDefinition:
    """Derive a declarative definition from >=2 materially similar traces."""
    first = traces[0]
    steps = []
    parameters: List[SkillParameter] = []
    used_params: set = set()

    for idx, ev in enumerate(first):
        step_id = f"step_{idx + 1}"
        tool_name = ev["tool_name"]
        inputs: Dict[str, Any] = {}

        tool = tool_executor.get_tool(tool_name) if tool_executor else None
        valid_keys: Optional[set] = None
        if tool:
            valid_keys = set(getattr(tool.input_schema, "model_fields", {}).keys())

        for key, value in ev["params"].items():
            if valid_keys is not None and key not in valid_keys:
                continue
            values = {str(t[idx]["params"].get(key)) for t in traces if idx < len(t)}
            if len(values) <= 1:
                inputs[key] = value
            else:
                pname = f"input_{len(used_params) + 1}"
                while pname in used_params:
                    pname = f"input_{len(used_params) + 1}"
                used_params.add(pname)
                parameters.append(SkillParameter(
                    name=pname,
                    type="string",
                    required=False,
                    description=f"Value for {tool_name}.{key} (varied between similar runs)",
                ))
                inputs[key] = f"inputs.{pname}"
        steps.append(SkillStep(step_id=step_id, tool=tool_name, inputs=inputs))

    return SkillDefinition(
        version="1.0.0",
        name=name,
        description="Proposed from %d materially similar verified successful traces" % len(traces),
        category=category,
        parameters=parameters,
        steps=steps,
    )


def _auto_enable_if_ready(
    db: Session,
    proposal: ProposedSkill,
    tool_executor: Any,
) -> Optional[Skill]:
    """Promote a proposal to a live skill once its verified trace count reaches threshold."""
    try:
        from backend.core.config import settings
    except ImportError:
        from core.config import settings

    threshold = getattr(settings, "skill_auto_enable_threshold", 0)
    if threshold <= 0:
        return None
    if proposal.skill_id:
        return None

    try:
        trace_ids = json.loads(proposal.trace_ids) if proposal.trace_ids else []
    except (json.JSONDecodeError, TypeError):
        trace_ids = []
    if len(trace_ids) < threshold:
        return None

    definition = parse_definition(proposal.definition)
    validation_errors = validate_definition(definition, allowed_tool_names(tool_executor))
    if validation_errors:
        proposal.validation_errors = json.dumps(validation_errors)
        db.commit()
        logger.warning(
            "Proposal %s not auto-enabled: re-validation failed: %s",
            proposal.id, "; ".join(validation_errors),
        )
        return None

    skill = approve_proposal(db, proposal, tool_executor)
    logger.info(
        "Auto-enabled proposal %s as skill %s (%d verified traces)",
        proposal.id, skill.id, len(trace_ids),
    )
    return skill


def _proposal_matches_trace(
    db: Session,
    proposal: ProposedSkill,
    trace_ids: List[str],
    trace: List[Dict[str, Any]],
    tool_executor: Any = None,
) -> bool:
    """True when any of the proposal's source tasks shares the trace signature."""
    for tid in trace_ids:
        other_task = db.query(Task).filter_by(id=tid).first()
        if not other_task:
            continue
        other = _task_trace(db, other_task, tool_executor=tool_executor)
        if other and _traces_materially_similar(trace, other):
            return True
    return False


def maybe_generate_proposal(
    db: Session,
    task_id: str,
    tool_executor: Any,
) -> Optional[ProposedSkill]:
    """Create or grow a proposed skill from materially similar verified traces."""
    task = db.query(Task).filter_by(id=task_id).first()
    if not task or task.state != "completed":
        return None

    trace = _task_trace(db, task, tool_executor=tool_executor)
    if not trace:
        return None

    for prop in (
        db.query(ProposedSkill)
        .filter(ProposedSkill.status.in_(["proposed", "rejected", "enabled"]))
        .all()
    ):
        try:
            meta = json.loads(prop.trace_ids) if prop.trace_ids else []
        except (json.JSONDecodeError, TypeError):
            meta = []
        if task.id in meta:
            return None
        if not _proposal_matches_trace(db, prop, meta, trace, tool_executor=tool_executor):
            continue
        if prop.status == "rejected":
            logger.info(
                "Not re-proposing signature of rejected proposal %s (task %s)",
                prop.id, task_id,
            )
            return None
        if prop.status == "enabled":
            return None
        meta.append(task.id)
        prop.trace_ids = json.dumps(meta)
        db.commit()
        _auto_enable_if_ready(db, prop, tool_executor)
        return prop

    similar: List[Task] = []
    candidates = (
        db.query(Task)
        .filter(Task.state == "completed", Task.id != task_id)
        .order_by(Task.created_at.asc())
        .all()
    )
    for candidate in candidates:
        if candidate.id == task_id:
            continue
        other = _task_trace(db, candidate, tool_executor=tool_executor)
        if other and _traces_materially_similar(trace, other):
            similar.append(candidate)
        if len(similar) >= PROPOSAL_MIN_SIMILAR_TRACES - 1:
            break

    if not similar:
        return None

    all_traces = [_task_trace(db, t, tool_executor=tool_executor) for t in ([task] + similar)]
    all_traces = [t for t in all_traces if t]

    definition = _proposal_definition(
        name=f"{trace[0]['tool_name']} workflow",
        category="custom",
        traces=all_traces,
        tool_executor=tool_executor,
    )
    validation_errors = validate_definition(definition, allowed_tool_names(tool_executor))
    proposal = ProposedSkill(
        definition=definition_to_json(definition),
        trace_ids=json.dumps([t.id for t in [task] + similar]),
        status="proposed",
        validation_errors=json.dumps(validation_errors),
    )
    db.add(proposal)
    db.commit()
    db.refresh(proposal)
    logger.info(
        "Proposed skill from %d traces: %s (task %s)", len(all_traces), definition.name, task.id
    )
    _auto_enable_if_ready(db, proposal, tool_executor)
    return proposal


def list_proposals(db: Session, status: Optional[str] = None) -> List[Dict[str, Any]]:
    query = db.query(ProposedSkill).order_by(ProposedSkill.id.desc())
    if status:
        query = query.filter_by(status=status)
    out = []
    for p in query.all():
        out.append({
            "id": p.id,
            "definition": json.loads(p.definition) if p.definition else None,
            "trace_ids": json.loads(p.trace_ids) if p.trace_ids else [],
            "status": p.status,
            "validation_errors": json.loads(p.validation_errors) if p.validation_errors else [],
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "decided_at": p.decided_at.isoformat() if p.decided_at else None,
        })
    return out


def get_proposal(db: Session, proposal_id: int) -> Optional[ProposedSkill]:
    return db.query(ProposedSkill).filter_by(id=proposal_id).first()


def approve_proposal(db: Session, proposal: ProposedSkill, tool_executor: Any) -> Skill:
    """Explicitly review and enable a proposed skill as a live, versioned skill."""
    if proposal.status != "proposed":
        raise ValueError(f"Proposal {proposal.id} is already {proposal.status}")
    definition = parse_definition(proposal.definition)
    validation_errors = validate_definition(definition, allowed_tool_names(tool_executor))
    if validation_errors:
        proposal.validation_errors = json.dumps(validation_errors)
        db.commit()
        raise ValueError(
            "Proposal cannot be enabled: validation failed: " + "; ".join(validation_errors)
        )

    skill_id = proposal.skill_id or str(proposal.id)
    skill = get_skill(db, skill_id)
    if not skill:
        skill = create_skill(
            db,
            skill_id=skill_id,
            name=definition.name,
            description=definition.description,
            category=definition.category,
            state="enabled",
        )
    add_skill_version(db, skill, definition, validation_errors)
    proposal.status = "enabled"
    proposal.decided_at = datetime.utcnow()
    db.commit()
    db.refresh(skill)
    return skill


def reject_proposal(db: Session, proposal: ProposedSkill) -> ProposedSkill:
    if proposal.status != "proposed":
        raise ValueError(f"Proposal {proposal.id} is already {proposal.status}")
    proposal.status = "rejected"
    proposal.decided_at = datetime.utcnow()
    db.commit()
    db.refresh(proposal)
    return proposal
