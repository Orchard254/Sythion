import { create } from 'react-test-renderer';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { PhoneRelayScreen } from './PhoneRelay';
import { relayService } from '../services/api';

vi.mock('../services/api', () => ({
  relayService: {
    open: vi.fn(),
    poll: vi.fn(),
    send: vi.fn(),
    close: vi.fn(),
  },
}));

const opened = {
  session_id: 'rs_test',
  device_id: 'dev_1',
  status: 'open',
  expires_at: null,
};

describe('PhoneRelayScreen', () => {
  beforeEach(() => {
    vi.mocked(relayService.open).mockResolvedValue(opened);
    vi.mocked(relayService.poll).mockResolvedValue([]);
    vi.mocked(relayService.send).mockResolvedValue({});
    vi.useRealTimers();
  });

  it('renders the first-party, non-authorizing notice', () => {
    const view = create(<PhoneRelayScreen />);
    expect(JSON.stringify(view.toJSON())).toContain('No third party');
    expect(JSON.stringify(view.toJSON())).toContain('cannot approve actions');
  });

  it('opens a session with the device credential', async () => {
    const view = create(<PhoneRelayScreen />);
    const inputs = view.root.findAllByType('input');
    const connectButton = view.root.findAllByType('button')
      .find((b) => b.children.join('') === 'Connect');
    expect(connectButton).toBeDefined();

    await vi.waitFor(() => {
      inputs[0].props.onChange({ target: { value: 'dev_1' } });
      inputs[1].props.onChange({ target: { value: 'secret-token' } });
      connectButton?.props.onClick();
    });
    // The credential input must never be rendered as plain text.
    expect(inputs[1].props.type).toBe('password');
  });
});
