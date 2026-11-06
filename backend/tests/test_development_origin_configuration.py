"""Regression tests for the shared development-origin security policy.

Only opt-in development mode may grant browser-dev origins. The explicit
environment switch has priority over settings, including when disabling it.
"""

import pytest

from backend.core.auth import DEVELOPMENT_APP_ORIGINS, PACKAGED_APP_ORIGINS, control_origins
from backend.core.config import effective_development_mode


@pytest.mark.parametrize(
    ("env_value", "configured", "expected"),
    [
        (None, False, False),
        (None, True, True),
        ("", True, True),
        ("   ", True, True),
        ("true", False, True),
        (" TRUE ", False, True),
        ("1", False, True),
        ("yes", False, True),
        ("on", False, True),
        ("false", True, False),
        (" 0 ", True, False),
        ("off", True, False),
        ("unexpected", True, False),
    ],
)
def test_effective_development_mode_env_precedence(
    monkeypatch, env_value, configured, expected
):
    if env_value is None:
        monkeypatch.delenv("SYTHION_DEVELOPMENT_MODE", raising=False)
    else:
        monkeypatch.setenv("SYTHION_DEVELOPMENT_MODE", env_value)

    assert effective_development_mode(configured) is expected


@pytest.mark.parametrize(
    ("env_value", "configured", "include_dev"),
    [
        (None, False, False),
        (None, True, True),
        ("true", False, True),
        ("false", True, False),
        ("invalid", True, False),
    ],
)
def test_origin_allowlist_uses_resolved_mode(
    monkeypatch, env_value, configured, include_dev
):
    if env_value is None:
        monkeypatch.delenv("SYTHION_DEVELOPMENT_MODE", raising=False)
    else:
        monkeypatch.setenv("SYTHION_DEVELOPMENT_MODE", env_value)

    origins = control_origins(effective_development_mode(configured))
    assert PACKAGED_APP_ORIGINS <= origins
    if include_dev:
        assert origins == PACKAGED_APP_ORIGINS | DEVELOPMENT_APP_ORIGINS
    else:
        assert origins == PACKAGED_APP_ORIGINS
