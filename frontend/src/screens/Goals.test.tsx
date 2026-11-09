import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { GoalsScreen } from './Goals';
import type { Task } from '../types';

const mock = vi.hoisted(() => ({ tasks: [] as Task[] }));
const goal = { id: 'goal-1', title: 'A goal', status: 'active', priority: 1 };
const step = { id: 'step-1', step_order: 1, description: 'A step', status: 'pending', linked_task_id: 'task-1' };
vi.mock('../hooks/useBackend', () => ({
  useGoals: () => ({ data: [goal], origin: 'live' }),
  useObjectives: () => ({ data: [] }),
  useTasks: () => ({ data: mock.tasks, origin: 'live', refetch: vi.fn() }),
  useGoalDetail: () => ({ data: { goal, active_plan: { version: 1, status: 'active', steps: [step] } } }),
  useGoalPlans: () => ({ data: [] }),
  useTaskEvents: () => ({ data: [] }),
  useActions: () => ({}),
}));

const task: Task = { id: 'task-1', original_request: 'A task', normalized_goal: null, state: 'created', version: 1, active_action_id: null, pending_question: null, final_result: null, terminal_reason: null, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' };

describe('goal linked tasks', () => {
  beforeEach(() => { mock.tasks = []; vi.stubGlobal('window', { addEventListener: vi.fn(), removeEventListener: vi.fn() }); });
  afterEach(() => { vi.unstubAllGlobals(); });

  it('does not open a nonexistent task and describes the missing record', () => {
    let view: ReactTestRenderer;
    act(() => { view = create(<GoalsScreen />); });
    act(() => { view.root.findByProps({ 'aria-label': 'Open A goal' }).props.onClick(); });
    const link = view!.root.findAllByType('button').find((button) => button.props.className === 'link-chip');
    if (link) act(() => { link.props.onClick(); });
    expect(JSON.stringify(view!.toJSON())).toContain('Linked task unavailable');
    act(() => { view.root.findByProps({ role: 'tab', children: 'Activity' }).props.onClick(); });
    expect(JSON.stringify(view!.toJSON())).toContain('Plan steps may still reference tasks');
    expect(JSON.stringify(view!.toJSON())).not.toContain('No task is linked to this goal');
    act(() => { view!.unmount(); });
  });

  it('handles a task disappearing after its detail is opened', () => {
    mock.tasks = [task];
    let view: ReactTestRenderer;
    act(() => { view = create(<GoalsScreen />); });
    act(() => { view.root.findByProps({ 'aria-label': 'Open A goal' }).props.onClick(); });
    act(() => { view.root.findByProps({ className: 'link-chip' }).props.onClick(); });
    expect(JSON.stringify(view!.toJSON())).toContain('A task');
    mock.tasks = [];
    act(() => { view.update(<GoalsScreen />); });
    expect(JSON.stringify(view!.toJSON())).toContain('Task unavailable');
    act(() => { view!.unmount(); });
  });
});
