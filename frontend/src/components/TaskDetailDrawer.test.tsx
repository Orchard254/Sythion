import { act, create } from 'react-test-renderer';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Task } from '../types';
import { TaskDetailDrawer } from './TaskDetailDrawer';

// Drawer registers an Escape-key listener; provide the minimal DOM surface.
if (typeof (globalThis as { window?: unknown }).window === 'undefined') {
  (globalThis as { window: unknown }).window = {
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  };
}

const replyToTask = vi.fn();
const resumeTask = vi.fn();
const resumePreflight = vi.hoisted(() => vi.fn());
const acknowledgeFailure = vi.hoisted(() => vi.fn());
const recoveryMock = vi.hoisted(() => ({
  data: undefined as import('../types').TaskResumePreflight | undefined,
  isLoading: false,
  isFetching: false,
  isError: false,
  refetch: vi.fn(),
}));
const eventsMock = vi.hoisted(() => ({ data: [] as Array<Record<string, unknown>> }));

vi.mock('../services/api', () => ({
  taskService: { resumePreflight, acknowledgeFailure },
}));

vi.mock('../hooks/useBackend', () => ({
  useTaskEvents: () => ({ data: eventsMock.data, refetch: vi.fn(), isLoading: false }),
  useTaskResumePreflight: () => recoveryMock,
  useActions: () => ({ live: true, replyToTask, resumeTask, cancelTask: vi.fn() }),
}));

function task(overrides: Partial<Task> = {}): Task {
  return {
    id: 't1',
    original_request: 'Do the thing',
    normalized_goal: null,
    state: 'waiting_for_user',
    version: 3,
    active_action_id: null,
    objective_id: null,
    pending_question: 'Question?',
    final_result: null,
    created_at: '2026-10-19T10:00:00',
    updated_at: '2026-10-19T10:00:00',
    terminal_reason: null,
    waiting_kind: undefined,
    ...overrides,
  };
}

describe('TaskDetailDrawer answer control', () => {
  afterEach(() => {
    replyToTask.mockClear();
  });

  it('hides answer input and Send for an unclassified wait while keeping the question visible', () => {
    const view = create(
      <TaskDetailDrawer task={task({ waiting_kind: 'unknown', pending_question: 'How many people?' })} onClose={() => {}} />,
    );
    expect(view.root.findAllByType('input')).toHaveLength(0);
    expect(view.root.findAllByType('button').filter((button) => button.children.join('') === 'Send')).toHaveLength(0);
    const rendered = JSON.stringify(view.toJSON());
    expect(rendered).toContain('How many people?');
    expect(rendered).toContain('cannot be resumed by typing an answer here');
  });

  it('keeps answer input and Send for an exact typed clarification', () => {
    const view = create(
      <TaskDetailDrawer task={task({ waiting_kind: 'clarification', version: 7 })} onClose={() => {}} />,
    );
    const inputs = view.root.findAllByType('input');
    const buttons = view.root.findAllByType('button').filter((button) => button.children.join('') === 'Send');
    expect(inputs).toHaveLength(1);
    expect(buttons).toHaveLength(1);
    expect((buttons[0].props as { disabled: boolean }).disabled).toBe(true);
  });

  it('does not offer free text for a staged permission wait', () => {
    const view = create(
      <TaskDetailDrawer task={task({ waiting_kind: 'permission', pending_question: 'Do you approve?' })} onClose={() => {}} />,
    );
    expect(view.root.findAllByType('input')).toHaveLength(0);
    expect(view.root.findAllByType('button').filter((button) => button.children.join('') === 'Send')).toHaveLength(0);
    const rendered = JSON.stringify(view.toJSON());
    expect(rendered).toContain('Do you approve?');
  });
});
describe('TaskDetailDrawer recovery controls', () => {
  afterEach(() => {
    recoveryMock.data = undefined;
    recoveryMock.isError = false;
    recoveryMock.isLoading = false;
    recoveryMock.isFetching = false;
    resumeTask.mockClear();
    resumePreflight.mockClear();
  });

  it('disables resumption when no preflight has been received', () => {
    const view = create(<TaskDetailDrawer task={task({ state: 'interrupted' })} onClose={() => {}} />);
    const resumeButton = view.root.findAllByType('button')
      .find((button) => button.children.includes('Resume task'));
    expect(resumeButton).toBeDefined();
    expect(resumeButton!.props.disabled).toBe(true);
    expect(JSON.stringify(view.toJSON())).toContain('Recovery status unavailable');
  });

  it('disables resumption and shows review guidance for blocked evidence', () => {
    recoveryMock.data = {
      task_id: 't1', task_state: 'interrupted', owner_lease: 'expired',
      unresolved_reasoning_calls: 1,
      reason: 'unresolved_reasoning_review_required', preflight_clear: false,
      review_required: true, automatic_replay_allowed: false,
      review: {
        status: 'review_required', blocker: 'unresolved_reasoning_review_required',
        severity: 'medium', observations: { task_state: 'interrupted', owner_lease: 'expired', unresolved_reasoning_calls: 1 },
        evidence_source_classes: ['reasoning_event_ledger'],
        verification_steps: ['Verify the prior call outcome independently.'],
        requires_authenticated_reconciliation: true, limited_to_current_snapshot: true,
        approved: false, authorization_issued: false, uncertainty_cleared: false,
        execution_started: false, automatic_replay_allowed: false,
        note: 'No authorization',
      },
    };
    const view = create(<TaskDetailDrawer task={task({ state: 'interrupted' })} onClose={() => {}} />);
    const resumeButton = view.root.findAllByType('button')
      .find((button) => button.children.includes('Resume task'));
    expect(resumeButton!.props.disabled).toBe(true);
    const text = JSON.stringify(view.toJSON());
    expect(text).toContain('Verify the prior call outcome independently.');
    expect(text).toContain('not an approval');
  });
});

function clearRecovery(): import('../types').TaskResumePreflight {
  return {
    task_id: 't1', task_state: 'interrupted', owner_lease: 'released',
    unresolved_reasoning_calls: 0,
    reason: 'clear_for_explicit_resume', preflight_clear: true,
    review_required: false, automatic_replay_allowed: false,
    review: {
      status: 'preflight_clear_not_authorized', blocker: 'clear_for_explicit_resume',
      severity: 'none', observations: {
        task_state: 'interrupted', owner_lease: 'released', unresolved_reasoning_calls: 0,
      },
      evidence_source_classes: ['task_state'],
      verification_steps: ['The backend must recheck the task state.'],
      requires_authenticated_reconciliation: false, limited_to_current_snapshot: true,
      approved: false, authorization_issued: false, uncertainty_cleared: false,
      execution_started: false, automatic_replay_allowed: false,
      note: 'Not an approval',
    },
  };
}

describe('TaskDetailDrawer explicit recovery interaction', () => {
  afterEach(() => {
    recoveryMock.data = undefined;
    recoveryMock.isError = false;
    recoveryMock.isLoading = false;
    recoveryMock.isFetching = false;
    resumePreflight.mockReset();
    resumeTask.mockReset();
  });

  it('keeps Resume task disabled if a displayed preflight request fails', () => {
    recoveryMock.data = clearRecovery();
    recoveryMock.isError = true;
    const view = create(<TaskDetailDrawer task={task({ state: 'interrupted' })} onClose={() => {}} />);
    const resume = view.root.findAllByType('button').find((b) => b.children.includes('Resume task'));
    expect(resume?.props.disabled).toBe(true);
    expect(JSON.stringify(view.toJSON())).toContain('Recovery check failed');
  });

  it('does not execute when fresh preflight disagrees with displayed clear state', async () => {
    recoveryMock.data = clearRecovery();
    const blocked = clearRecovery();
    blocked.preflight_clear = false;
    blocked.review_required = true;
    blocked.reason = 'ownership_recovery_required';
    blocked.owner_lease = 'expired';
    blocked.review.status = 'review_required';
    blocked.review.blocker = 'ownership_recovery_required';
    resumePreflight.mockResolvedValue(blocked);

    const view = create(<TaskDetailDrawer task={task({ state: 'interrupted' })} onClose={() => {}} />);
    const resume = view.root.findAllByType('button').find((b) => b.children.includes('Resume task'));
    expect(resume?.props.disabled).toBe(false);
    await act(async () => { resume?.props.onClick(); await new Promise((resolve) => setTimeout(resolve, 0)); });
    expect(resumePreflight).toHaveBeenCalledWith('t1');
    expect(resumeTask).not.toHaveBeenCalled();
    expect(JSON.stringify(view.toJSON())).toContain('Recovery conditions changed');
  });

  it('calls explicit resume only after successful fresh preflight', async () => {
    recoveryMock.data = clearRecovery();
    resumePreflight.mockResolvedValue(clearRecovery());
    resumeTask.mockResolvedValue(undefined);
    const view = create(<TaskDetailDrawer task={task({ state: 'paused' })} onClose={() => {}} />);
    const resume = view.root.findAllByType('button').find((b) => b.children.includes('Resume task'));
    expect(resume?.props.disabled).toBe(false);
    await act(async () => { resume?.props.onClick(); await new Promise((resolve) => setTimeout(resolve, 0)); });
    expect(resumePreflight).toHaveBeenCalledWith('t1');
    expect(resumeTask).toHaveBeenCalledTimes(1);
    expect(resumeTask).toHaveBeenCalledWith('t1');
  });

  it('re-reads fresh preflight on Resume before asking the backend to resume', async () => {
    // There is no manual recheck button anymore; owner-gated consent relies on
    // the click itself re-reading preflight immediately before the resume call.
    recoveryMock.data = clearRecovery();
    resumePreflight.mockResolvedValue(clearRecovery());
    resumeTask.mockResolvedValue(undefined);
    const view = create(<TaskDetailDrawer task={task({ state: 'interrupted' })} onClose={() => {}} />);
    const resume = view.root.findAllByType('button').find((b) => b.children.includes('Resume task'));
    expect(resume?.props.disabled).toBe(false);
    await act(async () => { resume?.props.onClick(); await new Promise((resolve) => setTimeout(resolve, 0)); });
    expect(resumePreflight).toHaveBeenCalledTimes(1);
    expect(resumePreflight).toHaveBeenCalledWith('t1');
    expect(resumePreflight.mock.invocationCallOrder[0]).toBeLessThan(resumeTask.mock.invocationCallOrder[0]);
    expect(resumeTask).toHaveBeenCalledWith('t1');
  });
});


describe('TaskDetailDrawer recovery snapshot revision', () => {
  afterEach(() => {
    recoveryMock.data = undefined;
    eventsMock.data = [];
  });

  it('shows the bounded evidence revision only under Advanced, without implying permission', async () => {
    recoveryMock.data = {
      ...clearRecovery(),
      review_binding: {
        schema: 'sythion.recovery_review_snapshot.v1',
        revision: 'sha256:' + 'a'.repeat(64),
        task_version: 7,
        last_event_sequence: 42,
        binding_status: 'read_only_observation',
        authenticated_human: false, authorization_issued: false,
        uncertainty_cleared: false, automatic_replay_allowed: false,
        note: 'Snapshot only',
      },
    };
    const view = create(<TaskDetailDrawer task={task({ state: 'interrupted' })} onClose={() => {}} />);
    const rendered = JSON.stringify(view.toJSON());
    // The safety wording stays in the default view; the raw hash does not.
    expect(rendered).toContain('not an approval');
    expect(rendered).not.toContain('Evidence snapshot');
    expect(rendered).not.toContain('aaaaaaaaaaaa');

    const toggle = view.root.findByProps({ 'data-testid': 'advanced-toggle-task-detail' });
    expect(toggle.props['aria-expanded']).toBe(false);
    await act(async () => { toggle.props.onClick(); });
    expect(view.root.findByProps({ 'data-testid': 'advanced-task-detail' })).toBeTruthy();
    const raw = JSON.stringify(view.toJSON());
    expect(raw).toContain('Evidence snapshot');
    expect(raw).toContain('aaaaaaaaaaaa');
    expect(raw).toContain('Task v');
    expect(raw).toContain('Event #');
  });
});

describe('failed task dismissal', () => {
  it('acknowledges a failed task without cancelling or deleting audit history', async () => {
    acknowledgeFailure.mockResolvedValue(undefined);
    const close = vi.fn();
    const view = create(<TaskDetailDrawer task={task({ state: 'failed' })} onClose={close} />);
    const action = view.root.findAllByType('button').find((b) =>
      b.children.includes('Dismiss failed task'));
    expect(action).toBeDefined();
    await act(async () => { action?.props.onClick(); await Promise.resolve(); });
    expect(acknowledgeFailure).toHaveBeenCalledWith('t1');
    expect(close).toHaveBeenCalled();
  });
});

describe('TaskDetailDrawer lazy-user default view', () => {
  const withBinding = (): import('../types').TaskResumePreflight => ({
    ...clearRecovery(),
    review_binding: {
      schema: 'sythion.recovery_review_snapshot.v1',
      revision: 'sha256:' + 'a'.repeat(64),
      task_version: 7,
      last_event_sequence: 42,
      binding_status: 'read_only_observation' as const,
      authenticated_human: false, authorization_issued: false,
      uncertainty_cleared: false, automatic_replay_allowed: false,
      note: 'Snapshot only',
    },
  });

  afterEach(() => {
    recoveryMock.data = undefined;
    eventsMock.data = [];
  });

  it('hides raw ids, versions, hashes and event data in the default view but keeps consent controls', () => {
    eventsMock.data = [{
      event_type: 'action.completed',
      sequence_num: 12,
      created_at: '2026-10-19T10:00:00',
      event_data: { tool_name: 'shell.exec' },
    }];
    recoveryMock.data = withBinding();
    const view = create(
      <TaskDetailDrawer task={task({ id: 'task-abc-123', state: 'interrupted' })} onClose={() => {}} />,
    );
    const text = JSON.stringify(view.toJSON());

    // Plain, owner-meaningful status and consent controls remain visible.
    expect(text).toContain('Recovery review');
    expect(text).toContain('not an approval');
    const buttons = view.root.findAllByType('button');
    expect(buttons.some((b) => b.children.includes('Resume task'))).toBe(true);
    // No redundant manual recheck control: the preflight hook auto-polls.
    expect(buttons.some((b) => b.children.includes('Recheck recovery'))).toBe(false);

    // No raw implementation detail in the default view.
    expect(text).not.toContain('task-abc-123');
    expect(text).not.toContain('Evidence snapshot');
    expect(text).not.toContain('aaaaaaaaaaaa');
    expect(text).not.toContain('Task v');
    expect(text).not.toContain('Event #');
    expect(text).not.toContain('The backend will verify again');
    expect(text).not.toContain('Current action');
    expect(text).not.toContain('Objective');
    expect(text).not.toContain('Version');
    expect(text).not.toContain('tool_name=shell.exec');
    expect(text).not.toContain('#12');
  });

  it('has one Advanced disclosure, closed by default, that reveals all raw detail when opened', async () => {
    eventsMock.data = [{
      event_type: 'action.completed',
      sequence_num: 12,
      created_at: '2026-10-19T10:00:00',
      event_data: { tool_name: 'shell.exec' },
    }];
    recoveryMock.data = withBinding();
    const view = create(
      <TaskDetailDrawer task={task({ id: 'task-abc-123', state: 'interrupted', version: 7 })} onClose={() => {}} />,
    );

    const toggles = view.root.findAllByType('button')
      .filter((b) => b.children.includes('Advanced / for support'));
    expect(toggles).toHaveLength(1);
    expect(toggles[0].props['aria-expanded']).toBe(false);
    expect(view.root.findAllByProps({ 'data-testid': 'advanced-task-detail' })).toHaveLength(0);

    await act(async () => { toggles[0].props.onClick(); });
    expect(view.root.findByProps({ 'data-testid': 'advanced-task-detail' })).toBeTruthy();
    const raw = JSON.stringify(view.toJSON());
    expect(raw).toContain('task-abc-123');
    expect(raw).toContain('Evidence snapshot');
    expect(raw).toContain('aaaaaaaaaaaa');
    expect(raw).toContain('Task v');
    expect(raw).toContain('Event #');
    expect(raw).toContain('The backend will verify again');
    expect(raw).toContain('Current action');
    expect(raw).toContain('Objective');
    expect(raw).toContain('Version');
    expect(raw).toContain('v7');
    expect(raw).toContain('tool_name=shell.exec');
    expect(raw).toContain('#12');
  });
});
