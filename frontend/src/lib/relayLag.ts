import type { RelayAckSummary } from '../types';

export interface RelayLagSignal {
  lagging: boolean;
  unacked: number;
  laggingSessions: number;
  /** Sessions on HTTP fallback, which physically cannot emit acks. */
  fallbackSessions: number;
}

/**
 * Derive a read-only "receipts lagging" signal from the durable relay-ack
 * summary. A signal is present when any session has frames the device has not
 * confirmed. This is delivery evidence only — it never authorizes a retry,
 * proves an effect, or changes any action.
 */
export function relayLagSignal(summary: RelayAckSummary | null): RelayLagSignal {
  if (!summary) return { lagging: false, unacked: 0, laggingSessions: 0, fallbackSessions: 0 };
  const sessions = Array.isArray(summary.sessions) ? summary.sessions : [];
  // HTTP fallback sessions cannot ack. Count them so the Overview can explain
  // a "no lag" state that is really "acks impossible", not "all confirmed".
  const fallbackSessions = sessions.filter(
    (s) => s.ack_mode === 'http' && s.acks_possible === false,
  ).length;
  // Use the backend's explicit per-session lagging flag when present; older
  // payloads without it fall back to unacked > 0. HTTP fallback sessions have
  // acks_possible=false and must never be counted as a silent device.
  const isLagging = (s: { unacked: number; lagging?: boolean; acks_possible?: boolean }) =>
    typeof s.lagging === 'boolean'
      ? s.lagging
      : s.unacked > 0 && s.acks_possible !== false;
  const laggingSessions = sessions.filter(isLagging).length;
  // Prefer the backend's total (which already excludes non-ack-capable
  // sessions); fall back to summing only lagging sessions if absent.
  const unacked = typeof summary.unacked_total === 'number'
    ? summary.unacked_total
    : sessions.reduce((sum, s) => sum + (isLagging(s) ? s.unacked : 0), 0);
  return {
    lagging: unacked > 0 || laggingSessions > 0,
    unacked, laggingSessions, fallbackSessions,
  };
}

export function describeRelayLag(signal: RelayLagSignal): string {
  if (!signal.lagging) return '';
  const sessions = signal.laggingSessions > 0
    ? ` across ${signal.laggingSessions} ${signal.laggingSessions === 1 ? 'session' : 'sessions'}`
    : '';
  return `${signal.unacked} frame${signal.unacked === 1 ? '' : 's'} awaiting receipt${sessions}`;
}
