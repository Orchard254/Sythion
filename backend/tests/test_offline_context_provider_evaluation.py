"""Offline context-provider integration harness tests.

These tests use the real ConversationTurnEngine with a SCRIPTED fake model.  They
exercise resolver/evidence/answer orchestration and fail-closed validation.  They
are deliberately NOT a semantic model benchmark: grader-only rubric metadata is
kept separate and is never placed in model input.
"""

from __future__ import annotations

import json

import pytest

try:
    from backend.services.conversation_turn_engine import (
        ConversationTurnEngine,
        ConversationTurnValidationError,
    )
    from backend.services.conversation_runtime import ConversationModelError
    from backend.tests.offline_context_provider_harness import (
        build_discovery_input,
        case_is_structurally_complete,
        eligible_selected_discussion_ids,
        grader_rubric,
        input_contains_grader_metadata,
        load_cases,
        neutral_resolver_output,
        NEUTRAL_ANSWER_OUTPUT,
        project_answer_source,
        project_resolver_input,
        RecordingModelClient,
        run_case,
        ScriptedModel,
    )
except ImportError:
    from services.conversation_turn_engine import (
        ConversationTurnEngine,
        ConversationTurnValidationError,
    )
    from services.conversation_runtime import ConversationModelError
    from tests.offline_context_provider_harness import (
        build_discovery_input,
        case_is_structurally_complete,
        eligible_selected_discussion_ids,
        grader_rubric,
        input_contains_grader_metadata,
        load_cases,
        neutral_resolver_output,
        NEUTRAL_ANSWER_OUTPUT,
        project_answer_source,
        project_resolver_input,
        RecordingModelClient,
        run_case,
        ScriptedModel,
    )


CASES = load_cases()


@pytest.fixture(autouse=True)
def _scrub_environment(monkeypatch):
    import os

    for key in list(os.environ):
        if key.upper().startswith("SYTHION_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")


@pytest.mark.asyncio
async def test_fixture_has_fifteen_cases():
    assert len(CASES) == 15


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES, ids=lambda case: case["case_id"])
async def test_case_runs_through_real_engine_within_budget(case):
    record = await run_case(case)

    assert record["grader_metadata_leak"] is False

    discovery = case["discovery"]
    if not discovery.get("discovery_complete") or not discovery.get("candidate_discussion_ids"):
        assert record["call_count"] == 0
        assert record["status"] == "completed"
        return

    if not case_is_structurally_complete(case):
        assert record["call_count"] == 1
        assert record["status"] == "completed"
        assert "structurally complete" in record["assistant_text"].lower() or "enough" in record["assistant_text"].lower()
        return

    assert record["call_count"] == 2
    assert isinstance(record["assistant_text"], str) and record["assistant_text"].strip()
    assert isinstance(record["commands"], list)
    assert len(record["commands"]) <= 3

    selected = record["selected_discussion_ids"]
    eligible = eligible_selected_discussion_ids(case, selected)
    assert tuple(selected) == eligible

    evidence = record["exact_answer_evidence_passed_to_model"]
    assert isinstance(evidence, dict)
    assert evidence.get("session_id") == case["session_id"]

    for call in record.get("final_call_messages", []):
        payload_text = json.dumps(call, ensure_ascii=False, default=str)
        assert "expected_resolver_output" not in payload_text


@pytest.mark.asyncio
async def test_incomplete_discovery_fail_closed_no_model_calls():
    incomplete = next(case for case in CASES if not case["discovery"]["discovery_complete"])
    record = await run_case(incomplete)
    assert record["call_count"] == 0
    assert record["status"] == "completed"


@pytest.mark.asyncio
async def test_missing_evidence_reader_fail_closed():
    complete = next(case for case in CASES if case_is_structurally_complete(case))
    record = await run_case(complete, evidence_reader=lambda selected: None)
    assert record["call_count"] == 1
    assert "insufficient" in record["assistant_text"].lower() or "enough" in record["assistant_text"].lower()


@pytest.mark.asyncio
async def test_candidate_and_session_validation_fail_closed():
    cross_case = next(case for case in CASES if case.get("foreign_discussion_ids"))
    foreign_id = cross_case["foreign_discussion_ids"][0]
    with pytest.raises(ValueError):
        eligible_selected_discussion_ids(cross_case, [foreign_id])
    with pytest.raises(ValueError):
        project_answer_source(cross_case, [foreign_id])

    complete = next(case for case in CASES if case["discovery"]["discovery_complete"])
    with pytest.raises(ValueError):
        eligible_selected_discussion_ids(complete, [complete["discovery"]["candidate_discussion_ids"][0] + " "])


@pytest.mark.asyncio
async def test_missing_and_incomplete_fail_closed_helper():
    incomplete = next(case for case in CASES if not case["discovery"]["discovery_complete"])
    assert eligible_selected_discussion_ids(incomplete, []) == ()
    with pytest.raises(ValueError):
        eligible_selected_discussion_ids(incomplete, ["disc-any"])


@pytest.mark.asyncio
async def test_one_shot_permanent_provider_failure_propagates():
    complete = next(case for case in CASES if case_is_structurally_complete(case))
    record = await run_case(complete, failures=[RuntimeError("provider exploded")])
    assert record["status"] == "raised"
    assert record["exception_type"] == "ConversationModelError"
    assert record["call_count"] == 1


@pytest.mark.asyncio
async def test_cancellation_propagates_without_retry():
    complete = next(case for case in CASES if case_is_structurally_complete(case))
    record = await run_case(complete, cancel_on_call=0)
    assert record["status"] == "raised"
    assert record["exception_type"] == "CancelledError"
    assert record["call_count"] == 1


@pytest.mark.asyncio
async def test_final_call_cancellation_propagates_without_retry():
    complete = next(case for case in CASES if case_is_structurally_complete(case))
    record = await run_case(complete, cancel_on_call=1)
    assert record["status"] == "raised"
    assert record["exception_type"] == "CancelledError"
    assert record["call_count"] == 2


@pytest.mark.asyncio
async def test_malformed_json_retry_budget_is_bounded_two():
    complete = next(case for case in CASES if case_is_structurally_complete(case))
    model = ScriptedModel(
        [
            "not json at all",
            json.dumps({"assistant_text": "recovered", "commands": []}),
        ]
    )
    engine = ConversationTurnEngine(model)
    result = await engine.run(complete["current_user_message"], [], [])
    assert result.assistant_text == "recovered"
    assert len(model.calls) <= 2


@pytest.mark.asyncio
async def test_output_budget_never_exceeds_two_in_labelled_path():
    complete = next(case for case in CASES if case_is_structurally_complete(case))
    model = ScriptedModel(
        [
            json.dumps(neutral_resolver_output(complete)),
            json.dumps({"assistant_text": "x", "commands": []}),
        ]
    )
    engine = ConversationTurnEngine(model, discussion_evidence_reader=__import__(
        "backend.tests.offline_context_provider_harness", fromlist=["make_evidence_reader"]
    ).make_evidence_reader(complete))
    await engine.run(
        user_text=complete["current_user_message"],
        recent_turns=[],
        clarification_descriptors=[],
        discussion_discovery=build_discovery_input(complete),
    )
    assert engine.call_count <= 2


@pytest.mark.asyncio
async def test_grader_rubric_contains_expected_but_model_input_does_not():
    case = CASES[0]
    rubric = grader_rubric(case)
    assert "expected_resolver_output" in rubric
    assert "forbidden_claims" in rubric
    projected = project_resolver_input(case)
    projected_text = json.dumps(projected, ensure_ascii=False)
    assert "expected_resolver_output" not in projected_text
    assert "forbidden_claims" not in projected_text
    for claim in case["forbidden_claims"]:
        assert claim not in projected_text


@pytest.mark.asyncio
async def test_all_fifteen_records_have_no_grader_metadata_leak():
    records = [await run_case(case) for case in CASES]
    assert len(records) == 15
    assert all(record.get("grader_metadata_leak") is False for record in records)
    assert all(record["call_count"] <= 2 for record in records)
    assert sum(1 for record in records if record["status"] == "completed") == 15


@pytest.mark.asyncio
async def test_correction_source_based_events():
    correction_case = next(case for case in CASES if case.get("corrected_constraints"))
    assert case_is_structurally_complete(correction_case)
    record = await run_case(correction_case)
    evidence = record["exact_answer_evidence_passed_to_model"]
    events = [entry.get("events", [])
              for entry in evidence.get("entries", [])]
    flattened = [event for entry_events in events for event in entry_events]
    assert flattened
    assert any(event.get("event_type") == "correction" for event in flattened)
    # The event is an observed source exchange, not a grader label.
    assert any(event.get("target_exchange_id") for event in flattened)
    assert any(event.get("payload_text_model") and "Correction" in str(event.get("payload_text_model")) for event in flattened)


@pytest.mark.asyncio
async def test_correction_marker_absent_case_has_no_invented_event():
    simple = next(case for case in CASES if case["case_id"].startswith("eval.paper-kite"))
    assert case_is_structurally_complete(simple)
    record = await run_case(simple)
    evidence = record["exact_answer_evidence_passed_to_model"]
    flattened = [event for entry in evidence.get("entries", [])
                 for event in entry.get("events", [])]
    assert flattened == []


@pytest.mark.asyncio
async def test_task_state_selected_fresh_without_false_complete():
    task_case = next(case for case in CASES if case["case_id"].startswith("eval.task_state"))
    record = await run_case(task_case)
    assert record["status"] == "completed"
    assert record["call_count"] == 2
    evidence = record["exact_answer_evidence_passed_to_model"]
    members = [member for entry in evidence.get("entries", [])
               for member in entry.get("members", [])]
    assert members and members[0]["task_state"] == "waiting"
    assert evidence.get("complete") is False
    assert evidence.get("revalidation_required_before_dispatch") is True
    flattened = [event for entry in evidence.get("entries", [])
                 for event in entry.get("events", [])]
    assert flattened == []


@pytest.mark.asyncio
async def test_no_leakage_mixed_case():
    mixed = next(case for case in CASES if case["case_id"].startswith("eval.mixed_topic"))
    record = await run_case(mixed)
    assert record["grader_metadata_leak"] is False
    all_payloads = json.dumps(record.get("final_call_messages", []), ensure_ascii=False, default=str)
    assert "ex-eval-joint-budget-004" in all_payloads
    # No grader-only keys appear even in nested serialized content.
    assert not input_contains_grader_metadata(record.get("final_call_messages", []))


@pytest.mark.asyncio
async def test_adversarial_nested_json_rubric_probe():
    leaked = [
        {
            "messages": [
                {
                    "role": "user",
                    "content": json.dumps(
                        {"nested": {"expected_resolver_output": {"selected_discussion_ids": ["leak"]}}}
                    ),
                }
            ]
        }
    ]
    assert input_contains_grader_metadata(leaked) is True

    hidden_marker = "x-grader-marker-91837465"
    rubric_like = {"expected_custom_probe": hidden_marker}
    message = json.dumps({"assistant_text": "ordinary model-facing text"})
    assert input_contains_grader_metadata({"messages": [{"content": message}]}) is False
    # A grader-only key is what makes leakage detectable, not the value alone.
    leaked_with_key = {
        "messages": [
            {"role": "user", "content": json.dumps({"expected_custom_probe": hidden_marker})}
        ]
    }
    assert input_contains_grader_metadata(leaked_with_key) is True


@pytest.mark.asyncio
async def test_exact_grader_value_does_not_flag_identical_source_text():
    identical_value = "budget is $15, superseding the earlier $9 value"
    legitimate = {
        "messages": [
            {
                "role": "user",
                "content": json.dumps({"assistant_text": identical_value}),
            }
        ]
    }
    # Identical quoted source text is not leakage when no grader-only key is present.
    assert input_contains_grader_metadata(legitimate) is False

    leaked = {
        "messages": [
            {
                "role": "user",
                "content": json.dumps({"corrected_constraints": [identical_value]}),
            }
        ]
    }
    assert input_contains_grader_metadata(leaked) is True


@pytest.mark.asyncio
async def test_truncated_missing_source_is_not_fabricated_complete():
    truncated = next(case for case in CASES if case["case_id"] == "eval.truncated.missing_source_honest_limits")
    assert not case_is_structurally_complete(truncated)
    record = await run_case(truncated)
    assert record["call_count"] == 1
    assert record["status"] == "completed"
    assert "structurally complete" in record["assistant_text"].lower()
    assert record["exact_answer_evidence_passed_to_model"] is None


@pytest.mark.asyncio
async def test_unsigned_history_and_unselected_sources_are_excluded():
    mixed = next(case for case in CASES if case["case_id"] == "eval.mixed_topic.shared_exchange_membership")
    record = await run_case(mixed)
    assert record["call_count"] == 2
    all_payloads = json.dumps(record.get("final_call_messages", []), ensure_ascii=False, default=str)
    assert "ex-eval-joint-budget-004" in all_payloads


@pytest.mark.asyncio
async def test_foreign_discussion_selection_fail_closed():
    cross = next(case for case in CASES if case.get("foreign_discussion_ids"))
    foreign_id = cross["foreign_discussion_ids"][0]
    with pytest.raises(ValueError):
        eligible_selected_discussion_ids(cross, [foreign_id])
    with pytest.raises(ValueError):
        project_answer_source(cross, [foreign_id])
    assert project_answer_source(cross, []) == []


@pytest.mark.asyncio
async def test_hidden_grader_keys_do_not_leak_into_any_call():
    case = next(case for case in CASES if case.get("corrected_constraints"))
    record = await run_case(case)
    assert record["grader_metadata_leak"] is False
    for key_name in (
        "expected_resolver_output",
        "forbidden_claims",
        "expected_source_exchange_ids",
        "critical_retained_constraints",
        "corrected_constraints",
    ):
        for call in record.get("final_call_messages", []):
            assert key_name not in json.dumps(call, ensure_ascii=False, default=str)


@pytest.mark.asyncio
async def test_no_full_history_is_injected_by_default():
    complete = next(case for case in CASES if case_is_structurally_complete(case))
    record = await run_case(complete)
    for call in record.get("final_call_messages", []):
        messages = call.get("messages", [])
        for message in messages:
            try:
                payload = json.loads(message.get("content", "{}"))
            except (json.JSONDecodeError, TypeError):
                payload = {}
            assert payload.get("recent_turns", []) == []
    assert record["call_count"] in (1, 2)


@pytest.mark.asyncio
async def test_uncertainty_is_displayed_for_truncated_evidence():
    truncated = next(case for case in CASES if not case_is_structurally_complete(case))
    record = await run_case(truncated)
    text = record["assistant_text"].lower()
    assert "cannot" in text or "enough" in text or "structurally complete" in text or "reliably" in text


@pytest.mark.asyncio
async def test_injected_model_client_is_recorded():
    complete = next(case for case in CASES if case_is_structurally_complete(case))
    base = ScriptedModel(
        [
            json.dumps(neutral_resolver_output(complete)),
            json.dumps(NEUTRAL_ANSWER_OUTPUT),
        ]
    )
    injected = RecordingModelClient(base)
    record = await run_case(complete, model_client=injected)
    assert record["call_count"] == 2
    assert len(injected.calls) == 2
    assert len(injected.returned_outputs) == 2
    assert record["model_provider_mode"] == "injected_recording_wrapper"


@pytest.mark.asyncio
async def test_one_shot_injected_failure_does_not_repeat():
    complete = next(case for case in CASES if case_is_structurally_complete(case))
    base = ScriptedModel(
        [json.dumps(neutral_resolver_output(complete)), json.dumps(NEUTRAL_ANSWER_OUTPUT)],
        failures=[RuntimeError("injected provider failure")],
    )
    record = await run_case(complete, model_client=base)
    assert record["status"] == "raised"
    assert record["exception_type"] == "ConversationModelError"
    assert record["call_count"] == 1
