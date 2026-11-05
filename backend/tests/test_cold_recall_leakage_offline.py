"""Offline regression for cold-recall leakage assertion semantics.

This test does not use a provider, network, database, migration, or runtime
production code.  It mirrors the payload extraction in
``test_new_fictional_http_lifecycle_real.py`` and checks the intended
distinction:

* a discovery label or preview may contain an incidental subset of factual
  markers, including a model-inferred summary such as "Juniper's yellow
  perch";
* the full distinctive recall fact contains the canonical source context as
  well ("... beside the window"), so partial discovery output is not enough to
  answer the full fact;
* the full distinctive recall fact must not be present in resolver-only
  inputs, final non-evidence, or recent turns;
* selected evidence must contain the full distinctive recall fact;
* the assertion must still fail when the full fact is present in a resolver
  label or preview.
"""

from __future__ import annotations

import json

import pytest

try:
    from backend.services.conversation_turn_engine import ConversationTurnEngine
except ImportError:
    from services.conversation_turn_engine import ConversationTurnEngine

FULL_RECALL_FACT = ("juniper", "yellow", "perch", "beside", "window")

FACT_TEXT = "my parrot Juniper prefers a yellow perch beside the window."


def _payloads(*, resolver_label: str, resolver_preview: str) -> tuple[dict, dict]:
    discovery_payload = {
        "session_id": "s",
        "available_count": 1,
        "returned_count": 1,
        "omitted_count": 0,
        "unresolved_discussion_count": 0,
        "unresolved_task_proof_count": 0,
        "candidate_discussion_ids": ["disc-1"],
        "discovery_complete": True,
        "reason": "complete",
        "representatives": [
            {
                "discussion_id": "disc-1",
                "label": resolver_label,
                "preview_text": resolver_preview,
            }
        ],
    }
    resolver = {
        "user_request": "what did I mention about my parrot?",
        "recent_turns": [{"role": "user", "content": "Unrelated weather note"}],
        "discussion_discovery": {
            "payload_json": json.dumps(discovery_payload),
            "available_count": 1,
            "returned_count": 1,
            "omitted_count": 0,
            "unresolved_discussion_count": 0,
            "unresolved_task_proof_count": 0,
            "candidate_discussion_ids": ["disc-1"],
            "discovery_complete": True,
        },
    }
    final = {
        "user_request": "what did I mention about my parrot?",
        "recent_turns": [{"role": "user", "content": "Unrelated weather note"}],
        "discussion_contexts": [],
        "selected_discussion_evidence": {
            "canonical_user_text": FACT_TEXT,
            "label": "fictional discussion with Juniper's perch",
        },
    }
    return resolver, final


def _discovery_text(resolver: dict) -> str:
    discovery = resolver["discussion_discovery"]
    parts = [json.dumps(discovery, ensure_ascii=False)]
    try:
        payload = json.loads(discovery["payload_json"])
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = {}
    for representative in payload.get("representatives", []):
        if isinstance(representative, dict):
            parts.append(str(representative.get("label", "")))
            parts.append(str(representative.get("preview_text", "")))
    return " ".join(parts).lower()


def _marker_count(text: str) -> int:
    return sum(1 for marker in FULL_RECALL_FACT if marker in text)


def test_incidental_partial_fact_in_resolver_label_does_not_fail():
    partial_cases = [
        (
            "fictional story: sunlit library explorer and Juniper's perch",
            "an explorer wandered through a sunlit library full of old maps and ",
        ),
        (
            "fictional library scene and Juniper's yellow perch",
            "an explorer wandered through a sunlit library full of old maps and ",
        ),
        (
            "single marker discussion",
            "an explorer mentioned Juniper and a perch",
        ),
    ]
    for resolver_label, resolver_preview in partial_cases:
        resolver, final = _payloads(
            resolver_label=resolver_label,
            resolver_preview=resolver_preview,
        )
        resolver_discovery_text = _discovery_text(resolver)

        # Partial discovery input may expose summary markers.  It is not the full
        # fact unless every distinctive part is present.  The test deliberately
        # includes a case containing three markers to prove that subset leakage
        # is not mistaken for a complete leak.
        assert _marker_count(resolver_discovery_text) < len(FULL_RECALL_FACT)
        assert not all(
            key in resolver_discovery_text for key in FULL_RECALL_FACT
        ), (resolver_label, resolver_preview, resolver_discovery_text)

        recent = json.dumps(final["recent_turns"]).lower()
        assert not all(key in recent for key in FULL_RECALL_FACT)

        outside_evidence = json.dumps(
            {key: value for key, value in final.items() if key != "selected_discussion_evidence"},
            ensure_ascii=False,
        ).lower()
        assert not all(key in outside_evidence for key in FULL_RECALL_FACT)

        evidence = json.dumps(final["selected_discussion_evidence"]).lower()
        assert all(key in evidence for key in FULL_RECALL_FACT)


def test_full_fact_in_resolver_label_or_preview_is_detected():
    cases = [
        (
            "juniper yellow perch beside window",
            "intro text",
        ),
        (
            "single marker discussion",
            "juniper prefers a yellow perch beside the window",
        ),
        (
            "juniper perch",
            "mentions yellow perch beside the window explicitly",
        ),
    ]
    for label, preview in cases:
        combined = (label + " " + preview).lower()
        if all(marker in combined for marker in FULL_RECALL_FACT):
            resolver, _ = _payloads(resolver_label=label, resolver_preview=preview)
            resolver_discovery_text = _discovery_text(resolver)
            assert all(
                key in resolver_discovery_text for key in FULL_RECALL_FACT
            ), (label, preview)


def test_complete_fact_in_resolver_label_or_preview_fails_assertion():
    resolver, final = _payloads(
        resolver_label="discussion about Juniper's yellow perch beside the window",
        resolver_preview="an explorer wandered through a sunlit library",
    )
    resolver_discovery_text = _discovery_text(resolver)
    failed = False
    try:
        assert not all(key in resolver_discovery_text for key in FULL_RECALL_FACT)
    except AssertionError:
        failed = True
    assert failed, "resolver leakage assertion did not fail for full fact"


def _structurally_complete_evidence(*, full_fact_present: bool = True) -> dict:
    fact_text = FACT_TEXT if full_fact_present else "my parrot Juniper prefers a yellow perch."
    return {
        "session_id": "s",
        "requested_discussion_ids": ["disc-1"],
        "selected_discussion_ids": ["disc-1"],
        "discussion_count": 1,
        "entries": [
            {
                "discussion_id": "disc-1",
                "session_id": "s",
                "members": [
                    {
                        "canonical_user_text": fact_text,
                        "canonical_assistant_text": "noted",
                        "user_text": fact_text,
                        "assistant_text": "noted",
                        "source_available": True,
                        "user_text_truncated": False,
                        "assistant_text_truncated": False,
                    }
                ],
                "member_coverage": {
                    "member_available_count": 1,
                    "member_returned_count": 1,
                    "member_omitted_count": 0,
                    "event_available_count": 0,
                    "event_returned_count": 0,
                    "event_omitted_count": 0,
                    "link_count": 1,
                    "unresolved_member_count": 0,
                    "member_source_complete": True,
                    "event_unresolved_subject_count": 0,
                    "event_source_complete": True,
                },
                "event_coverage": {
                    "member_available_count": 1,
                    "member_returned_count": 1,
                    "member_omitted_count": 0,
                    "event_available_count": 0,
                    "event_returned_count": 0,
                    "event_omitted_count": 0,
                    "link_count": 1,
                    "unresolved_member_count": 0,
                    "member_source_complete": True,
                    "event_unresolved_subject_count": 0,
                    "event_source_complete": True,
                },
                "is_structurally_complete": True,
                "has_role_text_truncation": False,
                "has_unverified_or_legacy_provenance": False,
                "insufficiency_reasons": [],
            }
        ],
        "is_structurally_complete": True,
        "unresolved_member_count": 0,
        "member_omitted_count": 0,
        "event_unresolved_subject_count": 0,
        "event_omitted_count": 0,
        "has_role_text_truncation": False,
        "member_source_complete": True,
        "event_source_complete": True,
        "has_unverified_or_legacy_provenance": False,
        "insufficient": False,
    }


@pytest.mark.asyncio
async def test_final_selected_evidence_injects_full_recall_fact_offline():
    resolver, _ = _payloads(
        resolver_label="fictional library scene and Juniper's yellow perch",
        resolver_preview="an explorer wandered through a sunlit library full of old maps and ",
    )
    discovery_payload = json.loads(resolver["discussion_discovery"]["payload_json"])
    discovery_input = {
        "payload_json": resolver["discussion_discovery"]["payload_json"],
        "candidate_discussion_ids": list(resolver["discussion_discovery"]["candidate_discussion_ids"]),
        "available_count": resolver["discussion_discovery"]["available_count"],
        "returned_count": resolver["discussion_discovery"]["returned_count"],
        "omitted_count": resolver["discussion_discovery"]["omitted_count"],
        "unresolved_discussion_count": resolver["discussion_discovery"]["unresolved_discussion_count"],
        "unresolved_task_proof_count": resolver["discussion_discovery"]["unresolved_task_proof_count"],
        "discovery_complete": resolver["discussion_discovery"]["discovery_complete"],
    }

    resolver_output = json.dumps(
        {"outcome": "selected", "selected_discussion_ids": ["disc-1"], "clarification": None}
    )
    final_output = json.dumps(
        {"assistant_text": "Juniper prefers a yellow perch beside the window.", "commands": []}
    )

    try:
        from backend.tests.offline_context_provider_harness import ScriptedModel as FakeModel
    except ImportError:
        from tests.offline_context_provider_harness import ScriptedModel as FakeModel

    model = FakeModel([resolver_output, final_output])
    engine = ConversationTurnEngine(
        model,
        discussion_evidence_reader=lambda ids: _structurally_complete_evidence(full_fact_present=True),
    )

    result = await engine.run(
        user_text="what did I mention about my parrot?",
        recent_turns=[],
        clarification_descriptors=[],
        discussion_discovery=discovery_input,
        discussion_contexts=None,
    )

    assert result.assistant_text.startswith("Juniper prefers")
    assert engine.call_count == 2
    final_call = model.calls[-1]
    final_payload = json.loads(final_call["messages"][-1]["content"])
    evidence = final_payload["selected_discussion_evidence"]
    evidence_text = json.dumps(evidence, ensure_ascii=False).lower()
    assert all(marker in evidence_text for marker in FULL_RECALL_FACT), evidence_text
    # Injection must occur through selected evidence, not through prior discovery.
    discovery_text = _discovery_text(resolver)
    assert not all(marker in discovery_text for marker in FULL_RECALL_FACT)


@pytest.mark.asyncio
async def test_omitted_or_truncated_evidence_does_not_inject_full_fact_offline():
    resolver, _ = _payloads(
        resolver_label="fictional library scene and Juniper's yellow perch",
        resolver_preview="an explorer wandered through a sunlit library full of old maps and ",
    )
    discovery_input = {
        "payload_json": resolver["discussion_discovery"]["payload_json"],
        "candidate_discussion_ids": list(resolver["discussion_discovery"]["candidate_discussion_ids"]),
        "available_count": resolver["discussion_discovery"]["available_count"],
        "returned_count": resolver["discussion_discovery"]["returned_count"],
        "omitted_count": resolver["discussion_discovery"]["omitted_count"],
        "unresolved_discussion_count": resolver["discussion_discovery"]["unresolved_discussion_count"],
        "unresolved_task_proof_count": resolver["discussion_discovery"]["unresolved_task_proof_count"],
        "discovery_complete": resolver["discussion_discovery"]["discovery_complete"],
    }

    resolver_output = json.dumps(
        {"outcome": "selected", "selected_discussion_ids": ["disc-1"], "clarification": None}
    )

    try:
        from backend.tests.offline_context_provider_harness import ScriptedModel as FakeModel
    except ImportError:
        from tests.offline_context_provider_harness import ScriptedModel as FakeModel

    # Structurally complete evidence may still omit the distinctive window
    # context.  In that case the turn can complete, but the injected evidence
    # must not falsely contain the full fact.
    missing_window = _structurally_complete_evidence(full_fact_present=False)
    factory_output = json.dumps(
        {"assistant_text": "Juniper prefers a yellow perch.", "commands": []}
    )
    model = FakeModel([resolver_output, factory_output])
    engine = ConversationTurnEngine(
        model,
        discussion_evidence_reader=lambda ids: missing_window,
    )
    result = await engine.run(
        user_text="what did I mention about my parrot?",
        recent_turns=[],
        clarification_descriptors=[],
        discussion_discovery=discovery_input,
        discussion_contexts=None,
    )
    assert engine.call_count == 2
    final_call = model.calls[-1]
    final_payload = json.loads(final_call["messages"][-1]["content"])
    evidence_text = json.dumps(final_payload["selected_discussion_evidence"], ensure_ascii=False).lower()
    assert not all(marker in evidence_text for marker in FULL_RECALL_FACT), evidence_text

    # Structurally incomplete/missing source case: fail closed.
    incomplete_source = _structurally_complete_evidence(full_fact_present=True)
    incomplete_source["is_structurally_complete"] = False
    incomplete_source["entries"][0]["is_structurally_complete"] = False
    incomplete_source["unresolved_member_count"] = 1
    incomplete_source["entries"][0]["member_coverage"]["unresolved_member_count"] = 1
    incomplete_source["entries"][0]["member_coverage"]["member_source_complete"] = False
    incomplete_source["member_source_complete"] = False
    incomplete_source["insufficient"] = True
    incomplete_source["entries"][0]["insufficiency_reasons"] = ["unresolved_or_missing_member_source"]
    model = FakeModel([resolver_output])
    engine = ConversationTurnEngine(
        model,
        discussion_evidence_reader=lambda ids: incomplete_source,
    )
    result = await engine.run(
        user_text="what did I mention about my parrot?",
        recent_turns=[],
        clarification_descriptors=[],
        discussion_discovery=discovery_input,
        discussion_contexts=None,
    )
    assert engine.call_count == 1
    assert "insufficient" in result.assistant_text.lower() or "enough" in result.assistant_text.lower()

    # Truncated evidence case: structurally complete proof fails.
    truncated = _structurally_complete_evidence(full_fact_present=True)
    truncated["has_role_text_truncation"] = True
    truncated["entries"][0]["has_role_text_truncation"] = True
    truncated["entries"][0]["member_coverage"]["member_source_complete"] = False
    truncated["member_source_complete"] = False
    truncated["is_structurally_complete"] = False
    truncated["entries"][0]["is_structurally_complete"] = False
    truncated["insufficient"] = True
    model = FakeModel([resolver_output])
    engine = ConversationTurnEngine(
        model,
        discussion_evidence_reader=lambda ids: truncated,
    )
    result = await engine.run(
        user_text="what did I mention about my parrot?",
        recent_turns=[],
        clarification_descriptors=[],
        discussion_discovery=discovery_input,
        discussion_contexts=None,
    )
    assert engine.call_count == 1
    assert "insufficient" in result.assistant_text.lower() or "enough" in result.assistant_text.lower()


@pytest.mark.asyncio
async def test_discovery_label_containing_full_fact_is_counterfactual_leak():
    resolver, _ = _payloads(
        resolver_label="Juniper's yellow perch beside the window",
        resolver_preview="an explorer wandered",
    )
    resolver_discovery_text = _discovery_text(resolver)
    assert all(marker in resolver_discovery_text for marker in FULL_RECALL_FACT)

    # Counterfactual: even if a discovery label contains the full fact, final
    # injection must be judged against selected canonical evidence.  Evidence
    # here omits the distinctive window context, so a model must not be able to
    # claim the full fact from discovery alone.
    evidence = _structurally_complete_evidence(full_fact_present=False)
    evidence_text = json.dumps(evidence, ensure_ascii=False).lower()
    assert not all(marker in evidence_text for marker in FULL_RECALL_FACT)

    # The discovery leak is independently detectable.
    failed = False
    try:
        assert not all(marker in resolver_discovery_text for marker in FULL_RECALL_FACT)
    except AssertionError:
        failed = True
    assert failed
