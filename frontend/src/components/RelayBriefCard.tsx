import { Badge, Button } from './ui';
import {
  briefActions, briefStatusSummary, type StructuredBrief,
} from '../lib/relayBrief';
import type { RouteId } from '../lib/routes';

interface Props {
  brief: StructuredBrief;
  /** Optional owner action navigation; when absent, no action buttons render. */
  onAction?: (route: RouteId) => void;
}

/**
 * Structured rendering of a seeded briefing / morning_brief relay message.
 * Navigation buttons only take the owner to the relevant panel; the card never
 * approves, grants, or clears anything.
 */
export function RelayBriefCard({ brief, onAction }: Props) {
  const actions = onAction ? briefActions(brief) : [];
  const status = briefStatusSummary(brief);
  const hasStatus = status.health !== null || status.agentState !== null
    || status.deviceTrust.length > 0 || status.needsDecision > 0
    || status.receiptSessions > 0 || status.receiptUnacked > 0;
  return (
    <div className="stack stack--tight" data-testid="relay-brief-card">
      <div className="row row--wrap" style={{ gap: 8 }}>
        <Badge tone="info">{brief.kind === 'morning_brief' ? 'Morning brief' : 'Heads up'}</Badge>
        <strong>{brief.headline}</strong>
      </div>
      {hasStatus && (
        <div className="row row--wrap" style={{ gap: 6 }} data-testid="relay-brief-status">
          {status.health && (
            <Badge tone={/HEALTHY/.test(status.health) ? 'ok' : 'warn'}>health: {status.health}</Badge>
          )}
          {status.agentState && (
            <Badge tone={status.agentState === 'active' ? 'ok' : 'warn'}>agent: {status.agentState}</Badge>
          )}
          {status.deviceTrust.map((trust, index) => (
            <Badge key={`${trust}:${index}`} tone="info">device: {trust}</Badge>
          ))}
          {status.needsDecision > 0 && (
            <Badge tone="warn">decisions: {status.needsDecision}</Badge>
          )}
          {status.receiptSessions > 0 && (
            <Badge tone="info">
              receipts: {status.receiptSessions}
              {status.receiptHighWater !== null ? ` · through #${status.receiptHighWater}` : ''}
            </Badge>
          )}
          {status.receiptUnacked > 0 && (
            <Badge tone="warn">{status.receiptUnacked} unacked</Badge>
          )}
        </div>
      )}
      {brief.rows.map((row, index) => (
        <div key={`${row.key}:${index}`} className="stack stack--tight">
          <span className="briefing__title">{row.label}</span>
          {row.detail && <span className="briefing__reason">{row.detail}</span>}
        </div>
      ))}
      {actions.length > 0 && (
        <div className="row row--wrap" style={{ gap: 8 }} data-testid="relay-brief-actions">
          {actions.map((action) => (
            <Button key={`${action.label}:${action.route}`} size="sm" variant="ghost"
              icon="chevronRight" onClick={() => onAction!(action.route)}>
              {action.label}
            </Button>
          ))}
        </div>
      )}
      {brief.note && <p className="briefing__note">{brief.note}</p>}
    </div>
  );
}
