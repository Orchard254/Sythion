import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { useAppStore } from './store/appStore';

const mock = vi.hoisted(() => ({
  tasks: [] as Array<Record<string, unknown>>,
  permissions: [] as Array<Record<string, unknown>>,
  uncertain: [] as Array<Record<string, unknown>>,
}));

vi.mock('./hooks/useAppInit', () => ({ useAppInit: () => ({ isInitialized: true, retry: vi.fn() }) }));
vi.mock('./hooks/useBackend', () => {
  const empty = () => ({ data: [], refetch: vi.fn() });
  const single = () => ({ data: undefined, refetch: vi.fn() });
  return {
    useHealth: () => ({ data: {}, dataUpdatedAt: Date.now() }),
    useAgent: single,
    useAgentHealth: single,
    useVersion: single,
    useTasks: () => ({ data: mock.tasks }),
    useSkillProposals: empty,
    useToolProposals: empty,
    usePendingApprovals: () => ({ data: { proposals: mock.permissions } }),
    useUncertainRemoteActions: () => ({ data: { actions: mock.uncertain } }),
  };
});
vi.mock('./services/api', () => ({ setupService: { readiness: async () => ({}) } }));
vi.mock('./lib/setupReadiness', () => ({ canStartChat: () => true }));
vi.mock('./lib/surface', () => ({ isCompanionSurface: () => false }));
vi.mock('./lib/companionActivation', () => ({ shouldActivateCompanion: () => false }));
vi.mock('./screens/Operations', () => ({ OperationsScreen: () => null }));

describe('navigation attention badge', () => {
  let view: ReactTestRenderer;

  beforeEach(() => {
    mock.tasks = [];
    mock.permissions = [];
    mock.uncertain = [];
    useAppStore.setState({ isConnected: true, error: null, notifications: [], unreadNotifications: 0 });
    vi.stubGlobal('document', { addEventListener: vi.fn(), removeEventListener: vi.fn() });
  });
  afterEach(() => {
    act(() => view?.unmount());
    vi.unstubAllGlobals();
  });

  it('counts permission proposals, waiting tasks and uncertain actions even with no skill/tool proposals', async () => {
    mock.permissions = [{ proposal_id: 'p1' }];
    mock.tasks = [{ id: 't1', state: 'waiting_for_user', original_request: 'Q' }];
    mock.uncertain = [{ id: 'ra1' }];
    await act(async () => { view = create(<App />); });
    expect(view.root.findByProps({ className: 'nav-item__count' }).children.join('')).toBe('3');
  });

  it('reads zero when there is nothing awaiting the owner', async () => {
    await act(async () => { view = create(<App />); });
    expect(view.root.findAllByProps({ className: 'nav-item__count' })).toHaveLength(0);
  });
});
