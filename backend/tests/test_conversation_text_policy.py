"""Behavioral contract for extracted, read-only response text classification."""

from types import SimpleNamespace

import pytest

try:
    from backend.services import conversation_text_policy as policy
    from backend.services.conversation_turn_engine import (
        ConversationTurnEngine,
        PLAIN_PROSE_NO_ACTION_DISCLOSURE,
    )
except ImportError:  # pragma: no cover - standalone backend layout
    from services import conversation_text_policy as policy
    from services.conversation_turn_engine import (
        ConversationTurnEngine,
        PLAIN_PROSE_NO_ACTION_DISCLOSURE,
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", False),
        (" ordinary", False),
        (" {\"answer\":1}", True),
        (" [1,2]", True),
        (chr(96) * 3 + "json\n{}", True),
        ("prefix {embedded}", False),
        ("prefix [embedded]", False),
    ],
)
def test_machine_payload_prefix_classification(text, expected):
    assert policy.looks_like_machine_readable_payload(text) is expected
    assert ConversationTurnEngine._looks_like_machine_readable_payload(text) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, False),
        (42, False),
        ("", False),
        ("   ", False),
        ("Good morning!", True),
        ("Plain answer [with bracket", False),
        ("Plain answer {with brace", False),
        ('{"assistant_text":"hello"}', False),
        (chr(96) * 3 + "json\n{}", False),
    ],
)
def test_ordinary_prose_never_admits_embedded_control_fragments(value, expected):
    assert policy.is_ordinary_prose(value) is expected
    assert ConversationTurnEngine._is_ordinary_prose(value) is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("  plain response  ", "plain response"),
        (" {\"k\":2} ", '{\"k\":2}'),
        (chr(96) * 3 + 'json\n{"k":2}\n' + chr(96) * 3, '{"k":2}'),
        (chr(96) * 3 + 'json\n{"k":2}', '{"k":2}'),
    ],
)
def test_machine_payload_unwrap_is_compatible(text, expected):
    assert policy.unwrap_machine_readable_payload(text) == expected
    assert ConversationTurnEngine._unwrap_machine_readable_payload(text) == expected


def test_legacy_disclosure_removed_without_touching_other_text():
    core = PLAIN_PROSE_NO_ACTION_DISCLOSURE.strip()
    samples = [
        "Answer" + PLAIN_PROSE_NO_ACTION_DISCLOSURE,
        "Answer\n" + core,
        "Answer\r\n   " + core,
        "Answer " + core,
    ]
    assert policy.PLAIN_PROSE_NO_ACTION_DISCLOSURE == PLAIN_PROSE_NO_ACTION_DISCLOSURE
    for value in samples:
        assert policy.strip_legacy_no_action_disclosure(value).strip() == "Answer"
        assert ConversationTurnEngine._strip_legacy_no_action_disclosure(value).strip() == "Answer"
    assert policy.strip_legacy_no_action_disclosure("A normal answer") == "A normal answer"
    sentinel = object()
    assert policy.strip_legacy_no_action_disclosure(sentinel) is sentinel


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, True),
        ({"discovery_complete": False}, False),
        ({"discovery_complete": 1}, False),
        ({"available_count": 1}, False),
        ({"returned_count": 1}, False),
        ({"omitted_count": 1}, False),
        ({"unresolved_discussion_count": 1}, False),
        ({"unresolved_task_proof_count": 1}, False),
        ({"candidate_discussion_ids": ("id",)}, False),
        ({"candidate_discussion_ids": "id"}, False),
        ({"candidate_discussion_ids": None}, False),
    ],
)
def test_empty_discovery_is_strict(overrides, expected):
    fields = dict(
        discovery_complete=True,
        available_count=0,
        returned_count=0,
        omitted_count=0,
        unresolved_discussion_count=0,
        unresolved_task_proof_count=0,
        candidate_discussion_ids=(),
    )
    fields.update(overrides)
    payload = SimpleNamespace(**fields)
    assert policy.is_empty_indexed_session_discovery(payload) is expected
    assert ConversationTurnEngine._is_empty_indexed_session_discovery(payload) is expected


def test_missing_discovery_is_not_empty():
    assert policy.is_empty_indexed_session_discovery(None) is False
    assert ConversationTurnEngine._is_empty_indexed_session_discovery(None) is False
