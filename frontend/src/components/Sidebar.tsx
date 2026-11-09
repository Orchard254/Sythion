import { Icon, SythionMark } from './icons';
import { Badge, Dot } from './ui';
import { NAV_GROUPS, ROUTES, type RouteDef } from '../lib/routes';
import { AGENT_STATE_TONE, humanize } from '../lib/format';
import type { AgentIdentity, HealthLevel } from '../types';
import type { RuntimeHealthState } from '../lib/runtimeHealth';

const HEALTH_DOT: Record<HealthLevel, 'ok' | 'warn' | 'danger' | 'idle'> = {
  HEALTHY: 'ok',
  DEGRADED: 'warn',
  UNAVAILABLE: 'danger',
  UNKNOWN: 'idle',
};

export function Sidebar({
  route,
  onNavigate,
  pendingApprovals,
  agent,
  overallHealth,
  connected,
  runtimeState,
  runtimeAgeMs,
  version,
  onOpenSettings,
}: {
  route: string;
  onNavigate: (id: RouteDef['id']) => void;
  pendingApprovals: number;
  agent: AgentIdentity | undefined;
  overallHealth: HealthLevel | undefined;
  connected: boolean;
  runtimeState: RuntimeHealthState;
  runtimeAgeMs: number | null;
  version: string | undefined;
  onOpenSettings: () => void;
}) {
  const agentState = agent?.state ?? 'unknown';
  const stateTone = AGENT_STATE_TONE[agentState] ?? 'idle';
  const runtimeTone = connected ? (runtimeState === 'degraded' ? 'warn' : (HEALTH_DOT[overallHealth ?? 'UNKNOWN'])) : 'danger';

  return (
    <>
      <div className="shell__brand">
        <SythionMark size={22} />
        <div className="shell__brand-text" style={{ minWidth: 0 }}>
          <div className="shell__brand-name">Sythion</div>
          <div className="shell__brand-sub">Personal AI runtime</div>
        </div>
      </div>

      <nav className="shell__sidebar" aria-label="Primary">
        <div className="shell__nav">
          {NAV_GROUPS.map((group) => {
            const items = ROUTES.filter((r) => r.group === group.id && r.id !== 'settings');
            if (items.length === 0) return null;
            return (
              <div className="nav-group" key={group.id}>
                <div className="nav-group__label">{group.label}</div>
                {items.map((item) => {
                  const active = route === item.id;
                  const count = item.id === 'security' ? pendingApprovals : 0;
                  return (
                    <button
                      key={item.id}
                      type="button"
                      className={`nav-item${active ? ' nav-item--active' : ''}`}
                      aria-current={active ? 'page' : undefined}
                      onClick={() => onNavigate(item.id)}
                      title={`${item.label} — ${item.blurb}`}
                    >
                      <span className="nav-item__icon">
                        <Icon name={item.icon} size={16} />
                      </span>
                      <span className="nav-item__label">{item.label}</span>
                      {count > 0 && <span className="nav-item__count">{count}</span>}
                    </button>
                  );
                })}
              </div>
            );
          })}
        </div>

        <div className="sidebar__footer">
          <button
            type="button"
            className="runtime-card"
            onClick={() => onNavigate('system')}
            title="Open System & Integrations"
          >
            <Dot tone={runtimeTone} pulse={runtimeState === 'healthy' && connected} />
            <span className="runtime-card__text">
              <span className="runtime-card__title">
                {runtimeState === 'healthy'
                  ? 'Runtime online'
                  : runtimeState === 'checking'
                    ? 'Checking runtime…'
                    : runtimeState === 'degraded'
                      ? 'Runtime degraded'
                      : runtimeState === 'stale'
                        ? 'Runtime unconfirmed'
                        : 'Backend offline'}
              </span>
              <span className="runtime-card__meta">
                {runtimeState === 'healthy'
                  ? `${humanize(agentState)} agent · v${version ?? '—'}`
                  : runtimeState === 'degraded'
                    ? 'Backend is serving with a degraded startup'
                    : runtimeState === 'stale'
                      ? `Last seen ${runtimeAgeMs !== null ? `${Math.round(runtimeAgeMs / 1000)}s ago` : 'recently'} · may be offline`
                      : runtimeState === 'checking'
                        ? 'Confirming connection…'
                        : 'No connection to local backend'}
              </span>
            </span>
          </button>

          <div className="row" style={{ gap: 6, alignItems: 'center' }}>
            <Badge tone={stateTone}>{humanize(agentState)}</Badge>
            <span className="spacer" />
            <button
              type="button"
              className={`nav-item${route === 'settings' ? ' nav-item--active' : ''}`}
              style={{ width: 'auto', padding: '6px 8px' }}
              onClick={onOpenSettings}
              title="Settings"
              aria-label="Settings"
            >
              <span className="nav-item__icon">
                <Icon name="settings" size={15} />
              </span>
              <span className="nav-item__label" style={{ fontSize: 12 }}>
                Settings
              </span>
            </button>
          </div>
        </div>
      </nav>
    </>
  );
}