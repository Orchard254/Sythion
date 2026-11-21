import { act, create, type ReactTestInstance, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { OperationsScreen } from './Operations';
import type { Objective, Task } from '../types';

const mock = vi.hoisted(() => ({
  tasks: [] as Task[],
  objectives: [] as Objective[],
  health: { status: 'ok', database_connected: true } as Record<string, unknown>,
  healthError: false,
  agentState: 'active' as string | undefined,
  capabilities: [] as Array<{ id: string; status: string }>,
}));

vi.mock('../hooks/useBackend', () => ({
  useTasks: () => ({ data: mock.tasks, isLoading: false, refetch: vi.fn() }),
  useObjectives: () => ({ data: mock.objectives, isLoading: false, refetch: vi.fn() }),
  useHealth: () => ({ data: mock.health, isError: mock.healthError, refetch: vi.fn() }),
  useAgent: () => ({ data: mock.agentState ? { state: mock.agentState } : null }),
  useCapabilities: () => ({ data: mock.capabilities }),
  useRemoteActions: () => ({ data: { actions: [] } }),
}));

vi.mock('../services/api', () => ({
  taskService: {
    acknowledgedFailures: async () => [],
    acknowledgeFailure: vi.fn(),
  },
  reconciliationService: { list: async () => [], reconcile: vi.fn() },
}));

// Drawers are out of scope: rendering them pulls in unrelated read models.
vi.mock('../components/GoalDetailDrawer', () => ({ GoalDetailDrawer: () => null }));
vi.mock('../components/TaskDetailDrawer', () => ({ TaskDetailDrawer: () => null }));

const task = (overrides: Record<string, unknown> = {}): Task => ({
  id: 'task-run',
  original_request: 'Wire the encoder',
  normalized_goal: null,
  state: 'running',
  version: 1,
  active_action_id: null,
  pending_question: null,
  final_result: null,
  terminal_reason: null,
  created_at: '2026-11-21T09:00:00Z',
  updated_at: '2099-01-01T00:00:00Z',
  ...overrides,
} as Task);

const objective = (status = 'running'): Objective => ({
  id: 'goal-1',
  title: 'Set up the studio',
  purpose: null,
  status: 'active',
  priority: 1,
  current_summary: null,
  last_task_id: null,
  completion_criteria: null,
  criteria_verified: false,
  parent_objective_id: null,
  created_at: '2026-11-21T09:00:00Z',
  updated_at: '2026-11-21T09:01:00Z',
  steps: [{
    id: 1,
    objective_id: 'goal-1',
    step_order: 1,
    description: 'Wire the encoder',
    status,
    linked_task_id: 'task-run',
    result_summary: null,
    blocker_reason: null,
    created_at: '2026-11-21T09:00:00Z',
    updated_at: '2026-11-21T09:01:00Z',
  }],
  sub_objectives: [],
} as Objective);

/** Visible text only — props such as data-testid carry raw ids, not shown text. */
function renderedText(view: ReactTestRenderer): string {
  const parts: string[] = [];
  const walk = (node: unknown): void => {
    if (node === null || node === undefined) return;
    if (typeof node === 'string' || typeof node === 'number') {
      parts.push(String(node));
      return;
    }
    if (Array.isArray(node)) {
      node.forEach(walk);
      return;
    }
    if (typeof node === 'object' && 'children' in node) {
      ((node as { children?: unknown[] }).children ?? []).forEach(walk);
    }
  };
  walk(view.toJSON());
  // JSX splits literal text from interpolated values into separate nodes.
  return parts.join(' ').replace(/\s+/g, ' ').trim();
}

/** Button text including icon-bearing buttons (whose first child is an Icon). */
function labelOf(node: ReactTestInstance): string {
  const parts: string[] = [];
  const walk = (value: unknown): void => {
    if (value === null || value === undefined || typeof value === 'boolean') return;
    if (typeof value === 'string' || typeof value === 'number') {
      parts.push(String(value));
      return;
    }
    if (Array.isArray(value)) {
      value.forEach(walk);
      return;
    }
    if (typeof value === 'object' && 'props' in value) {
      const children = (value as { props?: { children?: unknown } }).props?.children;
      if (children !== undefined) walk(children);
    }
  };
  walk(node.children);
  return parts.join(' ').replace(/\s+/g, ' ').trim();
}

const button = (view: ReactTestRenderer, label: string) =>
  view.root.findAllByType('button').find((b) => labelOf(b) === label);
const buttonLabels = (view: ReactTestRenderer) =>
  view.root.findAllByType('button').map(labelOf);

describe('Operations — lazy-user presentation', () => {
  let view: ReactTestRenderer;

  beforeEach(() => {
    mock.tasks = [];
    mock.objectives = [];
    mock.health = { status: 'ok', database_connected: true };
    mock.healthError = false;
    mock.agentState = 'active';
    mock.capabilities = [{ id: 'files', status: 'AVAILABLE' }];
  });

  afterEach(() => {
    act(() => view?.unmount());
  });

  it('shows a plain status and no raw agent state token by default', async () => {
    mock.agentState = 'degraded';
    await act(async () => { view = create(<OperationsScreen />); });

    const shown = renderedText(view);
    expect(shown).toContain('Synth needs your attention');
    expect(shown).toContain("Synth isn't active right now.");
    expect(shown).not.toContain('agent degraded');
    expect(shown.toLowerCase()).not.toContain('degraded');
    expect(shown).not.toContain('agent state');
    expect(shown).not.toContain('Everything working');
  });

  it('claims all-clear only when every signal is healthy', async () => {
    await act(async () => { view = create(<OperationsScreen />); });

    expect(renderedText(view)).toContain('Everything working');
    expect(renderedText(view)).not.toContain('Synth needs your attention');
  });

  it('does not report all-clear when a signal is unknown', async () => {
    mock.agentState = undefined;
    await act(async () => { view = create(<OperationsScreen />); });

    const shown = renderedText(view);
    expect(shown).toContain('Status unknown');
    expect(shown).not.toContain('Everything working');
  });

  it('keeps raw step statuses behind a closed Advanced / for support disclosure', async () => {
    mock.agentState = 'degraded';
    mock.tasks = [task({ objective_id: 'goal-1' })];
    mock.objectives = [objective('running')];
    await act(async () => { view = create(<OperationsScreen />); });

    // Default view: plain step text, no raw status or step-order token.
    const shown = renderedText(view);
    expect(shown).toContain('Latest step: Wire the encoder');
    expect(shown).not.toContain('step 1:');
    expect(shown).not.toContain('(running)');
    expect(shown).not.toContain('agent state:');
    expect(view.root.findAllByProps({ 'data-testid': 'operations-advanced' })).toHaveLength(0);
    expect(button(view, 'Advanced / for support')!.props['aria-expanded']).toBe(false);

    // Opening the single disclosure reveals the raw diagnostic values.
    await act(async () => { button(view, 'Advanced / for support')!.props.onClick(); });
    const detailed = renderedText(view);
    expect(detailed).toContain('step 1: Wire the encoder (running)');
    expect(detailed).toContain('agent state: degraded');
    expect(view.root.findAllByProps({ 'data-testid': 'operations-advanced' })).toHaveLength(1);
    expect(button(view, 'Hide advanced details')!.props['aria-expanded']).toBe(true);
  });

  it('keeps actionable controls visible in the default view', async () => {
    mock.tasks = [
      task({ objective_id: 'goal-1' }),
      task({
        id: 'task-fail',
        state: 'failed',
        objective_id: 'goal-1',
        terminal_reason: 'The upload did not complete.',
        updated_at: '2099-01-01T00:00:01Z',
      }),
      task({
        id: 'task-fail-2',
        state: 'failed',
        terminal_reason: 'The second upload did not complete.',
        updated_at: '2099-01-01T00:00:02Z',
      }),
    ];
    mock.objectives = [objective()];
    await act(async () => { view = create(<OperationsScreen />); });

    const labels = buttonLabels(view);
    for (const control of ['Dismiss failure', 'Open task', 'Open goal', 'Details', 'All goals']) {
      expect(labels).toContain(control);
    }
    // The disclosure is additive: it never replaces an actionable control.
    expect(labels).toContain('Advanced / for support');
  });
});
