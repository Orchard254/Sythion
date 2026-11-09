"""Synthetic SQLite integration for future host evidence; no OS verification.

The fake host signer in these tests is intentionally NOT a native attestor.
The result cannot authorize task resume or clear any action uncertainty.
"""
from datetime import datetime
import hashlib
import hmac

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models.database import Base, Task
from backend.runtime.task_ownership import TaskOwnershipRow  # register owned table in the synthetic metadata
from backend.services.recovery_native_challenge import NativePresenceChallengeBroker
from backend.services.recovery_review_binding import read_recovery_review_binding
from backend.services.resume_admission import read_task_resume_admission

KEY=b"S"*32


@pytest.fixture
def db():
    engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db=sessionmaker(bind=engine)()
    db.add(Task(id='task-1',original_request='Confidential work',state='interrupted',version=1))
    db.commit()
    yield db
    db.close()
    engine.dispose()


def current(db):
    return read_recovery_review_binding(db,'task-1', read_task_resume_admission(db,'task-1'))['revision']


def host_signature(challenge):
    message=NativePresenceChallengeBroker._canonical_message(challenge.challenge_id,
                                                               challenge.task_id,challenge.evidence_revision)
    return hmac.new(KEY,message,hashlib.sha256).hexdigest()


def test_host_message_bound_to_latest_server_observation(db):
    broker=NativePresenceChallengeBroker(KEY)
    revision=current(db)
    challenge=broker.issue('task-1',revision)
    obs=broker.verify_host_assertion(challenge_id=challenge.challenge_id,task_id='task-1',
                                     evidence_revision=revision,current_evidence_revision=current(db),
                                     signature_hex=host_signature(challenge))
    assert obs.host_message_authenticated
    assert obs.authenticated_human is False
    assert obs.authorization_issued is False
    assert obs.recovery_cleared is False
    assert db.get(Task,'task-1').state == 'interrupted'


def test_task_revision_change_invalidates_outstanding_native_challenge(db):
    broker=NativePresenceChallengeBroker(KEY)
    revision=current(db)
    challenge=broker.issue('task-1',revision)
    row=db.get(Task,'task-1')
    row.version += 1
    row.updated_at=datetime.utcnow()
    db.commit()
    assert current(db) != revision
    with pytest.raises(PermissionError,match='revision changed'):
        broker.verify_host_assertion(challenge_id=challenge.challenge_id,task_id='task-1',
                                     evidence_revision=revision,current_evidence_revision=current(db),
                                     signature_hex=host_signature(challenge))


def test_temporary_approval_claim_does_not_authorize_or_change_task(db):
    broker=NativePresenceChallengeBroker(KEY)
    revision=current(db)
    challenge=broker.issue('task-1',revision)
    with pytest.raises(TypeError):
        broker.verify_host_assertion(challenge_id=challenge.challenge_id,task_id='task-1',
                                     evidence_revision=revision,current_evidence_revision=revision,
                                     signature_hex=host_signature(challenge),approved=True)
    assert db.get(Task,'task-1').state == 'interrupted'


def test_missing_host_key_is_unavailable_even_with_valid_server_snapshot(db):
    broker=NativePresenceChallengeBroker()
    with pytest.raises(PermissionError, match='unavailable'):
        broker.issue('task-1',current(db))
