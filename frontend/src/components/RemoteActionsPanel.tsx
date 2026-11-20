import { Badge, EmptyState, Panel } from './ui';
import type { RemoteAction } from '../types';

const STATE_TONE = {
  received: 'idle',
  understanding: 'info',
  planned: 'info',
  awaiting_approval: 'warn',
  executing: 'accent',
  verifying: 'accent',
  completed: 'ok',
  failed: 'danger',
  cancelled: 'idle',
  uncertain: 'danger',
} as const;

/**
 * Read-only Control Center view of remote-device action lifecycle states.
 *
 * This panel only reports durable state. It can never approve, grant, replay,
 * or clear an uncertain outcome — those require the trusted owner routes.
 */
export function RemoteActionsPanel({ actions }: { actions: RemoteAction[] }) {
  const ordered = [...actions].sort((a, b) => {
    if (a.uncertain !== b.uncertain) return a.uncertain ? -1 : 1;
    return (b.updated_at ?? '').localeCompare(a.updated_at ?? '');
  });

  return (
    <Panel title="Device actions" hint="What Synth is doing on devices">
      {ordered.length === 0 ? (
        <EmptyState title="No device actions" description="No remote actions have been recorded." />
      ) : (
        <div className="devices__list" data-testid="remote-actions-panel">
          {ordered.map((action) => (
            <div key={action.action_id} className="devices__item">
              <div className="row row--wrap" style={{ gap: 8 }}>
                <strong>{action.action_id}</strong>
                <Badge tone={action.uncertain ? 'danger' : STATE_TONE[action.state]}>
                  {action.uncertain ? 'uncertain — needs reconciliation' : action.state}
                </Badge>
                <span className="t-mono t-faint">{action.device_id}</span>
              </div>
              {action.task_id && (
                <span className="t-faint">Task: {action.task_id}</span>
              )}
              {action.reason && <span>{action.reason}</span>}
              {action.evidence_ref && (
                <span className="t-faint">Evidence: {action.evidence_ref}</span>
              )}
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}
