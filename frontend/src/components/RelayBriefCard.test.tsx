import { create } from 'react-test-renderer';
import { describe, expect, it, vi } from 'vitest';
import { RelayBriefCard } from './RelayBriefCard';
import { parseRelayBrief, type StructuredBrief } from '../lib/relayBrief';

const brief: StructuredBrief = {
  kind: 'morning_brief',
  headline: 'Good morning.',
  rows: [
    { key: 'reconciliation', label: 'Needs reconciliation', detail: 'ra_1 unresolved' },
    { key: 'devices', label: 'Registered devices', detail: 'dev_1' },
    { key: 'goals', label: 'Goals finished', detail: 'Ship report' },
  ],
  note: 'Read-only brief.',
};

describe('RelayBriefCard', () => {
  it('renders structured rows and the note', () => {
    const view = create(<RelayBriefCard brief={brief} />);
    const rendered = JSON.stringify(view.toJSON());
    expect(rendered).toContain('Good morning.');
    expect(rendered).toContain('Needs reconciliation');
    expect(rendered).toContain('Read-only brief.');
  });

  it('renders a read-only status summary for a morning brief', () => {
    const statusBrief = parseRelayBrief('morning_brief', JSON.stringify({
      headline: 'Good morning.',
      sections: [
        { key: 'system', title: 'Local health', lines: ['Overall: DEGRADED.'] },
        { key: 'agent', title: 'Synth', lines: ['State: paused.'] },
        { key: 'devices', title: 'Registered devices', lines: ['this-pc [dev_1]: trust=trusted, last_seen=...'] },
      ],
      note: '',
    }))!;
    const view = create(<RelayBriefCard brief={statusBrief} />);
    expect(view.root.findByProps({ 'data-testid': 'relay-brief-status' })).toBeDefined();
    const statusText = view.root.findAllByType('span').map((b) =>
      b.children.filter((c) => typeof c === 'string').join('')).join(' ');
    expect(statusText).toContain('health: DEGRADED');
    expect(statusText).toContain('agent: paused');
    expect(statusText).toContain('device: trusted');
    // Status is read-only: no action buttons without a handler.
    expect(view.root.findAllByType('button')).toHaveLength(0);
  });

  it('renders a read-only decisions badge for decision rows', () => {
    const view = create(<RelayBriefCard brief={brief} />);
    const status = view.root.findByProps({ 'data-testid': 'relay-brief-status' });
    expect(status).toBeDefined();
    const statusText = view.root.findAllByType('span').map((b) =>
      b.children.filter((c) => typeof c === 'string').join('')).join(' ');
    expect(statusText).toContain('decisions: 1');
    // The badge is read-only: no action buttons without a handler.
    expect(view.root.findAllByType('button')).toHaveLength(0);
  });

  it('renders a read-only receipts badge with the ack high-water mark', () => {
    const withReceipts = parseRelayBrief('morning_brief', JSON.stringify({
      headline: 'Good morning.',
      sections: [
        { key: 'receipts', title: 'Delivery receipts',
          lines: ['session rs_1: acked through #12', 'session rs_2: acked through #7'] },
      ],
      note: '',
    }))!;
    const view = create(<RelayBriefCard brief={withReceipts} />);
    expect(view.root.findByProps({ 'data-testid': 'relay-brief-status' })).toBeDefined();
    const text = view.root.findAllByType('span').map((b) =>
      b.children.filter((c) => typeof c === 'string').join('')).join(' ');
    expect(text).toContain('receipts: 2');
    expect(text).toContain('through #12');
    // Status is read-only; no action buttons without a handler.
    expect(view.root.findAllByType('button')).toHaveLength(0);
  });

  it('renders no status strip when no status or decision sections exist', () => {
    const calm: StructuredBrief = {
      kind: 'morning_brief',
      headline: 'Good morning.',
      rows: [{ key: 'goals', label: 'Goals finished', detail: 'Ship report' }],
      note: '',
    };
    const view = create(<RelayBriefCard brief={calm} />);
    expect(view.root.findAllByProps({ 'data-testid': 'relay-brief-status' })).toHaveLength(0);
  });

  it('renders no action buttons without an onAction handler', () => {
    const view = create(<RelayBriefCard brief={brief} />);
    expect(view.root.findAllByType('button')).toHaveLength(0);
  });

  it('renders owner actions and navigates to the right routes', () => {
    const onAction = vi.fn();
    const view = create(<RelayBriefCard brief={brief} onAction={onAction} />);
    expect(view.root.findByProps({ 'data-testid': 'relay-brief-actions' })).toBeDefined();
    const labels = view.root.findAllByType('button').map((b) =>
      b.children.filter((c) => typeof c === 'string').join(''));
    expect(labels).toEqual(['Review', 'Devices', 'Goals']);
    view.root.findAllByType('button').forEach((b) => b.props.onClick());
    expect(onAction).toHaveBeenCalledWith('overview');
    expect(onAction).toHaveBeenCalledWith('system');
    expect(onAction).toHaveBeenCalledWith('missions');
  });

  it('renders diagnostics and agent-status actions for degraded sections', () => {
    const onAction = vi.fn();
    const degraded = parseRelayBrief('morning_brief', JSON.stringify({
      headline: 'Good morning.',
      sections: [
        { key: 'system', title: 'Local health', lines: ['Overall: DEGRADED.'] },
        { key: 'agent', title: 'Synth', lines: ['State: paused.'] },
      ],
      note: '',
    }))!;
    const view = create(<RelayBriefCard brief={degraded} onAction={onAction} />);
    const labels = view.root.findAllByType('button').map((b) =>
      b.children.filter((c) => typeof c === 'string').join(''));
    expect(labels).toEqual(['Diagnostics', 'Agent status']);
    view.root.findAllByType('button').forEach((b) => b.props.onClick());
    expect(onAction).toHaveBeenCalledWith('system');
  });

  it('renders no actions for a calm parsed brief', () => {
    const calm = parseRelayBrief('morning_brief', JSON.stringify({
      headline: 'Good morning.',
      sections: [{ key: 'goals', title: 'Goals', lines: ['None reported.'] }],
      note: '',
    }))!;
    const onAction = vi.fn();
    const view = create(<RelayBriefCard brief={calm} onAction={onAction} />);
    expect(view.root.findAllByType('button')).toHaveLength(0);
  });
});
