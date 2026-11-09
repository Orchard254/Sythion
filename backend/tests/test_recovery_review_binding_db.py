"""Synthetic SQLite tests for the actual ORM query logic (not full repo)."""
import json
from datetime import datetime, timedelta
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models.database import Base, Task, TaskEvent
from backend.runtime.task_ownership import TaskOwnershipRow
from backend.services.resume_admission import read_task_resume_admission
from backend.services.recovery_review_binding import read_recovery_review_binding


@pytest.fixture
def db():
    engine = create_engine('sqlite://', connect_args={'check_same_thread':False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(Task(id='t', original_request='secret private request', state='interrupted', version=1))
    session.commit()
    yield session
    session.close()
    engine.dispose()


def binding(db):
    return read_recovery_review_binding(db, 't', read_task_resume_admission(db, 't'))


def test_clean_revision_has_no_authority_or_task_text(db):
    snapshot = binding(db)
    assert snapshot['revision'].startswith('sha256:')
    assert snapshot['last_event_sequence'] == 0
    assert snapshot['binding_status'] == 'read_only_observation'
    assert snapshot['authorization_issued'] is False
    assert snapshot['authenticated_human'] is False
    assert 'secret private request' not in repr(snapshot)


def test_receipt_append_changes_revision_without_exposing_content(db):
    old = binding(db)
    db.add(TaskEvent(task_id='t', sequence_num=7, event_type='reasoning.started',
                     event_data=json.dumps({'job_id':'private-call', 'secret':'private-data'})))
    db.commit()
    new = binding(db)
    assert new['revision'] != old['revision']
    assert new['last_event_sequence'] == 7
    assert 'private-data' not in repr(new)
    assert 'private-call' not in repr(new)


def test_task_update_and_version_change_invalidate_binding(db):
    old = binding(db)
    task = db.query(Task).filter_by(id='t').one()
    task.version = 2
    task.updated_at = datetime.utcnow() + timedelta(days=1)
    db.commit()
    assert binding(db)['revision'] != old['revision']


def test_owner_fence_and_status_changes_invalidate_binding(db):
    row = TaskOwnershipRow(task_id='t', owner_nonce='very-secret-nonce', worker_id='worker-secret',
        status='RELEASED', expires_at=datetime.utcnow()+timedelta(minutes=5),
        fencing_token=3, version=2, uncertain_action_ids='[]')
    db.add(row)
    db.commit()
    a = binding(db)
    row.version = 3
    row.fencing_token = 4
    db.commit()
    b = binding(db)
    assert b['revision'] != a['revision']
    assert 'very-secret-nonce' not in repr(b)
    assert 'worker-secret' not in repr(b)


def test_new_pending_action_invalidate_revision_and_preflight(db):
    old = binding(db)
    task = db.get(Task, 't')
    task.pending_action = '{"permission":"do not trust"}'
    db.commit()
    new = binding(db)
    assert new['revision'] != old['revision']
    assert 'do not trust' not in repr(new)
    assert read_task_resume_admission(db,'t')['preflight_clear'] is False


def test_missing_task_refused_without_exposing_profile(db):
    with pytest.raises(LookupError):
        read_recovery_review_binding(db, 'missing', {'task_state':'interrupted'})


def test_bad_preflight_state_refused(db):
    with pytest.raises(ValueError, match='stale task state'):
        read_recovery_review_binding(db, 't', {
            'task_state':'running','reason':'clear_for_explicit_resume',
            'owner_lease':'missing','unresolved_reasoning_calls':0,'preflight_clear':True})


def test_read_only_does_not_add_or_modify_records(db):
    before = (db.query(Task).count(), db.query(TaskEvent).count(), db.query(TaskOwnershipRow).count())
    assert not db.dirty
    binding(db)
    assert before == (db.query(Task).count(), db.query(TaskEvent).count(), db.query(TaskOwnershipRow).count())
    assert not db.dirty


def test_caller_pending_writes_refused_without_autoflush(db):
    preflight = read_task_resume_admission(db, 't')
    db.get(Task, 't').pending_action = 'not yet committed'
    assert db.dirty
    with pytest.raises(RuntimeError, match='clean session'):
        read_recovery_review_binding(db, 't', preflight)
    assert db.query(Task).filter_by(id='t').one().pending_action == 'not yet committed'
    db.rollback()
    assert db.get(Task, 't').pending_action is None
