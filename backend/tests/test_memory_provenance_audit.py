"""
Adversarial tests for memory provenance audit.

Fake/temp-DB only. Covers:
- consolidation model labels (e.g. verified_tool_result) cannot mint
  TOOL_EVIDENCE/VERIFIED_TOOL without genuine independently validated evidence
- changed values do not inherit prior verified badge from MemoryService.update
- same-value (reinforcement) updates preserve a legitimate verified badge
- write authority + contradiction/supersession behavior

Characterization note (not endorsement): these tests pin the current
fail-closed provenance behavior. They do NOT endorse TOOL_EVIDENCE or
USER_EXPLICIT as blanket trust sources. Raw evidence authority remains
unresolved; see the proposal below.

UNRESOLVED EVIDENCE-BOUNDARY PROPOSAL (fail-closed; not implemented here):
1. Route ALL memory writes through MemoryService; no direct ORM/session
   writes from tools (MemoryStoreTool/MemoryForgetTool are read-only
   bypass surfaces today; see report for concrete paths/lines).
2. Require independently validated evidence (verified tool output + durable
   row re-query) before any VERIFIED_* status; labels alone never mint.
3. USER_EXPLICIT items remain UNVERIFIED unless captured with a signed/
   immutable provenance ref; remove label-only trust.
No new trusted path was enabled in this change.
"""
import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models.database import Base, MemoryEdge, MemoryEntry
from backend.services.memory_consolidation import consolidate_memory
from backend.services.memory_service import (
    MemoryAuthorityError,
    MemoryService,
)


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture
def svc(db_session):
    return MemoryService(db_session)


class _Turn:
    def __init__(self, role, summary):
        self.role = role
        self.summary = summary


def _adapter(content):
    from unittest.mock import AsyncMock

    adapter = AsyncMock()
    adapter.chat.return_value = {"message": {"content": content}}
    return adapter


def test_consolidation_verified_tool_result_does_not_mint_verified_tool(db_session):
    op_json = (
        '{"ops": [{"op": "add", "key": "app_running", '
        '"value": "Chrome is running", "category": "profile", '
        '"confidence": "high", "source": "verified_tool_result", '
        '"reason": "model claims a tool verified this"}]}'
    )
    turn = _Turn("user", "what is running?")
    applied = asyncio.run(consolidate_memory(
        db_session, [turn], [], _adapter(op_json)))
    assert len(applied) == 1
    row = db_session.query(MemoryEntry).filter_by(key="app_running").one()
    assert row.source_type != "TOOL_EVIDENCE"
    assert row.verification_status != "VERIFIED_TOOL"
    # Treated as an unverified consolidation candidate.
    assert row.source_type == "CONSOLIDATION"
    assert row.verification_status == "UNVERIFIED"


def test_update_same_value_preserves_verified_badge(db_session, svc):
    original = svc.create(
        key="verified_fact", value="sky is blue", kind="FACT",
        source_type="TOOL_EVIDENCE")
    assert original.verification_status == "VERIFIED_TOOL"

    reinforced = svc.update(
        original.id, new_value="sky is blue",
        source_type="MODEL_INFERENCE")
    assert reinforced.verification_status == "VERIFIED_TOOL"
    old = db_session.query(MemoryEntry).filter_by(id=original.id).one()
    assert old.status == "SUPERSEDED"
    assert old.superseded_by == reinforced.id


def test_consolidation_changed_value_verified_tool_result_stays_unverified(db_session, svc):
    original = svc.create(
        key="verified_fact", value="old verified", kind="FACT",
        source_type="TOOL_EVIDENCE")
    assert original.verification_status == "VERIFIED_TOOL"

    op_json = (
        '{"ops": [{"op": "update", "key": "verified_fact", '
        '"value": "new unverified claim", "category": "profile", '
        '"confidence": "high", "source": "verified_tool_result", '
        '"reason": "model claims update"}]}'
    )
    turn = _Turn("user", "what changed?")
    applied = asyncio.run(consolidate_memory(
        db_session, [turn], [], _adapter(op_json)))
    assert len(applied) == 1

    successor = db_session.query(MemoryEntry).filter_by(status="ACTIVE").one()
    assert successor.value == "new unverified claim"
    assert successor.verification_status == "UNVERIFIED"
    assert successor.source_type != "TOOL_EVIDENCE"

    history = db_session.query(MemoryEntry).filter_by(id=original.id).one()
    assert history.status == "SUPERSEDED"
    assert history.superseded_by == successor.id
    # Legitimate verified history is preserved, not silently erased.
    assert history.verification_status == "VERIFIED_TOOL"


def test_consolidation_source_label_matrix_cannot_mint_verified(db_session):
    labels = [
        "verified_tool_result",
        "VERIFIED_TOOL_RESULT",
        " verified_tool_result ",
        "Verified_Tool_Result",
        "tool_evidence",
        "TOOL_EVIDENCE",
        "made_up",
        "user_explicit",
    ]
    ops = []
    for i, label in enumerate(labels):
        ops.append({
            "op": "add", "key": f"matrix_{i}", "value": "v",
            "category": "profile", "confidence": "high",
            "source": label, "reason": "probe",
        })
    import json as _json
    op_json = _json.dumps({"ops": ops})
    turn = _Turn("user", "probe")
    applied = asyncio.run(consolidate_memory(
        db_session, [turn], [], _adapter(op_json)))
    assert len(applied) == len(labels)
    rows = db_session.query(MemoryEntry).all()
    assert len(rows) == len(labels)
    for row in rows:
        # Source labels (case, whitespace, arbitrary) can never mint
        # verified tool evidence through consolidation.
        assert row.source_type != "TOOL_EVIDENCE"
        assert row.verification_status != "VERIFIED_TOOL"
        assert row.verification_status == "UNVERIFIED"


def test_exact_string_preservation_boundary_does_not_inherit_badge(db_session, svc):
    original = svc.create(
        key="exact_fact", value="sky is blue", kind="FACT",
        source_type="TOOL_EVIDENCE")
    assert original.verification_status == "VERIFIED_TOOL"

    # A trailing-space difference is a different value under exact comparison;
    # it must not inherit the verified badge.
    changed = svc.update(
        original.id, new_value="sky is blue ",
        source_type="MODEL_INFERENCE")
    assert changed.verification_status == "UNVERIFIED"
    history = db_session.query(MemoryEntry).filter_by(id=original.id).one()
    assert history.status == "SUPERSEDED"
    assert history.verification_status == "VERIFIED_TOOL"


# ---------------------------------------------------------------------------
# Follow-up: MemoryService.update source-type normalization regression coverage
# (characterization only; not evidence endorsement)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("source", ["MODEL_INFERENCE", "model_inference", "Model_Inference"])
def test_update_model_inference_case_normalization_disallowed_kind(db_session, svc, source):
    # Canonical/lower/mixed MODEL_INFERENCE follows the same disallowed-kind
    # policy. A model source may never change a memory to FACT.
    original = svc.create(
        key="model_inference_policy_fact", value="base", kind="ASSUMPTION",
        source_type="MODEL_INFERENCE")
    with pytest.raises(MemoryAuthorityError):
        svc.update(
            original.id, new_value="changed", new_kind="FACT",
            source_type=source)


@pytest.mark.parametrize("source", ["MODEL_INFERENCE", "model_inference", "Model_Inference"])
def test_update_model_inference_allowed_stays_unverified_canonical(db_session, svc, source):
    # Allowed MODEL_INFERENCE updates remain UNVERIFIED and canonical.
    original = svc.create(
        key="model_inference_allowed", value="base", kind="ASSUMPTION",
        source_type="MODEL_INFERENCE")
    updated = svc.update(
        original.id, new_value="changed", new_kind="ASSUMPTION",
        source_type=source)
    assert updated.verification_status == "UNVERIFIED"
    assert updated.source_type == "MODEL_INFERENCE"
    history = db_session.query(MemoryEntry).filter_by(id=original.id).one()
    assert history.status == "SUPERSEDED"
    assert history.verification_status == "UNVERIFIED"


@pytest.mark.parametrize("source", ["TOOL_EVIDENCE", "tool_evidence", "Tool_Evidence"])
def test_update_tool_evidence_case_normalization_architectural_characterization(
        db_session, svc, source):
    # Lower/mixed TOOL_EVIDENCE currently matches uppercase. This is an
    # architectural characterization only; it is NOT an authenticated-evidence
    # endorsement. No new trusted path was added.
    original = svc.create(
        key="tool_evidence_case_fact", value="blue", kind="FACT",
        source_type="TOOL_EVIDENCE")
    updated = svc.update(
        original.id, new_value="blue", new_kind="FACT",
        source_type=source)
    assert updated.source_type == "TOOL_EVIDENCE"
    assert updated.verification_status == "VERIFIED_TOOL"
    history = db_session.query(MemoryEntry).filter_by(id=original.id).one()
    assert history.status == "SUPERSEDED"
    assert history.verification_status == "VERIFIED_TOOL"


@pytest.mark.parametrize("source", [
    " tool_evidence ", "TOOL_EVIDENCE ", "foo", "bar", "",
])
def test_update_spaced_arbitrary_labels_cannot_mint_verification(db_session, svc, source):
    # Spaced/arbitrary labels are not canonical TOOL_EVIDENCE and cannot mint
    # VERIFIED_TOOL status through update().
    original = svc.create(
        key="label_boundary_fact", value="stable", kind="FACT",
        source_type="TOOL_EVIDENCE")
    updated = svc.update(
        original.id, new_value="changed", new_kind="FACT",
        source_type=source)
    assert updated.source_type != "TOOL_EVIDENCE"
    assert updated.verification_status != "VERIFIED_TOOL"
    assert updated.verification_status == "UNVERIFIED"


def test_update_changed_value_does_not_inherit_verified_badge(db_session, svc):
    original = svc.create(
        key="verified_fact", value="sky is blue", kind="FACT",
        source_type="TOOL_EVIDENCE")
    changed = svc.update(
        original.id, new_value="sky is green",
        source_type="MODEL_INFERENCE")
    assert changed.value == "sky is green"
    assert changed.verification_status == "UNVERIFIED"
    # Legitimate verified history is retained, not silently erased.
    old = db_session.query(MemoryEntry).filter_by(id=original.id).one()
    assert old.status == "SUPERSEDED"
    assert old.verification_status == "VERIFIED_TOOL"


def test_update_changed_value_does_not_inherit_via_create_path(db_session, svc):
    svc.create(key="shared", value="old verified", kind="FACT",
               source_type="TOOL_EVIDENCE")
    row = db_session.query(MemoryEntry).filter_by(key="shared", status="ACTIVE").one()
    assert row.verification_status == "VERIFIED_TOOL"
    # Same-key create routes through update() with a changed value.
    successor = svc.create(key="shared", value="new unverified claim",
                          kind="ASSUMPTION", source_type="MODEL_INFERENCE",
                          confidence=0.9)
    assert successor.value == "new unverified claim"
    assert successor.verification_status == "UNVERIFIED"


def test_authority_model_inference_cannot_mint_verified_fact(db_session, svc):
    with pytest.raises(MemoryAuthorityError):
        svc.create(key="x", value="claims", kind="FACT",
                   source_type="MODEL_INFERENCE")


def test_authority_unknown_source_limited(db_session, svc):
    with pytest.raises(MemoryAuthorityError):
        svc.create(key="mystery", value="v", kind="PREFERENCE",
                   source_type="UNKNOWN")


def test_contradiction_stronger_newer_supersedes_weaker_old(db_session, svc):
    weak = svc.create(key="code_editor", value="Notepad++", kind="FACT",
                      source_type="USER_CONVERSATION")
    strong = svc.create(key="code_editor_pro", value="VS Code", kind="FACT",
                        source_type="TOOL_EVIDENCE")
    weak_fresh = db_session.query(MemoryEntry).filter_by(id=weak.id).one()
    strong_fresh = db_session.query(MemoryEntry).filter_by(id=strong.id).one()
    assert weak_fresh.status == "SUPERSEDED"
    assert weak_fresh.superseded_by == strong.id
    assert strong_fresh.status == "ACTIVE"
    edge = db_session.query(MemoryEdge).filter_by(relation="contradicts").first()
    assert edge is not None


def test_same_contradiction_keeps_both_active_when_not_stronger(db_session, svc):
    strong = svc.create(key="editor_pref", value="Notepad++", kind="PREFERENCE",
                        source_type="USER_EXPLICIT")
    weak = svc.create(key="preferred_editor", value="VS Code", kind="PREFERENCE",
                      source_type="USER_CONVERSATION")
    # USER_EXPLICIT outranks USER_CONVERSATION, so neither is silently destroyed.
    strong_fresh = db_session.query(MemoryEntry).filter_by(id=strong.id).one()
    weak_fresh = db_session.query(MemoryEntry).filter_by(id=weak.id).one()
    assert strong_fresh.status == "ACTIVE"
    assert weak_fresh.status == "ACTIVE"
    assert db_session.query(MemoryEdge).filter_by(relation="contradicts").count() >= 1
