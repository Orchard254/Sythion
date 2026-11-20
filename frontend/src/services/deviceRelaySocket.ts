import { resolveBridgeConfig } from './bridge';

export type RelaySocketFrame =
  | { type: 'session'; session_id: string; device_id: string; sequence: number; authorized: false }
  | { type: 'message'; sequence: number; role: 'synth' | 'owner'; kind: string; text: string; authorized: false }
  | { type: 'accepted'; sequence: number; reply_sequence: number; action_id: string; action_state: string; authorized: false }
  | { type: 'error'; reason: string; authorized: false };

export interface RelaySocketHandlers {
  onFrame: (frame: RelaySocketFrame) => void;
  onOpen?: () => void;
  onClose?: () => void;
  onError?: (error: string) => void;
}

/** Minimal WebSocket contract so tests can inject a fake socket. */
export interface RelaySocketLike {
  onopen: (() => void) | null;
  onmessage: ((event: { data: unknown }) => void) | null;
  onclose: (() => void) | null;
  onerror: (() => void) | null;
  send: (data: string) => void;
  close: () => void;
}

/**
 * Build the first-party device gateway URL from the resolved bridge base URL.
 * The device scope is deliberately NOT under /api/v1 (no control token): it
 * authenticates with the per-device credential in the query string.
 */
export function buildDeviceRelayWsUrl(baseUrl: string, deviceId: string, token: string): string {
  const withoutApi = baseUrl.replace(/\/api\/v1\/?$/, '');
  let origin: string;
  if (/^https?:\/\//.test(withoutApi)) {
    origin = withoutApi;
  } else {
    const host = typeof location !== 'undefined' ? location.host : '127.0.0.1:8000';
    const scheme = withoutApi.startsWith('http://') ? 'http' :
      (typeof location !== 'undefined' && location.protocol === 'https:' ? 'https' : 'http');
    origin = `${scheme}://${host}`;
  }
  const wsOrigin = origin.replace(/^http/, 'ws');
  const params = new URLSearchParams({ device_id: deviceId, token });
  return `${wsOrigin}/device/relay/ws?${params.toString()}`;
}

/** Open the device relay socket and bind handlers. */
export async function openDeviceRelaySocket(
  deviceId: string,
  token: string,
  handlers: RelaySocketHandlers,
  socketFactory: (url: string) => RelaySocketLike = (url) => new WebSocket(url) as unknown as RelaySocketLike,
): Promise<RelaySocketLike> {
  const config = await resolveBridgeConfig();
  const url = buildDeviceRelayWsUrl(config.baseUrl, deviceId, token);
  const socket = socketFactory(url);
  socket.onopen = () => handlers.onOpen?.();
  socket.onmessage = (event) => {
    try {
      const frame = JSON.parse(String(event.data)) as RelaySocketFrame;
      if (frame && typeof frame.type === 'string') handlers.onFrame(frame);
    } catch {
      handlers.onError?.('invalid frame from relay');
    }
  };
  socket.onclose = () => handlers.onClose?.();
  socket.onerror = () => handlers.onError?.('relay socket error');
  return socket;
}

export function sendRelayText(socket: RelaySocketLike, text: string, idempotencyKey?: string): void {
  socket.send(JSON.stringify({
    type: 'send', text,
    ...(idempotencyKey ? { idempotency_key: idempotencyKey } : {}),
  }));
}
