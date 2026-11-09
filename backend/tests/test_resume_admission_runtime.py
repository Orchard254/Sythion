"""Real-repository guarded task resume regressions.

These need Sythion's complete TaskRunner, ORM, API and dependency wiring and
are not runnable against the local synthetic-schema smoke harness.
"""
import json
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.schemas import TaskState
from backend.api.routes import get_task_resume_preflight, resume_task
from backend.models.database import Base, Task, TaskEvent
from backend.services.resume_admission import read_task_resume_admission
from backend.services.task_runner import TaskRunner


@pytest.fixture
def db():
    engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    Base.metadata.create_all(engine)
    s=sessionmaker(bind=engine)()
    s.add(Task(id='resume-me',original_request='Read-only information request',
               state=TaskState.INTERRUPTED.value,version=1))
    s.commit()
    yield s
    s.close()
    engine.dispose()


async def _leased_runner(db):
    runner=TaskRunner(db)
    assert await runner.acquire_lease()
    return runner


def _receipt(db,event_type='reasoning.started',job='j'):
    db.add(TaskEvent(task_id='resume-me',event_type=event_type,
                     event_data=json.dumps({'job_id':job,'purpose':'task_decision'}),
                     sequence_num=1))
    db.commit()


@pytest.mark.asyncio
async def test_clean_explicit_resume_transitions_using_existing_runner(db):
    runner=await _leased_runner(db)
    try:
        updated=await runner.transition_state(
            task_id='resume-me',new_state=TaskState.RUNNING,
            event_type='task.resumed',event_data={'reason':'explicit_user'})
        assert updated.state == 'running'
        assert any(event['event_type']=='task.resumed'
                   for event in runner.ledger.event_history('resume-me'))
    finally:
        await runner.release_lease()


@pytest.mark.asyncio
async def test_unresolved_reasoning_is_refused_at_transition_under_lock(db):
    runner=await _leased_runner(db)
    try:
        _receipt(db)
        with pytest.raises(RuntimeError,match='unresolved_reasoning_review_required'):
            await runner.transition_state('resume-me',TaskState.RUNNING,
                                          'task.resumed',{'reason':'explicit_user'})
        assert db.get(Task,'resume-me').state == 'interrupted'
        assert all(event['event_type']!='task.resumed'
                   for event in runner.ledger.event_history('resume-me'))
    finally:
        await runner.release_lease()


@pytest.mark.asyncio
async def test_new_uncertainty_after_clear_preview_blocks_transition(db):
    runner=await _leased_runner(db)
    try:
        assert read_task_resume_admission(db,'resume-me')['preflight_clear']
        _receipt(db)
        with pytest.raises(RuntimeError,match='Resume preflight refused'):
            await runner.transition_state('resume-me',TaskState.RUNNING,
                                          'task.execution_started',{})
        assert db.get(Task,'resume-me').state=='interrupted'
    finally:
        await runner.release_lease()


@pytest.mark.asyncio
async def test_uncertain_external_effect_blocks_resume_under_writer_lock(db):
    runner=await _leased_runner(db)
    try:
        db.get(Task,'resume-me').active_action_id='secret-action'
        db.commit()
        with pytest.raises(RuntimeError,match='external_effect_review_required'):
            await runner.transition_state('resume-me',TaskState.RUNNING,
                                          'task.resumed',{})
        assert db.get(Task,'resume-me').state=='interrupted'
    finally:
        await runner.release_lease()


@pytest.mark.asyncio
async def test_resume_http_refuses_before_launching_worker(db,monkeypatch):
    import backend.api.routes as routes
    _receipt(db)
    monkeypatch.setattr(routes,'get_tool_executor',
                        lambda: pytest.fail('Tool executor must not be requested'))
    with pytest.raises(HTTPException) as exc:
        await resume_task('resume-me',db=db)
    assert exc.value.status_code == 409
    assert exc.value.detail['code']=='unresolved_reasoning_review_required'
    assert db.get(Task,'resume-me').state == 'interrupted'


def test_read_only_preflight_route_is_not_execution_approval(db):
    info=get_task_resume_preflight('resume-me',db=db)
    assert info['task_id']=='resume-me'
    assert info['preflight_clear'] is True
    assert info['automatic_replay_allowed'] is False
    assert info['review']['status'] == 'preflight_clear_not_authorized'
    assert info['review']['authorization_issued'] is False
    assert info['review']['execution_started'] is False


def test_preflight_missing_task_is_404(db):
    with pytest.raises(HTTPException) as exc:
        get_task_resume_preflight('does-not-exist',db=db)
    assert exc.value.status_code == 404


def test_blocked_recovery_review_does_not_authorize_actions(db):
    _receipt(db)
    info = get_task_resume_preflight('resume-me', db=db)
    assert info['review']['status'] == 'review_required'
    assert info['review']['blocker'] == 'unresolved_reasoning_review_required'
    assert info['review']['verification_steps']
    assert info['review']['automatic_replay_allowed'] is False
    assert info['review']['uncertainty_cleared'] is False
    assert db.get(Task, 'resume-me').state == 'interrupted'


def test_recovery_preflight_includes_non_authorizing_evidence_revision(db):
    info = get_task_resume_preflight('resume-me', db=db)
    binder = info['review_binding']
    assert binder['binding_status'] == 'read_only_observation'
    assert binder['revision'].startswith('sha256:')
    assert binder['task_version'] == db.get(Task, 'resume-me').version
    assert binder['authenticated_human'] is False
    assert binder['authorization_issued'] is False
    assert binder['automatic_replay_allowed'] is False
    assert 'owner_nonce' not in str(binder)
    assert 'original_request' not in str(binder)


def test_preflight_revision_invalidates_when_task_version_changes(db):
    old = get_task_resume_preflight('resume-me', db=db)['review_binding']['revision']
    task_row = db.get(Task, 'resume-me')
    task_row.version += 1
    db.commit()
    current = get_task_resume_preflight('resume-me', db=db)['review_binding']['revision']
    assert old != current


@pytest.mark.asyncio
async def test_orphan_reasoning_terminal_blocks_real_runner_resume(db):
    runner = await _leased_runner(db)
    try:
        _receipt(db, event_type='reasoning.response_received', job='orphan')
        with pytest.raises(RuntimeError, match='corrupt_reasoning_receipt_review_required'):
            await runner.transition_state('resume-me', TaskState.RUNNING,
                                          'task.resumed', {'reason': 'explicit_user'})
        assert db.get(Task, 'resume-me').state == 'interrupted'
    finally:
        await runner.release_lease()


@pytest.mark.asyncio
async def test_unknown_reasoning_phase_blocks_real_runner_resume(db):
    runner = await _leased_runner(db)
    try:
        _receipt(db, event_type='reasoning.approved', job='forged')
        with pytest.raises(RuntimeError, match='corrupt_reasoning_receipt_review_required'):
            await runner.transition_state('resume-me', TaskState.RUNNING,
                                          'task.resumed', {'reason': 'explicit_user'})
        assert db.get(Task, 'resume-me').state == 'interrupted'
    finally:
        await runner.release_lease()


@pytest.mark.asyncio
async def test_orphan_receipt_blocks_resume_route_before_execution(db, monkeypatch):
    import backend.api.routes as routes
    _receipt(db, event_type='reasoning.response_received', job='orphan')
    monkeypatch.setattr(routes, 'get_tool_executor',
                        lambda: pytest.fail('No tool executor should be requested'))
    with pytest.raises(HTTPException) as exc:
        await resume_task('resume-me', db=db)
    assert exc.value.status_code == 409
    assert exc.value.detail['code'] == 'corrupt_reasoning_receipt_review_required'
    assert db.get(Task, 'resume-me').state == 'interrupted'
