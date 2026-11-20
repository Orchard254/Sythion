import { Button } from './ui';
import { useNavStore } from '../store/navStore';
import type { RouteId } from '../lib/routes';
import type { MorningBrief } from '../types';

interface Props {
  brief: MorningBrief;
  /** Injectable for tests; defaults to the app's navigation store. */
  onNavigate?: (route: RouteId) => void;
}

/**
 * Read-only morning brief, delivered as a proactive message from the Core.
 * Navigation buttons only take the owner to the trusted review panels; nothing
 * here can approve, grant, or clear anything.
 */
export function MorningBriefCard({ brief, onNavigate }: Props) {
  const setRoute = useNavStore((s) => s.setRoute);
  const navigate = onNavigate ?? setRoute;

  const hasReconciliation = brief.sections.some(
    (s) => s.key === 'reconciliation' && !s.lines.every((l) => l.includes('Nothing needs reconciliation')),
  );
  const hasBlockers = brief.sections.some(
    (s) => s.key === 'blockers' && !s.lines.every((l) => l.includes('Nothing is blocked')),
  );
  const hasRegisteredDevices = brief.sections.some(
    (s) => s.key === 'devices' && !s.lines.every((l) => l.includes('No devices have registered')),
  );
  const hasRecentDeviceActions = brief.sections.some(
    (s) => s.key === 'device_actions' && !s.lines.every((l) => l.includes('No device actions recorded')),
  );
  const hasFinishedWork = brief.sections.some(
    (s) => (s.key === 'goals' || s.key === 'tasks') && !s.lines.every((l) => l.includes('None reported.')),
  );
  // A health section is actionable when any check is not healthy.
  const healthNeedsAttention = brief.sections
    .filter((s) => s.key === 'system')
    .some((s) => s.lines.some((l) =>
      /DEGRADED|UNAVAILABLE|UNKNOWN|unavailable/i.test(l)));
  // The Synth (agent) section is actionable when the agent is not active.
  const agentNeedsAttention = brief.sections
    .filter((s) => s.key === 'agent')
    .some((s) => s.lines.some((l) =>
      /State:\s*(paused|degraded|shutdown|unknown)|not been initialized/i.test(l)));

  return (
    <section className="notice notice--info" role="status" aria-label="Morning brief" data-testid="morning-brief-card">
      <strong>{brief.headline}</strong>
      <div className="briefing__list">
        {brief.sections.map((section) => (
          <div key={section.key} className="briefing__item">
            <span className="briefing__title">{section.title}</span>
            {section.lines.map((line, index) => (
              <span key={index} className="briefing__reason">{line}</span>
            ))}
          </div>
        ))}
      </div>
      {(hasReconciliation || hasBlockers || hasRegisteredDevices || hasRecentDeviceActions || healthNeedsAttention || agentNeedsAttention || hasFinishedWork) && (
        <div className="row row--wrap" style={{ gap: 8 }}>
          <Button size="sm" variant="ghost" icon="chevronRight"
            onClick={() => navigate('overview')}>
            Review in Control Center
          </Button>
          <Button size="sm" variant="ghost" icon="chevronRight"
            onClick={() => navigate('security')}>
            Open approvals
          </Button>
          {hasRegisteredDevices && (
            <Button size="sm" variant="ghost" icon="chevronRight"
              onClick={() => navigate('system')}>
              View devices
            </Button>
          )}
          {hasRecentDeviceActions && (
            <Button size="sm" variant="ghost" icon="chevronRight"
              onClick={() => navigate('overview')}>
              View device actions
            </Button>
          )}
          {healthNeedsAttention && (
            <Button size="sm" variant="ghost" icon="chevronRight"
              onClick={() => navigate('system')}>
              View diagnostics
            </Button>
          )}
          {agentNeedsAttention && (
            <Button size="sm" variant="ghost" icon="chevronRight"
              onClick={() => navigate('system')}>
              View agent status
            </Button>
          )}
          {hasFinishedWork && (
            <Button size="sm" variant="ghost" icon="chevronRight"
              onClick={() => navigate('missions')}>
              View goals
            </Button>
          )}
        </div>
      )}
      <p className="briefing__note">{brief.note}</p>
    </section>
  );
}
