import { create } from 'react-test-renderer';
import { describe, expect, it, vi } from 'vitest';
import { RelayLagNotice } from './RelayLagNotice';
import type { RelayAckSummary } from '../types';

const lagging: RelayAckSummary = {
  acks: [],
  sessions: [
    { session_id: 'rs_1', highest_ack: 5, latest_message: 8, unacked: 3 },
    { session_id: 'rs_2', highest_ack: 5, latest_message: 6, unacked: 1 },
  ],
  unacked_total: 4,
  note: '',
  authorized: false,
  approval_issued: false,
};

const caughtUp: RelayAckSummary = { ...lagging, unacked_total: 0, sessions: [] };

describe('RelayLagNotice', () => {
  it('renders nothing when receipts are not lagging', () => {
    expect(create(<RelayLagNotice summary={null} />).toJSON()).toBeNull();
    expect(create(<RelayLagNotice summary={caughtUp} />).toJSON()).toBeNull();
  });

  it('renders a read-only warn notice with the unacked count', () => {
    const view = create(<RelayLagNotice summary={lagging} />);
    expect(view.root.findByProps({ 'data-testid': 'relay-lag-notice' })).toBeDefined();
    const rendered = JSON.stringify(view.toJSON());
    const badgeText = view.root.findAllByType('span').map((b) =>
      b.children.filter((c) => typeof c === 'string').join('')).join(' ');
    expect(badgeText).toContain('4 unacked');
    expect(rendered).toContain('delivery evidence only');
    // Without a handler there is no navigation control.
    expect(view.root.findAllByType('button')).toHaveLength(0);
  });

  it('offers read-only navigation to the receipt panel when a handler is given', () => {
    const onView = vi.fn();
    const view = create(<RelayLagNotice summary={lagging} onView={onView} />);
    const button = view.root.findAllByType('button')
      .find((b) => b.children.some((c) => typeof c === 'string' && c === 'View receipts'));
    expect(button).toBeDefined();
    button!.props.onClick();
    expect(onView).toHaveBeenCalledWith('system');
  });
});
