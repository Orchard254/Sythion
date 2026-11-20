import { describe, expect, it } from 'vitest';
import { describeRelayLag, relayLagSignal } from './relayLag';
import type { RelayAckSummary } from '../types';

const summary = (unacked_total: number, lagging: number): RelayAckSummary => ({
  acks: [],
  sessions: Array.from({ length: lagging }, (_, i) => ({
    session_id: `rs_${i}`, highest_ack: 5, latest_message: 5 + 2, unacked: 2,
  })),
  unacked_total,
  note: '',
  authorized: false,
  approval_issued: false,
});

describe('relayLagSignal', () => {
  it('is not lagging when the summary is null or fully acked', () => {
    expect(relayLagSignal(null)).toEqual({ lagging: false, unacked: 0, laggingSessions: 0 });
    expect(relayLagSignal(summary(0, 0)).lagging).toBe(false);
  });

  it('reports unacked total and lagging session count', () => {
    const signal = relayLagSignal(summary(6, 3));
    expect(signal).toEqual({ lagging: true, unacked: 6, laggingSessions: 3 });
  });

  it('falls back to summing sessions when unacked_total is absent', () => {
    const withoutTotal = { ...summary(0, 2), unacked_total: undefined } as unknown as RelayAckSummary;
    expect(relayLagSignal(withoutTotal).unacked).toBe(4);
  });
});

describe('describeRelayLag', () => {
  it('describes frames and sessions with correct pluralization', () => {
    expect(describeRelayLag(relayLagSignal(summary(3, 2)))).toBe(
      '3 frames awaiting receipt across 2 sessions');
    expect(describeRelayLag(relayLagSignal(summary(1, 1)))).toBe(
      '1 frame awaiting receipt across 1 session');
  });

  it('returns an empty string when not lagging', () => {
    expect(describeRelayLag(relayLagSignal(null))).toBe('');
  });
});
