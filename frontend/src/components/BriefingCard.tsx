import { Badge } from './ui';
import type { Briefing } from '../types';

/**
 * Read-only "heads up" card derived from durable runtime state.
 *
 * It is deliberately non-authorizing: no button here can clear a blocker,
 * grant a permission, or approve an action. Decisions stay in the existing
 * trusted review interfaces (task drawer / settings proposals).
 */
export function BriefingCard({ briefing }: { briefing: Briefing }) {
  if (!briefing.has_blockers || briefing.items.length === 0) return null;

  return (
    <section className="notice notice--warn" role="status" aria-label="Heads up" data-testid="briefing-card">
      <div className="briefing__head">
        <strong>{briefing.headline}</strong>
        <Badge tone="warn">Heads up</Badge>
      </div>
      <ul className="briefing__list">
        {briefing.items.map((item, index) => (
          <li key={`${item.kind}:${item.ref}:${index}`} className="briefing__item">
            <span className="briefing__title">{item.title}</span>
            <span className="briefing__reason">{item.reason}</span>
            <span className="briefing__action">Next: {item.suggested_action}</span>
          </li>
        ))}
      </ul>
      <p className="briefing__note">{briefing.note}</p>
    </section>
  );
}
