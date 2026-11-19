import { create } from 'react-test-renderer';
import { describe, expect, it } from 'vitest';
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
    expect(view.root.findAllByType('button')).toHaveLength(0);
  });
});
