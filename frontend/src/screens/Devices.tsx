import { useMemo, useState, type CSSProperties } from 'react';
import { Icon, type IconName } from '../components/icons';
import {
  Badge,
  Button,
  Chips,
  DataBadge,
  DemoTag,
  Drawer,
  EmptyState,
  KV,
  LoadingBlock,
  Notice,
  Panel,
  Rail,
  ScreenHead,
  SearchInput,
  Stat,
} from '../components/ui';
import { useCapabilities } from '../hooks/useBackend';
import { useAppStore } from '../store/appStore';
import { demoDevices } from '../data/demo';
import { CAPABILITY_TONE } from '../lib/format';

/* Devices & Home.
 *
 * The backend exposes a *capability registry* (GET /api/v1/capabilities) that
 * describes what Sythion can do, but it has no device-registry endpoint: there
 * is no list of paired machines, phones or smart-home nodes anywhere in the
 * FastAPI contract. The inventory below is therefore demonstration data, and
 * the real capability column is what the backend actually reports.
 */

type DeviceStatus = 'online' | 'offline' | 'degraded';

const STATUS_TONE: Record<DeviceStatus, 'ok' | 'danger' | 'warn'> = {
  online: 'ok',
  offline: 'danger',
  degraded: 'warn',
};

const CATEGORY_ICON: Record<string, IconName> = {
  Computer: 'laptop',
  Phone: 'plug',
  Storage: 'server',
  Climate: 'home',
  Lighting: 'power',
  Media: 'activity',
};

export function DevicesScreen() {
  const capabilities = useCapabilities();
  const dataMode = useAppStore((s) => s.dataMode);

  const [search, setSearch] = useState('');
  const [category, setCategory] = useState<'__all__' | string>('__all__');
  const [openId, setOpenId] = useState<string | null>(null);

  const categories = useMemo(
    () => Array.from(new Set(demoDevices.map((d) => d.category))),
    []
  );

  const list = useMemo(() => {
    const q = search.trim().toLowerCase();
    return demoDevices.filter((d) => {
      if (category !== '__all__' && d.category !== category) return false;
      if (!q) return true;
      return d.name.toLowerCase().includes(q) || d.detail.toLowerCase().includes(q);
    });
  }, [search, category]);

  const online = demoDevices.filter((d) => d.status === 'online').length;
  const offline = demoDevices.filter((d) => d.status === 'offline').length;
  const degraded = demoDevices.filter((d) => d.status === 'degraded').length;
  const controllable = demoDevices.filter((d) => d.actions.length > 0).length;
  const reachPct = demoDevices.length ? Math.round((online / demoDevices.length) * 100) : 0;

  const caps = capabilities.data ?? [];
  const deviceCaps = caps.filter((c) => c.category === 'Devices' || c.category === 'Desktop');

  return (
    <div className="screen">
      <div className="screen__inner">
        <ScreenHead
          eyebrow="Environment"
          title="Devices & Home"
          description="What Sythion is attached to, what it is permitted to do there, and which attachments are broken."
          actions={<DemoTag />}
        />

        <Notice tone="warn" icon="alert">
          <span>
            <strong>The inventory below is demonstration data.</strong> Sythion has no device-registry endpoint, so no
            paired phone, appliance or remote machine is actually being tracked. The capability column to the right{' '}
            <em>is</em> live backend data.
          </span>
        </Notice>

        <div className="grid grid--4">
          <Stat label="In inventory" icon="devices" value={demoDevices.length} foot="Demonstration records" />
          <Stat
            label="Reachable"
            icon="plug"
            value={online}
            suffix={`/ ${demoDevices.length}`}
            foot="Heartbeat within the last minute"
          />
          <Stat
            label="Unreachable"
            icon="alert"
            value={offline}
            tone={offline > 0 ? 'accent' : undefined}
            foot="No response to the last check"
          />
          <Stat
            label="Actionable"
            icon="power"
            value={controllable}
            foot="Have at least one permitted action"
          />
        </div>

        <div className="grid grid--aside">
          <div className="stack">
            {/* Read-only radial readout. Deliberately role="img" and not a
                control: these devices are demonstration records with no
                backend device registry, so a dial that looked operable would
                promise capability the API does not have. */}
            <Panel title="Fleet reachability" icon="activity" actions={<DemoTag />} tight>
              <div className="dial">
                <div
                  className="dial__ring"
                  role="img"
                  aria-label={`${online} of ${demoDevices.length} demonstration devices reachable`}
                >
                  <div className="dial__arc" style={{ '--dial-pct': String(reachPct) } as CSSProperties} />
                  <div className="dial__hub">
                    <span className="dial__value">{online}</span>
                    <span className="dial__unit">online</span>
                  </div>
                </div>
                <div className="dial__legend">
                  <span className="dial__legend-title">Last heartbeat</span>
                  <Badge tone="ok">
                    <span className="dot" style={{ background: 'currentColor' }} />
                    {online} online
                  </Badge>
                  <Badge tone="warn">
                    <span className="dot" style={{ background: 'currentColor' }} />
                    {degraded} degraded
                  </Badge>
                  <Badge tone="danger">
                    <span className="dot" style={{ background: 'currentColor' }} />
                    {offline} unreachable
                  </Badge>
                </div>
              </div>
            </Panel>

            <Panel title="Filter" icon="filter" tight>
              <div className="stack stack--tight">
                <SearchInput value={search} onChange={setSearch} placeholder="Search devices…" />
                <Chips
                  value={category}
                  onChange={setCategory}
                  options={categories.map((c) => ({ value: c, label: c }))}
                />
              </div>
            </Panel>

            <Panel
              title="Related capabilities"
              icon="plug"
              actions={dataMode === 'preview' ? <DemoTag /> : <DataBadge origin="live" />}
              flush
            >
              {capabilities.isLoading ? (
                <div className="panel__body">
                  <LoadingBlock rows={3} />
                </div>
              ) : deviceCaps.length === 0 ? (
                <EmptyState
                  icon="plug"
                  title="No device capabilities registered"
                  description="The backend capability registry contains nothing in the Devices or Desktop category."
                />
              ) : (
                <div className="list">
                  {deviceCaps.map((c) => (
                    <div className="list__row" key={c.id}>
                      <Rail tone={CAPABILITY_TONE[c.status] ?? 'idle'} />
                      <div className="list__main">
                        <span className="t-body t-truncate" style={{ color: 'var(--text-0)' }}>
                          {c.name}
                        </span>
                        <span className="list__meta">
                          <Badge tone={CAPABILITY_TONE[c.status] ?? 'idle'}>{c.status}</Badge>
                          <Badge tone={c.risk_class === 'HIGH' ? 'danger' : c.risk_class === 'MEDIUM' ? 'warn' : 'ok'}>
                            {c.risk_class} risk
                          </Badge>
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </Panel>
          </div>

          <Panel title="Device inventory" icon="devices" flush actions={<DemoTag />}>
            {list.length === 0 ? (
              <EmptyState
                icon="devices"
                title="No devices match"
                description="Clear the search or pick a different category."
              />
            ) : (
              <div className="list">
                {list.map((d) => (
                  <button
                    type="button"
                    key={d.id}
                    className={`list__row list__row--clickable${d.id === openId ? ' list__row--selected' : ''}`}
                    onClick={() => setOpenId(d.id)}
                  >
                    <span
                      className="tile-icon"
                      aria-hidden="true"
                    >
                      <Icon name={CATEGORY_ICON[d.category] ?? 'devices'} size={14} />
                    </span>
                    <div className="list__main">
                      <span className="t-title t-truncate" style={{ fontSize: 12.5 }}>
                        {d.name}
                      </span>
                      <span className="list__meta">
                        <Badge tone={STATUS_TONE[d.status]}>
                          <span className="dot" style={{ background: 'currentColor' }} />
                          {d.status}
                        </Badge>
                        <span>{d.category}</span>
                        <span>· seen {d.lastSeen}</span>
                      </span>
                      <span className="t-muted t-clamp-2">{d.detail}</span>
                    </div>
                    <div className="list__trail">
                      <Icon name="chevronRight" size={13} style={{ color: 'var(--text-3)' }} />
                    </div>
                  </button>
                ))}
              </div>
            )}
          </Panel>
        </div>

        <Panel title="Controls policy" icon="shield" tight>
          <div className="grid grid--2">
            <div className="stack stack--tight">
              <span className="field__label">Why device actions are mostly unavailable</span>
              <p className="t-muted">
                Sythion authorizes every tool call through its executor policy. A capability marked{' '}
                <Badge tone="warn">REQUIRES_PERMISSION</Badge> or <Badge tone="danger">HIGH risk</Badge> is not executed
                from the interface — a toggle here could never grant it.
              </p>
            </div>
            <div className="stack stack--tight">
              <span className="field__label">What is genuinely wired</span>
              <p className="t-muted">
                This machine's filesystem, desktop and application tools are real registered capabilities. They act on
                the local Windows host, not on the demonstration devices listed above.
              </p>
            </div>
          </div>
        </Panel>
      </div>

      {openId && (
        <DeviceDrawer
          device={demoDevices.find((d) => d.id === openId)!}
          onClose={() => setOpenId(null)}
        />
      )}
    </div>
  );
}

function DeviceDrawer({
  device,
  onClose,
}: {
  device: (typeof demoDevices)[number];
  onClose: () => void;
}) {
  return (
    <Drawer
      title={device.name}
      subtitle={
        <span className="row" style={{ gap: 8 }}>
          <Badge tone={STATUS_TONE[device.status]}>
            <span className="dot" style={{ background: 'currentColor' }} />
            {device.status}
          </Badge>
          <span>{device.category}</span>
          <DemoTag compact />
        </span>
      }
      onClose={onClose}
      footer={
        <Button variant="subtle" onClick={onClose}>
          Close
        </Button>
      }
    >
      <KV
        items={[
          { k: 'Description', v: device.detail },
          { k: 'Last seen', v: device.lastSeen },
          { k: 'Permission scope', v: device.scope },
        ]}
      />

      <div className="stack stack--tight">
        <span className="field__label">Capabilities</span>
        <div className="chips">
          {device.capabilities.map((c) => (
            <span className="chip" key={c}>
              {c}
            </span>
          ))}
        </div>
      </div>

      <div className="stack stack--tight">
        <span className="field__label">Available actions</span>
        {device.actions.length === 0 ? (
          <Notice tone="warn" icon="lock">
            <span>
              No actions are exposed for this device. No integration is registered, so there is nothing for Sythion to
              authenticate against.
            </span>
          </Notice>
        ) : (
          <div className="stack stack--tight">
            <p className="t-faint">
              The buttons below are inert. They show the intended control surface; they do not send anything to this
              device.
            </p>
            <div className="row row--wrap" style={{ gap: 7, marginTop: 4 }}>
              {device.actions.map((a) => (
                <Button key={a} size="sm" variant="subtle" disabled>
                  {a}
                </Button>
              ))}
            </div>
          </div>
        )}
      </div>

      <div className="stack stack--tight">
        <span className="field__label">How Sythion would reach this device</span>
        <p className="t-muted">{describeRoute(device.category)}</p>
      </div>
    </Drawer>
  );
}

function describeRoute(category: string): string {
  switch (category) {
    case 'Computer':
      return 'Through the local tool executor on this host, bounded by the approved file roots in Settings. A remote machine would need a separate execution agent — none is registered.';
    case 'Phone':
      return 'Through a companion app that exposes a messaging or notification bridge. No bridge process is registered.';
    case 'Storage':
      return 'Through an OS-level mount such as SMB. Reachability depends on the share being mounted and the path being inside an approved root.';
    case 'Climate':
    case 'Lighting':
    case 'Media':
      return 'Through a smart-home fabric integration such as Matter or Home Assistant. No such integration exists in the backend, so these capabilities resolve to UNAVAILABLE.';
    default:
      return 'No integration path is registered for this category.';
  }
}