import { act, create } from 'react-test-renderer';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { PhoneRelayScreen } from './PhoneRelay';
import { relayService } from '../services/api';
import { openDeviceRelaySocket, sendRelayText, type RelaySocketHandlers, type RelaySocketLike } from '../services/deviceRelaySocket';

vi.mock('../services/api', () => ({
  relayService: {
    open: vi.fn(), poll: vi.fn(), send: vi.fn(), close: vi.fn(),
  },
}));

vi.mock('../services/deviceRelaySocket', () => ({
  openDeviceRelaySocket: vi.fn(),
  sendRelayText: vi.fn(),
  buildDeviceRelayWsUrl: vi.fn(),
}));

const setRoute = vi.fn();
vi.mock('../store/navStore', () => ({ useNavStore: () => setRoute }));

const opened = {
  session_id: 'rs_test', device_id: 'dev_1', status: 'open', expires_at: null,
};

function fakeSocket(): RelaySocketLike {
  return {
    onopen: null, onmessage: null, onclose: null, onerror: null,
    send: vi.fn(), close: vi.fn(),
  };
}

const allText = (view: ReturnType<typeof create>): string =>
  view.root.findAllByType('span').map((b) =>
    b.children.filter((c) => typeof c === 'string').join('')).join(' ');

const click = (view: ReturnType<typeof create>, label: string) => {
  const button = view.root.findAllByType('button')
    .find((b) => b.children.join('') === label);
  expect(button).toBeDefined();
  return button!;
};

describe('PhoneRelayScreen', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setRoute.mockClear();
    vi.mocked(relayService.open).mockResolvedValue(opened);
    vi.mocked(relayService.poll).mockResolvedValue([]);
    vi.mocked(relayService.send).mockResolvedValue({});
    vi.useRealTimers();
  });

  it('renders the first-party, non-authorizing notice', () => {
    const view = create(<PhoneRelayScreen />);
    const rendered = JSON.stringify(view.toJSON());
    expect(rendered).toContain('No third party');
    expect(rendered).toContain('cannot approve actions');
    expect(rendered).toContain('WebSocket');
  });

  it('connects over WSS and renders real-time sequenced replies', async () => {
    const socket = fakeSocket();
    vi.mocked(openDeviceRelaySocket).mockResolvedValue(socket);
    let view!: ReturnType<typeof create>;
    await act(async () => { view = create(<PhoneRelayScreen />); });

    const inputs = view.root.findAllByType('input');
    await act(async () => {
      inputs[0].props.onChange({ target: { value: 'dev_1' } });
      inputs[1].props.onChange({ target: { value: 'secret-token' } });
    });
    await act(async () => { click(view, 'Connect').props.onClick(); });
    await act(async () => { await Promise.resolve(); });

    expect(openDeviceRelaySocket).toHaveBeenCalledWith('dev_1', 'secret-token', expect.any(Object));
    const handlers = vi.mocked(openDeviceRelaySocket).mock.calls[0][2] as RelaySocketHandlers;

    await act(async () => { handlers.onOpen!(); });
    await act(async () => { await Promise.resolve(); });
    expect(allText(view)).toContain('Connected via WS');

    await act(async () => {
      handlers.onFrame({
        type: 'message', sequence: 7, role: 'synth', kind: 'text',
        text: 'Hello from Synth.', authorized: false,
      });
    });
    await act(async () => { await Promise.resolve(); });
    const rendered = JSON.stringify(view.toJSON());
    expect(rendered).toContain('Hello from Synth.');
    expect(rendered).toContain('synth');
  });

  it('sends text over WSS and renders the tracked reply_sequence/action_state', async () => {
    const socket = fakeSocket();
    vi.mocked(openDeviceRelaySocket).mockResolvedValue(socket);
    let view!: ReturnType<typeof create>;
    await act(async () => { view = create(<PhoneRelayScreen />); });
    const inputs = view.root.findAllByType('input');
    await act(async () => {
      inputs[0].props.onChange({ target: { value: 'dev_1' } });
      inputs[1].props.onChange({ target: { value: 'secret-token' } });
    });
    await act(async () => { click(view, 'Connect').props.onClick(); });
    await act(async () => { await Promise.resolve(); });
    const handlers = vi.mocked(openDeviceRelaySocket).mock.calls[0][2] as RelaySocketHandlers;
    await act(async () => { handlers.onOpen!(); });
    await act(async () => { await Promise.resolve(); });

    // Type and send via the WSS transport.
    const messageInput = view.root.findAllByType('input').find(
      (i) => i.props['aria-label'] === 'Message Synth')!;
    await act(async () => {
      messageInput.props.onChange({ target: { value: 'hello over ws' } });
    });
    await act(async () => { click(view, 'Send').props.onClick(); });
    expect(sendRelayText).toHaveBeenCalledTimes(1);
    const [sentSocket, sentText, sentKey] = vi.mocked(sendRelayText).mock.calls[0];
    expect(sentSocket).toBe(socket);
    expect(sentText).toBe('hello over ws');
    expect(sentKey).toMatch(/^phone-/);

    // The server acknowledges with a tracked, verified reply.
    await act(async () => {
      handlers.onFrame({
        type: 'accepted', sequence: 3, reply_sequence: 9,
        action_id: 'ra_1', action_state: 'completed', authorized: false,
      });
    });
    await act(async () => { await Promise.resolve(); });

    const badge = view.root.findByProps({ 'data-testid': 'relay-tracked-action' });
    expect(badge).toBeDefined();
    const rendered = allText(view);
    expect(rendered).toContain('completed');
    expect(rendered).toContain('reply #9');
  });

  it('uses a stable UI-generated idempotency key per message', async () => {
    const socket = fakeSocket();
    vi.mocked(openDeviceRelaySocket).mockResolvedValue(socket);
    let view!: ReturnType<typeof create>;
    await act(async () => { view = create(<PhoneRelayScreen />); });
    const inputs = view.root.findAllByType('input');
    await act(async () => {
      inputs[0].props.onChange({ target: { value: 'dev_1' } });
      inputs[1].props.onChange({ target: { value: 'secret-token' } });
    });
    await act(async () => { click(view, 'Connect').props.onClick(); });
    await act(async () => { await Promise.resolve(); });
    const handlers = vi.mocked(openDeviceRelaySocket).mock.calls[0][2] as RelaySocketHandlers;
    await act(async () => { handlers.onOpen!(); });
    await act(async () => { await Promise.resolve(); });

    const messageInput = () => view.root.findAllByType('input').find(
      (i) => i.props['aria-label'] === 'Message Synth')!;

    // Typing then editing the same draft keeps the same key (dedup on retry).
    await act(async () => { messageInput().props.onChange({ target: { value: 'he' } }); });
    await act(async () => { messageInput().props.onChange({ target: { value: 'hello' } }); });
    await act(async () => { click(view, 'Send').props.onClick(); });
    const firstKey = vi.mocked(sendRelayText).mock.calls[0][2];
    expect(firstKey).toMatch(/^phone-/);

    // A new message after clearing gets a fresh key.
    await act(async () => { messageInput().props.onChange({ target: { value: '' } }); });
    await act(async () => { messageInput().props.onChange({ target: { value: 'second' } }); });
    await act(async () => { click(view, 'Send').props.onClick(); });
    const secondKey = vi.mocked(sendRelayText).mock.calls[1][2];
    expect(secondKey).toMatch(/^phone-/);
    expect(secondKey).not.toBe(firstKey);
  });

  it('falls back to HTTP polling when WSS is unavailable', async () => {
    vi.mocked(openDeviceRelaySocket).mockRejectedValue(new Error('no websocket'));
    let view!: ReturnType<typeof create>;
    await act(async () => { view = create(<PhoneRelayScreen />); });
    const inputs = view.root.findAllByType('input');
    await act(async () => {
      inputs[0].props.onChange({ target: { value: 'dev_1' } });
      inputs[1].props.onChange({ target: { value: 'secret-token' } });
    });
    await act(async () => { click(view, 'Connect').props.onClick(); });
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await Promise.resolve(); });

    expect(relayService.open).toHaveBeenCalledWith('dev_1', 'secret-token');
    expect(allText(view)).toContain('Connected via HTTP');
  });

  it('offers reconciliation navigation when a reply outcome is uncertain', async () => {
    const socket = fakeSocket();
    vi.mocked(openDeviceRelaySocket).mockResolvedValue(socket);
    let view!: ReturnType<typeof create>;
    await act(async () => { view = create(<PhoneRelayScreen />); });
    const inputs = view.root.findAllByType('input');
    await act(async () => {
      inputs[0].props.onChange({ target: { value: 'dev_1' } });
      inputs[1].props.onChange({ target: { value: 'secret-token' } });
    });
    await act(async () => { click(view, 'Connect').props.onClick(); });
    await act(async () => { await Promise.resolve(); });
    const handlers = vi.mocked(openDeviceRelaySocket).mock.calls[0][2] as RelaySocketHandlers;
    await act(async () => { handlers.onOpen!(); });
    await act(async () => { await Promise.resolve(); });

    // Completed outcomes must not show a reconcile affordance.
    await act(async () => {
      handlers.onFrame({
        type: 'accepted', sequence: 1, reply_sequence: 2,
        action_id: 'ra_1', action_state: 'completed', authorized: false,
      });
    });
    await act(async () => { await Promise.resolve(); });
    expect(view.root.findAllByType('button').some(
      (b) => b.children.some((c) => typeof c === 'string' && c === 'Reconcile'))).toBe(false);

    // An uncertain outcome gets a one-click path to the reconciliation panel.
    await act(async () => {
      handlers.onFrame({
        type: 'accepted', sequence: 2, reply_sequence: 3,
        action_id: 'ra_2', action_state: 'uncertain', authorized: false,
      });
    });
    await act(async () => { await Promise.resolve(); });
    const reconcile = view.root.findAllByType('button')
      .find((b) => b.children.some((c) => typeof c === 'string' && c === 'Reconcile'));
    expect(reconcile).toBeDefined();
    reconcile!.props.onClick();
    expect(setRoute).toHaveBeenCalledWith('overview');
  });

  it('sends over the HTTP fallback with the UI-generated idempotency key', async () => {
    vi.mocked(openDeviceRelaySocket).mockRejectedValue(new Error('no websocket'));
    let view!: ReturnType<typeof create>;
    await act(async () => { view = create(<PhoneRelayScreen />); });
    const inputs = view.root.findAllByType('input');
    await act(async () => {
      inputs[0].props.onChange({ target: { value: 'dev_1' } });
      inputs[1].props.onChange({ target: { value: 'secret-token' } });
    });
    await act(async () => { click(view, 'Connect').props.onClick(); });
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await Promise.resolve(); });

    const messageInput = view.root.findAllByType('input').find(
      (i) => i.props['aria-label'] === 'Message Synth')!;
    await act(async () => { messageInput.props.onChange({ target: { value: 'hello http' } }); });
    await act(async () => { click(view, 'Send').props.onClick(); });
    await act(async () => { await Promise.resolve(); });

    expect(relayService.send).toHaveBeenCalledTimes(1);
    const [sid, device, token, text, key] = vi.mocked(relayService.send).mock.calls[0];
    expect(sid).toBe('rs_test');
    expect(device).toBe('dev_1');
    expect(token).toBe('secret-token');
    expect(text).toBe('hello http');
    expect(key).toMatch(/^phone-/);
  });

  it('renders a seeded morning_brief frame as a structured card, not raw JSON', async () => {
    const socket = fakeSocket();
    vi.mocked(openDeviceRelaySocket).mockResolvedValue(socket);
    let view!: ReturnType<typeof create>;
    await act(async () => { view = create(<PhoneRelayScreen />); });
    const inputs = view.root.findAllByType('input');
    await act(async () => {
      inputs[0].props.onChange({ target: { value: 'dev_1' } });
      inputs[1].props.onChange({ target: { value: 'secret-token' } });
    });
    await act(async () => { click(view, 'Connect').props.onClick(); });
    await act(async () => { await Promise.resolve(); });
    const handlers = vi.mocked(openDeviceRelaySocket).mock.calls[0][2] as RelaySocketHandlers;
    await act(async () => { handlers.onOpen!(); });
    await act(async () => { await Promise.resolve(); });

    const seeded = JSON.stringify({
      date: '2026-11-20', headline: 'Good morning.',
      sections: [{ key: 'goals', title: 'Goals finished', lines: ['Ship the report'] }],
      note: 'Read-only brief.',
    });
    await act(async () => {
      handlers.onFrame({
        type: 'message', sequence: 1, role: 'synth', kind: 'morning_brief',
        text: seeded, authorized: false,
      });
    });
    await act(async () => { await Promise.resolve(); });

    expect(view.root.findByProps({ 'data-testid': 'relay-brief-card' })).toBeDefined();
    const rendered = JSON.stringify(view.toJSON());
    expect(rendered).toContain('Good morning.');
    expect(rendered).toContain('Goals finished');
    expect(rendered).toContain('Ship the report');
    // Raw JSON text must not be shown verbatim.
    expect(rendered).not.toContain(seeded);
  });

  it('never renders an approval control', () => {
    const view = create(<PhoneRelayScreen />);
    const labels = view.root.findAllByType('button').map((b) => b.children.join(''));
    expect(labels).not.toContain('Approve');
  });
});
