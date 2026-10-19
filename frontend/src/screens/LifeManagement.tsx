import { useMemo, useState } from 'react';
import { Icon } from '../components/icons';
import {
  Badge,
  Button,
  Chips,
  DemoTag,
  KV,
  Notice,
  Panel,
  Rail,
  ScreenHead,
  Stat,
  Switch,
} from '../components/ui';
import { useAppSettings } from '../hooks/useBackend';
import { useAppStore } from '../store/appStore';
import { demoAdmin, demoCalendar, demoReminders, demoRoutines } from '../data/demo';
import { relTime, dateTime } from '../lib/format';

/* Life Management.
 *
 * Sythion has no calendar, reminder or bill-provider integration today. This
 * screen therefore renders an explicitly-labelled demonstration workspace so
 * the intended surface is inspectable, and states plainly what would be real.
 */

const REMINDER_TONE: Record<string, 'ok' | 'warn' | 'danger' | 'idle' | 'info'> = {
  high: 'danger',
  medium: 'warn',
  low: 'idle',
};

const ROUTINE_TONE: Record<string, 'ok' | 'warn' | 'danger' | 'idle'> = {
  ok: 'ok',
  blocked: 'warn',
  failed: 'danger',
};

export function LifeManagementScreen() {
  const settings = useAppSettings();
  const dataMode = useAppStore((s) => s.dataMode);
  const [view, setView] = useState<'today' | 'reminders' | 'routines' | 'admin'>('today');
  const [done, setDone] = useState<Record<string, boolean>>(() =>
    Object.fromEntries(demoReminders.map((r) => [r.id, r.done]))
  );

  const openReminders = useMemo(
    () => demoReminders.filter((r) => !done[r.id]).length,
    [done]
  );
  const dueSoon = demoAdmin.filter((a) => a.status === 'due-soon').length;

  return (
    <div className="screen">
      <div className="screen__inner">
        <ScreenHead
          eyebrow="Personal"
          title="Life Management"
          description="Calendar, reminders, routines and recurring administration in one place."
          actions={
            <>
              <DemoTag />
              <Chips
                value={view}
                onChange={setView}
                allLabel=""
                options={[
                  { value: 'today', label: 'Today' },
                  { value: 'reminders', label: 'Reminders' },
                  { value: 'routines', label: 'Routines' },
                  { value: 'admin', label: 'Administration' },
                ]}
              />
            </>
          }
        />

        <Notice tone="warn" icon="alert">
          <span>
            <strong>Demonstration workspace.</strong> Sythion has no calendar provider, reminder store or billing
            integration at present. Nothing below is synchronised from a real account, and no notification will fire
            from it.
          </span>
        </Notice>

        <div className="grid grid--4">
          <Stat
            label="Scheduled today"
            icon="calendar"
            value={demoCalendar.length}
            foot="Items on the demonstration calendar"
            onClick={() => setView('today')}
            active={view === 'today'}
          />
          <Stat
            label="Open reminders"
            icon="clock"
            value={openReminders}
            foot="Unacknowledged"
            onClick={() => setView('reminders')}
            active={view === 'reminders'}
          />
          <Stat
            label="Routines"
            icon="refresh"
            value={demoRoutines.filter((r) => r.status === 'ok').length}
            suffix={`/ ${demoRoutines.length}`}
            foot="Running cleanly"
            onClick={() => setView('routines')}
            active={view === 'routines'}
          />
          <Stat
            label="Due within 48h"
            icon="file"
            value={dueSoon}
            foot="Bills and renewals"
            onClick={() => setView('admin')}
            active={view === 'admin'}
          />
        </div>

        {view === 'today' && (
          <div className="grid grid--main">
            <Panel title="Today's schedule" icon="calendar" flush>
              <div className="list">
                {demoCalendar.map((e) => (
                  <div className="list__row" key={e.id}>
                    <Rail tone={e.kind === 'focus' ? 'accent' : e.kind === 'call' ? 'warn' : 'info'} />
                    <div className="list__main">
                      <span className="t-title" style={{ fontSize: 12.5 }}>
                        {e.title}
                      </span>
                      <span className="list__meta">
                        <span>{e.time}</span>
                        <span>· {e.where}</span>
                        <span>
                          · {e.people} {e.people === 1 ? 'person' : 'people'}
                        </span>
                      </span>
                    </div>
                    <div className="list__trail">
                      <Badge tone={e.kind === 'focus' ? 'accent' : e.kind === 'call' ? 'warn' : 'idle'}>
                        {e.kind}
                      </Badge>
                    </div>
                  </div>
                ))}
              </div>
            </Panel>

            <Panel title="What Sythion would do here" icon="assistant" tight>
              <div className="stack stack--tight">
                {[
                  'Prepare the 16:15 supplier call with the usage figures already in memory',
                  'Protect the 13:00 focus block from incoming interruptions',
                  'Surface the two reminders that are due before the day ends',
                ].map((s) => (
                  <span key={s} className="row t-muted" style={{ gap: 7, alignItems: 'flex-start' }}>
                    <Icon name="check" size={12} style={{ color: 'var(--accent)', flex: '0 0 auto', marginTop: 2 }} />
                    <span>{s}</span>
                  </span>
                ))}
                <p className="t-faint" style={{ marginTop: 6 }}>
                  These are design intents, not running jobs. With a calendar provider connected they would become
                  background automations under Proactive mode.
                </p>
              </div>
            </Panel>
          </div>
        )}

        {view === 'reminders' && (
          <Panel
            title="Reminders"
            icon="clock"
            flush
            actions={
              <>
                <DemoTag />
                <span className="t-faint">Ticks are local to this session</span>
                <Button size="sm" variant="subtle" icon="plus" disabled title="No reminder store is connected">
                  New reminder
                </Button>
              </>
            }
          >
            <div className="list">
              {demoReminders.map((r) => (
                <div className="list__row" key={r.id}>
                  <Rail tone={done[r.id] ? 'ok' : REMINDER_TONE[r.priority]} />
                  <div className="list__main">
                    <span
                      className="t-title"
                      style={{ fontSize: 12.5, textDecoration: done[r.id] ? 'line-through' : undefined }}
                    >
                      {r.title}
                    </span>
                    <span className="list__meta">
                      <span>{relTime(r.due)}</span>
                      <span>· {dateTime(r.due)}</span>
                      <span>· from {r.source}</span>
                    </span>
                  </div>
                  <div className="list__trail">
                    <Switch
                      checked={Boolean(done[r.id])}
                      onChange={(v) => setDone((prev) => ({ ...prev, [r.id]: v }))}
                      label={undefined}
                    />
                  </div>
                </div>
              ))}
            </div>
          </Panel>
        )}

        {view === 'routines' && (
          <Panel
            title="Routines"
            icon="refresh"
            flush
            actions={
              <>
                <DemoTag />
                <span className="t-faint">Interactive state is local to this session</span>
              </>
            }
          >
            <div className="list">
              {demoRoutines.map((r) => (
                <div className="list__row" key={r.id}>
                  <Rail tone={ROUTINE_TONE[r.status]} />
                  <div className="list__main">
                    <span className="t-title" style={{ fontSize: 12.5 }}>
                      {r.name}
                    </span>
                    <span className="list__meta">
                      <span>{r.cadence}</span>
                      <span>· last run {r.lastRun}</span>
                      <span>· owner {r.owner}</span>
                    </span>
                  </div>
                  <div className="list__trail">
                    <Badge tone={ROUTINE_TONE[r.status]}>{r.status}</Badge>
                    <Switch checked={r.status !== 'blocked'} onChange={() => {}} label={undefined} />
                  </div>
                </div>
              ))}
            </div>
          </Panel>
        )}

        {view === 'admin' && (
          <div className="grid grid--main">
            <Panel title="Recurring administration" icon="file" flush actions={<DemoTag />}>
              <div className="table__scroll">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Item</th>
                      <th>Due</th>
                      <th>Amount</th>
                      <th>Auto-pay</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {demoAdmin.map((a) => (
                      <tr key={a.id}>
                        <td>{a.item}</td>
                        <td className="t-mono">{dateTime(a.due)}</td>
                        <td className="t-mono">{a.amount}</td>
                        <td>{a.autopay ? 'Yes' : 'No'}</td>
                        <td>
                          <Badge tone={a.status === 'due-soon' ? 'warn' : 'idle'}>{a.status}</Badge>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Panel>

            <Panel title="Connection status" icon="plug">
              {settings.isLoading ? (
                <p className="t-muted">Checking settings…</p>
              ) : (
                <KV
                  items={[
                    { k: 'Data mode', v: dataMode === 'preview' ? 'Preview (demonstration)' : 'Live backend' },
                    {
                      k: 'Retention',
                      v: `${settings.data?.retention_days ?? '—'} days`,
                    },
                    {
                      k: 'Voice',
                      v: settings.data?.voice_enabled ? 'Enabled' : 'Disabled',
                    },
                  ]}
                />
              )}
              <p className="t-faint" style={{ marginTop: 10 }}>
                A calendar or banking provider would be configured under System &amp; Integrations. Until one exists,
                Sythion can read and write nothing on this screen.
              </p>
            </Panel>
          </div>
        )}

        {demoRoutines.some((r) => r.status !== 'ok') && (
          <Notice tone="warn" icon="alert">
            <span>
              Two routines are not healthy: <strong>Weekly finance recap</strong> is blocked and{' '}
              <strong>Backup project folder</strong> failed. In a connected system these would raise real notifications
              on the Security &amp; Control screen.
            </span>
          </Notice>
        )}

        <Panel title="Sources" icon="info" tight>
          <p className="t-faint">
            Demonstration records were generated locally at page load from a fixed template. They are not read from or
            written to Sythion, and they reset when the window is reopened.
          </p>
        </Panel>
      </div>
    </div>
  );
}