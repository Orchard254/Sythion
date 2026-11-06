"""Pure text and discovery classification helpers for assistant responses.

These functions never schedule commands, grant permissions, or touch task state.
The conversation engine exposes its original static-method names as thin
compatibility wrappers while sharing one implementation here.
"""

import re
from typing import Any


PLAIN_PROSE_NO_ACTION_DISCLOSURE = (
    "\n\nNo background task, clarification, or other action was started or "
    "scheduled. This response is informative only."
)


def looks_like_machine_readable_payload(text: str) -> bool:
    stripped = text.lstrip()
    if stripped.startswith("```"):
        return True
    return stripped.startswith("{") or stripped.startswith("[")


def strip_legacy_no_action_disclosure(text: Any) -> Any:
    if not isinstance(text, str):
        return text
    legacy_constant = PLAIN_PROSE_NO_ACTION_DISCLOSURE
    legacy_core = (
        "No background task, clarification, or other action was started or "
        "scheduled. This response is informative only."
    )
    result = text
    # Exact historical marker, including its leading blank lines.
    result = result.replace(legacy_constant, "")
    # Handle common whitespace/newline presentation drift around the same
    # exact legacy marker without classifying natural language.
    result = re.sub(
        r"(?:\r?\n)+\s*" + re.escape(legacy_core),
        "",
        result,
    )
    # Finally remove any bare marker occurrences that have already lost
    # their leading newlines in persisted or model-echoed history.
    result = result.replace(legacy_core, "")
    return result


def is_ordinary_prose(raw: Any) -> bool:
    text = raw.strip() if isinstance(raw, str) else ""
    if not text:
        return False
    if looks_like_machine_readable_payload(text):
        return False
    # Ordinary conversational prose must not carry JSON-like control fragments,
    # including embedded objects or arrays.
    if "{" in text or "[" in text:
        return False
    return True


def unwrap_machine_readable_payload(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return stripped


def is_empty_indexed_session_discovery(discovery_input: Any) -> bool:
    if discovery_input is None:
        return False
    if getattr(discovery_input, "discovery_complete", False) is not True:
        return False
    for field_name in (
        "available_count",
        "returned_count",
        "omitted_count",
        "unresolved_discussion_count",
        "unresolved_task_proof_count",
    ):
        if getattr(discovery_input, field_name, None) != 0:
            return False
    candidate_ids = getattr(discovery_input, "candidate_discussion_ids", ())
    if isinstance(candidate_ids, (str, bytes, bytearray)):
        return False
    try:
        if any(True for _ in candidate_ids):
            return False
    except TypeError:
        return False
    return True

