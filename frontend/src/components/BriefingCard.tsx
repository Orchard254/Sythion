import { Badge, Button } from './ui';
import { useNavStore } from '../store/navStore';
import type { RouteId } from '../lib/routes';
import type { Briefing } from '../types';

interface Props {
  briefing: Briefing;
  /** Injectable for tests; defaults to the app's navigation store. */
  onNavigate?: (route: RouteId) => void;
}

/**
 * Read-only "heads up" card derived from durable runtime state.
 *
 * The navigation buttons only take the owner to the trusted review panels; the
 * card itself cannot clear a blocker, grant a permission, or approve an action.
 */
export function BriefingCard({ briefing, onNavigate }: Props) {
  const setRoute = useNavStore((s) => s.setRoute);
  const navigate = onNavigate ?? setRoute;

  if (!briefing.has_blockers || briefing.items.length === 0) return null;

  const hasPermissionItem = briefing.items.some((i) => i.kind === 'permission_awaiting');
  const hasReconciliationItem = briefing.items.some((i) =>
    i.kind === 'task_held' || i.kind === 'task_needs_input' || i.kind === 'goal_step_blocked');
  const hasAgentItem = briefing.items.some((i) => i.kind === 'agent_not_active');

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
      <div className="row row--wrap" style={{ gap: 8 }}>
        {hasReconciliationItem && (
          <Button size="sm" variant="ghost" icon="chevronRight"
            onClick={() => navigate('overview')}>
            Review in Control Center
          </Button>
        )}
        {hasPermissionItem && (
          <Button size="sm" variant="ghost" icon="chevronRight"
            onClick={() => navigate('security')}>
            Open approvals
          </Button>
        )}
        {hasAgentItem && (
          <Button size="sm" variant="ghost" icon="chevronRight"
            onClick={() => navigate('system')}>
            View agent status
          </Button>
        )}
      </div>
      <p className="briefing__note">{briefing.note}</p>
    </section>
  );
}
