"""Deterministic Model Registry + ResourceState + Health framework (Phase 5).

Truthfulness rules:
- SUPPORTED ≠ AVAILABLE_NOW ≠ EVALUATED. Model quality is `evaluated=False`
  until a real evaluation system exists — name-based inference is forbidden.
- Unknown stays unknown: no fabricated resource values.
- The registry describes; the Protected Runtime authorizes.
"""

from __future__ import annotations

import platform
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from backend.runtime.model_provider import ModelHealth, get_default_provider


# --------------------------------------------------------------------------- #
# Model registry                                                               #
# --------------------------------------------------------------------------- #

@dataclass
class ModelRecord:
    model_id: str
    provider_id: str
    selected: bool = False
    available_now: Optional[bool] = None   # None = unknown until probed/listed
    context_tokens: Optional[int] = None   # unknown unless measured/provided
    tool_calling_supported: Optional[bool] = None
    modalities: tuple = ("text",)
    tags: tuple = ()                       # descriptive only; NOT quality claims
    evaluated: bool = False                # no evaluation system exists yet
    health_status: str = ModelHealth.UNKNOWN


class ModelRegistry:
    def __init__(self, session=None):
        self.db = session

    def _provider(self):
        from backend.runtime.model_provider import get_default_provider

        return get_default_provider()

    def list_models(self, probe: bool = False) -> List[ModelRecord]:
        """Registered models. `probe=True` contacts the provider (explicit)."""
        provider = self._provider()
        selected = provider.selected_model_id()
        records: List[ModelRecord] = []
        try:
            raw_models = provider.list_models()
            available_now = True
        except Exception as exc:
            # Provider outage: represent the configured model as unavailable,
            # never corrupt agent/goal state over it.
            records.append(ModelRecord(
                model_id=selected or "unknown",
                provider_id=provider.provider_id(),
                selected=bool(selected),
                available_now=False,
                health_status=ModelHealth.UNAVAILABLE,
            ))
            return records

        for m in raw_models:
            model_id = m.get("name") or m.get("model") or ""
            if not model_id:
                continue
            records.append(ModelRecord(
                model_id=model_id,
                provider_id=provider.provider_id(),
                selected=(model_id == selected),
                available_now=available_now,
                tool_calling_supported=None,  # unknown until capability probe
                health_status=ModelHealth.UNKNOWN if not probe else (
                    ModelHealth.HEALTHY if model_id == selected else ModelHealth.UNKNOWN
                ),
            ))
        return records

    def get_model(self, model_id: str) -> Optional[ModelRecord]:
        for record in self.list_models():
            if record.model_id == model_id:
                return record
        return None

    def model_capabilities(self, model_id: str) -> Dict[str, Any]:
        """Structured capability metadata with explicit truthfulness."""
        return {
            "model_id": model_id,
            "context_tokens": None,          # unknown: not yet measured
            "tool_calling": {"supported": None, "available_now": None,
                             "evaluated": False},
            "reasoning": {"supported": None, "evaluated": False},
            "coding": {"supported": None, "evaluated": False},
            "vision": {"supported": False, "evaluated": False},  # text-only stack today
            "quality_claims": "none",        # never inferred from names
            "evaluated_claims": "none",      # explicit truthfulness marker
        }

    def selected_model(self) -> Optional[ModelRecord]:
        selected = self._provider().selected_model_id()
        return self.get_model(selected) if selected else None


# --------------------------------------------------------------------------- #
# Resource state                                                               #
# --------------------------------------------------------------------------- #

def resource_snapshot(db_session=None) -> Dict[str, Any]:
    """Known facts only; unknown/None where measurement isn't wired."""
    from backend.core.config import settings

    data_dir = None
    disk_free_bytes = None
    try:
        from backend.core.storage import data_dir as _data_dir

        data_dir = _data_dir()
        import shutil

        disk_free_bytes = shutil.disk_usage(data_dir).free
    except Exception:
        disk_free_bytes = None

    lease_held = None
    active_goals = None
    if db_session is not None:
        try:
            from backend.models.database import RunnerLease, Objective

            lease_held = (
                db_session.query(RunnerLease).filter_by(id=1).first() is not None
            )
            active_goals = (
                db_session.query(Objective)
                .filter(Objective.status == "active")
                .count()
            )
        except Exception:
            pass

    return {
        "platform": platform.system(),
        "python_version": platform.python_version(),
        "data_dir_set": bool(data_dir),
        "disk_free_bytes": disk_free_bytes,       # None = unknown
        "network_connectivity": None,             # unknown (no probe here)
        "cpu_percent": None,                      # not collected in this phase
        "memory_available_bytes": None,           # not collected in this phase
        "budgets": {
            "max_actions_per_task": settings.max_actions_per_task,
            "max_cloud_requests_per_task": settings.max_cloud_requests_per_task,
            "max_elapsed_time_seconds": settings.max_elapsed_time_seconds,
            "step_max_attempts": settings.step_max_attempts,
            "max_steps_per_background_cycle": settings.max_steps_per_background_cycle,
        },
        "runtime": {
            "lease_held": lease_held,
            "active_goals": active_goals,
        },
    }


# --------------------------------------------------------------------------- #
# Health-check framework                                                       #
# --------------------------------------------------------------------------- #

HEALTHY = "HEALTHY"
DEGRADED = "DEGRADED"
UNAVAILABLE = "UNAVAILABLE"
UNKNOWN = "UNKNOWN"


@dataclass
class CheckResult:
    name: str
    status: str
    evidence: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "status": self.status,
                "evidence": self.evidence}


def run_health_checks(include_provider: bool = False, db_session=None) -> Dict[str, Any]:
    """Run lightweight checks; expensive/remote probes are opt-in."""
    checks: List[CheckResult] = []

    # 1. Database availability.
    if db_session is not None:
        try:
            from sqlalchemy import text

            db_session.execute(text("SELECT 1"))
            checks.append(CheckResult("database", HEALTHY))
        except Exception as exc:
            checks.append(CheckResult("database", UNAVAILABLE, {"error": str(exc)[:200]}))
    else:
        checks.append(CheckResult("database", UNKNOWN, {"detail": "no session"}))

    # 2. Main Agent persistence.
    try:
        from backend.services.main_agent import MainAgentService

        agent = MainAgentService(db_session).get_or_create()
        checks.append(CheckResult("main_agent", HEALTHY,
                                  {"agent_id": agent.agent_id,
                                   "state": agent.state}))
    except Exception as exc:
        checks.append(CheckResult("main_agent", UNAVAILABLE,
                                  {"error": str(exc)[:200]}))

    # 3. Capability registry consistency.
    try:
        from backend.services.capabilities import CapabilityRegistry

        issues = CapabilityRegistry(db_session).validate()
        status = HEALTHY if not issues else DEGRADED
        checks.append(CheckResult("capability_consistency", status,
                                  {"issues": issues[:20]}))
    except Exception as exc:
        checks.append(CheckResult("capability_consistency", UNKNOWN,
                                  {"error": str(exc)[:200]}))

    # 4. Configuration validity.
    try:
        from backend.core.config import validate_startup_config

        problems = [msg for level, msg in validate_startup_config()
                    if level == "error"]
        checks.append(CheckResult(
            "configuration",
            HEALTHY if not problems else DEGRADED,
            {"errors": problems[:10]},
        ))
    except Exception as exc:
        checks.append(CheckResult("configuration", UNKNOWN,
                                  {"error": str(exc)[:200]}))

    # 5. Model/provider health (opt-in remote probe).
    if include_provider:
        try:
            import backend.runtime.model_provider as _mp
            health = _mp.get_default_provider().health()
            checks.append(CheckResult(f"provider.{health.provider}",
                                      health.status, {"detail": health.detail}))
        except Exception as exc:
            checks.append(CheckResult("provider", UNKNOWN,
                                      {"error": str(exc)[:200]}))
    else:
        checks.append(CheckResult("provider", UNKNOWN,
                                  {"detail": "remote probe disabled"}))

    # 6. Startup sequence aggregate (Phase 4.5). Partial startup must be
    # reported, never treated as fully healthy by consumers.
    try:
        import backend.main as _bm
        _boot = getattr(_bm, "startup_boot", None)
    except Exception:
        _boot = None
    if isinstance(_boot, dict):
        from backend.services.bootstrap import startup_status_projection

        _projection = startup_status_projection(_boot)
        _raw = _projection["startup_sequence_status"]
        _evidence = {"startup_sequence_status": _raw,
                     "stage_statuses": _projection["startup_stage_statuses"]}
    else:
        _raw = None
        _evidence = {"startup_sequence_status": None}
    if _raw == "completed":
        _st_status = HEALTHY
    elif _raw in ("failed", "degraded", "error"):
        _st_status = DEGRADED
    else:
        _st_status = UNKNOWN
    checks.append(CheckResult("startup_sequence", _st_status, _evidence))

    statuses = {c.status for c in checks}
    if UNAVAILABLE in statuses:
        overall = UNAVAILABLE
    elif DEGRADED in statuses or UNKNOWN in statuses:
        overall = DEGRADED
    else:
        overall = HEALTHY
    return {
        "overall": overall,
        "checked_at": datetime.utcnow().isoformat(),
        "checks": [c.to_dict() for c in checks],
    }