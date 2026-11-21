"""Small offline harness for the labelled context-provider evaluation fixture.

This is a PLUMBING/ORCHESTRATION harness, not a semantic model benchmark.  It
runs the real ``ConversationTurnEngine`` with a *scripted* model provider and a
fixture-derived evidence reader.  Grader-only rubric data from the approved
fixture is kept in separate record fields and is never placed in model-facing
messages.  Scripted model outputs are explicitly orchestration fixtures, not
assessments of model quality.

No network, live provider, database, migration, packaging, or runtime code is
used.  No expected resolver answer is used as model input.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


FIXTURE_PATH = Path(__file__).resolve().parent / "evaluations" / "labelled_memory_cases.json"

MODEL_FACING_DISCOVERY_FIELDS = {
    "session_id",
    "available_count",
    "returned_count",
    "omitted_count",
    "unresolved_discussion_count",
    "unresolved_task_proof_count",
    "candidate_discussion_ids",
    "discovery_complete",
    "reason",
    "representatives",
}

MODEL_FACING_REPRESENTATIVE_FIELDS = {
    "discussion_id",
    "label",
    "member_count",
    "last_activity_at",
    "preview",
}

MODEL_FACING_ANSWER_EXCHANGE_FIELDS = {
    "exchange_id",
    "user_text",
    "assistant_text",
}

MODEL_INVISIBLE_CASE_KEYS = {
    "primary_dimension",
    "tags",
    "expected_outcome",
    "expected_selected_discussion_ids",
    "expected_resolver_output",
    "expected_source_exchange_ids",
    "expected_commands",
    "expected_clarification_focus",
    "critical_retained_constraints",
    "corrected_constraints",
    "forbidden_claims",
    "forbidden_actions",
    "attribution_requirements",
    "source_limitations",
    "authorization_requirements",
    "uncertainty_expectation",
    "source_evidence_complete",
    "missing_source_exchange_ids",
    "correction_exchange_ids",
    "long_history_unrelated_exchange_count",
    "expected_history_usage",
    "historical_instructions_are_untrusted",
    "unicode_variants",
    "taskless",
    "foreign_discussion_ids",
    "mixed_membership_exchange_ids",
    "provenance_may_be_unverified",
}

# Exact model-visible casing is intentionally not listed.  These prefixes are the
# known grader namespaces, not evidence namespaces such as source_ or semantic_.
GRADER_KEY_PREFIXES = (
    "expected_",
    "forbidden_",
    "critical_",
    "corrected_",
    "attribution_",
    "authorization_",
    "uncertainty_",
)


def load_cases() -> list[dict[str, Any]]:
    with FIXTURE_PATH.open("r", encoding="utf-8") as handle:
        dataset = json.load(handle)
    return list(dataset["cases"])


def project_resolver_input(case: Mapping[str, Any]) -> dict[str, Any]:
    discovery = case["discovery"]
    projected_discovery = {
        field: discovery[field]
        for field in MODEL_FACING_DISCOVERY_FIELDS
        if field in discovery
    }
    projected_discovery["representatives"] = [
        {
            field: representative[field]
            for field in MODEL_FACING_REPRESENTATIVE_FIELDS
            if field in representative
        }
        for representative in projected_discovery.get("representatives", [])
    ]
    return {
        "current_user_message": case["current_user_message"],
        "discovery": projected_discovery,
    }


def eligible_selected_discussion_ids(
    case: Mapping[str, Any], selected_discussion_ids: Any
) -> tuple[str, ...]:
    if isinstance(selected_discussion_ids, (str, bytes, bytearray, dict)):
        raise ValueError("selected discussion IDs must be a sequence of strings")
    try:
        selected = list(selected_discussion_ids)
    except TypeError as exc:
        raise ValueError("selected discussion IDs must be a sequence of strings") from exc

    seen: set[str] = set()
    normalized: list[str] = []
    for item in selected:
        if type(item) is not str or item == "" or item != item.strip():
            raise ValueError("selected discussion IDs must be exact, nonempty, unpadded strings")
        if item in seen:
            raise ValueError("selected discussion IDs must be unique")
        seen.add(item)
        normalized.append(item)

    discovery = case["discovery"]
    if not discovery.get("discovery_complete"):
        if normalized:
            raise ValueError("selected discussion IDs are unavailable during incomplete discovery")
        return ()

    candidate_ids = discovery.get("candidate_discussion_ids", [])
    candidate_set = set(candidate_ids)
    if not candidate_set and normalized:
        raise ValueError("there are no eligible current-session candidate discussion IDs")

    discussion_by_id = {d["discussion_id"]: d for d in case["discussions"]}
    for discussion_id in normalized:
        if discussion_id not in candidate_set:
            raise ValueError("selected discussion ID is not a current-session discovery candidate")
        discussion = discussion_by_id.get(discussion_id)
        if discussion is None:
            raise ValueError("selected discussion ID is missing from case discussions")
        if discussion.get("foreign_session") is True:
            raise ValueError("selected discussion ID belongs to a foreign session")
        if discussion.get("model_visible") is False:
            raise ValueError("selected discussion ID is not visible to model-facing output")
        if discussion.get("session_id") != case["session_id"]:
            raise ValueError("selected discussion ID does not belong to the current session")

    return tuple(normalized)


def project_answer_source(case: Mapping[str, Any], selected_discussion_ids: Any) -> list[dict[str, Any]]:
    selected = eligible_selected_discussion_ids(case, selected_discussion_ids)
    selected_set = set(selected)
    if not selected_set:
        return []

    projected = []
    for exchange in case["exchanges"]:
        if exchange.get("model_visible") is False:
            continue
        if not (selected_set & set(exchange.get("discussion_ids", []))):
            continue
        projected.append(
            {
                field: exchange[field]
                for field in MODEL_FACING_ANSWER_EXCHANGE_FIELDS
                if field in exchange
            }
        )
    return projected


def grader_rubric(case: Mapping[str, Any]) -> dict[str, Any]:
    """Grader-only metadata. This must never be inserted into model input."""
    keys = [
        "case_id",
        "primary_dimension",
        "tags",
        "session_id",
        "expected_outcome",
        "expected_selected_discussion_ids",
        "expected_resolver_output",
        "expected_source_exchange_ids",
        "critical_retained_constraints",
        "corrected_constraints",
        "forbidden_claims",
        "forbidden_actions",
        "expected_commands",
        "uncertainty_expectation",
        "attribution_requirements",
        "source_limitations",
        "authorization_requirements",
        "source_evidence_complete",
        "missing_source_exchange_ids",
        "correction_exchange_ids",
        "long_history_unrelated_exchange_count",
        "expected_history_usage",
        "historical_instructions_are_untrusted",
    ]
    return {key: case.get(key) for key in keys if key in case}


def case_is_structurally_complete(case: Mapping[str, Any]) -> bool:
    if case.get("source_evidence_complete") is False:
        return False
    if case.get("missing_source_exchange_ids"):
        return False
    discovery = case.get("discovery", {})
    if discovery.get("discovery_complete") is False:
        return False
    if discovery.get("unresolved_discussion_count", 0) != 0:
        return False
    if discovery.get("unresolved_task_proof_count", 0) != 0:
        return False
    if discovery.get("omitted_count", 0) != 0:
        return False
    return True


def _is_grader_only_key(key: str) -> bool:
    if key in MODEL_INVISIBLE_CASE_KEYS:
        return True
    return any(key.startswith(prefix) for prefix in GRADER_KEY_PREFIXES)


def _normalize_json_strings(value: Any, seen: set[int] | None = None) -> Any:
    if seen is None:
        seen = set()
    if isinstance(value, str):
        marker = id(value)
        # Avoid reprocessing the exact same object repeatedly.
        if marker in seen:
            return value
        seen.add(marker)
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError, ValueError):
            return value
        if isinstance(parsed, (Mapping, list, tuple)):
            return _normalize_json_strings(parsed, seen)
        return value
    if isinstance(value, Mapping):
        return {key: _normalize_json_strings(item, seen) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize_json_strings(item, seen) for item in value]
    return value


def input_contains_grader_metadata(value: Any) -> bool:
    normalized = _normalize_json_strings(value)
    found = False

    def walk(obj: Any) -> None:
        nonlocal found
        if isinstance(obj, Mapping):
            for key, child in obj.items():
                if not isinstance(key, str):
                    continue
                if _is_grader_only_key(key):
                    found = True
                    return
                walk(child)
                if found:
                    return
        elif isinstance(obj, (list, tuple)):
            for child in obj:
                walk(child)
                if found:
                    return

    walk(normalized)
    text = json.dumps(normalized, ensure_ascii=False, default=str)
    return found


def neutral_selected_discussion_ids(case: Mapping[str, Any]) -> list[str]:
    discovery = case["discovery"]
    candidates = list(discovery.get("candidate_discussion_ids", []))
    if not discovery.get("discovery_complete") or not candidates:
        return []

    discussion_by_id = {d["discussion_id"]: d for d in case["discussions"]}
    active = [
        discussion_id
        for discussion_id in candidates
        if str(discussion_by_id.get(discussion_id, {}).get("task_state", "")).lower()
        in {"waiting", "running"}
    ]
    if active:
        return active
    return candidates


def neutral_resolver_output(case: Mapping[str, Any]) -> dict[str, Any]:
    selected = neutral_selected_discussion_ids(case)
    if not selected:
        return {
            "outcome": "clarification",
            "selected_discussion_ids": [],
            "clarification": "Verified discovery is incomplete or empty.",
        }
    return {
        "outcome": "selected",
        "selected_discussion_ids": selected,
        "clarification": None,
    }


NEUTRAL_ANSWER_OUTPUT = {
    "assistant_text": (
        "Offline harness neutral answer. The final semantic grader evaluates this "
        "separately; no correctness claim is made by this plumbing fixture."
    ),
    "commands": [],
}


class ScriptedModel:
    """A fake one-shot model provider for orchestration tests."""

    def __init__(
        self,
        outputs: Sequence[Any],
        *,
        failures: Sequence[BaseException | None] | None = None,
        cancel_on_call: int | None = None,
    ) -> None:
        self.outputs = list(outputs)
        self.failures = list(failures or [])
        self.cancel_on_call = cancel_on_call
        self.calls: list[dict[str, Any]] = []
        self.returned_outputs: list[str] = []
        self._failure_used: set[int] = set()

    async def chat(self, messages, **kwargs) -> str:
        index = len(self.calls)
        self.calls.append({"messages": messages, "kwargs": kwargs})

        if self.cancel_on_call is not None and index == self.cancel_on_call:
            raise asyncio.CancelledError()

        if index < len(self.failures) and self.failures[index] is not None and index not in self._failure_used:
            self._failure_used.add(index)
            raise self.failures[index]

        output = self.outputs[min(index, len(self.outputs) - 1)] if self.outputs else None
        if callable(output):
            output = output(messages)
        if isinstance(output, str):
            text = output
        else:
            text = json.dumps(output, ensure_ascii=False)
        self.returned_outputs.append(text)
        return text


class RecordingModelClient:
    """Recording wrapper that preserves injected provider behavior."""

    def __init__(
        self,
        base: Any,
        *,
        failures: Sequence[BaseException | None] | None = None,
        cancel_on_call: int | None = None,
    ) -> None:
        self.base = base
        self.failures = list(failures or [])
        self.cancel_on_call = cancel_on_call
        self.calls: list[dict[str, Any]] = []
        self.returned_outputs: list[str] = []
        self._failure_used: set[int] = set()

    async def chat(self, messages, **kwargs) -> Any:
        index = len(self.calls)
        self.calls.append({"messages": messages, "kwargs": kwargs})

        if self.cancel_on_call is not None and index == self.cancel_on_call:
            raise asyncio.CancelledError()

        if index < len(self.failures) and self.failures[index] is not None and index not in self._failure_used:
            self._failure_used.add(index)
            raise self.failures[index]

        if hasattr(self.base, "chat") and callable(self.base.chat):
            result = await self.base.chat(messages, **kwargs)
        else:
            result = self.base(messages, **kwargs)
            if asyncio.iscoroutine(result):
                result = await result
        if isinstance(result, str):
            self.returned_outputs.append(result)
        return result


def build_discovery_input(case: Mapping[str, Any]) -> dict[str, Any]:
    discovery = case["discovery"]
    return {
        "payload_json": json.dumps(discovery, ensure_ascii=False),
        "candidate_discussion_ids": list(discovery["candidate_discussion_ids"]),
        "available_count": discovery["available_count"],
        "returned_count": discovery["returned_count"],
        "omitted_count": discovery["omitted_count"],
        "unresolved_discussion_count": discovery["unresolved_discussion_count"],
        "unresolved_task_proof_count": discovery["unresolved_task_proof_count"],
        "discovery_complete": discovery["discovery_complete"],
    }


def _verification_from_discussion_provenance(discussion: Mapping[str, Any]) -> bool:
    provenance = str(discussion.get("semantic_provenance", "") or "")
    return "unverified" in provenance or "legacy" in provenance


def _source_explicit_correction_text(exchange: Mapping[str, Any]) -> str | None:
    """Return source text when it explicitly records a correction/conflict.

    This never reads grader-only corrected/expected/forbidden fields.  It
    treats source phrases as fallible observations, not canonical model facts.
    """
    texts = [str(exchange.get("user_text", "") or ""), str(exchange.get("assistant_text", "") or "")]
    combined = "\n".join(texts)
    lowered = combined.lower()
    if "conflict" not in lowered and "correction" not in lowered and "supersed" not in lowered:
        return None
    return combined.strip()


def build_evidence_result(case: Mapping[str, Any], selected_discussion_ids: Any) -> Any:
    """Build fixture-derived evidence without claiming DB proof.

    Missing, truncated, omitted, or unverified markers are represented as
    unresolved/truncated/unverified fields.  Correction events are derived only
    from explicitly model-visible source text and linkage.  Grader-only metadata
    is not used.
    """
    selected = eligible_selected_discussion_ids(case, selected_discussion_ids)
    selected_set = set(selected)
    answer_projection = project_answer_source(case, selected)
    answer_by_exchange = {item["exchange_id"]: item for item in answer_projection}

    discussion_by_id = {d["discussion_id"]: d for d in case["discussions"]}
    exchange_rows_by_discussion: dict[str, list[str]] = {}
    for exchange in case["exchanges"]:
        if exchange.get("model_visible") is False:
            continue
        for discussion_id in exchange.get("discussion_ids", []):
            exchange_rows_by_discussion.setdefault(discussion_id, []).append(exchange["exchange_id"])

    source_complete = case_is_structurally_complete(case)
    source_unverified = bool(case.get("provenance_may_be_unverified"))
    truncated = (case.get("source_evidence_complete") is False) or bool(case.get("missing_source_exchange_ids"))
    missing_source_ids = list(case.get("missing_source_exchange_ids") or [])

    entries = []
    for discussion_id in selected:
        discussion = discussion_by_id[discussion_id]
        member_exchange_ids = [
            exchange_id
            for exchange_id in exchange_rows_by_discussion.get(discussion_id, [])
            if exchange_id in answer_by_exchange
        ]
        members = []
        for exchange_id in member_exchange_ids:
            source_exchange = next(
                (exchange for exchange in case["exchanges"] if exchange["exchange_id"] == exchange_id),
                None,
            )
            assert source_exchange is not None
            member_text_truncated = (
                (not source_complete)
                or len(str(source_exchange.get("user_text", "") or "")) > 2000
                or len(str(source_exchange.get("assistant_text", "") or "")) > 2000
            )
            member_unverified = _verification_from_discussion_provenance(discussion) or source_unverified
            members.append(
                {
                    "discussion_id": discussion_id,
                    "session_id": case["session_id"],
                    "exchange_id": exchange_id,
                    "user_turn_id": source_exchange["user_turn_id"],
                    "assistant_turn_id": source_exchange["assistant_turn_id"],
                    "link_provenance": str(discussion.get("label_provenance", "") or "fixture_derived"),
                    "source_available": True,
                    "canonical_user_text": source_exchange["user_text"],
                    "canonical_assistant_text": source_exchange["assistant_text"],
                    "user_text": source_exchange["user_text"][:2000],
                    "assistant_text": source_exchange["assistant_text"][:2000],
                    "user_text_truncated": member_text_truncated,
                    "assistant_text_truncated": member_text_truncated,
                    "task_id": None,
                    "task_state": discussion.get("task_state"),
                    "task_version": None,
                    "task_original_request": None,
                    "task_normalized_goal": None,
                    "task_final_result": None,
                    "exact_task_association": False,
                }
            )

        # Events are observations in source text, not grader-only corrections.
        history_events = []
        for exchange_id in member_exchange_ids:
            source_exchange = next(
                (exchange for exchange in case["exchanges"] if exchange["exchange_id"] == exchange_id),
                None,
            )
            assert source_exchange is not None
            source_text = _source_explicit_correction_text(source_exchange)
            if source_text is None:
                continue
            is_conflict = "conflict" in source_text.lower()
            event_type = "conflict" if is_conflict else "correction"
            payload_text = source_exchange["assistant_text"]
            event_text_truncated = (
                not source_complete
                or len(str(payload_text)) > 2000
                or len(str(source_exchange["user_text"])) > 2000
            )
            history_events.append(
                {
                    "event_id": source_exchange["assistant_turn_id"],
                    "discussion_id": discussion_id,
                    "session_id": case["session_id"],
                    "event_type": event_type,
                    "target_exchange_id": source_exchange["exchange_id"],
                    "target_user_turn_id": source_exchange["user_turn_id"],
                    "target_assistant_turn_id": source_exchange["assistant_turn_id"],
                    "prior_event_id": None,
                    "payload_text": payload_text,
                    "payload_text_model": payload_text[:2000],
                    "payload_text_truncated": event_text_truncated,
                    "provenance": str(discussion.get("label_provenance", "") or discussion.get("semantic_provenance", "") or "fixture_derived_observation"),
                    "created_at": None,
                    "target_source_complete": True,
                    "is_correction": not is_conflict and event_type == "correction",
                    "is_conflict": is_conflict,
                    "is_link_repair": False,
                    "is_member_linked": True,
                }
            )

        discussion_task_fields = None
        if discussion.get("task_state") is not None:
            discussion_task_fields = {
                "task_id": None,
                "task_state": discussion.get("task_state"),
                "task_version": None,
                "task_original_request": None,
                "task_normalized_goal": discussion.get("label"),
                "task_final_result": None,
                "exact_task_association": False,
            }

        member_unresolved = len(missing_source_ids)
        member_omitted = 0
        member_available = len(member_exchange_ids)
        entry_unverified = _verification_from_discussion_provenance(discussion) or source_unverified
        entry_insufficient_reasons = []
        if member_unresolved:
            entry_insufficient_reasons.append("unresolved_or_missing_member_source")
        if truncated:
            entry_insufficient_reasons.append("role_or_event_text_projection_truncated")
        if missing_source_ids:
            entry_insufficient_reasons.append("omitted_member_evidence_under_caller_bounds")
        if member_unverified:
            entry_insufficient_reasons.append("unverified_or_legacy_provenance_present")
        if discussion.get("task_state") is not None:
            entry_insufficient_reasons.append("discussion_task_metadata_requires_exact_association_proof")

        provenance_values = [str(discussion.get("label_provenance", "") or "fixture_derived")]
        entries.append(
            {
                "discussion_id": discussion_id,
                "session_id": case["session_id"],
                "label": discussion["label"],
                "label_provenance": str(discussion.get("label_provenance", "") or "fixture_derived"),
                "label_unverified": entry_unverified,
                "task_id": None,
                "created_at": None,
                "updated_at": None,
                "member_count_available": len(member_exchange_ids),
                "members": members,
                "member_coverage": {
                    "member_available_count": member_available,
                    "member_returned_count": member_available,
                    "member_omitted_count": member_omitted,
                    "event_available_count": len(history_events),
                    "event_returned_count": len(history_events),
                    "event_omitted_count": 0,
                    "link_count": len(member_exchange_ids),
                    "unresolved_member_count": member_unresolved,
                    "member_source_complete": member_unresolved == 0 and not truncated,
                    "event_unresolved_subject_count": 0,
                    "event_source_complete": True,
                },
                "events": history_events,
                "event_coverage": {
                    "member_available_count": member_available,
                    "member_returned_count": member_available,
                    "member_omitted_count": member_omitted,
                    "event_available_count": len(history_events),
                    "event_returned_count": len(history_events),
                    "event_omitted_count": 0,
                    "link_count": len(member_exchange_ids),
                    "unresolved_member_count": member_unresolved,
                    "member_source_complete": member_unresolved == 0 and not truncated,
                    "event_unresolved_subject_count": 0,
                    "event_source_complete": True,
                },
                "discussion_task_fields": discussion_task_fields,
                "provenance_values": provenance_values,
                "has_unverified_or_legacy_provenance": entry_unverified,
                "has_role_text_truncation": truncated or any(m.get("user_text_truncated") or m.get("assistant_text_truncated") for m in members),
                "is_structurally_complete": bool(members) and member_unresolved == 0 and not truncated and len(member_exchange_ids) == member_available,
                "semantic_complete": False,
                "insufficiency_reasons": tuple(entry_insufficient_reasons),
            }
        )

    try:
        from backend.services.grounded_discussion_evidence import (
            DiscussionHistoryEventEvidence,
            EvidenceCoverage,
            SelectedDiscussionEvidenceEntry,
            SelectedDiscussionEvidenceResult,
            SourceExchangeEvidence,
        )
    except Exception:
        try:
            from services.grounded_discussion_evidence import (
                DiscussionHistoryEventEvidence,
                EvidenceCoverage,
                SelectedDiscussionEvidenceEntry,
                SelectedDiscussionEvidenceResult,
                SourceExchangeEvidence,
            )
        except Exception:
            return _fallback_evidence_result(case, selected, entries, source_complete, truncated, missing_source_ids, source_unverified)

    production_entries = []
    for entry in entries:
        production_members = tuple(
            SourceExchangeEvidence(
                discussion_id=member["discussion_id"],
                session_id=member["session_id"],
                exchange_id=member["exchange_id"],
                user_turn_id=member["user_turn_id"],
                assistant_turn_id=member["assistant_turn_id"],
                link_provenance=member["link_provenance"],
                link_created_at=None,
                source_available=member["source_available"],
                canonical_user_text=member["canonical_user_text"],
                canonical_assistant_text=member["canonical_assistant_text"],
                user_text=member["user_text"],
                assistant_text=member["assistant_text"],
                user_text_truncated=member["user_text_truncated"],
                assistant_text_truncated=member["assistant_text_truncated"],
                task_id=member["task_id"],
                task_state=member["task_state"],
                task_version=member["task_version"],
                task_original_request=member["task_original_request"],
                task_normalized_goal=member["task_normalized_goal"],
                task_final_result=member["task_final_result"],
                exact_task_association=member["exact_task_association"],
            )
            for member in entry["members"]
        )

        production_events = tuple(
            DiscussionHistoryEventEvidence(
                event_id=event["event_id"],
                discussion_id=event["discussion_id"],
                session_id=event["session_id"],
                event_type=event["event_type"],
                target_exchange_id=event["target_exchange_id"],
                target_user_turn_id=event["target_user_turn_id"],
                target_assistant_turn_id=event["target_assistant_turn_id"],
                prior_event_id=event["prior_event_id"],
                payload_text=event["payload_text"],
                payload_text_model=event["payload_text_model"],
                payload_text_truncated=event["payload_text_truncated"],
                provenance=event["provenance"],
                created_at=event["created_at"],
                target_source_complete=event["target_source_complete"],
                is_correction=event["is_correction"],
                is_conflict=event["is_conflict"],
                is_link_repair=event["is_link_repair"],
                is_member_linked=event["is_member_linked"],
            )
            for event in entry["events"]
        )

        member_coverage = EvidenceCoverage(
            member_available_count=entry["member_coverage"]["member_available_count"],
            member_returned_count=entry["member_coverage"]["member_returned_count"],
            member_omitted_count=entry["member_coverage"]["member_omitted_count"],
            event_available_count=entry["member_coverage"]["event_available_count"],
            event_returned_count=entry["member_coverage"]["event_returned_count"],
            event_omitted_count=entry["member_coverage"]["event_omitted_count"],
            link_count=entry["member_coverage"]["link_count"],
            unresolved_member_count=entry["member_coverage"]["unresolved_member_count"],
            member_source_complete=entry["member_coverage"]["member_source_complete"],
            event_unresolved_subject_count=entry["member_coverage"]["event_unresolved_subject_count"],
            event_source_complete=entry["member_coverage"]["event_source_complete"],
        )
        event_coverage = EvidenceCoverage(
            member_available_count=entry["event_coverage"]["member_available_count"],
            member_returned_count=entry["event_coverage"]["member_returned_count"],
            member_omitted_count=entry["event_coverage"]["member_omitted_count"],
            event_available_count=entry["event_coverage"]["event_available_count"],
            event_returned_count=entry["event_coverage"]["event_returned_count"],
            event_omitted_count=entry["event_coverage"]["event_omitted_count"],
            link_count=entry["event_coverage"]["link_count"],
            unresolved_member_count=entry["event_coverage"]["unresolved_member_count"],
            member_source_complete=entry["event_coverage"]["member_source_complete"],
            event_unresolved_subject_count=entry["event_coverage"]["event_unresolved_subject_count"],
            event_source_complete=entry["event_coverage"]["event_source_complete"],
        )
        production_entries.append(
            SelectedDiscussionEvidenceEntry(
                discussion_id=entry["discussion_id"],
                session_id=entry["session_id"],
                label=entry["label"],
                label_provenance=entry["label_provenance"],
                label_unverified=entry["label_unverified"],
                task_id=entry["task_id"],
                created_at=entry["created_at"],
                updated_at=entry["updated_at"],
                member_count_available=entry["member_count_available"],
                members=production_members,
                member_coverage=member_coverage,
                events=production_events,
                event_coverage=event_coverage,
                discussion_task_fields=entry["discussion_task_fields"],
                provenance_values=tuple(entry["provenance_values"]),
                has_unverified_or_legacy_provenance=entry["has_unverified_or_legacy_provenance"],
                has_role_text_truncation=entry["has_role_text_truncation"],
                is_structurally_complete=entry["is_structurally_complete"],
                semantic_complete=False,
                insufficiency_reasons=tuple(entry["insufficiency_reasons"]),
            )
        )

    aggregate_member_available = sum(e.member_coverage.member_available_count for e in production_entries)
    aggregate_member_returned = sum(e.member_coverage.member_returned_count for e in production_entries)
    aggregate_member_omitted = sum(e.member_coverage.member_omitted_count for e in production_entries)
    aggregate_event_available = sum(e.event_coverage.event_available_count for e in production_entries)
    aggregate_event_returned = sum(e.event_coverage.event_returned_count for e in production_entries)
    aggregate_event_omitted = sum(e.event_coverage.event_omitted_count for e in production_entries)
    aggregate_unresolved_members = sum(e.member_coverage.unresolved_member_count for e in production_entries)
    aggregate_unresolved_events = sum(e.event_coverage.event_unresolved_subject_count for e in production_entries)
    aggregate_truncation = any(e.has_role_text_truncation for e in production_entries)
    aggregate_unverified = any(e.has_unverified_or_legacy_provenance for e in production_entries)

    dedup_reasons: list[str] = []
    seen_reasons: set[str] = set()
    for entry in production_entries:
        for reason in entry.insufficiency_reasons:
            if reason not in seen_reasons:
                seen_reasons.add(reason)
                dedup_reasons.append(reason)

    insufficient = bool(dedup_reasons) or aggregate_unverified or truncated or bool(missing_source_ids)
    structurally_complete = (
        len(production_entries) > 0
        and aggregate_unresolved_members == 0
        and aggregate_member_omitted == 0
        and aggregate_event_omitted == 0
        and aggregate_unresolved_events == 0
        and not aggregate_truncation
        and all(e.is_structurally_complete for e in production_entries)
    )

    return SelectedDiscussionEvidenceResult(
        session_id=case["session_id"],
        requested_discussion_ids=tuple(selected),
        selected_discussion_ids=tuple(selected),
        discussion_count=len(production_entries),
        entries=tuple(production_entries),
        member_available_count=aggregate_member_available,
        member_returned_count=aggregate_member_returned,
        member_omitted_count=aggregate_member_omitted,
        event_available_count=aggregate_event_available,
        event_returned_count=aggregate_event_returned,
        event_omitted_count=aggregate_event_omitted,
        unresolved_member_count=aggregate_unresolved_members,
        event_unresolved_subject_count=aggregate_unresolved_events,
        member_source_complete=aggregate_unresolved_members == 0 and not truncated,
        event_source_complete=aggregate_unresolved_events == 0,
        has_role_text_truncation=aggregate_truncation,
        has_unverified_or_legacy_provenance=aggregate_unverified,
        is_structurally_complete=structurally_complete,
        semantic_complete=False,
        untrusted=True,
        insufficiency_reasons=tuple(dedup_reasons),
        insufficient=insufficient,
        revalidation_required_before_dispatch=True,
        complete=False,
    )


def _fallback_evidence_result(case, selected, entries, source_complete, truncated, missing, unverified):
    return {
        "session_id": case["session_id"],
        "requested_discussion_ids": list(selected),
        "selected_discussion_ids": list(selected),
        "entries": entries,
        "is_structurally_complete": source_complete and not missing and not truncated,
        "has_role_text_truncation": truncated,
        "unresolved_member_count": len(missing),
        "member_omitted_count": 0,
        "event_unresolved_subject_count": 0,
        "event_omitted_count": 0,
        "insufficient": not source_complete or bool(missing) or truncated or unverified,
    }


def make_evidence_reader(case: Mapping[str, Any]) -> Callable[[Any], Any]:
    def reader(selected_ids: Any) -> Any:
        return build_evidence_result(case, selected_ids)

    return reader


async def run_case(
    case: Mapping[str, Any],
    *,
    model_client: Any = None,
    model_outputs: Sequence[Any] | None = None,
    failures: Sequence[BaseException | None] | None = None,
    cancel_on_call: int | None = None,
    evidence_reader: Any = None,
    recent_turns: Sequence[Mapping[str, Any]] | None = None,
    wrap_model: bool = True,
) -> dict[str, Any]:
    try:
        from backend.services.conversation_turn_engine import ConversationTurnEngine
    except ImportError:
        from services.conversation_turn_engine import ConversationTurnEngine

    if model_outputs is not None:
        base = ScriptedModel(model_outputs, failures=failures, cancel_on_call=cancel_on_call)
        wrapper_failures = None
        wrapper_cancel = None
    elif model_client is not None:
        base = model_client
        wrapper_failures = failures
        wrapper_cancel = cancel_on_call
    else:
        model_outputs = (neutral_resolver_output(case), NEUTRAL_ANSWER_OUTPUT)
        base = ScriptedModel(model_outputs, failures=failures, cancel_on_call=cancel_on_call)
        wrapper_failures = None
        wrapper_cancel = None

    if wrap_model or wrapper_failures or wrapper_cancel is not None:
        wrapped = RecordingModelClient(base, failures=wrapper_failures, cancel_on_call=wrapper_cancel)
    else:
        wrapped = base

    engine = ConversationTurnEngine(
        wrapped,
        discussion_evidence_reader=evidence_reader if evidence_reader is not None else make_evidence_reader(case),
    )

    record: dict[str, Any] = {
        "case_id": case["case_id"],
        "session_id": case["session_id"],
        "grader_rubric": grader_rubric(case),
        "scripted_model_note": "scripted_orchestration_only_not_semantic_model_performance",
        "model_provider_mode": "injected_recording_wrapper" if model_client is not None or wrap_model else "scripted",
    }

    try:
        result = await engine.run(
            user_text=case["current_user_message"],
            recent_turns=list(recent_turns or []),
            clarification_descriptors=[],
            discussion_discovery=build_discovery_input(case),
            discussion_contexts=None,
        )
        record["status"] = "completed"
        record["assistant_text"] = result.assistant_text
        record["status_text"] = result.status_text
        record["commands"] = list(result.commands)
        record["call_count"] = engine.call_count
    except BaseException as exc:  # returned to caller for assertions
        record["status"] = "raised"
        record["exception_type"] = type(exc).__name__
        record["exception"] = exc
        record["call_count"] = engine.call_count
        return record

    raw_outputs = getattr(wrapped, "returned_outputs", [])
    record["raw_resolver_output"] = raw_outputs[0] if raw_outputs else None
    record["raw_answer_output"] = raw_outputs[-1] if len(raw_outputs) > 1 else None
    try:
        parsed_resolver = json.loads(record["raw_resolver_output"]) if record["raw_resolver_output"] else None
    except (json.JSONDecodeError, TypeError):
        parsed_resolver = None
    record["selected_discussion_ids"] = (
        parsed_resolver.get("selected_discussion_ids") if isinstance(parsed_resolver, Mapping) else None
    )
    record["resolver_outcome"] = parsed_resolver.get("outcome") if isinstance(parsed_resolver, Mapping) else None
    record["grader_rubric_only"] = True

    final_evidence = None
    if getattr(wrapped, "calls", None) and len(wrapped.calls) > 1:
        try:
            final_user_payload = json.loads(wrapped.calls[-1]["messages"][-1]["content"])
            final_evidence = final_user_payload.get("selected_discussion_evidence")
        except (json.JSONDecodeError, TypeError, KeyError):
            final_evidence = None
    record["exact_answer_evidence_passed_to_model"] = final_evidence
    record["raw_final_model_input"] = wrapped.calls[-1] if getattr(wrapped, "calls", None) else None
    record["grader_metadata_leak"] = input_contains_grader_metadata(getattr(wrapped, "calls", []))
    record["structurally_complete_evidence"] = case_is_structurally_complete(case)
    record["final_call_messages"] = getattr(wrapped, "calls", [])

    return record


async def run_all_cases(**kwargs: Any) -> list[dict[str, Any]]:
    records = []
    for case in load_cases():
        records.append(await run_case(case, **kwargs))
    return records