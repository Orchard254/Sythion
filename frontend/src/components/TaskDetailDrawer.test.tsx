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
const recoveryMock = vi.hoisted(() => ({
  data: undefined as import('../types').TaskResumePreflight | undefined,
  isLoading: false,
  isFetching: false,
  isError: false,
  refetch: vi.fn(),
}));

vi.mock('../services/api', () => ({
  taskService: { resumePreflight },
}));

vi.mock('../hooks/useBackend', () => ({
  useTaskEvents: () => ({ data: [], refetch: vi.fn(), isLoading: false }),
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
});


describe('TaskDetailDrawer recovery snapshot revision', () => {
  it('shows the bounded evidence revision without implying permission', () => {
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
    expect(rendered).toContain('Evidence snapshot');
    expect(rendered).toContain('aaaaaaaaaaaa');
    expect(rendered).toContain('Task v');
    expect(rendered).toContain('Event #');
    expect(rendered).toContain('not an approval');
    recoveryMock.data = undefined;
  });
});
