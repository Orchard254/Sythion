import { useState } from 'react';
import { Badge, Button, ConfirmDialog, Notice } from './ui';
import type { DeviceCapability } from '../types';
import { deviceAccessService } from '../services/api';

interface AccessApi {
  issueCredential: (deviceId: string) => Promise<{ device_id: string; token: string }>;
  revokeCredential: (deviceId: string) => Promise<{ device_id: string; revoked: boolean }>;
  addGrant: (deviceId: string, capability: string) => Promise<unknown>;
  removeGrant: (deviceId: string, capability: string) => Promise<unknown>;
}

interface Props {
  deviceId: string;
  capabilities: DeviceCapability[];
  api?: AccessApi;
  onChanged?: () => void;
}

/**
 * Explicit owner controls for one device's remote access.
 *
 * A credential is shown ONCE when issued (the server stores only a hash) and
 * grants are owner actions. Nothing here lets a device widen its own access.
 */
export function DeviceAccessControls({ deviceId, capabilities, api, onChanged }: Props) {
  const service = api ?? deviceAccessService;
  const [token, setToken] = useState<string | null>(null);
  const [confirmRevoke, setConfirmRevoke] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState('');

  const run = async <T,>(key: string, action: () => Promise<T>, done?: (result: T) => void) => {
    setBusy(key);
    setError('');
    try {
      const result = await action();
      done?.(result);
      onChanged?.();
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="stack stack--tight" data-testid="device-access-controls">
      <Notice tone="info">
        Credentials and grants are owner actions. A device can never widen its
        own access. The credential plaintext is shown once — store it safely.
      </Notice>
      {error && <Notice tone="danger">{error}</Notice>}

      <div className="row row--wrap" style={{ gap: 8 }}>
        <Button
          size="sm"
          variant="primary"
          disabled={Boolean(busy)}
          onClick={() => void run('issue',
            () => service.issueCredential(deviceId),
            (result) => setToken(result.token),
          )}
        >
          Issue credential
        </Button>
        <Button
          size="sm"
          disabled={Boolean(busy)}
          onClick={() => setConfirmRevoke(true)}
        >
          Revoke credential
        </Button>
      </div>

      {token && (
        <div className="stack stack--tight" data-testid="device-credential-once">
          <Badge tone="warn">Shown once — copy now</Badge>
          <code className="t-mono">{token}</code>
        </div>
      )}

      {capabilities.length > 0 && (
        <div className="stack stack--tight" data-testid="device-grants">
          {capabilities.map((cap) => (
            <div key={cap.capability} className="row row--wrap" style={{ gap: 8 }}>
              <span className="t-mono">{cap.capability}</span>
              <Badge tone={cap.granted ? 'ok' : 'idle'}>
                {cap.granted ? 'granted' : 'not granted'}
              </Badge>
              {cap.granted ? (
                <Button size="sm" disabled={Boolean(busy)}
                  onClick={() => void run(`remove:${cap.capability}`,
                    () => service.removeGrant(deviceId, cap.capability))}>
                  Remove grant
                </Button>
              ) : (
                <Button size="sm" disabled={Boolean(busy)}
                  onClick={() => void run(`add:${cap.capability}`,
                    () => service.addGrant(deviceId, cap.capability))}>
                  Add grant
                </Button>
              )}
            </div>
          ))}
        </div>
      )}

      {confirmRevoke && (
        <ConfirmDialog
          title={`Revoke credential for this device?`}
          tone="danger"
          confirmLabel="Revoke credential"
          busy={busy === 'revoke'}
          body={
            <div className="stack stack--tight">
              <p>The device will immediately lose remote access with its current credential.</p>
              <p className="t-muted">This does not change grants or trust; a new credential can be issued later.</p>
            </div>
          }
          onCancel={() => setConfirmRevoke(false)}
          onConfirm={() => {
            setConfirmRevoke(false);
            void run('revoke', () => service.revokeCredential(deviceId), () => setToken(null));
          }}
        />
      )}
    </div>
  );
}
