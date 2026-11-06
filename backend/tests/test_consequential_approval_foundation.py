"""Bounded offline foundation tests for real consequential approvals."""

import tempfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models.database import Base, Task
from backend.runtime.permission_proposal import SUPPORTED_ACTION


@pytest.fixture
def db_session(tmp_path):
    db_path = tmp_path / "ca.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(Task(id="task-1", original_request="x", state="running", version=3))
    session.commit()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _params():
    return {"path": "C:/tmp/demo.txt", "content": "hello", "overwrite": False}


def test_default_authenticator_denies_and_no_dispatch(db_session):
    from backend.services.consequential_approval import ConsequentialApprovalService

    svc = ConsequentialApprovalService(db_session)
    prop = svc.stage_proposal(task_id="task-1", action_id="a1", tool_name=SUPPORTED_ACTION,
                             task_version=3, params=_params())
    db_session.commit()
    result = svc.approve(task_id="task-1", action_id="a1", proposal=prop, decision_id="d1")
    assert result.allowed is False
    assert result.approved is False
    assert "default deny" in result.authenticator_reason
    assert result.receipt is not None
    assert result.receipt.status == "denied"


def test_injected_authenticator_can_approve_exact_binding(db_session):
    from backend.services.consequential_approval import ConsequentialApprovalService

    def auth(ctx):
        assert ctx["task_id"] == "task-1"
        assert ctx["action_id"] == "a2"
        return True

    svc = ConsequentialApprovalService(db_session, authenticator=auth)
    prop = svc.stage_proposal(task_id="task-1", action_id="a2", tool_name=SUPPORTED_ACTION,
                             task_version=3, params=_params())
    db_session.commit()
    result = svc.approve(task_id="task-1", action_id="a2", proposal=prop, decision_id="d2")
    assert result.allowed is True
    assert result.approved is True
    verify = svc.verify(task_id="task-1", action_id="a2", proposal=prop, decision_id="d2")
    assert verify.allowed is True


def test_wrong_task_version_fails(db_session):
    from backend.services.consequential_approval import ConsequentialApprovalService

    def auth(ctx):
        return True

    svc = ConsequentialApprovalService(db_session, authenticator=auth)
    prop = svc.stage_proposal(task_id="task-1", action_id="av", tool_name=SUPPORTED_ACTION,
                             task_version=3, params=_params())
    db_session.commit()
    # mutate task version out from under the proposal
    task = db_session.query(Task).filter_by(id="task-1").first()
    task.version = 4
    db_session.commit()
    with pytest.raises(ValueError):
        svc.approve(task_id="task-1", action_id="av", proposal=prop, task_version=3, decision_id="dv")
    task.version = 3
    db_session.commit()


def test_cancelled_task_preserved_not_approved(db_session):
    from backend.services.consequential_approval import ConsequentialApprovalService

    def auth(ctx):
        return True

    db_session.query(Task).filter_by(id="task-1").update({"state": "cancelled"})
    db_session.commit()
    svc = ConsequentialApprovalService(db_session, authenticator=auth)
    with pytest.raises(ValueError):
        svc.stage_proposal(task_id="task-1", action_id="ac", tool_name=SUPPORTED_ACTION,
                          task_version=3, params=_params())
    # no data mutation: task still cancelled
    task = db_session.query(Task).filter_by(id="task-1").first()
    assert task.state == "cancelled"


def test_injected_authenticator_default_deny_alias(db_session):
    from backend.services.consequential_approval import ConsequentialApprovalService

    # auth keyword alias
    svc = ConsequentialApprovalService(db_session, auth=lambda ctx: True)
    prop = svc.stage_proposal(task_id="task-1", action_id="aa", tool_name=SUPPORTED_ACTION,
                             task_version=3, params=_params())
    db_session.commit()
    result = svc.approve(task_id="task-1", action_id="aa", proposal=prop, decision_id="da")
    assert result.allowed is True


def test_replay_is_refused(db_session):
    from backend.services.consequential_approval import ConsequentialApprovalService

    def auth(ctx):
        return True

    svc = ConsequentialApprovalService(db_session, authenticator=auth)
    prop = svc.stage_proposal(task_id="task-1", action_id="ar", tool_name=SUPPORTED_ACTION,
                             task_version=3, params=_params())
    db_session.commit()
    res1 = svc.approve(task_id="task-1", action_id="ar", proposal=prop, decision_id="dr1")
    assert res1.allowed is True
    # Now task has no active action; verifying same receipt remains valid offline,
    # but attempting to consume it after an active action starts must fail closed.
    task = db_session.query(Task).filter_by(id="task-1").first()
    task.active_action_id = "ar"
    db_session.commit()
    vr = svc.verify(task_id="task-1", action_id="ar", proposal=prop, decision_id="dr1")
    assert vr.allowed is False
    assert "replay" in vr.reason or "already active" in vr.reason

def test_transaction_is_not_committed_by_service(db_session):
    from backend.services.consequential_approval import ConsequentialApprovalService

    def auth(ctx):
        return True

    svc = ConsequentialApprovalService(db_session, authenticator=auth)
    prop = svc.stage_proposal(task_id="task-1", action_id="at", tool_name=SUPPORTED_ACTION,
                             task_version=3, params=_params())
    # no explicit commit; rollback should erase
    db_session.rollback()
    from backend.runtime.permission_proposal import PermissionProposalManager
    mgr = PermissionProposalManager(db_session)
    fetched = mgr.get(task_id="task-1", action_id="at")
    assert fetched is None


def _approve_receipt(db_session, action_id, decision_id):
    from backend.services.consequential_approval import ConsequentialApprovalService
    from backend.runtime.permission_proposal import SUPPORTED_ACTION

    svc = ConsequentialApprovalService(db_session, authenticator=lambda ctx: True)
    prop = svc.stage_proposal(
        task_id="task-1", action_id=action_id, tool_name=SUPPORTED_ACTION,
        task_version=3, params=_params(),
    )
    db_session.commit()
    result = svc.approve(
        task_id="task-1", action_id=action_id, proposal=prop, decision_id=decision_id,
    )
    assert result.allowed is True
    assert result.approved is True
    return svc, prop


def test_verify_rejects_mismatched_source_task_id(db_session):
    from sqlalchemy import text

    svc, prop = _approve_receipt(db_session, "asm", "dsm")
    # Tamper the receipt so source_task_id disagrees with the verified task.
    db_session.execute(
        text("UPDATE permission_decision_receipts SET source_task_id='evil-task' "
             "WHERE decision_id='dsm'")
    )
    db_session.commit()

    verify = svc.verify(task_id="task-1", action_id="asm", proposal=prop, decision_id="dsm")
    assert verify.allowed is False
    assert verify.safeguards.get("binding") is False


def test_verify_rejects_mismatched_task_id(db_session):
    from sqlalchemy import text

    svc, prop = _approve_receipt(db_session, "atm", "dtm")
    # Tamper the receipt so task_id disagrees with the verified task.
    db_session.execute(
        text("UPDATE permission_decision_receipts SET task_id='evil-task' "
             "WHERE decision_id='dtm'")
    )
    db_session.commit()

    verify = svc.verify(task_id="task-1", action_id="atm", proposal=prop, decision_id="dtm")
    assert verify.allowed is False
    assert verify.safeguards.get("binding") is False


def test_verify_accepts_valid_receipt_with_matching_task_ids(db_session):
    from sqlalchemy import text

    svc, prop = _approve_receipt(db_session, "avm", "dvm")
    # Ensure both receipt task identifiers legitimately match task-1.
    row = db_session.execute(
        text("SELECT task_id, source_task_id FROM permission_decision_receipts "
             "WHERE decision_id='dvm'")
    ).first()
    assert row is not None
    assert (row[0] is None or row[0] == "task-1")
    assert (row[1] is None or row[1] == "task-1")

    verify = svc.verify(task_id="task-1", action_id="avm", proposal=prop, decision_id="dvm")
    assert verify.allowed is True
    assert verify.safeguards.get("binding") is True


def test_verify_rejects_receipt_with_both_task_ids_null(db_session):
    from sqlalchemy import text

    svc, prop = _approve_receipt(db_session, "abn", "dbn")
    db_session.execute(
        text("UPDATE permission_decision_receipts "
             "SET task_id = NULL, source_task_id = NULL "
             "WHERE decision_id = 'dbn'")
    )
    db_session.commit()

    verify = svc.verify(task_id="task-1", action_id="abn", proposal=prop, decision_id="dbn")
    assert verify.allowed is False
    assert verify.safeguards.get("binding") is False


def test_verify_accepts_receipt_with_null_task_id_when_source_matches(db_session):
    from sqlalchemy import text

    svc, prop = _approve_receipt(db_session, "atn", "dtn")
    db_session.execute(
        text("UPDATE permission_decision_receipts SET task_id = NULL "
             "WHERE decision_id = 'dtn'")
    )
    db_session.commit()

    verify = svc.verify(task_id="task-1", action_id="atn", proposal=prop, decision_id="dtn")
    assert verify.allowed is True
    assert verify.safeguards.get("binding") is True


def test_verify_accepts_receipt_with_null_source_task_id_when_task_matches(db_session):
    from sqlalchemy import text

    svc, prop = _approve_receipt(db_session, "asn", "dsn")
    db_session.execute(
        text("UPDATE permission_decision_receipts SET source_task_id = NULL "
             "WHERE decision_id = 'dsn'")
    )
    db_session.commit()

    verify = svc.verify(task_id="task-1", action_id="asn", proposal=prop, decision_id="dsn")
    assert verify.allowed is True
    assert verify.safeguards.get("binding") is True


def test_verify_does_not_fallback_for_unknown_decision_id(db_session):
    svc, prop = _approve_receipt(db_session, "auf", "duf")

    verify = svc.verify(
        task_id="task-1", action_id="auf", proposal=prop, decision_id="does-not-exist",
    )
    assert verify.allowed is False
    assert verify.receipt is None
    assert verify.safeguards.get("receipt") is False