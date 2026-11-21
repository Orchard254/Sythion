import { act, create } from 'react-test-renderer';
import { describe, expect, it, vi } from 'vitest';
import { RemoteActionsPanel } from './RemoteActionsPanel';
import type { ReconciliationRecord, RemoteAction } from '../types';

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

type View = ReturnType<typeof create>;

/** Visible text only — ignores props such as data-testid that carry raw ids. */
function renderedText(view: View): string {
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
  return parts.join(' ');
}

const button = (view: View, label: string) =>
  view.root.findAllByType('button').find((b) => b.children.join('') === label);

describe('RemoteActionsPanel', () => {
  it('renders an empty state when there are no actions', () => {
    const view = create(<RemoteActionsPanel actions={[]} />);
    expect(JSON.stringify(view.toJSON())).toContain('No device actions');
  });

  it('default view uses plain language and exposes no raw ids, keys or evidence', () => {
    const withEvidence: RemoteAction = {
      ...action('executing'),
      evidence_ref: 'evidence:task-system:checked',
    };
    const view = create(<RemoteActionsPanel actions={[withEvidence]} />);
    const text = renderedText(view);
    expect(text).toContain('Device action');
    expect(text).toContain('Running');
    expect(text).toContain('connection lost after send');

    for (const raw of [
      'ra_1', 'dev_1', 'task-1', 'k1', 'evidence:task-system:checked',
      'idempotency', 'evidence reference', 'effect_not_performed',
    ]) {
      expect(text.toLowerCase()).not.toContain(raw.toLowerCase());
    }
    expect(view.root.findAllByType('select')).toHaveLength(0);
    expect(view.root.findAllByType('input')).toHaveLength(0);
  });

  it('puts uncertain actions first and flags them for review in plain language', () => {
    const view = create(<RemoteActionsPanel actions={[
      action('executing', false, 'ra_running'),
      action('uncertain', true, 'ra_uncertain'),
    ]} />);
    const text = renderedText(view);
    expect(text).toContain('Needs review');
    expect(text).not.toContain('uncertain — needs reconciliation');
    const items = view.root.findAllByProps({ className: 'devices__item' });
    expect(items[0].findByType('strong').children.join('')).toBe('Action needs your review');
  });

  it('never renders a resolve control for a non-uncertain action', () => {
    const view = create(<RemoteActionsPanel actions={[action('awaiting_approval')]} />);
    const labels = view.root.findAllByType('button').map((b) => b.children.join(''));
    expect(labels).not.toContain('Resolve this action');
    expect(labels).not.toContain('Record decision');
    expect(labels).not.toContain('It worked');
  });

  it('maps the plain choices to the existing outcome values and keeps the safety promise', async () => {
    const cases = [
      ['It worked', 'effect_confirmed'],
      ["It didn't", 'effect_not_performed'],
      ["I'm not sure", 'abandoned'],
    ] as const;

    for (const [choiceLabel, expectedOutcome] of cases) {
      const onReconcile = vi.fn().mockResolvedValue(undefined);
      let view!: View;
      await act(async () => {
        view = create(
          <RemoteActionsPanel
            actions={[action('uncertain', true)]}
            onReconcile={onReconcile}
          />,
        );
      });

      expect(renderedText(view)).toContain('never repeats the original action');
      await act(async () => { button(view, 'Resolve this action')!.props.onClick(); });
      expect(renderedText(view)).toContain('Did this action go through?');

      await act(async () => { button(view, choiceLabel)!.props.onClick(); });
      const record = button(view, 'Record decision');
      expect(record).toBeDefined();
      expect(record!.props.disabled).toBe(false);
      await act(async () => { record!.props.onClick(); });
      await act(async () => { await Promise.resolve(); });

      expect(onReconcile).toHaveBeenCalledTimes(1);
      const [actionId, decisionId, outcome, evidenceRef, newKey] = onReconcile.mock.calls[0];
      expect(actionId).toBe('ra_1');
      expect(decisionId.startsWith('owner-ui-rec-')).toBe(true);
      expect(outcome).toBe(expectedOutcome);
      expect(evidenceRef).toBeUndefined();
      expect(newKey).toBeUndefined();
    }
  });

  it('requires an explicit choice before recording (fail-closed consent)', async () => {
    const onReconcile = vi.fn().mockResolvedValue(undefined);
    let view!: View;
    await act(async () => {
      view = create(
        <RemoteActionsPanel actions={[action('uncertain', true)]} onReconcile={onReconcile} />,
      );
    });
    await act(async () => { button(view, 'Resolve this action')!.props.onClick(); });
    expect(button(view, 'Record decision')!.props.disabled).toBe(true);
    await act(async () => { button(view, "It didn't")!.props.onClick(); });
    expect(button(view, 'Record decision')!.props.disabled).toBe(false);
    expect(onReconcile).not.toHaveBeenCalled();
  });

  it('keeps evidence and fresh-key fields in a collapsed advanced disclosure', async () => {
    const onReconcile = vi.fn().mockResolvedValue(undefined);
    let view!: View;
    await act(async () => {
      view = create(
        <RemoteActionsPanel actions={[action('uncertain', true)]} onReconcile={onReconcile} />,
      );
    });
    await act(async () => { button(view, 'Resolve this action')!.props.onClick(); });
    expect(view.root.findAllByType('input')).toHaveLength(0);

    await act(async () => { button(view, 'Advanced / for support')!.props.onClick(); });
    const inputs = view.root.findAllByType('input');
    expect(inputs).toHaveLength(2);
    const evidenceInput = inputs.find((i) => i.props['aria-label'] === 'Independent evidence reference');
    const keyInput = inputs.find((i) => i.props['aria-label'] === 'New idempotency key');
    expect(evidenceInput).toBeDefined();
    expect(keyInput).toBeDefined();

    await act(async () => {
      evidenceInput!.props.onChange({ target: { value: 'evidence:checked' } });
    });
    await act(async () => {
      keyInput!.props.onChange({ target: { value: 'fresh-key-2' } });
    });
    await act(async () => { button(view, 'It worked')!.props.onClick(); });
    await act(async () => { button(view, 'Record decision')!.props.onClick(); });
    await act(async () => { await Promise.resolve(); });

    const call = onReconcile.mock.calls[0];
    expect(call[2]).toBe('effect_confirmed');
    expect(call[3]).toBe('evidence:checked');
    expect(call[4]).toBe('fresh-key-2');
  });

  it('surfaces reconciliation errors', async () => {
    const onReconcile = vi.fn().mockRejectedValue(new Error('REPLAY_REFUSED'));
    let view!: View;
    await act(async () => {
      view = create(
        <RemoteActionsPanel actions={[action('uncertain', true)]} onReconcile={onReconcile} />,
      );
    });
    await act(async () => { button(view, 'Resolve this action')!.props.onClick(); });
    await act(async () => { button(view, "It didn't")!.props.onClick(); });
    await act(async () => { button(view, 'Record decision')!.props.onClick(); });
    await act(async () => { await Promise.resolve(); });
    expect(renderedText(view)).toContain('REPLAY_REFUSED');
  });

  it('shows past decisions in plain language without raw ids, outcomes or evidence', async () => {
    const record: ReconciliationRecord = {
      id: 'rec_1',
      action_id: 'ra_1',
      decision_id: 'owner-ui-rec-1',
      outcome: 'effect_confirmed',
      evidence_ref: 'evidence:checked',
      note: 'verified in target system',
      new_action_id: 'ra_fresh',
      created_at: '2026-11-20T09:00:00',
    };
    const loadHistory = vi.fn().mockResolvedValue([record]);
    let view!: View;
    await act(async () => {
      view = create(
        <RemoteActionsPanel actions={[action('uncertain', true)]} loadHistory={loadHistory} />,
      );
    });

    const toggle = view.root.findByProps({ 'data-testid': 'history-toggle-ra_1' });
    await act(async () => { toggle.props.onClick(); });
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await Promise.resolve(); });

    expect(loadHistory).toHaveBeenCalledWith('ra_1');
    const text = renderedText(view);
    expect(text).toContain('It worked');
    expect(text).toContain('verified in target system');
    for (const raw of ['owner-ui-rec-1', 'evidence:checked', 'ra_fresh', 'effect_confirmed']) {
      expect(text).not.toContain(raw);
    }
  });

  it('shows an explicit empty state in history', async () => {
    const loadHistory = vi.fn().mockResolvedValue([]);
    let view!: View;
    await act(async () => {
      view = create(
        <RemoteActionsPanel actions={[action('completed')]} loadHistory={loadHistory} />,
      );
    });
    const toggle = view.root.findByProps({ 'data-testid': 'history-toggle-ra_1' });
    await act(async () => { toggle.props.onClick(); });
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await Promise.resolve(); });
    expect(renderedText(view)).toContain('No decisions yet');
  });
});
