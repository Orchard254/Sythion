import { Badge } from './ui';
import type { StructuredBrief } from '../lib/relayBrief';

/**
 * Structured rendering of a seeded briefing / morning_brief relay message.
 * Purely informational: it never approves, grants, or clears anything.
 */
export function RelayBriefCard({ brief }: { brief: StructuredBrief }) {
  return (
    <div className="stack stack--tight" data-testid="relay-brief-card">
      <div className="row row--wrap" style={{ gap: 8 }}>
        <Badge tone="info">{brief.kind === 'morning_brief' ? 'Morning brief' : 'Heads up'}</Badge>
        <strong>{brief.headline}</strong>
      </div>
      {brief.rows.map((row, index) => (
        <div key={`${row.label}:${index}`} className="stack stack--tight">
          <span className="briefing__title">{row.label}</span>
          {row.detail && <span className="briefing__reason">{row.detail}</span>}
        </div>
      ))}
      {brief.note && <p className="briefing__note">{brief.note}</p>}
    </div>
  );
}
