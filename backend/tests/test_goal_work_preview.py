from datetime import datetime, timedelta
from copy import deepcopy
import pytest
from backend.services.goal_work_preview import project_goal_work

NOW = datetime(2026, 11, 9, 14)

def s(i, key, status='ready', deps=None, priority=3):
    return dict(id=i, step_key=key, description='Step '+key, status=status,
                priority=priority, step_order=i, depends_on=[] if deps is None else deps)

def preview(steps, **kw):
    return project_goal_work('active', steps, has_active_plan=True, now=NOW, **kw)

def test_readiness_without_writing_blocked_state():
    steps=[s(1,'a','completed'),s(2,'b','blocked',['a'])]
    before=deepcopy(steps)
    out=preview(steps)
    assert out['runnable'] and out['next_step']['key']=='b'
    assert out['next_step']['would_unlock']
    assert steps==before and out['read_only']
    assert not out['execution_started'] and not out['authorization_issued']

def test_dependencies_really_block_when_not_complete():
    out=preview([s(1,'a','running'),s(2,'b','blocked',['a'])])
    assert out['state']=='step_in_progress' and out['next_step'] is None
    assert out['blocked_steps']==[{'step_key':'b','waiting_for':['a']}]

def test_stale_ready_dependent_step_not_runnable():
    out=preview([s(1,'a','failed'),s(2,'b','ready',['a'])])
    assert not out['runnable'] and out['state']=='failed_step_needs_review'

def test_ready_order_matches_planner_policy():
    out=preview([s(1,'low',priority=4),s(2,'high',priority=1),s(3,'mid',priority=3)])
    assert out['next_step']['key']=='high'

def test_backoff_exposed_without_starting_work():
    out=preview([s(1,'a')],backoff_until=NOW+timedelta(minutes=10))
    assert out['state']=='goal_backoff' and out['structurally_ready']
    assert not out['runnable']
    assert preview([s(1,'a')],backoff_until=NOW+timedelta(minutes=10),respect_backoff=False)['runnable']

def test_goal_status_gates_work():
    for status in ('paused','completed','failed','abandoned'):
        out=project_goal_work(status,[s(1,'a')],has_active_plan=True,now=NOW)
        assert not out['runnable']

def test_no_plan_requires_plan():
    out=project_goal_work('active',[],has_active_plan=False,now=NOW)
    assert out['state']=='plan_needed' and not out['runnable']

def test_completed_plan_asks_for_review_not_fake_completion():
    out=preview([s(1,'a','completed')])
    assert out['state']=='steps_finished_review_goal' and not out['runnable']

def test_invalid_json_dependency_fails_closed():
    out=preview([s(1,'a'),s(2,'b','blocked','not json')])
    assert out['state']=='invalid_dependency_data' and not out['runnable']

def test_unknown_dependency_fails_closed_even_with_ready_step():
    out=preview([s(1,'a'),s(2,'b','blocked',['missing'])])
    assert out['state']=='invalid_dependency_data' and not out['runnable']

def test_duplicate_keys_fail_closed():
    assert preview([s(1,'x'),s(2,'x')])['state']=='invalid_dependency_data'

def test_invalid_self_dependency_fails_closed():
    assert preview([s(1,'x','ready',['x'])])['state']=='invalid_dependency_data'

def test_failed_step_needs_review():
    assert preview([s(1,'x','failed')])['state']=='failed_step_needs_review'

def test_pending_step_is_not_runnable():
    assert preview([s(1,'x','pending')])['state']=='no_eligible_step'

def test_incomplete_dependencies_are_reported():
    out=preview([s(1,'a','completed'),s(2,'b','pending'),s(3,'c','blocked',['a','b'])])
    assert out['blocked_steps'][0]['waiting_for']==['b']

def test_json_dependency_string_supported():
    assert preview([s(1,'a','completed'),s(2,'b','blocked','["a"]')])['runnable']

def test_duplicate_dependency_rejected():
    assert preview([s(1,'a','completed'),s(2,'b','blocked',['a','a'])])['state']=='invalid_dependency_data'

def test_bad_clock_fails_safely():
    with pytest.raises(ValueError): preview([s(1,'a')],backoff_until=datetime.now().astimezone())

@pytest.mark.parametrize('state', ['unknown','invalid'])
def test_bad_step_status_fails_closed(state):
    assert preview([s(1,'a',state)])['state']=='invalid_dependency_data'

def test_cycle_corruption_fails_closed():
    out=preview([s(1,'a','blocked',['b']),s(2,'b','blocked',['a'])])
    assert out['state']=='invalid_dependency_data' and not out['runnable']

def test_diamond_dependencies_are_not_cycles():
    out=preview([s(1,'root','completed'),s(2,'left','completed',['root']),
                 s(3,'right','completed',['root']),s(4,'join','blocked',['left','right'])])
    assert out['state']=='ready' and out['next_step']['key']=='join'


def test_inflight_step_blocks_another_independent_ready_step():
    out = preview([s(1,'uncertain','running'),s(2,'independent','ready')])
    assert out['state'] == 'step_in_progress'
    assert out['runnable'] is False
    assert out['next_step'] is None
    assert out['counts']['effectively_ready'] == 0


def test_inflight_step_blocks_virtual_dependency_unlock():
    out = preview([s(1,'done','completed'),s(2,'interrupted','running',['done']),
                   s(3,'later','blocked',['done'])])
    assert out['state'] == 'step_in_progress'
    assert out['runnable'] is False
    assert out['next_step'] is None
    assert out['counts']['running'] == 1


def test_no_block_when_no_step_inflight():
    out=preview([s(1,'done','completed'),s(2,'next','blocked',['done'])])
    assert out['state'] == 'ready'
    assert out['next_step']['key'] == 'next'
