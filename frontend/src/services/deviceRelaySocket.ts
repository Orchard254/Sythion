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
 * WebSocket subprotocol carrying the credential. Mirrors
 * backend.core.auth.TOKEN_SUBPROTOCOL_PREFIX / make_token_subprotocol.
 *
 * Wire format: `sythion-token.<base64url(utf8(token)) without '=' padding>`.
 * The stable prefix cannot be confused with other protocols, and base64url
 * keeps the raw token out of the Sec-WebSocket-Protocol header value. Browser
 * JS can set this via `new WebSocket(url, [subprotocol])` even though it cannot
 * set arbitrary custom headers, so the credential never enters the URL where
 * it would leak into devtools, proxy logs or access logs.
 */
export const TOKEN_SUBPROTOCOL_PREFIX = 'sythion-token.';

function base64UrlEncode(value: string): string {
  const bytes = new TextEncoder().encode(value);
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

/** Encode a credential as the WebSocket subprotocol the backend expects. */
export function tokenSubprotocol(token: string): string {
  return `${TOKEN_SUBPROTOCOL_PREFIX}${base64UrlEncode(token)}`;
}

/**
 * Build the first-party device gateway URL from the resolved bridge base URL.
 * The device scope is deliberately NOT under /api/v1 (no control token). Only
 * the non-secret device id is a query param; the credential travels in the
 * negotiated WebSocket subprotocol (see tokenSubprotocol).
 */
export function buildDeviceRelayWsUrl(baseUrl: string, deviceId: string): string {
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
  const params = new URLSearchParams({ device_id: deviceId });
  return `${wsOrigin}/device/relay/ws?${params.toString()}`;
}

/** Injectable socket constructor; tests record the requested subprotocols. */
export type RelaySocketFactory = (
  url: string,
  subprotocols: string[],
) => RelaySocketLike;

/** Open the device relay socket and bind handlers. */
export async function openDeviceRelaySocket(
  deviceId: string,
  token: string,
  handlers: RelaySocketHandlers,
  socketFactory: RelaySocketFactory = (url, subprotocols) =>
    new WebSocket(url, subprotocols) as unknown as RelaySocketLike,
): Promise<RelaySocketLike> {
  const config = await resolveBridgeConfig();
  const url = buildDeviceRelayWsUrl(config.baseUrl, deviceId);
  const socket = socketFactory(url, [tokenSubprotocol(token)]);
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

/**
 * Acknowledge one sequenced relay message as delivered. The Core records this
 * as durable delivery evidence (never authority/proof of an effect). Sending is
 * idempotent on the server per (session_id, sequence), so re-acks are harmless.
 */
export function sendRelayAck(socket: RelaySocketLike, sequence: number): void {
  socket.send(JSON.stringify({ type: 'ack', sequence }));
}
