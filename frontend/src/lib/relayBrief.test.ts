import { describe, expect, it } from 'vitest';
import { parseRelayBrief } from './relayBrief';

describe('parseRelayBrief', () => {
  it('parses a seeded morning_brief into structured rows', () => {
    const text = JSON.stringify({
      date: '2026-11-20', headline: 'Good morning.',
      sections: [
        { key: 'goals', title: 'Goals finished', lines: ['Ship the report'] },
        { key: 'system', title: 'Local health', lines: ['Overall: DEGRADED.'] },
      ],
      note: 'Read-only brief.',
    });
    const result = parseRelayBrief('morning_brief', text);
    expect(result).not.toBeNull();
    expect(result!.kind).toBe('morning_brief');
    expect(result!.headline).toBe('Good morning.');
    expect(result!.rows).toEqual([
      { label: 'Goals finished', detail: 'Ship the report' },
      { label: 'Local health', detail: 'Overall: DEGRADED.' },
    ]);
    expect(result!.note).toBe('Read-only brief.');
  });

  it('parses a seeded briefing into structured rows', () => {
    const text = JSON.stringify({
      headline: 'Heads up.', has_blockers: true,
      items: [{ title: 'A task is waiting', reason: 'Which file?', kind: 'task_needs_input' }],
      note: 'Not an approval.',
    });
    const result = parseRelayBrief('briefing', text);
    expect(result).not.toBeNull();
    expect(result!.kind).toBe('briefing');
    expect(result!.rows[0]).toEqual({
      label: 'A task is waiting', detail: 'Which file?',
    });
  });

  it('returns null for non-brief kinds and malformed text', () => {
    expect(parseRelayBrief('text', 'hello')).toBeNull();
    expect(parseRelayBrief('morning_brief', 'not json')).toBeNull();
    expect(parseRelayBrief('briefing', '{}')).toBeNull();
  });
});
