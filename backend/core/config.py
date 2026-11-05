"""
Core configuration and constants for Sythion (Synth) backend.
"""

from pydantic_settings import BaseSettings
from typing import List
import secrets
import os
from .secrets import resolve_ollama_api_key
from .secrets import resolve_secret_context


# Prototype/ambiguous detection happens here, independently of Settings, so the
# backend/.env file is never opened (and never read) in those modes.
try:
    _secret_namespace, _is_prototype_context, _fail_closed_context, _ = resolve_secret_context()
    _PRODUCTION_ENV_FILE = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        ".env",
    )
    _USE_PRODUCTION_ENV_FILE = (
        _secret_namespace == "Sythion (Synth)"
        and not _is_prototype_context
        and not _fail_closed_context
    )
except Exception:
    _PRODUCTION_ENV_FILE = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        ".env",
    )
    _USE_PRODUCTION_ENV_FILE = False

_SETTINGS_ENV_FILE = _PRODUCTION_ENV_FILE if _USE_PRODUCTION_ENV_FILE else None


class Settings(BaseSettings):
    """Application settings."""
    
    # Server configuration
    host: str = "127.0.0.1"
    port: int = 8000
    
    # Security & Autonomy
    control_token: str = ""  # Set per-launch by Tauri
    development_mode: bool = False  # Enables the Vite dashboard only; never for packaged releases
    autonomy_level: str = "guarded"  # "guarded" (default/contract) or "autonomous" (accessibility/unattended mode)
    
    # Database
    database_path: str = ""  # Set to user's local app data on Windows
    
    # Ollama Cloud
    ollama_cloud_base_url: str = "https://ollama.com"
    ollama_api_key: str = ""  # From Windows Credential Manager
    selected_model: str = ""  # Selected from model list
    
    # File roots (approved directories for file operations)
    file_roots: List[str] = []
    
    # Task limits — gemma4:31b supports 256K tokens; raised accordingly
    max_actions_per_task: int = 100
    max_cloud_requests_per_task: int = 50
    max_input_tokens: int = 200000   # soft warning threshold (not enforced hard)
    max_output_tokens: int = 8192    # raised from 4000; model handles long outputs fine
    max_elapsed_time_seconds: int = 3600
    max_repair_attempts: int = 2

    # Tool summary char caps — gemma4:31b has 256K context; raised from tiny defaults
    tool_summary_cap_default: int = 1500       # was 220 — now shows full tool output
    tool_summary_cap_browser_tabs: int = 8000  # was 2000 — all tabs always visible
    tool_summary_cap_browser_read_page: int = 12000  # was 3000 — full page content
    tool_summary_cap_browser_screenshot: int = 1000  # was 400 — full metadata

    # Max seconds to wait for a single model decision before failing the task
    # instead of hanging silently on a slow/unresponsive cloud call.
    decision_timeout_seconds: float = 120.0

    
    # Continuation detection window (hours)
    continuation_window_hours: int = 12
    
    # Runner lease contract (single source of truth):
    # - lease_timeout_seconds: lease expiry AND stale-takeover threshold.
    #   A lease whose expires_at has passed may be taken over; the previous
    #   holder's task is marked interrupted on takeover.
    # - lease_heartbeat_seconds: how often the active execution loop renews
    #   the lease. MUST be strictly smaller than lease_timeout_seconds
    #   (enforced at import in services/task_runner.py).
    lease_timeout_seconds: int = 30
    lease_heartbeat_seconds: int = 10
    
    # Voice
    voice_enabled: bool = True
    stt_model_path: str = ""  # Path to pinned STT model
    tts_voice_name: str = ""  # Name of pinned TTS voice

    # Memory consolidation (background LLM distillation of conversations)
    memory_consolidation_enabled: bool = True

    # Phase 3 work orchestration:
    # - step_max_attempts: persisted, bounded per-step retry limit. A retry is
    #   an audited event; exhausting the limit fails the step and pauses the
    #   goal (replanning remains owner-triggered).
    # - max_steps_per_goal_execution: hard bound on automatic continuation
    #   within ONE explicitly initiated goal execution session.
    # - background_orchestration_enabled: operator switch for the Phase 4
    #   event-driven Main Agent wake worker. DEFAULT OFF; persisted override
    #   via settings_store ('background_orchestration_enabled').
    step_max_attempts: int = 3
    max_steps_per_goal_execution: int = 10
    max_steps_per_background_cycle: int = 5
    background_orchestration_enabled: bool = False

    # Phase 4.5 hardening:
    # - wake_reclaim_seconds: a wake stuck in RUNNING (worker crash) older
    #   than this may be reclaimed by another worker pass.
    # - backoff: after `backoff_failure_threshold` consecutive failed sessions
    #   a goal is deprioritized for base * 2^(n-threshold) seconds, capped.
    wake_reclaim_seconds: int = 600
    backoff_failure_threshold: int = 2
    backoff_base_seconds: int = 300
    backoff_max_seconds: int = 3600

    # Phase 6 model selection/fallback:
    # - model_max_attempts: total decision attempts (primary + fallbacks)
    #   per engine instance; each attempt consumes normal cloud budget.
    # - model_eval_version pins the deterministic evaluation suite version.
    model_max_attempts: int = 2

    # Skill self-growth (L2): auto-enable a proposal once its verified trace
    # count reaches this threshold (default 5; 0 or negative restores explicit
    # owner review of every proposal). Auto-enabling re-validates against the
    # current read-only tool set and creates a versioned, pinned skill.
    skill_auto_enable_threshold: int = 5

    # Phase 9 bounded specialist delegation policy. All caps come from
    # configuration — no specialist recursion (max_specialist_depth = 1),
    # no unbounded fan-out, no unbounded specialist runtime.
    specialists_enabled: bool = True
    max_specialists_per_task: int = 2
    max_specialist_depth: int = 1
    max_specialist_steps: int = 5
    max_specialist_time_seconds: int = 300
    max_specialist_model_calls: int = 10
    max_specialist_revisions: int = 1
    specialist_ttl_seconds: int = 3600

    # Phase 10 multi-specialist coordination & controlled parallelism.
    # All fan-out / budget / concurrency caps are configuration, never
    # prompt-invented. Parallelism defaults OFF.
    max_specialists_per_plan: int = 5
    max_active_specialists_per_parent: int = 5
    max_total_specialist_model_calls: int = 50
    max_total_specialist_steps: int = 25
    max_total_specialist_time_seconds: int = 1800

    # Controlled parallelism — Stage 0 default is serialized.
    parallelism_enabled: bool = False
    max_parallel_specialists: int = 2
    max_parallel_model_calls: int = 4
    max_parallel_runtime_actions: int = 2

    # GUI concurrency: always serialized unless explicitly validated.
    gui_parallelism_enabled: bool = False

    # Phase 11 — controlled learning layer
    strategy_learning_enabled: bool = True
    strategy_evidence_ttl_days: int = 90
    strategy_recent_window_days: int = 30
    strategy_min_samples_supported: int = 5
    strategy_min_samples_established: int = 20
    strategy_low_sample_threshold: int = 3
    strategy_exploration_rate: float = 0.10
    strategy_max_exploration_cost: float = 2.0
    strategy_min_confidence_to_exploit: float = 0.65
    strategy_high_risk_threshold: str = "HIGH"
    strategy_decay_half_life_days: int = 45

    # Phase 12 — planning/replanning adaptive policy learning
    planning_learning_enabled: bool = True
    planning_learning_mode: str = "LEARNING_ONLY"  # LEARNING_ONLY | SHADOW | ACTIVE
    planning_evidence_ttl_days: int = 90
    planning_recent_window_days: int = 30
    planning_min_samples_supported: int = 5
    planning_min_samples_established: int = 20
    planning_low_sample_threshold: int = 3
    planning_exploration_rate: float = 0.05
    planning_max_exploration_cost: float = 2.0
    planning_min_confidence_to_exploit: float = 0.65
    planning_high_risk_threshold: str = "HIGH"
    planning_shadow_enabled: bool = True
    planning_promotion_requires_owner_approval: bool = True
    planning_safe_auto_promotion: bool = False  # only LOW-cost policies may auto-promote when True
    # Phase 12.5 — promotion requires real evidence (synthetic alone cannot promote)
    planning_min_real_samples: int = 5
    planning_min_real_verified_successes: int = 3

    model_config = {
        "env_prefix": "SYTHION_",
        "env_file": _SETTINGS_ENV_FILE,
    }


settings = Settings()

# Prototype / ambiguous identity must never inherit a production credential or
# selected model from SYTHION_OLLAMA_API_KEY, SYTHION_SELECTED_MODEL or
# backend/.env.
try:
    _secret_namespace, _is_prototype, _fail_closed, _ = resolve_secret_context()
    if _is_prototype or _fail_closed or _secret_namespace != "Sythion (Synth)":
        settings.ollama_api_key = ""
        settings.selected_model = ""
except Exception:
    settings.ollama_api_key = ""
    settings.selected_model = ""


def generate_control_token() -> str:
    """Use the trusted launcher token or generate a cryptographically random one."""
    return settings.control_token or secrets.token_urlsafe(32)


def resolve_selected_model() -> str:
    """Return the active selected Cloud model ID.

    Precedence:
      1. Environment override (SYTHION_SELECTED_MODEL, dev convenience),
         only in an unambiguous production identity context.
      2. Persisted non-secret profile metadata (set during onboarding).

    Prototype / ambiguous / fail-closed identities must never inherit a
    production model id from the environment or ``backend/.env``; they only
    ever read the isolated profile metadata under their own data directory.
    This is the single resolution point for the selected model; callers must
    not read Settings.selected_model directly.
    """
    namespace, is_prototype, fail_closed, _ = resolve_secret_context()
    allow_env_override = (
        namespace == "Sythion (Synth)"
        and not is_prototype
        and not fail_closed
    )
    if allow_env_override and settings.selected_model:
        return settings.selected_model
    from .profile import get_selected_model
    return get_selected_model()


def validate_startup_config():
    """Validate critical configuration at startup.

    Returns a list of (level, message) tuples. Errors indicate the app
    cannot function as intended; warnings indicate degraded function.
    """
    issues = []

    if not resolve_ollama_api_key(settings.ollama_api_key):
        issues.append((
            "error",
            "No Ollama API key configured. Store one via "
            "PUT /api/v1/secrets/ollama_api_key (write-only secret store) or "
            "set SYTHION_OLLAMA_API_KEY for development. Tasks will fail until "
            "this is set."
        ))
    if not resolve_selected_model():
        issues.append((
            "error",
            "No model selected. Select one from the authenticated Cloud model "
            "list via POST /api/v1/onboarding/models (or set "
            "SYTHION_SELECTED_MODEL for development). Tasks will fail until "
            "this is set."
        ))

    if settings.max_actions_per_task < 1:
        issues.append((
            "error",
            f"max_actions_per_task must be >= 1 (got {settings.max_actions_per_task})."
        ))
    if settings.max_elapsed_time_seconds < 1:
        issues.append((
            "error",
            f"max_elapsed_time_seconds must be >= 1 (got {settings.max_elapsed_time_seconds})."
        ))

    return issues
