"""
Core configuration and constants for JARVIS backend.
"""

from pydantic_settings import BaseSettings
from typing import List
import secrets
import os
from .secrets import resolve_ollama_api_key


class Settings(BaseSettings):
    """Application settings."""
    
    # Server configuration
    host: str = "127.0.0.1"
    port: int = 8000
    
    # Security
    control_token: str = ""  # Set per-launch by Tauri
    development_mode: bool = False  # Enables the Vite dashboard only; never for packaged releases
    
    # Database
    database_path: str = ""  # Set to user's local app data on Windows
    
    # Ollama Cloud
    ollama_cloud_base_url: str = "https://ollama.com"
    ollama_api_key: str = ""  # From Windows Credential Manager
    selected_model: str = ""  # Selected from model list
    
    # File roots (approved directories for file operations)
    file_roots: List[str] = []
    
    # Task limits
    max_actions_per_task: int = 100
    max_cloud_requests_per_task: int = 50
    max_input_tokens: int = 50000
    max_output_tokens: int = 4000
    max_elapsed_time_seconds: int = 3600
    max_repair_attempts: int = 2
    
    # Voice
    voice_enabled: bool = True
    stt_model_path: str = ""  # Path to pinned STT model
    tts_voice_name: str = ""  # Name of pinned TTS voice
    
    class Config:
        env_prefix = "JARVIS_"
        env_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")


settings = Settings()


def generate_control_token() -> str:
    """Use the trusted launcher token or generate a cryptographically random one."""
    return settings.control_token or secrets.token_urlsafe(32)


def resolve_selected_model() -> str:
    """Return the active selected Cloud model ID.

    Precedence:
      1. Environment override (JARVIS_SELECTED_MODEL, dev convenience).
      2. Persisted non-secret profile metadata (set during onboarding).

    This is the single resolution point for the selected model; callers must
    not read Settings.selected_model directly.
    """
    if settings.selected_model:
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
            "set JARVIS_OLLAMA_API_KEY for development. Tasks will fail until "
            "this is set."
        ))
    if not resolve_selected_model():
        issues.append((
            "error",
            "No model selected. Select one from the authenticated Cloud model "
            "list via POST /api/v1/onboarding/models (or set "
            "JARVIS_SELECTED_MODEL for development). Tasks will fail until "
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
