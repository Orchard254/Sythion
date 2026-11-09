import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { useAppStore } from './store/appStore';
import type { Task } from './types';

const mock = vi.hoisted(() => ({ tasks: [] as Task[] }));
vi.mock('./hooks/useAppInit', () => ({ useAppInit: () => ({ isInitialized: true, retry: vi.fn() }) }));
vi.mock('./hooks/useBackend', () => ({
  useHealth: () => ({ data: {}, dataUpdatedAt: Date.now() }),
  useAgent: () => ({}),
  useAgentHealth: () => ({}),
  useVersion: () => ({}),
  useTasks: () => ({ data: mock.tasks }),
  useSkillProposals: () => ({ data: [] }),
  useToolProposals: () => ({ data: [] }),
}));
vi.mock('./services/api', () => ({ setupService: { readiness: async () => ({}) } }));
vi.mock('./lib/setupReadiness', () => ({ canStartChat: () => true }));
vi.mock('./lib/surface', () => ({ isCompanionSurface: () => false }));
vi.mock('./lib/companionActivation', () => ({ shouldActivateCompanion: () => false }));
vi.mock('./components/Sidebar', () => ({ Sidebar: () => null }));
vi.mock('./screens/Operations', () => ({ OperationsScreen: () => null }));

const task: Task = { id: 'task-1', original_request: 'A task', normalized_goal: null, state: 'running', version: 1, active_action_id: null, pending_question: null, final_result: null, terminal_reason: 'Something went wrong', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' };

describe('task notification polling', () => {
  beforeEach(() => {
    mock.tasks = [task];
    useAppStore.setState({ isConnected: true, notifications: [], unreadNotifications: 0 });
    vi.stubGlobal('document', { addEventListener: vi.fn(), removeEventListener: vi.fn() });
  });
  afterEach(() => { vi.unstubAllGlobals(); });

  it('notifies once per failure transition, not on identical polls', async () => {
    let view: ReactTestRenderer;
    await act(async () => { view = create(<App />); });
    const poll = (state: Task['state']) => {
      mock.tasks = [{ ...task, state }];
      act(() => { view.update(<App />); });
    };
    expect(useAppStore.getState().notifications).toHaveLength(0);
    poll('running');
    poll('failed');
    poll('failed');
    expect(useAppStore.getState().notifications).toHaveLength(1);
    poll('running');
    poll('running');
    poll('failed');
    poll('failed');
    const notifications = useAppStore.getState().notifications;
    expect(notifications).toHaveLength(2);
    expect(new Set(notifications.map((n) => n.id)).size).toBe(2);
    expect(useAppStore.getState().unreadNotifications).toBe(2);
    act(() => { view.root.findByProps({ 'aria-label': 'Notifications (2 unread)' }).props.onClick(); });
    expect(JSON.stringify(view!.toJSON())).toContain('Task failed');
    expect(JSON.stringify(view!.toJSON())).toContain('Something went wrong');
    act(() => { view.root.findAllByProps({ 'aria-label': 'Dismiss Task failed' })[0].props.onClick(); });
    expect(useAppStore.getState().notifications).toHaveLength(1);
    expect(JSON.stringify(view!.toJSON())).toContain('Task failed');
    act(() => { view.unmount(); });
  });
});
