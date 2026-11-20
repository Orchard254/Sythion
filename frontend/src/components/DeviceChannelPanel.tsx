import { useEffect, useState } from 'react';
import { Badge, Button, Notice, Panel, Switch } from './ui';
import {
  getDeviceChannelConfig,
  setDeviceChannelConfig,
  type DeviceChannelConfig,
} from '../services/deviceChannel';

interface Props {
  /** Injectable for tests; defaults to the real Tauri-backed service. */
  load?: () => Promise<DeviceChannelConfig>;
  save?: (config: DeviceChannelConfig) => Promise<DeviceChannelConfig>;
}

/**
 * Control Center view of the shell-owned device channel.
 *
 * These flags can only disable channels. They never grant a capability, widen
 * the loopback boundary, or expose credentials.
 */
export function DeviceChannelPanel({ load = getDeviceChannelConfig, save = setDeviceChannelConfig }: Props) {
  const [config, setConfig] = useState<DeviceChannelConfig | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    load().then(
      (value) => { if (active) setConfig(value); },
      (err) => { if (active) setError(String(err)); },
    );
    return () => { active = false; };
  }, [load]);

  const update = async (patch: Partial<DeviceChannelConfig>) => {
    if (!config || busy) return;
    const next = { ...config, ...patch };
    setBusy(true);
    setError('');
    try {
      const stored = await save(next);
      setConfig(stored);
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel title="Device channel" hint="Shell-owned first-party relay and device scope">
      <Notice tone="info">
        These switches can only disable device access. They never grant a
        permission or expose the sidecar beyond the loopback boundary.
      </Notice>
      {error && <Notice tone="danger">{error}</Notice>}
      {!config ? (
        <p className="t-faint">Loading device channel configuration…</p>
      ) : (
        <div className="stack" data-testid="device-channel-panel">
          <div className="row row--between">
            <div>
              <strong>Device channel</strong>
              <div className="t-faint">Master switch for all device endpoints</div>
            </div>
            <Switch
              checked={config.enabled}
              disabled={busy}
              onChange={(checked) => void update({ enabled: checked })}
            />
          </div>
          <div className="row row--between">
            <div>
              <strong>HTTP relay</strong>
              <div className="t-faint">Device-authenticated relay sessions and messages</div>
            </div>
            <Switch
              checked={config.relay_enabled}
              disabled={busy || !config.enabled}
              onChange={(checked) => void update({ relay_enabled: checked })}
            />
          </div>
          <div className="row row--between">
            <div>
              <strong>Device WebSocket</strong>
              <div className="t-faint">Device-authenticated /device/relay/ws scope</div>
            </div>
            <Switch
              checked={config.ws_enabled}
              disabled={busy || !config.enabled}
              onChange={(checked) => void update({ ws_enabled: checked })}
            />
          </div>
          <div className="row row--wrap" style={{ gap: 8 }}>
            <Badge tone={config.enabled ? 'ok' : 'idle'}>
              {config.enabled ? 'enabled' : 'disabled'}
            </Badge>
            <span className="t-mono t-faint">version {config.version}</span>
            {busy && <Button disabled>Saving…</Button>}
          </div>
        </div>
      )}
    </Panel>
  );
}
