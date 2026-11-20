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
