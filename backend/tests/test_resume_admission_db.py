"""SQLite query smoke checks against disposable *synthetic* model definitions."""
from datetime import datetime, timedelta
import json
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models.database import Base, Task, TaskEvent
from backend.runtime.task_ownership import TaskOwnershipRow
from backend.services.resume_admission import read_task_resume_admission


@pytest.fixture
def db():
    engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session=sessionmaker(bind=engine)()
    session.add(Task(id='t',original_request='test',state='interrupted',version=1))
    session.commit()
    yield session
    session.close()
    engine.dispose()


def owner(db,status='RELEASED',uncertain=None,active=None,delta=60):
    db.add(TaskOwnershipRow(task_id='t',owner_nonce='secret-owner',worker_id='secret-worker',status=status,
                            expires_at=datetime.utcnow()+timedelta(seconds=delta),
                            active_action_id=active,uncertain_action_ids=uncertain))
    db.commit()


def event(db,kind,seq=1,data=None):
    db.add(TaskEvent(task_id='t',event_type=kind,sequence_num=seq,
                     event_data=json.dumps({'job_id':'reasoning-job','purpose':'task_decision'} if data is None else data)))
    db.commit()


def test_clean_legacy_interrupted_task_can_be_preflighted(db):
    a=read_task_resume_admission(db,'t')
    assert a['preflight_clear'] is True
    assert not a['automatic_replay_allowed']


def test_missing_task_rejected(db):
    with pytest.raises(LookupError):
        read_task_resume_admission(db,'missing')


def test_current_active_owner_blocks(db):
    owner(db,status='ACTIVE')
    assert read_task_resume_admission(db,'t')['reason']=='ownership_recovery_required'


def test_expired_active_owner_still_blocks(db):
    owner(db,status='ACTIVE',delta=-30)
    assert read_task_resume_admission(db,'t')['reason']=='ownership_recovery_required'


def test_released_owner_with_empty_uncertainty_is_clear(db):
    owner(db,uncertain='[]')
    assert read_task_resume_admission(db,'t')['preflight_clear']


@pytest.mark.parametrize('payload',['["never replay"]','null','garbled'])
def test_unknown_external_effect_is_not_replayed(db,payload):
    owner(db,uncertain=payload)
    result=read_task_resume_admission(db,'t')
    assert result['reason']=='external_effect_review_required'
    assert 'never replay' not in str(result)
    assert 'secret-owner' not in str(result)


def test_action_uncertain_event_blocks_even_without_owner(db):
    event(db,'action.uncertain',data={'action_id':'secret'})
    assert read_task_resume_admission(db,'t')['reason']=='external_effect_review_required'


def test_task_action_flag_blocks_without_owner(db):
    t=db.get(Task,'t'); t.active_action_id='secret';db.commit()
    assert read_task_resume_admission(db,'t')['reason']=='external_effect_review_required'


def test_pending_action_blocks_even_if_denied(db):
    t=db.get(Task,'t'); t.pending_action='{"status":"approved"}';db.commit()
    assert read_task_resume_admission(db,'t')['reason']=='pending_action_review_required'


def test_unresolved_reasoning_receipt_blocks(db):
    event(db,'reasoning.started')
    assert read_task_resume_admission(db,'t')['reason']=='unresolved_reasoning_review_required'


def test_settled_reasoning_receipt_allows_preflight(db):
    event(db,'reasoning.started',1)
    event(db,'reasoning.timed_out',2)
    assert read_task_resume_admission(db,'t')['preflight_clear']


def test_invalid_started_receipt_blocks(db):
    event(db,'reasoning.started',1,{'job_id':''})
    assert read_task_resume_admission(db,'t')['reason']=='corrupt_reasoning_receipt_review_required'


def test_unexpected_terminal_task_refuses_preflight(db):
    t=db.get(Task,'t');t.state='completed';db.commit()
    assert read_task_resume_admission(db,'t')['reason']=='task_not_resumable'


def test_read_does_not_write_any_rows(db):
    owner(db)
    event(db,'reasoning.started')
    assert not db.dirty
    before=(db.query(Task).count(),db.query(TaskEvent).count(),db.query(TaskOwnershipRow).count())
    read_task_resume_admission(db,'t')
    after=(db.query(Task).count(),db.query(TaskEvent).count(),db.query(TaskOwnershipRow).count())
    assert before==after


def test_mutating_tool_receipt_requires_review_even_when_successful(db):
    event(db,'tool.executed',data={'mutating':True,'success':True,'output':'secret output'})
    result=read_task_resume_admission(db,'t')
    assert result['reason']=='prior_mutating_effect_review_required'
    assert 'secret output' not in str(result)


def test_explicit_read_only_tool_receipt_does_not_block_clean_resume(db):
    event(db,'tool.executed',data={'mutating':False,'success':True})
    assert read_task_resume_admission(db,'t')['preflight_clear']


def test_unlabeled_tool_receipt_is_conservatively_treated_as_mutation(db):
    event(db,'tool.executed',data={'success':True})
    assert read_task_resume_admission(db,'t')['reason']=='prior_mutating_effect_review_required'


def test_action_started_requires_review_even_with_otherwise_clean_task(db):
    event(db,'action.started',data={'action_id':'opaque'})
    assert read_task_resume_admission(db,'t')['reason']=='prior_mutating_effect_review_required'


def test_malformed_tool_receipt_never_bypasses_mutation_check(db):
    event(db,'tool.executed',data=['unexpected', 'shape'])
    assert read_task_resume_admission(db,'t')['reason']=='prior_mutating_effect_review_required'
