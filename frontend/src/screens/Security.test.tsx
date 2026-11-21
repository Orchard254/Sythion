import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SecurityScreen } from './Security';

const mock = vi.hoisted(() => ({
  toolProposals: [] as Array<Record<string, unknown>>,
  tasks: [] as Array<Record<string, unknown>>,
  permissions: [] as Array<Record<string, unknown>>,
  uncertain: [] as Array<Record<string, unknown>>,
  skills: [] as Array<Record<string, unknown>>,
  approveTool: vi.fn(),
  rejectTool: vi.fn(),
  approveSkill: vi.fn(),
  rejectSkill: vi.fn(),
  notify: vi.fn(),
  toolProposalsRefetch: vi.fn(),
  skillProposalsRefetch: vi.fn(),
  capabilitiesRefetch: vi.fn(),
  consistencyRefetch: vi.fn(),
  toolsRefetch: vi.fn(),
  skillsRefetch: vi.fn(),
}));

vi.mock('../hooks/useBackend', () => {
  const refetch = vi.fn();
  return {
    useAgent: () => ({ data: undefined, refetch }),
    useBackgroundPolicy: () => ({ data: undefined, refetch }),
    useCapabilities: () => ({ data: [], isLoading: false, refetch: mock.capabilitiesRefetch }),
    useCapabilityConsistency: () => ({ data: [], isLoading: false, refetch: mock.consistencyRefetch }),
    useTools: () => ({ data: [], refetch: mock.toolsRefetch }),
    useSkills: () => ({ data: [], refetch: mock.skillsRefetch }),
    useAgentHealth: () => ({ data: undefined, refetch }),
    useHealth: () => ({ data: undefined, isError: false, refetch }),
    useTasks: () => ({ data: mock.tasks, refetch }),
    useAgentActivity: () => ({ data: undefined, refetch }),
    useMemoryEdges: () => ({ data: [], refetch }),
    useSkillProposals: () => ({ data: mock.skills, refetch: mock.skillProposalsRefetch }),
    useToolProposals: () => ({ data: mock.toolProposals, refetch: mock.toolProposalsRefetch }),
    useDevices: () => ({ data: { devices: [] }, refetch }),
    usePendingApprovals: () => ({ data: { proposals: mock.permissions }, refetch }),
    useUncertainRemoteActions: () => ({ data: { actions: mock.uncertain }, refetch }),
    useActions: () => ({
      live: true,
      approveToolProposal: mock.approveTool,
      rejectToolProposal: mock.rejectTool,
      approveSkillProposal: mock.approveSkill,
      rejectSkillProposal: mock.rejectSkill,
      pauseAgent: vi.fn(),
      resumeAgent: vi.fn(),
      setBackground: vi.fn(),
      resumeTask: vi.fn(),
      setDeviceTrust: vi.fn(),
      decideApproval: vi.fn(),
    }),
  };
});

vi.mock('../lib/autonomy', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/autonomy')>();
  return {
    ...actual,
    deriveAutonomy: () => ({ mode: 'assisted', agentState: 'ACTIVE' }),
    deriveAuthorization: () => ({
      tone: 'ok',
      known: true,
      label: 'Local owner',
      level: 'owner',
      summary: 'You authorize actions on this device.',
      readOnlyReason: 'Reported by the backend.',
    }),
  };
});

vi.mock('../store/appStore', () => ({ useAppStore: (sel: (s: unknown) => unknown) => sel({ notify: mock.notify }) }));
vi.mock('../store/navStore', () => ({ useNavStore: (sel: (s: unknown) => unknown) => sel({ setRoute: vi.fn() }) }));

const toolProposal = {
  id: 7,
  name: 'shell.exec',
  description: 'Runs a shell command on this device.',
  status: 'pending_review',
  enabled_mode: null,
  successful_uses: 0,
  validation_errors: [],
  sandbox_result: null,
  created_at: '2026-11-01T00:00:00Z',
  decided_at: null,
  enabled_at: null,
};

describe('Security screen presentability', () => {
  let view: ReactTestRenderer;

  beforeEach(() => {
    mock.toolProposals = [];
    mock.tasks = [];
    mock.permissions = [];
    mock.uncertain = [];
    mock.skills = [];
    vi.clearAllMocks();
    mock.approveTool.mockResolvedValue(undefined);
    mock.rejectTool.mockResolvedValue(undefined);
    mock.approveSkill.mockResolvedValue(undefined);
    mock.rejectSkill.mockResolvedValue(undefined);
  });

  afterEach(() => { act(() => view?.unmount()); });

  it('does not approve on Review; the detail view offers explicit Approve and Reject', async () => {
    mock.toolProposals = [toolProposal];
    await act(async () => { view = create(<SecurityScreen />); });

    expect(mock.approveTool).not.toHaveBeenCalled();
    const reviewButton = view.root.findAllByType('button').find((b) => b.children.includes('Review'))!;
    await act(async () => { reviewButton.props.onClick(); });

    expect(mock.approveTool).not.toHaveBeenCalled();
    const detail = view.root.findByProps({ 'data-testid': 'tool-review-7' });
    const approve = detail.findAllByType('button').find((b) => b.children.includes('Approve tool'))!;
    const reject = detail.findAllByType('button').find((b) => b.children.includes('Reject tool'))!;
    expect(approve).toBeTruthy();
    expect(reject).toBeTruthy();

    await act(async () => { reject.props.onClick(); });
    expect(mock.rejectTool).toHaveBeenCalledWith(7);
    expect(mock.approveTool).not.toHaveBeenCalled();
  });

  it('describes device access honestly instead of claiming there are no remote sessions', async () => {
    await act(async () => { view = create(<SecurityScreen />); });
    const text = JSON.stringify(view.toJSON());
    expect(text).not.toContain('there are no remote sessions to revoke');
    expect(text).toContain('Device credentials and trust grants');
  });

  it('counts permission proposals, waiting tasks and uncertain actions as awaiting the owner', async () => {
    mock.permissions = [{ proposal_id: 'p1', tool_name: 'x', task_id: 't', target_canonical: 'y' }];
    mock.tasks = [{ id: 't1', state: 'waiting_for_user', updated_at: '2026-11-01T00:00:00Z', original_request: 'Q' }];
    mock.uncertain = [{ id: 'ra1' }];
    await act(async () => { view = create(<SecurityScreen />); });
    const badge = view.root.findByProps({ className: 'badge badge--accent' });
    expect(badge.children.join('')).toBe('3 awaiting you');
    // The summary stat must agree with the header badge.
    const stat = view.root.findAllByProps({ className: 'stat stat--accent' })[0];
    expect(stat.findAllByProps({ className: 'stat__value' })[0].children.join('')).toBe('3');
  });

  it('refetches the capability, consistency and tools panels after a tool approval', async () => {
    mock.toolProposals = [toolProposal];
    await act(async () => { view = create(<SecurityScreen />); });
    const reviewButton = view.root.findAllByType('button').find((b) => b.children.includes('Review'))!;
    await act(async () => { reviewButton.props.onClick(); });
    const detail = view.root.findByProps({ 'data-testid': 'tool-review-7' });
    const approve = detail.findAllByType('button').find((b) => b.children.includes('Approve tool'))!;
    await act(async () => { approve.props.onClick(); });

    expect(mock.approveTool).toHaveBeenCalledWith(7);
    expect(mock.toolProposalsRefetch).toHaveBeenCalled();
    expect(mock.capabilitiesRefetch).toHaveBeenCalled();
    expect(mock.consistencyRefetch).toHaveBeenCalled();
    expect(mock.toolsRefetch).toHaveBeenCalled();
    expect(mock.skillsRefetch).toHaveBeenCalled();
  });

  it('refetches the skills panel after a skill approval', async () => {
    mock.skills = [{ id: 3, name: 'draft skill', status: 'proposed', evidence_count: 2, validation_errors: [] }];
    await act(async () => { view = create(<SecurityScreen />); });
    const approve = view.root.findAllByType('button').find((b) => b.children.includes('Approve'))!;
    expect(approve).toBeTruthy();
    await act(async () => { approve.props.onClick(); });

    expect(mock.approveSkill).toHaveBeenCalledWith(3);
    expect(mock.skillProposalsRefetch).toHaveBeenCalled();
    expect(mock.skillsRefetch).toHaveBeenCalled();
  });

  it('does not refetch any live panel when a tool decision fails', async () => {
    mock.toolProposals = [toolProposal];
    mock.approveTool.mockRejectedValueOnce(new Error('backend refused'));
    await act(async () => { view = create(<SecurityScreen />); });
    const reviewButton = view.root.findAllByType('button').find((b) => b.children.includes('Review'))!;
    await act(async () => { reviewButton.props.onClick(); });
    const detail = view.root.findByProps({ 'data-testid': 'tool-review-7' });
    const approve = detail.findAllByType('button').find((b) => b.children.includes('Approve tool'))!;
    await act(async () => { approve.props.onClick(); });

    expect(mock.approveTool).toHaveBeenCalledWith(7);
    expect(mock.toolProposalsRefetch).not.toHaveBeenCalled();
    expect(mock.capabilitiesRefetch).not.toHaveBeenCalled();
    expect(mock.consistencyRefetch).not.toHaveBeenCalled();
    expect(mock.toolsRefetch).not.toHaveBeenCalled();
    expect(mock.skillsRefetch).not.toHaveBeenCalled();
    expect(mock.notify).toHaveBeenCalledWith(expect.objectContaining({ tone: 'danger' }));
  });

  it('does not refetch any live panel when a skill decision fails', async () => {
    mock.skills = [{ id: 3, name: 'draft skill', status: 'proposed', validation_errors: [] }];
    mock.approveSkill.mockRejectedValueOnce(new Error('backend refused'));
    await act(async () => { view = create(<SecurityScreen />); });
    const approve = view.root.findAllByType('button').find((b) => b.children.includes('Approve'))!;
    await act(async () => { approve.props.onClick(); });

    expect(mock.approveSkill).toHaveBeenCalledWith(3);
    expect(mock.skillProposalsRefetch).not.toHaveBeenCalled();
    expect(mock.skillsRefetch).not.toHaveBeenCalled();
    expect(mock.capabilitiesRefetch).not.toHaveBeenCalled();
  });
});
