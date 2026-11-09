import { create } from 'react-test-renderer';
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

vi.mock('../hooks/useBackend', () => ({
  useTaskEvents: () => ({ data: [], refetch: vi.fn(), isLoading: false }),
  useActions: () => ({ live: true, replyToTask, resumeTask: vi.fn(), cancelTask: vi.fn() }),
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