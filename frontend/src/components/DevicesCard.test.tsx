import { act, create } from 'react-test-renderer';
import { describe, expect, it, vi } from 'vitest';
import { DevicesCard } from './DevicesCard';
import type { DeviceCapabilities, RegisteredDevice } from '../types';

const device: RegisteredDevice = {
  device_id: 'dev_abc',
  name: 'this-pc',
  kind: 'local_windows',
  trust_level: 'observed',
  first_seen: '2026-11-20T08:00:00',
  last_seen: '2026-11-21T08:00:00',
  capabilities: ['files', 'system'],
};

describe('DevicesCard', () => {
  it('shows device identity, trust and capabilities', () => {
    const view = create(<DevicesCard devices={[device]} />);
    const rendered = JSON.stringify(view.toJSON());
    expect(rendered).toContain('this-pc');
    expect(rendered).toContain('observed');
    expect(rendered).toContain('files, system');
    expect(rendered).toContain('dev_abc');
  });

  it('renders trust controls only when an owner callback is provided', () => {
    const without = create(<DevicesCard devices={[device]} />);
    const trustLabels = without.root.findAllByType('button')
      .map((b) => b.children.join(''));
    expect(trustLabels).not.toContain('trusted');

    const onChange = vi.fn();
    const withCallback = create(
      <DevicesCard devices={[device]} onTrustChange={onChange} />,
    );
    const buttons = withCallback.root.findAllByType('button');
    buttons.find((b) => b.children.join('') === 'trusted')?.props.onClick();
    expect(onChange).toHaveBeenCalledWith('dev_abc', 'trusted');
  });

  it('handles an empty registry', () => {
    const view = create(<DevicesCard devices={[]} />);
    expect(JSON.stringify(view.toJSON())).toContain('No devices');
  });

  it('shows per-device capability status on demand', async () => {
    const capabilities: DeviceCapabilities = {
      device_id: 'dev_abc',
      trust_level: 'observed',
      note: 'Read-only discovery.',
      capabilities: [
        { capability: 'briefing.read', status: 'granted', granted: true,
          min_trust: 'observed', global_capability: null, global_status: null,
          allowed: true, reason: 'ok' },
        { capability: 'goals.submit', status: 'restricted', granted: true,
          min_trust: 'privileged', global_capability: 'goal_planning',
          global_status: 'AVAILABLE', allowed: false, reason: 'insufficient_trust' },
      ],
    };
    const load = vi.fn().mockResolvedValue(capabilities);
    let view!: ReturnType<typeof create>;
    await act(async () => {
      view = create(<DevicesCard devices={[device]} loadCapabilities={load} />);
    });

    const toggle = view.root.findByProps({ 'data-testid': 'capabilities-toggle-dev_abc' });
    await act(async () => { toggle.props.onClick(); });
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await Promise.resolve(); });

    expect(load).toHaveBeenCalledWith('dev_abc');
    const rendered = JSON.stringify(view.toJSON());
    expect(rendered).toContain('briefing.read');
    expect(rendered).toContain('granted');
    expect(rendered).toContain('goals.submit');
    expect(rendered).toContain('restricted');
    expect(rendered).toContain('insufficient_trust');
  });

  it('surfaces capability load errors without granting', async () => {
    const load = vi.fn().mockRejectedValue(new Error('endpoint unavailable'));
    let view!: ReturnType<typeof create>;
    await act(async () => {
      view = create(<DevicesCard devices={[device]} loadCapabilities={load} />);
    });
    const toggle = view.root.findByProps({ 'data-testid': 'capabilities-toggle-dev_abc' });
    await act(async () => { toggle.props.onClick(); });
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await Promise.resolve(); });
    expect(JSON.stringify(view.toJSON())).toContain('endpoint unavailable');
  });
});
