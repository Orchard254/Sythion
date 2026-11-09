"""Read-only review guidance for a blocked task resume.

The packet is *not* a review decision, approval, clearance, or proof of an
external action's outcome. It is a sanitized, deterministic explanation of
what an accountable human/integration would need to verify. Never put raw
model output, task instructions, action IDs, credentials, or owner nonces here.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# These are intentionally fixed templates: untrusted model/tool text must not
# become instructions to the reviewer or a way to forge authorization.
_CHECKS: dict[str, tuple[str, tuple[str, ...], tuple[str, ...]]] = {
    "external_effect_review_required": (
        "high",
        ("task_state", "action_evidence", "ownership_state"),
        (
            "Inspect the target system independently to establish whether the external effect occurred.",
            "Compare durable action-start/uncertainty evidence with independently observed results.",
            "Do not repeat the action until a separate authenticated reconciliation process establishes safety.",
        ),
    ),
    "prior_mutating_effect_review_required": (
        "high",
        ("task_state", "historical_tool_events"),
        (
            "Determine which prior mutating effects have independently verified outcomes.",
            "Establish a continuation plan that does not repeat non-idempotent work.",
            "Require the dedicated authenticated reconciliation path before clearing the blocker.",
        ),
    ),
    "pending_action_review_required": (
        "high",
        ("task_state", "pending_action_state"),
        (
            "Identify the pending request using the trusted application review interface.",
            "Obtain independently authenticated consent if a consequential action is proposed.",
            "Do not treat a pending-action status string or this packet as an approval.",
        ),
    ),
    "corrupt_reasoning_receipt_review_required": (
        "high",
        ("task_state", "reasoning_event_ledger"),
        (
            "Investigate the integrity of the committed reasoning event sequence.",
            "Do not infer a provider outcome or repair the ledger by inventing missing receipts.",
        ),
    ),
    "unresolved_reasoning_review_required": (
        "medium",
        ("task_state", "reasoning_event_ledger"),
        (
            "Determine whether the previous reasoning invocation ended or remains in flight.",
            "Re-observe task and tool state before planning any continuation.",
            "An incomplete reasoning receipt does not prove an external action was not performed.",
        ),
    ),
    "ownership_recovery_required": (
        "high",
        ("task_state", "task_ownership_lease"),
        (
            "Establish whether a worker is still active using an authoritative runtime check.",
            "Do not force-reclaim solely because a lease expired or a process ID appears absent.",
            "Use an independently verified, ownership-fenced recovery process.",
        ),
    ),
    "task_not_resumable": (
        "medium",
        ("task_state",),
        (
            "Inspect the task lifecycle; only paused or interrupted tasks are candidates for manual resume.",
            "Do not overwrite a terminal state to manufacture eligibility.",
        ),
    ),
    "clear_for_explicit_resume": (
        "none",
        ("task_state", "task_ownership_lease", "reasoning_event_ledger"),
        (
            "A user may request explicit resume, subject to fresh transaction-bound checks.",
            "The execution lease, ownership, policy, and action guards remain authoritative.",
        ),
    ),
}

_VALID_STATES = frozenset({
    "created", "running", "paused", "interrupted", "waiting_for_user",
    "completed", "failed", "cancelled",
})
_VALID_LEASES = frozenset({
    "missing", "current", "expired", "released", "recovered", "indeterminate",
})


def build_recovery_review_packet(admission: Mapping[str, Any]) -> dict[str, Any]:
    """Convert a trusted preflight observation into non-authorizing guidance.

    Unknown, contradictory, or malformed observations fail closed. The packet
    intentionally echoes no task ID, parameters, owner nonces or raw evidence.
    Its evidence classes identify *where* independent proof is needed, not a
    claim that the referenced source has been verified.
    """
    if not isinstance(admission, Mapping):
        raise TypeError("admission must be a mapping")

    reason = admission.get("reason")
    clear = reason == "clear_for_explicit_resume"
    status_clear = admission.get("preflight_clear")
    count = admission.get("unresolved_reasoning_calls")
    state = admission.get("task_state")
    lease = admission.get("owner_lease")

    coherent = (
        isinstance(reason, str) and reason in _CHECKS
        and type(status_clear) is bool
        and status_clear is clear
        and type(count) is int and count >= 0
        and isinstance(state, str) and state in _VALID_STATES
        and isinstance(lease, str) and lease in _VALID_LEASES
        and (not clear or (
            state in ("paused", "interrupted")
            and count == 0 and lease in ("missing", "released")
        ))
        and (reason != "unresolved_reasoning_review_required" or count > 0)
        and (reason != "task_not_resumable" or state not in ("paused", "interrupted"))
        and (reason != "ownership_recovery_required" or lease in (
            "current", "expired", "recovered", "indeterminate"
        ))
    )
    if not coherent:
        severity, sources, checks = (
            "high",
            ("preflight_integrity",),
            (
                "Obtain a fresh preflight from the trusted task runtime.",
                "Do not resume or clear uncertainty from inconsistent evidence.",
            ),
        )
        reason = "inconsistent_preflight_review_required"
        clear = False
        state = state if isinstance(state, str) and state in _VALID_STATES else "unknown"
        lease = lease if isinstance(lease, str) and lease in _VALID_LEASES else "indeterminate"
        count = count if type(count) is int and count >= 0 else None
    else:
        severity, sources, checks = _CHECKS[reason]

    return {
        "status": "preflight_clear_not_authorized" if clear else "review_required",
        "blocker": reason,
        "severity": severity,
        "observations": {
            "task_state": state,
            "owner_lease": lease,
            "unresolved_reasoning_calls": count,
        },
        "evidence_source_classes": list(sources),
        "verification_steps": list(checks),
        "requires_authenticated_reconciliation": not clear,
        "limited_to_current_snapshot": True,
        "approved": False,
        "authorization_issued": False,
        "uncertainty_cleared": False,
        "execution_started": False,
        "automatic_replay_allowed": False,
        "note": (
            "This read-only packet is guidance, not evidence that checks were "
            "performed or permission to change task state or replay effects."
        ),
    }
