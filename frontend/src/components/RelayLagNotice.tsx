import { Badge, Button, Notice } from './ui';
import { describeRelayLag, relayLagSignal } from '../lib/relayLag';
import type { RouteId } from '../lib/routes';
import type { RelayAckSummary } from '../types';

interface Props {
  summary: RelayAckSummary | null;
  /** Navigation handler for the read-only "View receipts" affordance. */
  onView?: (route: RouteId) => void;
}

/**
 * Read-only Control Center signal that device relay receipts are lagging.
 * It never authorizes a retry, proves an effect, or changes an action — it only
 * reports the durable unacked count and links to the receipt panel.
 */
export function RelayLagNotice({ summary, onView }: Props) {
  const signal = relayLagSignal(summary);
  if (!signal.lagging) return null;

  return (
    <div data-testid="relay-lag-notice">
      <Notice
        tone="warn"
        action={onView ? (
          <Button size="sm" variant="ghost" onClick={() => onView('system')}>
            View receipts
          </Button>
        ) : undefined}
      >
        <Badge tone="warn">{signal.unacked} unacked</Badge>{' '}
        {describeRelayLag(signal)}. This is delivery evidence only — no action
        has been authorized or retried.
      </Notice>
    </div>
  );
}
