import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { useAppStore } from './store/appStore';

const healthRef = vi.hoisted(() => ({
  current: {
    isError: false as boolean,
    data: {} as unknown,
    dataUpdatedAt: Date.now(),
    error: null as Error | null,
  },
}));

vi.mock('./hooks/useAppInit', () => ({ useAppInit: () => ({ isInitialized: true, retry: vi.fn() }) }));
vi.mock('./hooks/useBackend', () => ({
  useHealth: () => healthRef.current,
  useAgent: () => ({}),
  useAgentHealth: () => ({}),
  useVersion: () => ({}),
  useTasks: () => ({ data: [] }),
  useSkillProposals: () => ({ data: [] }),
  useToolProposals: () => ({ data: [] }),
}));
vi.mock('./services/api', () => ({ setupService: { readiness: async () => ({}) } }));
vi.mock('./lib/setupReadiness', () => ({ canStartChat: () => true }));
vi.mock('./lib/surface', () => ({ isCompanionSurface: () => false }));
vi.mock('./lib/companionActivation', () => ({ shouldActivateCompanion: () => false }));
vi.mock('./components/Sidebar', () => ({ Sidebar: () => null }));
vi.mock('./screens/Operations', () => ({ OperationsScreen: () => null }));

describe('connection gating', () => {
  let view: ReactTestRenderer;

  beforeEach(() => {
    healthRef.current = { isError: false, data: {}, dataUpdatedAt: Date.now(), error: null };
    useAppStore.setState({ isConnected: true, error: null, notifications: [], unreadNotifications: 0 });
    vi.stubGlobal('document', { addEventListener: vi.fn(), removeEventListener: vi.fn() });
  });

  afterEach(() => {
    act(() => view?.unmount());
    vi.unstubAllGlobals();
  });

  it('stays connected on a healthy check (existing behavior)', async () => {
    await act(async () => { view = create(<App />); });
    expect(useAppStore.getState().isConnected).toBe(true);
    expect(JSON.stringify(view.toJSON())).not.toContain('Sythion backend unreachable');
  });

  it('drops the gate and shows the offline screen when a health check fails', async () => {
    await act(async () => { view = create(<App />); });
    healthRef.current = { isError: true, data: undefined, dataUpdatedAt: 0, error: new Error('backend health failed') };
    await act(async () => { view.update(<App />); });
    expect(useAppStore.getState().isConnected).toBe(false);
    expect(JSON.stringify(view.toJSON())).toContain('Sythion backend unreachable');
  });

  it('recovers the gate when a later health check succeeds', async () => {
    await act(async () => { view = create(<App />); });
    healthRef.current = { isError: true, data: undefined, dataUpdatedAt: 0, error: new Error('backend health failed') };
    await act(async () => { view.update(<App />); });
    expect(useAppStore.getState().isConnected).toBe(false);

    healthRef.current = { isError: false, data: { status: 'healthy' }, dataUpdatedAt: Date.now(), error: null };
    await act(async () => { view.update(<App />); });
    expect(useAppStore.getState().isConnected).toBe(true);
    expect(JSON.stringify(view.toJSON())).not.toContain('Sythion backend unreachable');
  });

  it('does not drop the gate for a successful but degraded check (partial degradation)', async () => {
    await act(async () => { view = create(<App />); });
    healthRef.current = { isError: false, data: { status: 'degraded', startup_sequence_status: 'degraded' }, dataUpdatedAt: Date.now(), error: null };
    await act(async () => { view.update(<App />); });
    expect(useAppStore.getState().isConnected).toBe(true);
    expect(JSON.stringify(view.toJSON())).not.toContain('Sythion backend unreachable');
  });

});