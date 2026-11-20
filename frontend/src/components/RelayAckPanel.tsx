import { Badge, EmptyState, Panel } from './ui';
import type { RelayAckSummary } from '../types';

/**
 * Read-only Control Center view of relay delivery acknowledgements.
 *
 * An ack is evidence that a device confirmed receiving a sequenced frame — it
 * is never authority, proof of an effect, or an authorization of any action.
 * This panel only reports durable state; it has no controls.
 */
export function RelayAckPanel({ summary }: { summary: RelayAckSummary | null }) {
  const sessions = summary?.sessions ?? [];
  const acks = summary?.acks ?? [];
  const unackedTotal = summary?.unacked_total ?? 0;

  return (
    <Panel title="Delivery receipts" hint="Which sequenced frames devices confirmed receiving">
      {sessions.length === 0 && acks.length === 0 ? (
        <EmptyState
          title="No delivery receipts"
          description="No device relay receipts have been recorded yet."
        />
      ) : (
        <div className="stack stack--tight" data-testid="relay-ack-panel">
          {unackedTotal > 0 && (
            <div data-testid="relay-ack-lag">
              <Badge tone="warn">{unackedTotal} unacked across all sessions</Badge>
            </div>
          )}
          {sessions.length > 0 && (
            <div className="stack stack--tight">
              <span className="field__label">Per-session high-water marks</span>
              {sessions.map((mark) => {
                // An HTTP fallback session cannot emit acks. Do not render a
                // misleading "acked through #0" — the fallback badge explains it.
                const isFallback = mark.ack_mode === 'http' && !mark.acks_possible;
                return (
                  <div key={mark.session_id} className="row row--wrap" style={{ gap: 8 }}>
                    <span className="t-mono">{mark.session_id}</span>
                    {!isFallback && (
                      <Badge tone="info">acked through #{mark.highest_ack}</Badge>
                    )}
                    {mark.lagging && (
                      <Badge tone="warn">{mark.unacked} unacked</Badge>
                    )}
                    {isFallback && (
                      <Badge tone="idle">HTTP fallback · acks unavailable</Badge>
                    )}
                  </div>
                );
              })}
            </div>
          )}
          {acks.length > 0 && (
            <div className="stack stack--tight">
              <span className="field__label">Recent receipts</span>
              {acks.map((ack) => (
                <div key={ack.id} className="row row--wrap" style={{ gap: 8 }}>
                  <span className="t-mono">#{ack.sequence}</span>
                  <span className="t-faint">{ack.device_id}</span>
                  <span className="t-faint">{ack.session_id}</span>
                </div>
              ))}
            </div>
          )}
          <p className="briefing__note">
            {summary?.note ?? 'Read-only evidence. A receipt grants no authority and proves no effect.'}
          </p>
        </div>
      )}
    </Panel>
  );
}
