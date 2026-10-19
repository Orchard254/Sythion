import { useEffect, useState } from 'react';
import { Icon } from './icons';
import { Badge, Button, IconButton, Popover, DemoTag } from './ui';
import { relTime } from '../lib/format';
import { useAppStore } from '../store/appStore';
import type { RouteDef } from '../lib/routes';
import type { RuntimeHealthState } from '../lib/runtimeHealth';

function useNow(intervalMs = 30_000) {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), intervalMs);
    return () => clearInterval(t);
  }, [intervalMs]);
  return now;
}

export function TopBar({
  route,
  origin,
  connected,
  runtimeState,
  attentionCount,
  onNavigate,
}: {
  route: RouteDef;
  origin: 'live' | 'demo';
  connected: boolean;
  runtimeState?: RuntimeHealthState;
  attentionCount: number;
  onNavigate: (id: RouteDef['id']) => void;
}) {
  const now = useNow();
  const notifications = useAppStore((s) => s.notifications);
  const unread = useAppStore((s) => s.unreadNotifications);
  const dismiss = useAppStore((s) => s.dismissNotification);
  const clearAll = useAppStore((s) => s.clearNotifications);
  const [trayOpen, setTrayOpen] = useState(false);

  const time = now.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
  const date = now.toLocaleDateString(undefined, {
    weekday: 'long',
    day: 'numeric',
    month: 'long',
  });

  return (
    <header className="topbar">
      <div className="topbar__heading">
        <span className="topbar__title">{route.label}</span>
        <span className="topbar__subtitle">{route.blurb}</span>
      </div>

      <div className="topbar__spacer" />

      <div className="topbar__actions">
        {origin === 'demo' ? (
          <DemoTag />
        ) : (
          <Badge tone={runtimeState === 'healthy' ? 'ok' : runtimeState === 'checking' ? 'idle' : runtimeState === 'stale' ? 'warn' : connected ? 'ok' : 'danger'}>
            {runtimeState === 'healthy' ? 'Live' : runtimeState === 'checking' ? 'Checking' : runtimeState === 'stale' ? 'Stale' : connected ? 'Live' : 'Offline'}
          </Badge>
        )}

        <div className="clock">
          <div className="clock__time">{time}</div>
          <div className="clock__date">{date}</div>
        </div>

        <Popover
          open={trayOpen}
          onClose={() => setTrayOpen(false)}
          width={340}
          trigger={
            <span style={{ position: 'relative', display: 'flex' }}>
              <IconButton
                icon="bell"
                label={unread > 0 ? `Notifications (${unread} unread)` : 'Notifications'}
                tone="ghost"
                onClick={() => setTrayOpen((v) => !v)}
              />
              {unread > 0 && (
                <span
                  aria-hidden="true"
                  style={{
                    position: 'absolute',
                    top: 3,
                    right: 3,
                    minWidth: 14,
                    height: 14,
                    padding: '0 3px',
                    borderRadius: 999,
                    background: 'var(--danger)',
                    color: '#fff',
                    fontSize: 9,
                    fontWeight: 700,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                  }}
                >
                  {unread}
                </span>
              )}
            </span>
          }
        >
          <div>
            <div className="panel__head">
                <h2 className="panel__title">Attention</h2>
                <div className="panel__actions">
                  {notifications.length > 0 && (
                    <Button size="sm" variant="ghost" onClick={clearAll}>
                      Clear
                    </Button>
                  )}
                  <IconButton icon="close" label="Close notifications" tone="ghost" onClick={() => setTrayOpen(false)} />
                </div>
              </div>
              <div style={{ maxHeight: 380, overflowY: 'auto' }}>
                {notifications.length === 0 ? (
                  <div className="empty" style={{ padding: '26px 16px' }}>
                    <span className="empty__icon">
                      <Icon name="check" size={22} strokeWidth={1.4} />
                    </span>
                    <p className="empty__title">Nothing needs you</p>
                    <p className="empty__desc">
                      Approvals, failures and background outcomes appear here.
                    </p>
                  </div>
                ) : (
                  <div className="list">
                    {notifications.map((n) => (
                      <div className="list__row" key={n.id}>
                        <span className={`rail rail--${n.tone === 'accent' ? 'accent' : n.tone}`} style={{ minHeight: 30 }} />
                        <div className="list__main">
                          <span className="t-title" style={{ fontSize: 12.5 }}>
                            {n.title}
                          </span>
                          {n.body && <span className="t-muted">{n.body}</span>}
                          <span className="list__meta">
                            <span>{relTime(new Date(n.createdAt).toISOString())}</span>
                            {n.demo && <DemoTag compact />}
                          </span>
                        </div>
                        <div className="list__trail">
                          {n.route && (
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() => {
                                onNavigate(n.route as RouteDef['id']);
                                setTrayOpen(false);
                              }}
                            >
                              View
                            </Button>
                          )}
                          <IconButton
                            icon="close"
                            label={`Dismiss ${n.title}`}
                            tone="ghost"
                            onClick={() => dismiss(n.id)}
                          />
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
              {attentionCount > 0 && (
                <div className="panel__foot">
                  <Button
                    size="sm"
                    variant="subtle"
                    icon="shield"
                    onClick={() => {
                      onNavigate('security');
                      setTrayOpen(false);
                    }}
                  >
                    {attentionCount} item{attentionCount === 1 ? '' : 's'} awaiting your decision
                  </Button>
                </div>
              )}
          </div>
        </Popover>
      </div>
    </header>
  );
}