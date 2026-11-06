"""Synthetic checks for the offline approval foundation, not real consent."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models.database import Base, Task
from backend.runtime.permission_proposal import SUPPORTED_ACTION
from backend.services.consequential_approval import ConsequentialApprovalService


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'synthetic.db'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(Task(id="synthetic-task", original_request="synthetic", state="running", version=3))
    db.commit()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def decide(session, authenticator):
    service = ConsequentialApprovalService(session, authenticator=authenticator)
    proposal = service.stage_proposal(
        task_id="synthetic-task", action_id="action", tool_name=SUPPORTED_ACTION,
        task_version=3, params={"path": "C:/synthetic/demo.txt", "content": "synthetic", "overwrite": False},
    )
    session.commit()
    return service.approve(task_id="synthetic-task", action_id="action", proposal=proposal, decision_id="decision")


@pytest.mark.parametrize("value", [None, False, 0, 1, "yes", {}, {"approved": True}])
def test_only_literal_true_approves(session, value):
    result = decide(session, lambda context: value)
    assert result.allowed is False
    assert result.approved is False
    assert result.receipt.status == "denied"


def test_authenticator_exception_denies(session):
    def fail(context):
        raise RuntimeError("synthetic failure")

    result = decide(session, fail)
    assert result.allowed is False
    assert result.receipt.status == "denied"


def test_unawaited_authenticator_denies(session):
    async def authenticate(context):
        return True

    result = decide(session, authenticate)
    assert result.allowed is False
    assert result.receipt.status == "denied"


def test_literal_true_can_approve_offline_foundation(session):
    result = decide(session, lambda context: True)
    assert result.allowed is True
    assert result.receipt.status == "approved"
