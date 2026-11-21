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

const initRef = vi.hoisted(() => ({ isInitialized: true }));

vi.mock('./hooks/useAppInit', () => ({
  useAppInit: () => ({ isInitialized: initRef.isInitialized, isBridgeReady: true, retry: vi.fn() }),
}));
vi.mock('./hooks/useBackend', () => ({
  useHealth: () => healthRef.current,
  useAgent: () => ({}),
  useAgentHealth: () => ({}),
  useVersion: () => ({}),
  useTasks: () => ({ data: [] }),
  useSkillProposals: () => ({ data: [] }),
  useToolProposals: () => ({ data: [] }),
  usePendingApprovals: () => ({ data: { proposals: [] } }),
  useUncertainRemoteActions: () => ({ data: { actions: [] } }),
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
    initRef.isInitialized = true;
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
    expect(JSON.stringify(view.toJSON())).not.toContain('Sythion is still unreachable');
  });

  it('drops the gate and shows an honest self-healing state when a health check fails', async () => {
    await act(async () => { view = create(<App />); });
    healthRef.current = { isError: true, data: undefined, dataUpdatedAt: 0, error: new Error('backend health failed') };
    await act(async () => { view.update(<App />); });
    expect(useAppStore.getState().isConnected).toBe(false);
    const text = JSON.stringify(view.toJSON());
    expect(text).toContain('Sythion is still unreachable');
    expect(text).toContain('checking quietly in the background');
    // Plain, honest reassurance for a non-technical owner: no developer log
    // filenames or jargon, and no fake "send a report" action.
    expect(text).toContain("Synth couldn't start this time");
    expect(text).toContain('Your data is safe');
    expect(text).toContain('keep trying');
    expect(text).not.toContain('sidecar.err.log');
    expect(text).not.toContain('sythion-shell.log');
    // The owner is never told to launch the backend or to retry by hand.
    expect(text).not.toContain('Start the local backend');
    expect(text).not.toContain('start_sythion.cmd');
    expect(text).not.toContain('Retry connection');
  });

  it('shows a self-healing checking state while the bridge is resolving', async () => {
    initRef.isInitialized = false;
    await act(async () => { view = create(<App />); });
    const text = JSON.stringify(view.toJSON());
    expect(text).toContain('Checking connection');
    expect(text).toContain('keeps checking on its own');
    expect(text).not.toContain('Retry connection');
    expect(text).not.toContain('start_sythion.cmd');
  });

  it('recovers the gate when a later health check succeeds', async () => {
    await act(async () => { view = create(<App />); });
    healthRef.current = { isError: true, data: undefined, dataUpdatedAt: 0, error: new Error('backend health failed') };
    await act(async () => { view.update(<App />); });
    expect(useAppStore.getState().isConnected).toBe(false);

    healthRef.current = { isError: false, data: { status: 'healthy' }, dataUpdatedAt: Date.now(), error: null };
    await act(async () => { view.update(<App />); });
    expect(useAppStore.getState().isConnected).toBe(true);
    expect(JSON.stringify(view.toJSON())).not.toContain('Sythion is still unreachable');
  });

  it('does not drop the gate for a successful but degraded check (partial degradation)', async () => {
    await act(async () => { view = create(<App />); });
    healthRef.current = { isError: false, data: { status: 'degraded', startup_sequence_status: 'degraded' }, dataUpdatedAt: Date.now(), error: null };
    await act(async () => { view.update(<App />); });
    expect(useAppStore.getState().isConnected).toBe(true);
    expect(JSON.stringify(view.toJSON())).not.toContain('Sythion is still unreachable');
  });

});