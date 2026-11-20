import { create } from 'react-test-renderer';
import { describe, expect, it } from 'vitest';
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

  it('never renders a control that could approve or replay an action', () => {
    const view = create(<RemoteActionsPanel actions={[action('awaiting_approval')]} />);
    expect(view.root.findAllByType('button')).toHaveLength(0);
  });
});
