import { useEffect, useState } from 'react';
import { Badge, EmptyState, KV, Panel } from './ui';
import type {
  DeviceCapability, DeviceCapabilities, RegisteredDevice,
} from '../types';
import { deviceCapabilityService } from '../services/api';

const TRUST_TONE = {
  observed: 'idle',
  trusted: 'info',
  privileged: 'accent',
} as const;

const CAPABILITY_TONE: Record<DeviceCapability['status'], 'ok' | 'info' | 'warn' | 'idle' | 'danger'> = {
  granted: 'ok',
  available: 'info',
  restricted: 'warn',
  unavailable: 'idle',
};

interface Props {
  devices: RegisteredDevice[];
  onTrustChange?: (deviceId: string, level: RegisteredDevice['trust_level']) => void;
  /** Injectable for tests; defaults to the real read-only discovery endpoint. */
  loadCapabilities?: (deviceId: string) => Promise<DeviceCapabilities>;
}

/**
 * Owner-visible device registry with per-device capability discovery.
 *
 * Capability status is read-only observation: available/restricted/granted/
 * unavailable never changes a grant or a trust level. Trust changes are separate
 * explicit owner actions; a device can never widen its own trust.
 */
export function DevicesCard({ devices, onTrustChange, loadCapabilities }: Props) {
  const load = loadCapabilities ?? deviceCapabilityService.get;
  const [expanded, setExpanded] = useState<string | null>(null);
  const [capabilities, setCapabilities] = useState<Record<string, DeviceCapability[]>>({});
  const [error, setError] = useState('');

  useEffect(() => {
    if (!expanded || capabilities[expanded]) return;
    let active = true;
    load(expanded).then(
      (result) => { if (active) setCapabilities((old) => ({ ...old, [expanded]: result.capabilities })); },
      (err) => { if (active) setError(String(err)); },
    );
    return () => { active = false; };
  }, [expanded, load, capabilities]);

  return (
    <Panel title="Devices" hint="Endpoints Synth knows about">
      {devices.length === 0 ? (
        <EmptyState title="No devices" description="No devices have registered yet." />
      ) : (
        <div className="devices__list">
          {devices.map((device) => (
            <div key={device.device_id} className="devices__item">
              <div className="row row--wrap" style={{ gap: 8 }}>
                <strong>{device.name}</strong>
                <Badge tone={TRUST_TONE[device.trust_level]}>{device.trust_level}</Badge>
                <span className="t-mono t-faint">{device.kind}</span>
              </div>
              <KV
                items={[
                  { k: 'Device ID', v: device.device_id },
                  { k: 'Last seen', v: device.last_seen ?? '—' },
                  { k: 'Capabilities', v: device.capabilities.join(', ') || 'none' },
                ]}
              />
              {onTrustChange && (
                <div className="row row--wrap" style={{ gap: 6 }}>
                  {(['observed', 'trusted', 'privileged'] as const).map((level) => (
                    <button
                      key={level}
                      type="button"
                      className={`stat stat--clickable${device.trust_level === level ? ' stat--active' : ''}`}
                      disabled={device.trust_level === level}
                      onClick={() => onTrustChange(device.device_id, level)}
                    >
                      {level}
                    </button>
                  ))}
                </div>
              )}

              <button
                type="button"
                className="stat stat--clickable"
                data-testid={`capabilities-toggle-${device.device_id}`}
                onClick={() => setExpanded(expanded === device.device_id ? null : device.device_id)}
              >
                {expanded === device.device_id ? 'Hide capabilities' : 'Show capabilities'}
              </button>

              {expanded === device.device_id && (
                <div className="stack stack--tight" data-testid="device-capabilities">
                  {error && <Badge tone="danger">{error}</Badge>}
                  {!capabilities[device.device_id] ? (
                    <span className="t-faint">Loading capabilities…</span>
                  ) : capabilities[device.device_id].length === 0 ? (
                    <EmptyState title="No capabilities" description="No capabilities discovered." />
                  ) : (
                    capabilities[device.device_id].map((cap) => (
                      <div key={cap.capability} className="row row--wrap" style={{ gap: 8 }}>
                        <span className="t-mono">{cap.capability}</span>
                        <Badge tone={CAPABILITY_TONE[cap.status]}>{cap.status}</Badge>
                        <span className="t-faint">
                          min trust {cap.min_trust}
                          {cap.reason && cap.reason !== 'ok' ? ` · ${cap.reason}` : ''}
                        </span>
                      </div>
                    ))
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}
