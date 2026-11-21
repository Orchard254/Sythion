import { describe, expect, it, vi } from 'vitest';
import {
  TOKEN_SUBPROTOCOL_PREFIX, buildDeviceRelayWsUrl, openDeviceRelaySocket,
  sendRelayAck, sendRelayText, tokenSubprotocol, type RelaySocketLike,
} from './deviceRelaySocket';

vi.mock('./bridge', () => ({
  resolveBridgeConfig: async () => ({ baseUrl: 'http://127.0.0.1:8000/api/v1', controlToken: 'ctl' }),
}));

function fakeSocket(): RelaySocketLike {
  return {
    onopen: null, onmessage: null, onclose: null, onerror: null,
    send: vi.fn(), close: vi.fn(),
  };
}

describe('deviceRelaySocket', () => {
  it('builds a device-scope WS URL with only the non-secret device id', () => {
    const url = buildDeviceRelayWsUrl('http://127.0.0.1:8000/api/v1', 'dev_1');
    expect(url).toContain('ws://127.0.0.1:8000/device/relay/ws');
    expect(url).toContain('device_id=dev_1');
    expect(url).not.toContain('token=');
    expect(url).not.toContain('/api/v1');
  });

  it('uses the stable, unambiguous token subprotocol format', () => {
    expect(TOKEN_SUBPROTOCOL_PREFIX).toBe('sythion-token.');
    expect(tokenSubprotocol('tok')).toBe('sythion-token.dG9r');
    // Check only the encoded payload: the stable prefix literally contains
    // the word "token", so asserting on the whole string is a false positive.
    expect(tokenSubprotocol('tok').split('sythion-token.')[1]).not.toContain('tok');
  });

  it('passes the token via subprotocol, never in the URL, and never in logs', async () => {
    const socket = fakeSocket();
    let seenUrl = '';
    let seenProtocols: string[] = [];
    const spies = ['log', 'info', 'warn', 'error', 'debug'].map((level) =>
      vi.spyOn(console, level as 'log').mockImplementation(() => {}));
    try {
      await openDeviceRelaySocket('dev_1', 'secret-token', { onFrame: vi.fn() }, (url, protocols) => {
        seenUrl = url;
        seenProtocols = protocols;
        return socket;
      });
    } finally {
      spies.forEach((spy) => spy.mockRestore());
    }
    expect(seenUrl).toContain('device_id=dev_1');
    expect(seenUrl).not.toContain('secret-token');
    expect(seenUrl).not.toContain('token=');
    expect(seenProtocols).toEqual([tokenSubprotocol('secret-token')]);
    expect(seenProtocols[0]).not.toContain('secret-token');
    for (const spy of spies) {
      for (const call of spy.mock.calls) {
        expect(JSON.stringify(call)).not.toContain('secret-token');
      }
    }
  });

  it('binds handlers and delivers parsed sequenced frames', async () => {
    const socket = fakeSocket();
    const onFrame = vi.fn();
    await openDeviceRelaySocket('dev_1', 'tok', { onFrame }, () => socket);

    socket.onopen!();
    socket.onmessage!({ data: JSON.stringify({
      type: 'message', sequence: 5, role: 'synth', kind: 'text',
      text: 'hello', authorized: false,
    }) });

    expect(onFrame).toHaveBeenCalledWith(expect.objectContaining({
      type: 'message', sequence: 5, text: 'hello',
    }));
  });

  it('reports malformed frames as errors', async () => {
    const socket = fakeSocket();
    const onError = vi.fn();
    await openDeviceRelaySocket('dev_1', 'tok', { onFrame: vi.fn(), onError }, () => socket);
    socket.onmessage!({ data: 'not json' });
    expect(onError).toHaveBeenCalledWith('invalid frame from relay');
  });

  it('sends an ack frame for a received sequence', () => {
    const socket = fakeSocket();
    sendRelayAck(socket, 42);
    expect(JSON.parse((socket.send as ReturnType<typeof vi.fn>).mock.calls[0][0])).toEqual({
      type: 'ack', sequence: 42,
    });
  });

  it('sends text as a send frame with an optional idempotency key', () => {
    const socket = fakeSocket();
    sendRelayText(socket, 'ping');
    expect(JSON.parse((socket.send as ReturnType<typeof vi.fn>).mock.calls[0][0])).toEqual({
      type: 'send', text: 'ping',
    });
    sendRelayText(socket, 'ping', 'k1');
    expect(JSON.parse((socket.send as ReturnType<typeof vi.fn>).mock.calls[1][0])).toEqual({
      type: 'send', text: 'ping', idempotency_key: 'k1',
    });
  });
});
