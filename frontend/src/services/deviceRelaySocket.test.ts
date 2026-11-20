import { describe, expect, it, vi } from 'vitest';
import {
  buildDeviceRelayWsUrl, openDeviceRelaySocket, sendRelayText,
  type RelaySocketLike,
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
  it('builds a device-scope WS URL with credential query params', () => {
    const url = buildDeviceRelayWsUrl('http://127.0.0.1:8000/api/v1', 'dev_1', 'tok');
    expect(url).toContain('ws://127.0.0.1:8000/device/relay/ws');
    expect(url).toContain('device_id=dev_1');
    expect(url).toContain('token=tok');
    expect(url).not.toContain('/api/v1');
  });

  it('never puts the control token in the device WS URL', () => {
    const url = buildDeviceRelayWsUrl('http://127.0.0.1:8000/api/v1', 'dev_1', 'tok');
    expect(url).not.toContain('ctl');
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
