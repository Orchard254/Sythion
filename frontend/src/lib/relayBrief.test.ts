import { describe, expect, it } from 'vitest';
import { briefActions, briefStatusSummary, parseRelayBrief } from './relayBrief';

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

  it('maps a receipts section to the System receipt panel and skips empty ones', () => {
    const withReceipts = parseRelayBrief('morning_brief', JSON.stringify({
      headline: 'Good morning.',
      sections: [{ key: 'receipts', title: 'Delivery receipts', lines: ['session rs_1: acked through #12'] }],
      note: '',
    }))!;
    expect(briefActions(withReceipts)).toEqual([
      { label: 'Receipts', route: 'system' },
    ]);

    const empty = parseRelayBrief('morning_brief', JSON.stringify({
      headline: 'Good morning.',
      sections: [{ key: 'receipts', title: 'Delivery receipts', lines: ['No delivery receipts recorded.'] }],
      note: '',
    }))!;
    expect(briefActions(empty)).toEqual([]);
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

  it('extracts a read-only status summary from health, agent and device rows', () => {
    const brief = parseRelayBrief('morning_brief', JSON.stringify({
      headline: 'Good morning.',
      sections: [
        { key: 'system', title: 'Local health', lines: ['Overall: DEGRADED.', 'database: HEALTHY'] },
        { key: 'agent', title: 'Synth', lines: ['State: paused.', 'Last outcome: completed.'] },
        { key: 'devices', title: 'Registered devices',
          lines: ['this-pc [dev_1]: trust=trusted, last_seen=...',
                  'phone [dev_2]: trust=observed, last_seen=...'] },
      ],
      note: '',
    }))!;
    expect(briefStatusSummary(brief)).toEqual({
      health: 'DEGRADED',
      agentState: 'paused',
      deviceTrust: ['trusted', 'observed'],
      needsDecision: 0,
      receiptSessions: 0,
      receiptHighWater: null,
      receiptUnacked: 0,
      receiptFallbackLines: 0,
      receiptFallbackOnly: false,
    });
  });

  it('returns null/empty status values when no status sections exist', () => {
    const brief = parseRelayBrief('morning_brief', JSON.stringify({
      headline: 'Good morning.',
      sections: [{ key: 'goals', title: 'Goals', lines: ['Ship report'] }],
      note: '',
    }))!;
    expect(briefStatusSummary(brief)).toEqual({
      health: null, agentState: null, deviceTrust: [], needsDecision: 0,
      receiptSessions: 0, receiptHighWater: null, receiptUnacked: 0,
      receiptFallbackLines: 0, receiptFallbackOnly: false,
    });
  });

  it('summarizes receipt sessions and the ack high-water mark', () => {
    const brief = parseRelayBrief('morning_brief', JSON.stringify({
      headline: 'Good morning.',
      sections: [
        { key: 'receipts', title: 'Delivery receipts',
          lines: ['session rs_1: acked through #12', 'session rs_2: acked through #7'] },
      ],
      note: '',
    }))!;
    const summary = briefStatusSummary(brief);
    expect(summary.receiptSessions).toBe(2);
    expect(summary.receiptHighWater).toBe(12);

    const empty = parseRelayBrief('morning_brief', JSON.stringify({
      headline: 'Good morning.',
      sections: [{ key: 'receipts', title: 'Delivery receipts', lines: ['No delivery receipts recorded.'] }],
      note: '',
    }))!;
    expect(briefStatusSummary(empty).receiptSessions).toBe(0);
    expect(briefStatusSummary(empty).receiptHighWater).toBeNull();
  });

  it('does not count HTTP fallback lines as receipt-bearing sessions', () => {
    const brief = parseRelayBrief('morning_brief', JSON.stringify({
      headline: 'Good morning.',
      sections: [
        { key: 'receipts', title: 'Delivery receipts',
          lines: ['session rs_1: HTTP fallback (acks unavailable)'] },
      ],
      note: '',
    }))!;
    const summary = briefStatusSummary(brief);
    expect(summary.receiptSessions).toBe(0);
    expect(summary.receiptHighWater).toBeNull();
    expect(summary.receiptUnacked).toBe(0);
    expect(summary.receiptFallbackLines).toBe(1);
    expect(summary.receiptFallbackOnly).toBe(true);
  });

  it('counts only real WSS receipts when a fallback session is also present', () => {
    const brief = parseRelayBrief('morning_brief', JSON.stringify({
      headline: 'Good morning.',
      sections: [
        { key: 'receipts', title: 'Delivery receipts',
          lines: [
            'session rs_http: HTTP fallback (acks unavailable)',
            'session rs_wss: acked through #9',
          ] },
      ],
      note: '',
    }))!;
    const summary = briefStatusSummary(brief);
    expect(summary.receiptSessions).toBe(1);
    expect(summary.receiptHighWater).toBe(9);
    // The fallback line is still counted even alongside a real WSS receipt.
    expect(summary.receiptFallbackLines).toBe(1);
    expect(summary.receiptFallbackOnly).toBe(false);
  });

  it('is not fallback-only when there are no receipt lines at all', () => {
    const brief = parseRelayBrief('morning_brief', JSON.stringify({
      headline: 'Good morning.',
      sections: [{ key: 'goals', title: 'Goals', lines: ['Ship report'] }],
      note: '',
    }))!;
    expect(briefStatusSummary(brief).receiptFallbackOnly).toBe(false);
  });

  it('sums unacked lag markers across lagging receipt sessions', () => {
    const brief = parseRelayBrief('morning_brief', JSON.stringify({
      headline: 'Good morning.',
      sections: [
        { key: 'receipts', title: 'Delivery receipts',
          lines: ['session rs_1: acked through #12 · 3 unacked',
                  'session rs_2: acked through #7 · 2 unacked',
                  'session rs_3: acked through #4'] },
      ],
      note: '',
    }))!;
    const summary = briefStatusSummary(brief);
    expect(summary.receiptSessions).toBe(3);
    expect(summary.receiptHighWater).toBe(12);
    expect(summary.receiptUnacked).toBe(5);
  });

  it('counts decision rows but not empty markers or non-decision rows', () => {
    const brief = parseRelayBrief('morning_brief', JSON.stringify({
      headline: 'Good morning.',
      sections: [
        { key: 'reconciliation', title: 'Needs reconciliation',
          lines: ['Uncertain action ra_1: outcome unknown', 'Uncertain action ra_2: lost connection'] },
        { key: 'blockers', title: 'Needs your attention', lines: ['A task is waiting: Which file?'] },
        { key: 'permission_awaiting', title: 'Approval', lines: ['file_create awaiting decision'] },
        { key: 'devices', title: 'Registered devices', lines: ['dev_1'] },
      ],
      note: '',
    }))!;
    // 2 reconciliation + 1 blocker + 1 permission = 4; devices is not a decision.
    expect(briefStatusSummary(brief).needsDecision).toBe(4);

    const calm = parseRelayBrief('morning_brief', JSON.stringify({
      headline: 'Good morning.',
      sections: [
        { key: 'reconciliation', title: 'Needs reconciliation', lines: ['Nothing needs reconciliation.'] },
        { key: 'blockers', title: 'Needs your attention', lines: ['Nothing is blocked.'] },
      ],
      note: '',
    }))!;
    expect(briefStatusSummary(calm).needsDecision).toBe(0);
  });

  it('counts decision-owing heads-up item kinds, not just section keys', () => {
    const brief = parseRelayBrief('briefing', JSON.stringify({
      headline: 'Heads up — 4 item(s) need your attention.', has_blockers: true,
      items: [
        { kind: 'task_needs_input', title: 'A task is waiting', reason: 'Which file?', suggested_action: 'Answer', authority: 'none', approved: false },
        { kind: 'task_held', title: 'A task is held', reason: 'state: interrupted', suggested_action: 'Review', authority: 'none', approved: false },
        { kind: 'goal_step_blocked', title: 'A goal step is blocked', reason: 'no permission', suggested_action: 'Review', authority: 'none', approved: false },
        { kind: 'permission_awaiting', title: 'A permission request', reason: 'file_create', suggested_action: 'Review', authority: 'none', approved: false },
      ],
      note: 'Not an approval.',
    }))!;
    expect(briefStatusSummary(brief).needsDecision).toBe(4);
  });

  it('does not count the agent_not_active heads-up item as an owner decision', () => {
    const brief = parseRelayBrief('briefing', JSON.stringify({
      headline: 'Heads up.', has_blockers: true,
      items: [{ kind: 'agent_not_active', title: 'Background work is paused', reason: 'state: paused', suggested_action: 'Resume', authority: 'none', approved: false }],
      note: '',
    }))!;
    // Agent state is a status item, not a pending decision (matches desktop Overview).
    expect(briefStatusSummary(brief).needsDecision).toBe(0);
  });

  it('maps an agent_not_active heads-up item to agent status', () => {
    const text = JSON.stringify({
      headline: 'Heads up.', has_blockers: true,
      items: [{ kind: 'agent_not_active', title: 'Background work is paused',
                reason: 'Main Agent state: paused.' }],
      note: 'Not an approval.',
    });
    expect(briefActions(parseRelayBrief('briefing', text)!)).toEqual([
      { label: 'Agent status', route: 'system' },
    ]);
  });

  it('dedupes agent actions when both a heads-up item and agent row are present', () => {
    const text = JSON.stringify({
      headline: 'Good morning.', has_blockers: true,
      items: [{ kind: 'agent_not_active', title: 'Agent paused', reason: 'state=paused' }],
      note: '',
      morning_brief: {
        date: '2026-11-20', headline: 'Good morning.', generated_at: '',
        sections: [{ key: 'agent', title: 'Synth', lines: ['State: paused.'] }],
        text: '', authorized: false, approval_issued: false,
        execution_started: false, note: '',
      },
    });
    // The heads-up parser reads top-level items; the agent row is covered by the
    // conditional rule. A combined brief would dedupe by label+route.
    const actions = briefActions(parseRelayBrief('briefing', text)!);
    expect(actions.filter((a) => a.label === 'Agent status')).toHaveLength(1);
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
