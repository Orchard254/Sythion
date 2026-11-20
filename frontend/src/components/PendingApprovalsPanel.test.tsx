import { create, type ReactTestRenderer } from 'react-test-renderer';
import { beforeAll, describe, expect, it, vi } from 'vitest';
import { newDecisionId, PendingApprovalsPanel } from './PendingApprovalsPanel';
import type { PendingApproval } from '../types';

beforeAll(() => {
  if (typeof globalThis.window === 'undefined') {
    (globalThis as { window: unknown }).window = {
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    };
  }
});

const proposal: PendingApproval = {
  proposal_id: 'p-1',
  task_id: 'task-1',
  action_id: 'a-1',
  tool_name: 'file_create',
  task_version: 3,
  target_canonical: 'C:/tmp/demo.txt',
  proposal_digest: 'abc',
  created_at: '2026-11-20T08:00:00',
};

describe('newDecisionId', () => {
  it('is UI-generated, prefixed, and unique', () => {
    const first = newDecisionId();
    const second = newDecisionId();
    expect(first.startsWith('owner-ui-')).toBe(true);
    expect(first).not.toBe(second);
  });
});

describe('PendingApprovalsPanel', () => {
  it('renders an empty state when nothing is pending', () => {
    const view = create(
      <PendingApprovalsPanel proposals={[]} onDecide={vi.fn()} />,
    );
    expect(JSON.stringify(view.toJSON())).toContain('Nothing to decide');
  });

  it('shows the exact proposal being decided', () => {
    const view = create(
      <PendingApprovalsPanel proposals={[proposal]} onDecide={vi.fn()} />,
    );
    const rendered = JSON.stringify(view.toJSON());
    expect(rendered).toContain('file_create');
    expect(rendered).toContain('task-1');
    expect(rendered).toContain('C:/tmp/demo.txt');
    expect(screenlessTestId(view)).toBe('pending-approvals-panel');
  });

  it('sends deny with a generated decision id', async () => {
    const onDecide = vi.fn().mockResolvedValue(undefined);
    const view = create(
      <PendingApprovalsPanel proposals={[proposal]} onDecide={onDecide} />,
    );
    const deny = view.root.findAllByType('button')
      .find((button) => button.children.join('') === 'Deny');
    expect(deny).toBeDefined();
    await deny!.props.onClick();
    expect(onDecide).toHaveBeenCalledTimes(1);
    const [proposalId, decisionId, decision] = onDecide.mock.calls[0];
    expect(proposalId).toBe('p-1');
    expect(decision).toBe('deny');
    expect(decisionId.startsWith('owner-ui-')).toBe(true);
  });

  it('requires confirmation before approve', () => {
    const onDecide = vi.fn();
    const view = create(
      <PendingApprovalsPanel proposals={[proposal]} onDecide={onDecide} />,
    );
    const approve = view.root.findAllByType('button')
      .find((button) => button.children.join('') === 'Approve');
    approve!.props.onClick();
    expect(onDecide).not.toHaveBeenCalled();
    expect(JSON.stringify(view.toJSON())).toContain('Approve file_create?');
  });
});

function screenlessTestId(view: ReactTestRenderer): string | null {
  const panel = view.root.findAllByProps({ 'data-testid': 'pending-approvals-panel' });
  return panel.length ? 'pending-approvals-panel' : null;
}
