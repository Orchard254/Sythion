import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AutomationsScreen } from './Automations';
import { useAppStore } from '../store/appStore';

const mock = vi.hoisted(() => ({ approve: vi.fn(), reject: vi.fn(), proposalsRefetch: vi.fn(), skillsRefetch: vi.fn(), status: 'proposed' }));
vi.mock('../hooks/useBackend', async () => {
  const { useAppStore } = await import('../store/appStore');
  return {
    useBackgroundPolicy: () => ({ data: { enabled: false } }),
    useSkills: () => ({ data: [], refetch: mock.skillsRefetch }),
    useSkillProposals: () => ({ data: [{ id: 42, status: mock.status, definition: { name: 'test skill', steps: ['inspect'] }, trace_ids: [1, 2] }], refetch: mock.proposalsRefetch }),
    useAgentActivity: () => ({ data: { wakes: [] } }),
    useActions: () => ({ live: useAppStore((s) => s.isConnected), approveSkillProposal: mock.approve, rejectSkillProposal: mock.reject }),
  };
});

let view: ReactTestRenderer;
const button = (label: string) => view.root.findAllByType('button').find((b) => b.children.includes(label))!;
const openReview = () => act(() => button('Review').props.onClick());
beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal('window', { addEventListener: vi.fn(), removeEventListener: vi.fn() });
  mock.status = 'proposed';
  useAppStore.setState({ isConnected: true });
  act(() => { view = create(<AutomationsScreen />); });
});
afterEach(() => { act(() => view.unmount()); vi.unstubAllGlobals(); });

describe('Automations proposal review', () => {
  it('shows live definition and approves once, refreshing both lists', async () => {
    let resolve!: () => void;
    mock.approve.mockImplementation(() => new Promise<void>((r) => { resolve = r; }));
    expect(JSON.stringify(view.toJSON())).toContain('test skill');
    expect(JSON.stringify(view.toJSON())).not.toContain('Proposal 42');
    openReview();
    expect(JSON.stringify(view.toJSON())).toContain('test skill');
    await act(async () => { button('Approve').props.onClick(); button('Approve').props.onClick(); });
    expect(mock.approve).toHaveBeenCalledTimes(1);
    expect(mock.approve).toHaveBeenCalledWith(42);
    expect(button('Reject').props.disabled).toBe(true);
    await act(async () => { resolve(); });
    expect(mock.proposalsRefetch).toHaveBeenCalledOnce();
    expect(mock.skillsRefetch).toHaveBeenCalledOnce();
  });

  it('keeps pending_review visible but does not send unsupported decisions', async () => {
    mock.status = 'pending_review';
    act(() => view.update(<AutomationsScreen />));
    openReview();
    expect(button('Approve').props.disabled).toBe(true);
    expect(button('Reject').props.disabled).toBe(true);
    expect(JSON.stringify(view.toJSON())).toContain('This status cannot be decided by the backend.');
    await act(async () => { button('Approve').props.onClick(); button('Reject').props.onClick(); });
    expect(mock.approve).not.toHaveBeenCalled();
    expect(mock.reject).not.toHaveBeenCalled();
  });

  it('reports rejection errors and leaves the review open', async () => {
    mock.reject.mockRejectedValue(new Error('Not authorized'));
    openReview();
    await act(async () => { button('Reject').props.onClick(); });
    expect(mock.reject).toHaveBeenCalledWith(42);
    expect(JSON.stringify(view.toJSON())).toContain('Not authorized');
    expect(mock.proposalsRefetch).not.toHaveBeenCalled();
  });

  it('blocks disconnected and already decided proposals', () => {
    openReview();
    act(() => useAppStore.setState({ isConnected: false }));
    expect(button('Approve').props.disabled).toBe(true);
    act(() => button('Approve').props.onClick());
    expect(mock.approve).not.toHaveBeenCalled();
    expect(button('Reject').props.disabled).toBe(true);
    mock.status = 'enabled';
    act(() => useAppStore.setState({ isConnected: true }));
    act(() => view.update(<AutomationsScreen />));
    expect(button('Approve').props.disabled).toBe(true);
  });

});
