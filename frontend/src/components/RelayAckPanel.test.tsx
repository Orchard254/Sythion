import { create } from 'react-test-renderer';
import { describe, expect, it } from 'vitest';
import { RelayAckPanel } from './RelayAckPanel';
import type { RelayAckSummary } from '../types';

const summary: RelayAckSummary = {
  sessions: [
    { session_id: 'rs_1', highest_ack: 12, latest_message: 15, unacked: 3 },
    { session_id: 'rs_2', highest_ack: 7, latest_message: 7, unacked: 0 },
  ],
  unacked_total: 3,
  acks: [
    { id: 1, session_id: 'rs_1', device_id: 'dev_1', sequence: 12,
      created_at: '2026-11-20T08:00:00' },
    { id: 2, session_id: 'rs_2', device_id: 'phone_1', sequence: 7,
      created_at: '2026-11-20T08:01:00' },
  ],
  note: 'Read-only evidence. A receipt grants no authority and proves no effect.',
  authorized: false,
  approval_issued: false,
};

const textOf = (view: ReturnType<typeof create>): string =>
  view.root.findAllByType('span').map((b) =>
    b.children.filter((c) => typeof c === 'string').join('')).join(' ');

describe('RelayAckPanel', () => {
  it('renders an empty state when there are no receipts', () => {
    const view = create(<RelayAckPanel summary={null} />);
    expect(JSON.stringify(view.toJSON())).toContain('No delivery receipts');
    expect(view.root.findAllByProps({ 'data-testid': 'relay-ack-panel' })).toHaveLength(0);
  });

  it('renders high-water marks and recent receipts read-only', () => {
    const view = create(<RelayAckPanel summary={summary} />);
    expect(view.root.findByProps({ 'data-testid': 'relay-ack-panel' })).toBeDefined();
    const text = textOf(view);
    expect(text).toContain('rs_1');
    expect(text).toContain('acked through #12');
    expect(text).toContain('rs_2');
    expect(text).toContain('dev_1');
    expect(text).toContain('phone_1');
    expect(JSON.stringify(view.toJSON())).toContain('grants no authority');
  });

  it('renders a lagging-receipt signal when some frames are unacked', () => {
    const view = create(<RelayAckPanel summary={summary} />);
    expect(view.root.findByProps({ 'data-testid': 'relay-ack-lag' })).toBeDefined();
    const text = textOf(view);
    expect(text).toContain('3 unacked across all sessions');
    expect(text).toContain('3 unacked'); // per-session badge
  });

  it('omits the lag signal when every session is caught up', () => {
    const caughtUp: RelayAckSummary = {
      ...summary,
      unacked_total: 0,
      sessions: summary.sessions.map((s) => ({ ...s, unacked: 0 })),
    };
    const view = create(<RelayAckPanel summary={caughtUp} />);
    expect(view.root.findAllByProps({ 'data-testid': 'relay-ack-lag' })).toHaveLength(0);
  });

  it('never renders a control that could authorize or clear a receipt', () => {
    const view = create(<RelayAckPanel summary={summary} />);
    expect(view.root.findAllByType('button')).toHaveLength(0);
  });
});
