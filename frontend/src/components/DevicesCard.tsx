import { Badge, EmptyState, KV, Panel } from './ui';
import type { RegisteredDevice } from '../types';

const TRUST_TONE = {
  observed: 'idle',
  trusted: 'info',
  privileged: 'accent',
} as const;

/**
 * Owner-visible device registry. Trust changes are explicit owner actions sent
 * through the authenticated API; a device can never widen its own trust.
 */
export function DevicesCard({
  devices,
  onTrustChange,
}: {
  devices: RegisteredDevice[];
  onTrustChange?: (deviceId: string, level: RegisteredDevice['trust_level']) => void;
}) {
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
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}
