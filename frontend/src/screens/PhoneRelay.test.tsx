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
    expect(sendRelayText).toHaveBeenCalledWith(socket, 'hello over ws');

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

  it('never renders an approval control', () => {
    const view = create(<PhoneRelayScreen />);
    const labels = view.root.findAllByType('button').map((b) => b.children.join(''));
    expect(labels).not.toContain('Approve');
  });
});
