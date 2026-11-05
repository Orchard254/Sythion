"""Offline integrity tests for the labelled memory semantic evaluation fixture.

These tests deliberately import no application code, start no services, and make
no provider or database calls. They validate the shape, isolation, and internal
consistency of the fictional evaluation dataset only.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

DATASET_PATH = Path(__file__).resolve().parent / "evaluations" / "labelled_memory_cases.json"
CANONICAL_RESOLVER_KEYS = {"outcome", "selected_discussion_ids", "clarification"}
ALLOWED_DIMENSIONS = {
    "selection_correctness",
    "correction_retention",
    "attribution_uncertainty",
    "authorization_safety",
}
ALLOWED_OUTCOMES = {"selected", "clarification"}
ALLOWED_UNCERTAINTY = {"low", "medium", "high"}


@pytest.fixture(autouse=True)
def _scrub_environment(monkeypatch):
    for key in list(os.environ):
        upper = key.upper()
        if upper.startswith("SYTHION_") or upper == "SYTHION (SYNTH)_DATA_DIR":
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")


@pytest.fixture(scope="module")
def dataset() -> dict[str, Any]:
    with DATASET_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture(scope="module")
def cases(dataset: dict[str, Any]) -> list[dict[str, Any]]:
    return dataset["cases"]


def test_dataset_file_exists():
    assert DATASET_PATH.is_file()


def test_dataset_top_level_structure(dataset):
    assert dataset["dataset_id"] == "sythion-labelled-memory-semantic-evaluation"
    assert isinstance(dataset["dataset_version"], str) and dataset["dataset_version"]
    assert dataset["schema_version"] == 1
    assert isinstance(dataset["synthetic_fixture_notice"], str)
    assert dataset["offline_only"] is True
    assert dataset["live_provider_required"] is False
    assert dataset["provider_calls_made_by_this_fixture"] == 0
    assert dataset["semantic_judge_implemented"] is False
    assert set(dataset["canonical_resolver_schema"]["required_keys"]) == CANONICAL_RESOLVER_KEYS
    assert set(dataset["canonical_resolver_schema"]["allowed_outcomes"]) == ALLOWED_OUTCOMES
    assert isinstance(dataset["grading_dimensions"], list)
    assert len(dataset["grading_dimensions"]) >= 4
    dimension_ids = {dimension["id"] for dimension in dataset["grading_dimensions"]}
    assert ALLOWED_DIMENSIONS.issubset(dimension_ids)
    assert isinstance(dataset["grading_criteria"], dict)
    for dimension in ALLOWED_DIMENSIONS:
        assert dimension in dataset["grading_criteria"]
    assert isinstance(dataset["cases"], list)
    assert 12 <= len(dataset["cases"]) <= 20


def test_environment_was_scrubbed():
    assert not any(key.upper().startswith("SYTHION_") for key in os.environ)
    assert not any(key.upper() == "SYTHION (SYNTH)_DATA_DIR" for key in os.environ)


def test_case_ids_are_nonempty_and_unique(cases):
    case_ids = [case["case_id"] for case in cases]
    assert all(isinstance(case_id, str) and case_id for case_id in case_ids)
    assert len(case_ids) == len(set(case_ids))


def test_session_ids_are_nonempty_and_unique(cases):
    session_ids = [case["session_id"] for case in cases]
    assert all(isinstance(session_id, str) and session_id for session_id in session_ids)
    assert len(session_ids) == len(set(session_ids))


def test_discussion_ids_are_globally_unique(cases):
    discussion_ids = []
    for case in cases:
        for discussion in case["discussions"]:
            discussion_ids.append(discussion["discussion_id"])
    assert len(discussion_ids) == len(set(discussion_ids))


def test_exchange_ids_are_globally_unique(cases):
    exchange_ids = []
    for case in cases:
        for exchange in case["exchanges"]:
            exchange_ids.append(exchange["exchange_id"])
    assert len(exchange_ids) == len(set(exchange_ids))


def test_case_required_fields(cases):
    required = {
        "case_id",
        "primary_dimension",
        "tags",
        "session_id",
        "current_user_message",
        "discovery",
        "discussions",
        "exchanges",
        "expected_outcome",
        "expected_selected_discussion_ids",
        "expected_resolver_output",
        "critical_retained_constraints",
        "corrected_constraints",
        "forbidden_claims",
        "forbidden_actions",
        "expected_commands",
        "uncertainty_expectation",
        "attribution_requirements",
        "source_limitations",
        "authorization_requirements",
    }
    for case in cases:
        assert required.issubset(case.keys()), case["case_id"]
        assert case["primary_dimension"] in ALLOWED_DIMENSIONS
        assert isinstance(case["tags"], list) and case["tags"]
        assert case["expected_outcome"] in ALLOWED_OUTCOMES
        assert isinstance(case["critical_retained_constraints"], list)
        assert isinstance(case["corrected_constraints"], list)
        assert isinstance(case["forbidden_claims"], list) and case["forbidden_claims"]
        assert isinstance(case["forbidden_actions"], list) and case["forbidden_actions"]
        assert isinstance(case["expected_commands"], list)
        assert case["uncertainty_expectation"] in ALLOWED_UNCERTAINTY
        assert isinstance(case["attribution_requirements"], list) and case["attribution_requirements"]
        assert isinstance(case["source_limitations"], list)
        assert isinstance(case["authorization_requirements"], list) and case["authorization_requirements"]
        assert isinstance(case["current_user_message"], str) and case["current_user_message"].strip()


def test_cases_are_independently_understandable(cases):
    for case in cases:
        assert len(case["current_user_message"].split()) >= 3
        assert len(case["critical_retained_constraints"]) >= 1
        assert any(len(constraint.strip()) >= 4 for constraint in case["critical_retained_constraints"])


def test_discovery_is_consistent(cases):
    for case in cases:
        discovery = case["discovery"]
        assert discovery["session_id"] == case["session_id"]
        assert isinstance(discovery["available_count"], int)
        assert isinstance(discovery["returned_count"], int)
        assert isinstance(discovery["omitted_count"], int)
        assert isinstance(discovery["unresolved_discussion_count"], int)
        assert isinstance(discovery["unresolved_task_proof_count"], int)
        assert discovery["available_count"] >= discovery["returned_count"] >= 0
        assert discovery["omitted_count"] == discovery["available_count"] - discovery["returned_count"]
        assert discovery["unresolved_discussion_count"] >= 0
        assert discovery["unresolved_task_proof_count"] >= 0
        assert isinstance(discovery["candidate_discussion_ids"], list)
        assert len(set(discovery["candidate_discussion_ids"])) == len(discovery["candidate_discussion_ids"])
        assert all(isinstance(item, str) and item for item in discovery["candidate_discussion_ids"])
        assert discovery["discovery_complete"] is True or discovery["discovery_complete"] is False
        if discovery["discovery_complete"]:
            assert discovery["omitted_count"] == 0
            assert discovery["unresolved_discussion_count"] == 0
            assert discovery["unresolved_task_proof_count"] == 0
            assert discovery["returned_count"] == discovery["available_count"]
            assert set(discovery["candidate_discussion_ids"]).issubset(
                {discussion["discussion_id"] for discussion in case["discussions"]}
            )
        else:
            assert discovery["candidate_discussion_ids"] == []
            assert discovery["representatives"] == []
        assert isinstance(discovery["representatives"], list)
        representative_ids = [item["discussion_id"] for item in discovery["representatives"]]
        if discovery["candidate_discussion_ids"]:
            assert set(representative_ids) == set(discovery["candidate_discussion_ids"])
        else:
            assert representative_ids == []
        for representative in discovery["representatives"]:
            assert isinstance(representative["label"], str) and representative["label"]
            assert isinstance(representative["member_count"], int) and representative["member_count"] >= 0
            assert isinstance(representative["preview"], str) and representative["preview"]
            assert "compact preview only" in representative["preview"] or "preview" in representative["preview"]


def test_candidates_are_current_session_discussions(cases):
    for case in cases:
        discussion_ids = {discussion["discussion_id"] for discussion in case["discussions"] if not discussion.get("foreign_session")}
        for candidate in case["discovery"]["candidate_discussion_ids"]:
            assert candidate in discussion_ids, (case["case_id"], candidate)


def test_discussion_session_isolation(cases):
    for case in cases:
        for discussion in case["discussions"]:
            if discussion.get("foreign_session"):
                assert discussion["session_id"] != case["session_id"], case["case_id"]
            else:
                assert discussion["session_id"] == case["session_id"], case["case_id"]
            assert isinstance(discussion["member_exchange_ids"], list)
            assert discussion["member_exchange_ids"]


def test_member_exchanges_exist_in_case(cases):
    for case in cases:
        exchange_ids = {exchange["exchange_id"] for exchange in case["exchanges"]}
        for discussion in case["discussions"]:
            for member_id in discussion["member_exchange_ids"]:
                assert member_id in exchange_ids, (case["case_id"], member_id)


def test_exchange_structure(cases):
    for case in cases:
        seen_orders = []
        for exchange in case["exchanges"]:
            assert isinstance(exchange["exchange_id"], str) and exchange["exchange_id"]
            assert isinstance(exchange["user_turn_id"], int)
            assert isinstance(exchange["assistant_turn_id"], int)
            assert isinstance(exchange["observed_order"], int) and exchange["observed_order"] >= 1
            assert isinstance(exchange["user_text"], str) and exchange["user_text"].strip()
            assert isinstance(exchange["assistant_text"], str) and exchange["assistant_text"].strip()
            assert isinstance(exchange["discussion_ids"], list)
            seen_orders.append(exchange["observed_order"])
        assert seen_orders == sorted(seen_orders)


def test_expected_selection_matches_outcome(cases):
    for case in cases:
        selected = case["expected_selected_discussion_ids"]
        outcome = case["expected_outcome"]
        if outcome == "selected":
            assert selected, case["case_id"]
        else:
            assert selected == [], case["case_id"]
        assert all(isinstance(item, str) and item for item in selected)
        assert len(selected) == len(set(selected))
        assert set(selected).issubset(set(case["discovery"]["candidate_discussion_ids"]))


def test_expected_resolver_output_matches_canonical_schema(cases):
    for case in cases:
        output = case["expected_resolver_output"]
        assert set(output.keys()) == CANONICAL_RESOLVER_KEYS
        assert output["outcome"] == case["expected_outcome"]
        assert output["selected_discussion_ids"] == case["expected_selected_discussion_ids"]
        assert output["outcome"] in ALLOWED_OUTCOMES
        if output["outcome"] == "clarification":
            assert isinstance(output["clarification"], str) and output["clarification"].strip()
        else:
            assert output["clarification"] is None
        if output["outcome"] == "selected":
            assert output["selected_discussion_ids"]


def test_expected_source_exchanges_are_valid(cases):
    for case in cases:
        exchange_ids = {exchange["exchange_id"] for exchange in case["exchanges"]}
        sources = case["expected_source_exchange_ids"]
        assert isinstance(sources, list)
        assert len(sources) == len(set(sources))
        assert set(sources).issubset(exchange_ids)
        if case["expected_outcome"] == "selected":
            assert sources


def test_later_correction_sources_are_anchored(cases):
    for case in cases:
        if not case["corrected_constraints"]:
            continue
        correction_ids = case.get("correction_exchange_ids", [])
        assert isinstance(correction_ids, list)
        exchange_ids = {exchange["exchange_id"] for exchange in case["exchanges"]}
        for correction_id in correction_ids:
            assert correction_id in exchange_ids, (case["case_id"], correction_id)
        if correction_ids:
            assert set(correction_ids).issubset(set(case["expected_source_exchange_ids"]))


def test_long_history_cases_have_more_than_twenty_unrelated_messages(cases):
    for case in cases:
        filler_count = sum(1 for exchange in case["exchanges"] if exchange["discussion_ids"] == [])
        if case.get("long_history_unrelated_exchange_count") is not None:
            assert filler_count >= 21, case["case_id"]
            assert case["long_history_unrelated_exchange_count"] >= 21
            correction_ids = case.get("correction_exchange_ids", [])
            assert correction_ids
            correction_order = next(
                exchange["observed_order"]
                for exchange in case["exchanges"]
                if exchange["exchange_id"] in correction_ids
            )
            assert sum(1 for exchange in case["exchanges"] if exchange["observed_order"] < correction_order and exchange["discussion_ids"] == []) >= 20


def test_correction_case_retains_constraints(cases):
    correction_cases = [case for case in cases if case["corrected_constraints"]]
    assert correction_cases
    for case in correction_cases:
        assert case["critical_retained_constraints"]
        assert set(case["corrected_constraints"]).isdisjoint(set(case["critical_retained_constraints"]))


def test_no_correction_to_summary_leakage_previews(cases):
    for case in cases:
        previews = " ".join(
            representative["preview"].lower()
            for representative in case["discovery"]["representatives"]
        )
        for fact in case["corrected_constraints"]:
            assert fact.lower() not in previews, (case["case_id"], fact)
        for source_id in case.get("correction_exchange_ids", []):
            assert source_id.lower() not in previews, (case["case_id"], source_id)


def test_unicode_variant_cases_contain_non_ascii(cases):
    unicode_cases = [case for case in cases if case.get("unicode_variants")]
    assert unicode_cases
    for case in unicode_cases:
        serialized = json.dumps(case, ensure_ascii=False)
        assert any(ord(char) > 127 for char in serialized), case["case_id"]


def test_taskless_cases_forbid_task_creation(cases):
    taskless_cases = [case for case in cases if case.get("taskless")]
    assert taskless_cases
    for case in taskless_cases:
        joined = " ".join(case["forbidden_actions"]).lower()
        assert "task" in joined or "deadline" in joined
        assert "create task" in joined or "set deadline" in joined
        assert case["expected_commands"] == []


def test_malicious_history_cases_are_untrusted(cases):
    malicious_cases = [case for case in cases if case.get("malicious_history")]
    assert malicious_cases
    for case in malicious_cases:
        assert case["historical_instructions_are_untrusted"] is True
        assert case["expected_commands"] == []
        joined = " ".join(case["forbidden_actions"]).lower()
        assert "delete" in joined or "safety" in joined or "execute" in joined


def test_truncated_cases_have_high_uncertainty_and_limits(cases):
    truncated_cases = [case for case in cases if case.get("source_evidence_complete") is False]
    assert truncated_cases
    for case in truncated_cases:
        assert case["uncertainty_expectation"] == "high"
        assert case["source_limitations"]
        joined_forbidden = " ".join(case["forbidden_claims"]).lower()
        assert "exact" in joined_forbidden or "invent" in " ".join(case["forbidden_actions"]).lower()


def test_unverified_provenance_cases_do_not_upgrade(cases):
    unverified_cases = [case for case in cases if case.get("provenance_may_be_unverified")]
    assert unverified_cases
    for case in unverified_cases:
        joined_forbidden = " ".join(case["forbidden_claims"]).lower()
        if "model_inferred" in str(case.get("discussions", [])).lower():
            assert "verified" in joined_forbidden
        assert "verified permission" not in joined_forbidden


def test_task_state_cases_prefer_fresh_source(cases):
    task_state_cases = [case for case in cases if any(d.get("task_state") for d in case["discussions"])]
    assert task_state_cases
    for case in task_state_cases:
        selected = case["expected_selected_discussion_ids"]
        discussions = {discussion["discussion_id"]: discussion for discussion in case["discussions"]}
        assert all(discussions[did].get("task_state") == "waiting" for did in selected)
        joined_forbidden = " ".join(case["forbidden_claims"]).lower()
        assert "cancelled" in joined_forbidden


def test_task_authorization_cases_keep_history_non_authoritative(cases):
    authorization_cases = [case for case in cases if "task_creation" in case.get("tags", []) or "authorization" in case.get("case_id", "")]
    assert authorization_cases
    for case in authorization_cases:
        joined = " ".join(case["authorization_requirements"]).lower()
        assert "history" in joined or "explicit" in joined
        if "no_task_creation" in case.get("tags", []):
            assert case["expected_commands"] == []
            assert case.get("conditional_command_types", []) == []
        elif case.get("explicit_current_action_request"):
            assert case["expected_commands"] == []
            assert case.get("conditional_command_types") == ["start_task"]
            assert case.get("command_preconditions")
            joined_commands = " ".join(case["command_preconditions"]).lower()
            assert "available" in joined_commands or "appropriate" in joined_commands
            assert "current user" in joined_commands or "current request" in joined_commands
            assert "history" in joined_commands
        else:
            assert case["expected_commands"] == []


def test_cross_session_cases_exclude_foreign_discussions(cases):
    cross_cases = [case for case in cases if case.get("foreign_discussion_ids")]
    assert cross_cases
    for case in cross_cases:
        assert set(case["expected_selected_discussion_ids"]).isdisjoint(set(case["foreign_discussion_ids"]))
        assert set(case["discovery"]["candidate_discussion_ids"]).isdisjoint(set(case["foreign_discussion_ids"]))
        for discussion in case["discussions"]:
            if discussion.get("foreign_session"):
                assert discussion["discussion_id"] in case["foreign_discussion_ids"]


def test_incomplete_discovery_requires_clarification(cases):
    incomplete_cases = [case for case in cases if case["discovery"]["discovery_complete"] is False]
    assert incomplete_cases
    for case in incomplete_cases:
        assert case["expected_outcome"] == "clarification"
        assert case["expected_selected_discussion_ids"] == []


def test_self_contained_cases_do_not_select_history(cases):
    self_contained_cases = [case for case in cases if case.get("expected_history_usage") == "none"]
    assert self_contained_cases
    for case in self_contained_cases:
        assert case["expected_selected_discussion_ids"] == []
        assert case["expected_source_exchange_ids"] == []


def test_mixed_membership_is_disclosed(cases):
    mixed_cases = [case for case in cases if case.get("mixed_membership_exchange_ids")]
    assert mixed_cases
    for case in mixed_cases:
        exchange_ids = case["mixed_membership_exchange_ids"]
        assert set(exchange_ids).issubset({exchange["exchange_id"] for exchange in case["exchanges"]})
        assert len(case["expected_selected_discussion_ids"]) >= 2


def test_clarification_cases_have_focus(cases):
    clarification_cases = [case for case in cases if case["expected_outcome"] == "clarification"]
    assert clarification_cases
    for case in clarification_cases:
        if "expected_clarification_focus" in case:
            assert isinstance(case["expected_clarification_focus"], list)
            assert case["expected_clarification_focus"]


def test_no_preview_contains_answer_leakage(cases):
    for case in cases:
        preview_text = " ".join(
            representative["preview"] for representative in case["discovery"]["representatives"]
        ).lower()
        discussions = {discussion["discussion_id"]: discussion for discussion in case["discussions"]}
        for representative in case["discovery"]["representatives"]:
            discussion = discussions.get(representative["discussion_id"])
            if discussion is not None:
                assert representative["label"] == discussion["label"], case["case_id"]
        for claim in case["forbidden_claims"]:
            assert claim.lower() not in preview_text, (case["case_id"], claim)


def test_provenance_is_not_upgraded_to_verified_or_permission(cases):
    for case in cases:
        for discussion in case["discussions"]:
            provenance = discussion.get("semantic_provenance", "")
            assert "verified" not in provenance or provenance.endswith("unverified"), (case["case_id"], provenance)
            assert "permission" not in provenance


def test_session_isolation_by_case(cases):
    for case in cases:
        session_ids = {discussion["session_id"] for discussion in case["discussions"] if not discussion.get("foreign_session")}
        assert session_ids == {case["session_id"]}, case["case_id"]
        discussion_ids = {discussion["discussion_id"] for discussion in case["discussions"]}
        assert case["session_id"] not in discussion_ids


def test_case_count_diversity(cases):
    categories = {case["primary_dimension"] for case in cases}
    assert categories == ALLOWED_DIMENSIONS
    assert len(cases) >= 12


def test_fixture_declares_actual_evaluation_pending(dataset):
    assert dataset["evaluation_protocol"]["actual_model_evaluation_status"] == "pending"
    assert dataset["semantic_judge_implemented"] is False
    assert dataset["provider_calls_made_by_this_fixture"] == 0


MODEL_FACING_RESOLVER_CASE_FIELDS = {"current_user_message", "discovery"}
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
GRADER_ONLY_KEY_PREFIXES = (
    "expected_",
    "forbidden_",
    "critical_",
    "corrected_",
    "attribution_",
    "authorization_",
    "source_",
    "uncertainty_",
    "expected_clarification_focus",
)


def _is_grader_only_key(key):
    return any(key.startswith(prefix) for prefix in GRADER_ONLY_KEY_PREFIXES)


def _all_keys(value, prefix=""):
    keys = []
    if isinstance(value, dict):
        for key, child in value.items():
            full = f"{prefix}.{key}" if prefix else key
            keys.append(full)
            keys.extend(_all_keys(child, full))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            keys.extend(_all_keys(child, f"{prefix}[{index}]"))
    return keys


def project_resolver_input(case):
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


def eligible_selected_discussion_ids(case, selected_discussion_ids):
    discovery = case["discovery"]
    discussion_by_id = {discussion["discussion_id"]: discussion for discussion in case["discussions"]}

    # Fail closed on a scalar or other non-sequence input.
    if isinstance(selected_discussion_ids, (str, bytes, bytearray, dict)):
        raise ValueError("selected discussion IDs must be a sequence of strings")
    try:
        selected = list(selected_discussion_ids)
    except TypeError as exc:
        raise ValueError("selected discussion IDs must be a sequence of strings") from exc

    seen = set()
    normalized = []
    for item in selected:
        if type(item) is not str or item == "" or item != item.strip():
            raise ValueError("selected discussion IDs must be exact, nonempty, unpadded strings")
        if item in seen:
            raise ValueError("selected discussion IDs must be unique")
        seen.add(item)
        normalized.append(item)

    # Incomplete discovery cannot produce an affirmative selected set.
    if not discovery.get("discovery_complete"):
        if normalized:
            raise ValueError("selected discussion IDs are unavailable during incomplete discovery")
        return ()

    candidate_ids = discovery.get("candidate_discussion_ids", [])
    candidate_set = set(candidate_ids)
    if not candidate_set and normalized:
        raise ValueError("there are no eligible current-session candidate discussion IDs")
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


def project_answer_source(case, selected_discussion_ids):
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


def test_model_facing_projection_policy_is_explicit(dataset):
    policy = dataset["model_facing_projection_policy"]
    assert policy["status"] == "fixture_contract_only_no_runtime"
    assert policy["resolver_stage"]["case_fields"] == ["current_user_message", "discovery"]
    assert policy["resolver_stage"]["includes_raw_history"] is False
    assert policy["resolver_stage"]["includes_grader_metadata"] is False
    assert policy["answer_stage"]["selected_source_exchanges_only"] is True
    assert policy["answer_stage"]["exchange_fields"] == [
        "exchange_id",
        "user_text",
        "assistant_text",
    ]
    assert policy["answer_stage"]["includes_omitted_or_missing_evidence"] is False
    assert policy["answer_stage"]["includes_grader_metadata"] is False


def test_model_facing_resolver_projection_is_allowlisted(cases):
    for case in cases:
        projected = project_resolver_input(case)
        assert set(projected.keys()) == MODEL_FACING_RESOLVER_CASE_FIELDS
        assert set(projected["discovery"].keys()) == MODEL_FACING_DISCOVERY_FIELDS
        for representative in projected["discovery"]["representatives"]:
            assert set(representative.keys()).issubset(MODEL_FACING_REPRESENTATIVE_FIELDS)
        assert "discussions" not in projected
        assert "exchanges" not in projected
        projected_text = json.dumps(projected)
        for key in _all_keys(projected):
            leaf = key.rsplit(".", 1)[-1].split("[", 1)[0]
            assert not _is_grader_only_key(leaf), (case["case_id"], key)


def test_model_facing_answer_projection_scopes_to_selected_sources(cases):
    for case in cases:
        selected = case["expected_selected_discussion_ids"]
        projected = project_answer_source(case, selected)
        expected_keys = set()
        for exchange in case["exchanges"]:
            if exchange.get("model_visible") is False:
                continue
            if selected and (set(selected) & set(exchange.get("discussion_ids", []))):
                expected_keys.add(exchange["exchange_id"])
        assert {item["exchange_id"] for item in projected} == expected_keys, case["case_id"]
        for item in projected:
            assert set(item.keys()) == MODEL_FACING_ANSWER_EXCHANGE_FIELDS


def test_model_facing_projections_exclude_grader_metadata_and_forbidden_claims(cases):
    for case in cases:
        resolver_projection = project_resolver_input(case)
        answer_projection = project_answer_source(case, case["expected_selected_discussion_ids"])
        for projection in (resolver_projection, answer_projection):
            projection_text = json.dumps(projection, ensure_ascii=False)
            for claim in case["forbidden_claims"]:
                assert claim not in projection_text, (case["case_id"], claim)
            for expected_source in case["expected_source_exchange_ids"]:
                # Source identifiers may legitimately appear as evidence references.
                pass
        resolver_keys = _all_keys(resolver_projection)
        answer_keys = _all_keys(answer_projection)
        assert all(
            not _is_grader_only_key(key.rsplit(".", 1)[-1].split("[", 1)[0])
            for key in resolver_keys + answer_keys
        )


def test_omitted_and_missing_evidence_are_not_model_facing_sources(cases):
    incomplete = next(case for case in cases if case["case_id"] == "eval.incomplete.discovery_scope_exhaustion")
    assert project_answer_source(incomplete, incomplete["expected_selected_discussion_ids"]) == []
    assert incomplete["discovery"]["candidate_discussion_ids"] == []
    serialized = json.dumps(project_resolver_input(incomplete)) + json.dumps(project_answer_source(incomplete, []))
    assert "ex-eval-omitted-lantern-014" not in serialized
    truncated = next(case for case in cases if case["case_id"] == "eval.truncated.missing_source_honest_limits")
    projected = project_answer_source(truncated, truncated["expected_selected_discussion_ids"])
    assert {item["exchange_id"] for item in projected} == {"ex-eval-truncated-009"}
    serialized_projection = json.dumps(projected)
    assert "final budget is $9" not in serialized_projection
    assert "final budget is $15" not in serialized_projection


def test_cross_session_and_task_state_projections_stay_scoped(cases):
    cross = next(case for case in cases if case["case_id"] == "eval.cross_session.lookalike_labels")
    projected = project_answer_source(cross, cross["expected_selected_discussion_ids"])
    assert {item["exchange_id"] for item in projected} == {"ex-eval-current-lantern-012"}
    assert "ex-eval-foreign-lantern-012" not in json.dumps(projected)
    state = next(case for case in cases if case["case_id"] == "eval.task_state.fresh_waiting_vs_historical_cancelled")
    state_projected = project_answer_source(state, state["expected_selected_discussion_ids"])
    assert {item["exchange_id"] for item in state_projected} == {"ex-eval-report-fresh-008"}
    assert "Historical Report Task" not in json.dumps(state_projected)


def test_full_fixture_is_never_a_model_input(cases):
    full_fixture_text = json.dumps(cases)
    assert "expected_resolver_output" in full_fixture_text
    assert "forbidden_claims" in full_fixture_text
    projected = [project_resolver_input(case) for case in cases]
    projected_text = json.dumps(projected)
    assert "expected_resolver_output" not in projected_text
    assert "forbidden_claims" not in projected_text
    assert "expected_source_exchange_ids" not in projected_text


@pytest.mark.parametrize(
    "selected",
    [
        ["disc-eval-foreign-lantern-012"],
        ["disc-does-not-exist-000"],
        ["disc-eval-current-lantern-012", "disc-eval-foreign-lantern-012"],
        ["disc-eval-current-lantern-012", "disc-does-not-exist-000"],
        ["disc-eval-current-lantern-012", "disc-eval-current-lantern-012"],
    ],
)
def test_answer_projection_fail_closed_for_cross_session_adversarial_selections(cases, selected):
    cross_case = next(case for case in cases if case["case_id"] == "eval.cross_session.lookalike_labels")
    with pytest.raises(ValueError):
        project_answer_source(cross_case, selected)
    with pytest.raises(ValueError):
        eligible_selected_discussion_ids(cross_case, selected)


def test_answer_projection_reported_foreign_repro_fail_closed(cases):
    cross_case = next(case for case in cases if case["case_id"] == "eval.cross_session.lookalike_labels")
    with pytest.raises(ValueError):
        eligible_selected_discussion_ids(cross_case, ["disc-eval-foreign-lantern-012"])
    with pytest.raises(ValueError):
        project_answer_source(cross_case, ["disc-eval-foreign-lantern-012"])
    assert project_answer_source(cross_case, []) == []
    assert "ex-eval-foreign-lantern-012" not in json.dumps(project_answer_source(cross_case, []))


def test_answer_projection_fail_closed_for_omitted_hidden_and_mixed_selections(cases):
    incomplete_case = next(case for case in cases if case["case_id"] == "eval.incomplete.discovery_scope_exhaustion")
    with pytest.raises(ValueError):
        project_answer_source(incomplete_case, ["disc-eval-omitted-lantern-014"])
    with pytest.raises(ValueError):
        project_answer_source(incomplete_case, ["disc-eval-omitted-lantern-014", "disc-eval-current-lantern-012"])

    cross_case = next(case for case in cases if case["case_id"] == "eval.cross_session.lookalike_labels")
    with pytest.raises(ValueError):
        project_answer_source(cross_case, ["disc-eval-foreign-lantern-012", "disc-eval-current-lantern-012"])

    truncated_case = next(case for case in cases if case["case_id"] == "eval.truncated.missing_source_honest_limits")
    assert project_answer_source(truncated_case, truncated_case["expected_selected_discussion_ids"]) == [
        {"exchange_id": "ex-eval-truncated-009", "user_text": truncated_case["exchanges"][0]["user_text"], "assistant_text": truncated_case["exchanges"][0]["assistant_text"]}
    ]
    with pytest.raises(ValueError):
        project_answer_source(truncated_case, ["not-a-candidate"])


def test_nonsequence_and_blank_selected_ids_fail_closed(cases):
    case = next(case for case in cases if case["case_id"] == "eval.lantern.natural.back_to_lantern")
    for invalid in ["disc-eval-lantern-natural-001", 1, None, False, b"text"]:
        with pytest.raises(ValueError):
            project_answer_source(case, invalid)
    with pytest.raises(ValueError):
        project_answer_source(case, [" disc-eval-lantern-natural-001"])
    with pytest.raises(ValueError):
        project_answer_source(case, ["disc-eval-lantern-natural-001", "disc-eval-lantern-natural-001"])


def test_hidden_grader_fields_do_notAppear_in_any_projection(cases):
    for case in cases:
        for projection in (
            project_resolver_input(case),
            project_answer_source(case, case["expected_selected_discussion_ids"]),
        ):
            for key in _all_keys(projection):
                leaf = key.rsplit(".", 1)[-1].split("[", 1)[0]
                assert not _is_grader_only_key(leaf), (case["case_id"], key)
            projected_text = json.dumps(projection, ensure_ascii=False)
            for forbidden in case["forbidden_claims"]:
                assert forbidden not in projected_text, case["case_id"]


def test_correction_and_multi_membership_projection_preservation(cases):
    correction_case = next(case for case in cases if case["case_id"] == "eval.lantern.correction.budget_after_unrelated")
    projected = project_answer_source(correction_case, correction_case["expected_selected_discussion_ids"])
    assert {item["exchange_id"] for item in projected} == {
        "ex-eval-lantern-correction-002-establish",
        "ex-eval-lantern-correction-002-budget",
    }
    projected_text = json.dumps(projected, ensure_ascii=False)
    assert "Correction recorded: Project Lantern budget is now $15" in projected_text

    mixed_case = next(case for case in cases if case["case_id"] == "eval.mixed_topic.shared_exchange_membership")
    mixed_projected = project_answer_source(mixed_case, mixed_case["expected_selected_discussion_ids"])
    assert {item["exchange_id"] for item in mixed_projected} == {"ex-eval-joint-budget-004"}
