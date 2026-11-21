import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { useAppStore } from './store/appStore';
import { useNavStore } from './store/navStore';

const mock = vi.hoisted(() => ({ proposals: [] as { id: number; definition: { name: string }; status: string; trace_ids: number[]; validation_errors: string[] }[], live: true, approve: vi.fn(), reject: vi.fn() }));
vi.mock('./hooks/useAppInit', () => ({ useAppInit: () => ({ isInitialized: true, retry: vi.fn() }) }));
vi.mock('./hooks/useBackend', () => {
  const empty = () => ({ data: [], refetch: vi.fn() });
  const single = () => ({ refetch: vi.fn() });
  return {
    useHealth: () => ({ data: {}, dataUpdatedAt: Date.now() }),
    useAgent: single, useAgentHealth: single, useVersion: single, useBackgroundPolicy: single,
    useCapabilities: empty, useCapabilityConsistency: single, useSkills: empty, useTools: empty,
    useTasks: empty, useAgentActivity: single, useMemoryEdges: empty,
    useDevices: empty, usePendingApprovals: empty, useUncertainRemoteActions: empty, useRelayAcks: empty,
    useSkillProposals: () => ({ data: mock.proposals, refetch: vi.fn() }),
    useToolProposals: empty, useActions: () => ({ live: mock.live, approveSkillProposal: mock.approve, rejectSkillProposal: mock.reject }),
  };
});
vi.mock('./services/api', () => ({
  setupService: { readiness: async () => ({}) },
  deviceCapabilityService: { get: async () => ({ capabilities: [] }) },
}));
vi.mock('./lib/setupReadiness', () => ({ canStartChat: () => true }));
vi.mock('./lib/surface', () => ({ isCompanionSurface: () => false }));
vi.mock('./lib/companionActivation', () => ({ shouldActivateCompanion: () => false }));

describe('pending skill proposals', () => {
  beforeEach(() => {
    mock.proposals = [
      { id: 1, definition: { name: 'Proposed skill' }, status: 'proposed', trace_ids: [1], validation_errors: [] },
      { id: 2, definition: { name: 'Review skill' }, status: 'pending_review', trace_ids: [], validation_errors: [] },
      { id: 3, definition: { name: 'Approved skill' }, status: 'enabled', trace_ids: [], validation_errors: [] },
      { id: 4, definition: { name: 'Other pending skill' }, status: 'pending_other', trace_ids: [], validation_errors: [] },
    ];
    mock.live = true;
    vi.clearAllMocks();
    mock.approve.mockResolvedValue(undefined);
    mock.reject.mockResolvedValue(undefined);
    useAppStore.setState({ isConnected: true });
    useNavStore.setState({ route: 'security' });
    vi.stubGlobal('document', { addEventListener: vi.fn(), removeEventListener: vi.fn() });
  });
  afterEach(() => { vi.unstubAllGlobals(); });

  it('shows pending skill proposals and updates the navigation badge', async () => {
    let view: ReactTestRenderer;
    await act(async () => { view = create(<App />); });
    const text = JSON.stringify(view!.toJSON());
    expect(text).toContain('Proposed skill');
    expect(text).toContain('Review skill');
    expect(text).not.toContain('Approved skill');
    expect(text).not.toContain('Other pending skill');
    expect(text).toContain('awaiting you');
    expect(view!.root.findByProps({ className: 'nav-item__count' }).children).toEqual(['2']);
    const rows = view!.root.findAllByProps({ className: 'list__row' });
    const proposed = rows.find((row) => row.findAllByType('span').some((span) => span.children.includes('Proposed skill')))!;
    const review = rows.find((row) => row.findAllByType('span').some((span) => span.children.includes('Review skill')))!;
    const buttons = (row: typeof proposed) => row.findAllByType('button');
    expect(buttons(proposed).map((b) => b.props.disabled)).toEqual([false, false]);
    expect(buttons(review).map((b) => b.props.disabled)).toEqual([true, true]);
    await act(async () => { buttons(review)[0].props.onClick(); buttons(review)[1].props.onClick(); });
    expect(mock.approve).not.toHaveBeenCalled();
    expect(mock.reject).not.toHaveBeenCalled();
    await act(async () => { buttons(proposed)[0].props.onClick(); });
    expect(mock.approve).toHaveBeenCalledWith(1);

    mock.proposals = [{ id: 3, definition: { name: 'Approved skill' }, status: 'enabled', trace_ids: [], validation_errors: [] }];
    act(() => { view!.update(<App />); });
    expect(JSON.stringify(view!.toJSON())).toContain('Nothing pending');
    expect(view!.root.findAllByProps({ className: 'nav-item__count' })).toHaveLength(0);
    act(() => { view!.unmount(); });
  });

  it('blocks decisions when disconnected', async () => {
    let view: ReactTestRenderer;
    await act(async () => { view = create(<App />); });
    const proposalButtons = () => view!.root.findAllByProps({ className: 'list__row' })
      .find((row) => row.findAllByType('span').some((span) => span.children.includes('Proposed skill')))!.findAllByType('button');
    mock.live = false;
    act(() => view!.update(<App />));
    expect(proposalButtons().every((b) => b.props.disabled)).toBe(true);
    await act(async () => { proposalButtons()[1].props.onClick(); });
    expect(mock.approve).not.toHaveBeenCalled();
    expect(mock.reject).not.toHaveBeenCalled();
    act(() => view!.unmount());
  });
});
