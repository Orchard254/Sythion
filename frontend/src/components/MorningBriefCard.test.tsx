import { create } from 'react-test-renderer';
import { describe, expect, it, vi } from 'vitest';
import { MorningBriefCard } from './MorningBriefCard';
import type { MorningBrief } from '../types';

const brief: MorningBrief = {
  date: '2026-11-20',
  generated_at: '2026-11-20T09:00:00',
  headline: 'Good morning — here is your brief for 2026-11-20.',
  sections: [
    { key: 'goals', title: 'Goals finished since yesterday', lines: ['Ship the report'] },
    { key: 'blockers', title: 'Needs your attention', lines: ['A task is waiting for your input: Which file?'] },
  ],
  text: 'read-only text',
  authorized: false,
  approval_issued: false,
  execution_started: false,
  note: 'This is a read-only brief from local state. It is not an approval.',
};

describe('MorningBriefCard', () => {
  it('renders the brief sections and its read-only note', () => {
    const view = create(<MorningBriefCard brief={brief} />);
    const rendered = JSON.stringify(view.toJSON());
    expect(rendered).toContain('Good morning');
    expect(rendered).toContain('Ship the report');
    expect(rendered).toContain('Which file?');
    expect(rendered).toContain('not an approval');
  });

  it('never renders a control that could authorize an action', () => {
    const view = create(<MorningBriefCard brief={brief} />);
    const labels = view.root.findAllByType('button').map((b) =>
      b.children.filter((c) => typeof c === 'string').join(''));
    expect(labels).not.toContain('Approve');
  });

  it('links reconciliation and approvals to the Control Center panels', () => {
    const onNavigate = vi.fn();
    const withReconciliation = {
      ...brief,
      sections: [
        ...brief.sections,
        { key: 'reconciliation', title: 'Needs reconciliation',
          lines: ['Uncertain remote action ra_1: outcome unknown'] },
      ],
    };
    const view = create(<MorningBriefCard brief={withReconciliation} onNavigate={onNavigate} />);
    const review = view.root.findAllByType('button')
      .find((b) => b.children.some((c) => typeof c === 'string' && c === 'Review in Control Center'));
    const approvals = view.root.findAllByType('button')
      .find((b) => b.children.some((c) => typeof c === 'string' && c === 'Open approvals'));
    expect(review).toBeDefined();
    expect(approvals).toBeDefined();
    review!.props.onClick();
    approvals!.props.onClick();
    expect(onNavigate).toHaveBeenCalledWith('overview');
    expect(onNavigate).toHaveBeenCalledWith('security');
  });

  it('renders no navigation when there is nothing to review', () => {
    const calm = {
      ...brief,
      sections: [
        { key: 'goals', title: 'Goals', lines: ['Ship the report'] },
        { key: 'blockers', title: 'Needs your attention', lines: ['Nothing is blocked.'] },
        { key: 'reconciliation', title: 'Needs reconciliation', lines: ['Nothing needs reconciliation.'] },
      ],
    };
    const view = create(<MorningBriefCard brief={calm} />);
    expect(view.root.findAllByType('button')).toHaveLength(0);
  });
});
