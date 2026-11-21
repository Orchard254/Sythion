import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SecurityScreen } from './Security';

const mock = vi.hoisted(() => ({
  toolProposals: [] as Array<Record<string, unknown>>,
  tasks: [] as Array<Record<string, unknown>>,
  permissions: [] as Array<Record<string, unknown>>,
  uncertain: [] as Array<Record<string, unknown>>,
  skills: [] as Array<Record<string, unknown>>,
  agentHealthData: undefined as { overall: string } | undefined,
  capabilitiesData: [] as Array<Record<string, unknown>>,
  activityData: undefined as { events: Array<Record<string, unknown>> } | undefined,
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
    useCapabilities: () => ({ data: mock.capabilitiesData, isLoading: false, refetch: mock.capabilitiesRefetch }),
    useCapabilityConsistency: () => ({ data: [], isLoading: false, refetch: mock.consistencyRefetch }),
    useTools: () => ({ data: [], refetch: mock.toolsRefetch }),
    useSkills: () => ({ data: [], refetch: mock.skillsRefetch }),
    useAgentHealth: () => ({ data: mock.agentHealthData, refetch }),
    useHealth: () => ({ data: undefined, isError: false, refetch }),
    useTasks: () => ({ data: mock.tasks, refetch }),
    useAgentActivity: () => ({ data: mock.activityData, refetch }),
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
    mock.agentHealthData = undefined;
    mock.capabilitiesData = [];
    mock.activityData = undefined;
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
    // The backend-capability prose is implementation detail: available under
    // the limits panel's Advanced disclosure, never in the default view.
    const toggle = view.root.findByProps({ 'data-testid': 'advanced-toggle-limits' });
    await act(async () => { toggle.props.onClick(); });
    expect(view.root.findByProps({ 'data-testid': 'advanced-limits' })).toBeTruthy();
    expect(JSON.stringify(view.toJSON())).toContain('Device credentials and trust grants');
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

  it('default view shows plain health status and no raw backend detail', async () => {
    mock.agentHealthData = { overall: 'HEALTHY' };
    mock.capabilitiesData = [{
      id: 'cap-1',
      name: 'Shell',
      category: 'system',
      status: 'REQUIRES_PERMISSION',
      risk_class: 'HIGH',
      requires_permission: true,
      requires_external_service: true,
    }];
    mock.activityData = {
      events: [{
        event_type: 'action.completed',
        sequence_num: 9,
        created_at: '2026-11-01T00:00:00Z',
        event_data: { tool_name: 'shell.exec' },
      }],
    };
    await act(async () => { view = create(<SecurityScreen />); });
    const text = JSON.stringify(view.toJSON());

    expect(text).toContain('Synth is healthy');
    // Human-readable capability decisions stay in the default view.
    expect(text).toContain('need your approval first');
    expect(text).toContain('high risk');

    // No raw ids, enums, event-data dumps, versions or backend prose by default.
    expect(text).not.toContain('Agent id');
    expect(text).not.toContain('DEGRADED');
    expect(text).not.toContain('Last wake');
    expect(text).not.toContain('Proposal id');
    expect(text).not.toContain('Enabled mode');
    expect(text).not.toContain('Successful uses');
    expect(text).not.toContain('Validation problems');
    expect(text).not.toContain('This status cannot be decided by the backend');
    expect(text).not.toContain('Derived from two backend switches');
    expect(text).not.toContain('requires_permission');
    expect(text).not.toContain('tool_name: shell.exec');
  });

  it('never claims healthy when health is degraded; raw health sits under Advanced', async () => {
    mock.agentHealthData = { overall: 'DEGRADED' };
    await act(async () => { view = create(<SecurityScreen />); });
    const text = JSON.stringify(view.toJSON());
    expect(text).toContain('Synth needs attention');
    expect(text).not.toContain('Synth is healthy');

    const toggle = view.root.findByProps({ 'data-testid': 'advanced-toggle-runtime' });
    expect(toggle.props['aria-expanded']).toBe(false);
    await act(async () => { toggle.props.onClick(); });
    expect(view.root.findByProps({ 'data-testid': 'advanced-runtime' })).toBeTruthy();
    const opened = JSON.stringify(view.toJSON());
    expect(opened).toContain('DEGRADED');
    expect(opened).toContain('Agent id');
  });

  it('every Advanced / for support disclosure is closed by default and reveals detail when opened', async () => {
    await act(async () => { view = create(<SecurityScreen />); });
    const advancedButtons = view.root.findAllByType('button')
      .filter((b) => b.children.includes('Advanced / for support'));
    expect(advancedButtons.length).toBeGreaterThan(0);
    advancedButtons.forEach((b) => expect(b.props['aria-expanded']).toBe(false));

    // No raw detail panels are mounted while every disclosure is closed.
    expect(view.root.findAllByProps({ 'data-testid': 'advanced-runtime' })).toHaveLength(0);
    expect(view.root.findAllByProps({ 'data-testid': 'advanced-capabilities' })).toHaveLength(0);
    expect(view.root.findAllByProps({ 'data-testid': 'advanced-audit' })).toHaveLength(0);

    for (const b of advancedButtons) {
      await act(async () => { b.props.onClick(); });
    }
    expect(view.root.findAllByProps({ 'data-testid': 'advanced-autonomy' })).toHaveLength(1);
    expect(view.root.findAllByProps({ 'data-testid': 'advanced-proposals' })).toHaveLength(1);
    expect(view.root.findAllByProps({ 'data-testid': 'advanced-capabilities' })).toHaveLength(1);
    expect(view.root.findAllByProps({ 'data-testid': 'advanced-audit' })).toHaveLength(1);
    expect(view.root.findAllByProps({ 'data-testid': 'advanced-runtime' })).toHaveLength(1);
    expect(view.root.findAllByProps({ 'data-testid': 'advanced-limits' })).toHaveLength(1);
  });

  it('keeps all owner consent and pause controls visible in the default view', async () => {
    mock.toolProposals = [toolProposal];
    mock.permissions = [{ proposal_id: 'p1', tool_name: 'x', task_id: 't', target_canonical: 'y' }];
    mock.skills = [{ id: 3, name: 'draft skill', status: 'proposed', evidence_count: 2, validation_errors: [] }];
    mock.tasks = [{
      id: 't1',
      state: 'interrupted',
      updated_at: '2026-11-01T00:00:00Z',
      original_request: 'Held task',
      normalized_goal: null,
      terminal_reason: 'Needs a decision',
    }];
    await act(async () => { view = create(<SecurityScreen />); });
    const buttons = view.root.findAllByType('button');
    const hasButton = (label: string) => buttons.some((b) => b.children.includes(label));
    expect(hasButton('Approve')).toBe(true);
    expect(hasButton('Reject')).toBe(true);
    expect(hasButton('Deny')).toBe(true);
    expect(hasButton('Review')).toBe(true);
    expect(hasButton('Resume if safe')).toBe(true);
    expect(hasButton('Pause everything')).toBe(true);
    expect(hasButton('Resume agent')).toBe(true);
    const text = JSON.stringify(view.toJSON());
    expect(text).toContain('Owner decisions');
    expect(text).toContain('Resuming is not permission approval');
  });
});
