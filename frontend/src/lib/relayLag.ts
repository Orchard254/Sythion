import type { RelayAckSummary } from '../types';

export interface RelayLagSignal {
  lagging: boolean;
  unacked: number;
  laggingSessions: number;
}

/**
 * Derive a read-only "receipts lagging" signal from the durable relay-ack
 * summary. A signal is present when any session has frames the device has not
 * confirmed. This is delivery evidence only — it never authorizes a retry,
 * proves an effect, or changes any action.
 */
export function relayLagSignal(summary: RelayAckSummary | null): RelayLagSignal {
  if (!summary) return { lagging: false, unacked: 0, laggingSessions: 0 };
  const sessions = Array.isArray(summary.sessions) ? summary.sessions : [];
  const laggingSessions = sessions.filter((s) => s.unacked > 0).length;
  // Prefer the backend's total; fall back to summing sessions if absent.
  const unacked = typeof summary.unacked_total === 'number'
    ? summary.unacked_total
    : sessions.reduce((sum, s) => sum + (s.unacked > 0 ? s.unacked : 0), 0);
  return { lagging: unacked > 0 || laggingSessions > 0, unacked, laggingSessions };
}

export function describeRelayLag(signal: RelayLagSignal): string {
  if (!signal.lagging) return '';
  const sessions = signal.laggingSessions > 0
    ? ` across ${signal.laggingSessions} ${signal.laggingSessions === 1 ? 'session' : 'sessions'}`
    : '';
  return `${signal.unacked} frame${signal.unacked === 1 ? '' : 's'} awaiting receipt${sessions}`;
}
