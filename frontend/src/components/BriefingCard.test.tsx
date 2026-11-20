import { create } from 'react-test-renderer';
import { describe, expect, it, vi } from 'vitest';
import { BriefingCard } from './BriefingCard';
import type { Briefing } from '../types';

const briefing: Briefing = {
  headline: 'Heads up — 1 item(s) need your attention.',
  items: [{
    kind: 'task_needs_input',
    ref: 't-1',
    title: 'A task is waiting for your input',
    reason: 'Which file should I use?',
    suggested_action: 'Answer the question in the task view.',
    authority: 'none',
    approved: false,
  }],
  has_blockers: true,
  authorized: false,
  approval_issued: false,
  execution_started: false,
  note: 'This is a read-only summary, not an approval.',
};

describe('BriefingCard', () => {
  it('shows the heads-up and its read-only note', () => {
    const view = create(<BriefingCard briefing={briefing} />);
    const rendered = JSON.stringify(view.toJSON());
    expect(rendered).toContain('Heads up');
    expect(rendered).toContain('Which file should I use?');
    expect(rendered).toContain('not an approval');
  });

  it('renders nothing when there are no blockers', () => {
    const view = create(<BriefingCard briefing={{ ...briefing, has_blockers: false, items: [] }} />);
    expect(view.toJSON()).toBeNull();
  });

  it('never renders an approve control', () => {
    const view = create(<BriefingCard briefing={briefing} />);
    const labels = view.root.findAllByType('button').map((b) =>
      b.children.filter((c) => typeof c === 'string').join(''));
    expect(labels).not.toContain('Approve');
    expect(labels).not.toContain('Approve this action');
  });

  it('links reconciliation items to the Control Center review panel', () => {
    const onNavigate = vi.fn();
    const view = create(<BriefingCard briefing={briefing} onNavigate={onNavigate} />);
    const review = view.root.findAllByType('button')
      .find((b) => b.children.some((c) => typeof c === 'string' && c === 'Review in Control Center'));
    expect(review).toBeDefined();
    review!.props.onClick();
    expect(onNavigate).toHaveBeenCalledWith('overview');
  });

  it('links pending approvals to the Security approvals panel', () => {
    const onNavigate = vi.fn();
    const withPermission = {
      ...briefing,
      items: [{ ...briefing.items[0], kind: 'permission_awaiting' }],
    };
    const view = create(<BriefingCard briefing={withPermission} onNavigate={onNavigate} />);
    const approvals = view.root.findAllByType('button')
      .find((b) => b.children.some((c) => typeof c === 'string' && c === 'Open approvals'));
    expect(approvals).toBeDefined();
    approvals!.props.onClick();
    expect(onNavigate).toHaveBeenCalledWith('security');
  });
});
