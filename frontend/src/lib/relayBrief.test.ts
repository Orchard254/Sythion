import { describe, expect, it } from 'vitest';
import { briefActions, parseRelayBrief } from './relayBrief';

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
      { key: 'goals', label: 'Goals finished', detail: 'Ship the report' },
      { key: 'system', label: 'Local health', detail: 'Overall: DEGRADED.' },
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
      key: 'task_needs_input', label: 'A task is waiting', detail: 'Which file?',
    });
  });

  it('returns null for non-brief kinds and malformed text', () => {
    expect(parseRelayBrief('text', 'hello')).toBeNull();
    expect(parseRelayBrief('morning_brief', 'not json')).toBeNull();
    expect(parseRelayBrief('briefing', '{}')).toBeNull();
  });
});

describe('briefActions', () => {
  it('derives distinct reconciliation/approvals/devices/goals actions', () => {
    const text = JSON.stringify({
      headline: 'Good morning.',
      sections: [
        { key: 'reconciliation', title: 'Needs reconciliation', lines: ['Uncertain action ra_1'] },
        { key: 'blockers', title: 'Needs your attention', lines: ['task held'] },
        { key: 'devices', title: 'Registered devices', lines: ['dev_1'] },
        { key: 'device_actions', title: 'Recent device actions', lines: ['ra_1: completed'] },
        { key: 'goals', title: 'Goals finished', lines: ['Ship report'] },
        { key: 'system', title: 'Local health', lines: ['Overall: HEALTHY.'] },
      ],
    });
    const brief = parseRelayBrief('morning_brief', text)!;
    const actions = briefActions(brief);
    expect(actions).toEqual([
      { label: 'Review', route: 'overview' },
      { label: 'Devices', route: 'system' },
      { label: 'Device actions', route: 'overview' },
      { label: 'Goals', route: 'missions' },
    ]);
  });

  it('skips empty sections and yields no actions for a calm brief', () => {
    const text = JSON.stringify({
      headline: 'Good morning.',
      sections: [
        { key: 'reconciliation', title: 'Needs reconciliation', lines: ['Nothing needs reconciliation.'] },
        { key: 'goals', title: 'Goals finished', lines: ['None reported.'] },
      ],
    });
    expect(briefActions(parseRelayBrief('morning_brief', text)!)).toEqual([]);
  });

  it('offers diagnostics only for a non-healthy system section', () => {
    const detail = 'Overall: DEGRADED. · database: HEALTHY';
    const degraded = parseRelayBrief('morning_brief', JSON.stringify({
      headline: 'Good morning.',
      sections: [{ key: 'system', title: 'Local health', lines: [detail] }],
      note: '',
    }))!;
    expect(briefActions(degraded)).toEqual([
      { label: 'Diagnostics', route: 'system' },
    ]);

    const healthy = parseRelayBrief('morning_brief', JSON.stringify({
      headline: 'Good morning.',
      sections: [{ key: 'system', title: 'Local health', lines: ['Overall: HEALTHY.'] }],
      note: '',
    }))!;
    expect(briefActions(healthy)).toEqual([]);
  });

  it('offers agent status only for a non-active agent section', () => {
    const paused = parseRelayBrief('morning_brief', JSON.stringify({
      headline: 'Good morning.',
      sections: [{ key: 'agent', title: 'Synth', lines: ['State: paused.'] }],
      note: '',
    }))!;
    expect(briefActions(paused)).toEqual([
      { label: 'Agent status', route: 'system' },
    ]);

    const active = parseRelayBrief('morning_brief', JSON.stringify({
      headline: 'Good morning.',
      sections: [{ key: 'agent', title: 'Synth', lines: ['State: active.'] }],
      note: '',
    }))!;
    expect(briefActions(active)).toEqual([]);
  });

  it('maps a permission_awaiting heads-up item to approvals', () => {
    const text = JSON.stringify({
      headline: 'Heads up.', has_blockers: true,
      items: [{ kind: 'permission_awaiting', title: 'Permission', reason: 'file_write' }],
      note: '',
    });
    expect(briefActions(parseRelayBrief('briefing', text)!)).toEqual([
      { label: 'Approvals', route: 'security' },
    ]);
  });
});
