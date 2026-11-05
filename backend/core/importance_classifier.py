"""
Deterministic importance classifier for Sythion (Synth).

Classifies every planned tool action as ROUTINE (auto-execute),
CONSEQUENTIAL (requires owner approval), or FORBIDDEN (hard-blocked).

This is the single policy gate that decides whether an action needs
approval.  It replaces the ad-hoc ``requires_confirmation`` check with
a deterministic, testable contract that considers the tool's declared
policy *and* the action's context (parameters, task history, autonomy
mode).

Design rule from the owner directive: "The deciding gate must be a
policy layer (importance classifier), not the model's in-context
judgment, because the model cannot be trusted to self-judge importance
consistently."

Lives in ``core/`` (bottom layer) so both ``services/`` and ``tools/``
can import it without creating cycles.  Must never import ``api`` or
``tools``.
"""

from __future__ import annotations

import logging
import re
from enum import Enum
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class Importance(Enum):
    """Classification of a planned tool action."""

    ROUTINE = "routine"
    CONSEQUENTIAL = "consequential"
    FORBIDDEN = "forbidden"


# ---------------------------------------------------------------------------
# Hard-block rules (FORBIDDEN)
# ---------------------------------------------------------------------------

# Tools that are never allowed under any circumstances.
_FORBIDDEN_TOOLS: frozenset[str] = frozenset({
    "package_install",
    "package_uninstall",
})

# Parameter patterns that elevate any tool to CONSEQUENTIAL regardless of
# its self-declared risk level.  A write to a system directory, a delete
# of a user file, or a URL fetch to an internal host all qualify.
_SYSTEM_PATH_RE = re.compile(
    r"(^|[\\/])(windows|program files|programdata|syswow64)"
    r"(?=[\\/]|$)",
    re.IGNORECASE,
)

_LOOPBACK_RE = re.compile(
    r"^https?://(localhost|127\.0\.0\.1|0\.0\.0\.0)(:|/|$)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Core classifier
# ---------------------------------------------------------------------------


def classify_action(
    tool_name: str,
    tool_policy: Dict[str, Any],
    tool_params: Optional[Dict[str, Any]] = None,
    *,
    autonomy_level: str = "guarded",
    direct_user_instruction: bool = False,
) -> Importance:
    """Classify a planned tool action.

    Args:
        tool_name: The tool identifier (e.g. ``file_delete``).
        tool_policy: The dict returned by the tool's
            ``get_policy_requirements()``.
        tool_params: The parameters the model wants to pass to the tool.
            Used for input-aware classification (e.g. target path, URL).
        autonomy_level: Current autonomy level (``"guarded"`` or
            ``"autonomous"``).  In autonomous mode routine actions
            execute without approval; consequential actions still require
            approval unless the user explicitly approved them.
        direct_user_instruction: Whether the action originates from a
            direct user instruction (``task.original_request`` is
            non-empty).  When ``True`` and the tool requires
            confirmation, the action is still CONSEQUENTIAL (the user
            must explicitly approve via the reply endpoint), but the
            ``DIRECT_INSTRUCTION_REQUIRED`` error is not raised.

    Returns:
        An :class:`Importance` value.
    """
    # --- FORBIDDEN (hard block, never overridden) ---

    if tool_name in _FORBIDDEN_TOOLS:
        logger.info("Action %s forbidden: tool is in the blocklist", tool_name)
        return Importance.FORBIDDEN

    if tool_policy.get("requires_admin"):
        logger.info("Action %s forbidden: requires admin elevation", tool_name)
        return Importance.FORBIDDEN

    # --- Input-aware escalation ---

    params = tool_params or {}
    escalated = _input_escalates(tool_name, params, tool_policy)
    if escalated:
        logger.info(
            "Action %s escalated to CONSEQUENTIAL by input-aware rule",
            tool_name,
        )
        return Importance.CONSEQUENTIAL

    # --- Policy-based classification ---

    risk = tool_policy.get("risk_level", "none")
    destructive = bool(tool_policy.get("destructive"))
    requires_confirmation = bool(tool_policy.get("requires_confirmation"))

    # Canonical identity, metadata, app_internal status, configuration, or a
    # caller's direct_user_instruction are never authenticated consent.  A
    # mutating, app-internal, privacy-sensitive, screen/UI, or unpermissioned
    # effect therefore escalates to CONSEQUENTIAL even when its risk level or
    # confirmation flag looks benign.
    if (
        tool_policy.get("app_internal") is True
        or destructive
        or tool_policy.get("read_only") is False
        or tool_policy.get("privacy_sensitive") is True
        or tool_policy.get("requires_screen_access") is True
        or tool_policy.get("requires_ui_automation") is True
        or tool_policy.get("requires_admin") is True
    ):
        return Importance.CONSEQUENTIAL

    if destructive or risk == "high":
        return Importance.CONSEQUENTIAL

    if requires_confirmation or risk == "medium":
        return Importance.CONSEQUENTIAL

    # Low-risk, non-mutating, no confirmation required → routine.
    return Importance.ROUTINE


def _input_escalates(
    tool_name: str,
    params: Dict[str, Any],
    policy: Dict[str, Any],
) -> bool:
    """Return True if the action's *input* elevates importance beyond
    what the tool's self-declared policy implies.

    This catches cases where a tool is declared ``risk_level: low`` but
    the specific invocation targets a system path, loopback URL, or
    other sensitive resource.
    """
    # Target path in file/app tools
    path = params.get("path") or params.get("target") or ""
    if path and _SYSTEM_PATH_RE.search(str(path)):
        return True

    # URL in web tools
    url = params.get("url") or ""
    if url and _LOOPBACK_RE.search(str(url)):
        return True

    return False


def needs_approval(
    tool_name: str,
    tool_policy: Dict[str, Any],
    tool_params: Optional[Dict[str, Any]] = None,
    *,
    autonomy_level: str = "guarded",
    direct_user_instruction: bool = False,
    approved_by_user: bool = False,
) -> bool:
    """Convenience predicate: should this action be staged for approval?

    Returns ``True`` when :func:`classify_action` returns
    :attr:`Importance.CONSEQUENTIAL` **and** we are not in autonomous
    mode **and** the user hasn't already approved the tool.  Returns
    ``False`` for both ROUTINE and FORBIDDEN (the caller handles
    FORBIDDEN separately as a hard block).
    """
    classification = classify_action(
        tool_name,
        tool_policy,
        tool_params,
        autonomy_level=autonomy_level,
        direct_user_instruction=direct_user_instruction,
    )

    if classification == Importance.FORBIDDEN:
        return False  # caller must raise, not stage

    if classification == Importance.ROUTINE:
        return False

    # An internally stored approval cancels staging, but autonomous mode does
    # not. Execution remains separately authorization-gated.
    if approved_by_user:
        return False
    return True
