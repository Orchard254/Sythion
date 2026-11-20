import { act, create } from 'react-test-renderer';
import { describe, expect, it, vi } from 'vitest';
import { RemoteActionsPanel } from './RemoteActionsPanel';
import type { RemoteAction } from '../types';

const action = (state: RemoteAction['state'], uncertain = false, id = 'ra_1'): RemoteAction => ({
  action_id: id,
  idempotency_key: 'k1',
  device_id: 'dev_1',
  task_id: 'task-1',
  owned_action_id: null,
  state,
  version: 2,
  reason: 'connection lost after send',
  approval_decision_id: null,
  evidence_ref: null,
  created_at: '2026-11-20T08:00:00',
  updated_at: '2026-11-20T08:01:00',
  resolved_at: null,
  terminal: uncertain,
  uncertain,
  authorized: false,
  approval_issued: false,
});

describe('RemoteActionsPanel', () => {
  it('renders an empty state when there are no actions', () => {
    const view = create(<RemoteActionsPanel actions={[]} />);
    expect(JSON.stringify(view.toJSON())).toContain('No device actions');
  });

  it('shows action state, device and reason', () => {
    const view = create(<RemoteActionsPanel actions={[action('executing')]} />);
    const rendered = JSON.stringify(view.toJSON());
    expect(rendered).toContain('ra_1');
    expect(rendered).toContain('executing');
    expect(rendered).toContain('dev_1');
    expect(rendered).toContain('connection lost after send');
  });

  it('puts uncertain actions first and flags them for reconciliation', () => {
    const view = create(<RemoteActionsPanel actions={[
      action('executing', false, 'ra_running'),
      action('uncertain', true, 'ra_uncertain'),
    ]} />);
    const rendered = JSON.stringify(view.toJSON());
    expect(rendered).toContain('uncertain — needs reconciliation');
    const items = view.root.findAllByProps({ className: 'devices__item' });
    expect(items[0].findByType('strong').children.join('')).toBe('ra_uncertain');
  });

  it('never renders a control that could approve or replay a non-uncertain action', () => {
    const view = create(<RemoteActionsPanel actions={[action('awaiting_approval')]} />);
    expect(view.root.findAllByType('button')).toHaveLength(0);
  });

  it('records a reconciliation with a UI-generated decision id for uncertain actions', async () => {
    const onReconcile = vi.fn().mockResolvedValue(undefined);
    const onReconciled = vi.fn();
    let view!: ReturnType<typeof create>;
    await act(async () => {
      view = create(
        <RemoteActionsPanel
          actions={[action('uncertain', true)]}
          onReconcile={onReconcile}
          onReconciled={onReconciled}
        />,
      );
    });

    const reconcileButton = view.root.findAllByType('button')
      .find((b) => b.children.join('') === 'Reconcile');
    expect(reconcileButton).toBeDefined();
    await act(async () => { reconcileButton!.props.onClick(); });
    await act(async () => { await Promise.resolve(); });

    const recordButton = view.root.findAllByType('button')
      .find((b) => b.children.join('') === 'Record reconciliation');
    expect(recordButton).toBeDefined();
    await act(async () => { recordButton!.props.onClick(); });
    await act(async () => { await Promise.resolve(); });

    expect(onReconcile).toHaveBeenCalledTimes(1);
    const [actionId, decisionId, outcome] = onReconcile.mock.calls[0];
    expect(actionId).toBe('ra_1');
    expect(decisionId.startsWith('owner-ui-rec-')).toBe(true);
    expect(outcome).toBe('effect_not_performed');
    expect(onReconciled).toHaveBeenCalledTimes(1);
  });

  it('surfaces reconciliation errors', async () => {
    const onReconcile = vi.fn().mockRejectedValue(new Error('REPLAY_REFUSED'));
    let view!: ReturnType<typeof create>;
    await act(async () => {
      view = create(
        <RemoteActionsPanel
          actions={[action('uncertain', true)]}
          onReconcile={onReconcile}
        />,
      );
    });
    const reconcileButton = view.root.findAllByType('button')
      .find((b) => b.children.join('') === 'Reconcile');
    await act(async () => { reconcileButton!.props.onClick(); });
    const recordButton = view.root.findAllByType('button')
      .find((b) => b.children.join('') === 'Record reconciliation');
    await act(async () => { recordButton!.props.onClick(); });
    await act(async () => { await Promise.resolve(); });
    expect(JSON.stringify(view.toJSON())).toContain('REPLAY_REFUSED');
  });
});
