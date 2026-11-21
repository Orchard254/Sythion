"""Structured deterministic SelfModel (Phase 5).

Derived ENTIRELY from authoritative runtime sources: MainAgentService,
CapabilityRegistry, ModelRegistry, resource snapshot, health checks. The LLM
can never write it — there is no write path. Answers, without guessing:

  Who am I? What can I do? What can't I do right now?
  Which capabilities require permission? Which models/providers are healthy?
  What resources/budgets exist? What Goals/tasks are active?
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import backend as _backend_pkg

from backend.services.capabilities import (
    AVAILABLE,
    CapabilityRegistry,
    DEGRADED,
    REQUIRES_CONFIGURATION,
    REQUIRES_PERMISSION,
    UNAVAILABLE,
)


def build_self_model(db_session, tool_executor=None) -> Dict[str, Any]:
    """Compose the full self-model from authoritative runtime state."""
    from backend.core.config import settings
    from backend.runtime.model_provider import get_default_provider
    from backend.services.main_agent import MainAgentService
    from backend.services.system_state import (
        ModelRegistry,
        resource_snapshot,
        run_health_checks,
    )

    agent_svc = MainAgentService(db_session)
    agent = agent_svc.get_or_create()

    # -- identity ------------------------------------------------------------
    identity = {
        "agent_id": agent.agent_id,
        "name": agent.name,
        "runtime_version": _backend_pkg.__version__,
        "architecture_version": "phase-5",
    }

    # -- runtime state ----------------------------------------------------------
    current_task = None
    try:
        from backend.models.database import RunnerLease

        lease = db_session.query(RunnerLease).filter_by(id=1).first()
        lease_state = {
            "held": lease is not None,
            "expires_at": lease.expires_at.isoformat() if lease else None,
            "task_id": lease.last_task_id if lease else None,
        }
        current_task = lease.last_task_id if lease else None
    except Exception:
        lease_state = {"held": None}

    runtime_state = {
        "agent_state": agent.state,
        "background_orchestration_enabled": agent_svc.background_enabled(),
        "current_execution_session": agent.last_session_outcome,
        "current_task": current_task,
        "lease": lease_state,
    }

    # -- capabilities -------------------------------------------------------------
    registry = CapabilityRegistry(db_session, tool_executor)
    caps = registry.list_capabilities(include_details=True)
    capability_summary = {
        "available": [c["id"] for c in caps if c["status"] == AVAILABLE],
        "unavailable": [
            {"id": c["id"], "reason": c["status"]} for c in caps
            if c["status"] == UNAVAILABLE
        ],
        "degraded": [{"id": c["id"]} for c in caps if c["status"] == DEGRADED],
        "requires_permission": [
            {"id": c["id"], "risk_class": c["risk_class"]}
            for c in caps if c.get("requires_permission")
        ],
        "requires_configuration": [
            {"id": c["id"]} for c in caps
            if c["status"] == REQUIRES_CONFIGURATION
        ],
        "total": len(caps),
    }

    # -- models ---------------------------------------------------------------------
    provider = get_default_provider()
    model_registry = ModelRegistry(db_session)
    provider_health = provider.health()
    selected = model_registry.selected_model()
    # The fields above describe the LOCAL/DEFAULT provider registry. Expose the
    # authoritative active reasoning binding under distinct names so consumers
    # never mistake the default provider for the one actually used at runtime.
    from backend.runtime.active_inference import (
        get_active_inference_selection, ActiveInferenceUnavailable,
    )
    try:
        _active = get_active_inference_selection()
        _active_provider_id = _active.provider_id
        _active_model_id = _active.model_id
    except ActiveInferenceUnavailable:
        _active_provider_id = "unavailable"
        _active_model_id = ""

    models_summary = {
        "active_provider_id": _active_provider_id,
        "active_model_id": _active_model_id,
        "provider_id": provider.provider_id(),
        "provider_health": provider_health.to_dict(),
        "selected_model": selected.model_id if selected else None,
        "selected_available_now": (
            selected.available_now if selected else None
        ),
        "registered_count": len(model_registry.list_models()),
        # Truthfulness contract:
        "evaluated_claims": False,  # no evaluation system exists yet
    }

    return {
        "identity": identity,
        "runtime_state": runtime_state,
        "capabilities": capability_summary,
        "capabilities_detail": caps,
        "models": models_summary,
        "resources": resource_snapshot(db_session),
        "health": run_health_checks(db_session=db_session),
    }