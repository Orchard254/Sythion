import { create } from 'react-test-renderer';
import { describe, expect, it, vi } from 'vitest';
import { DevicesCard } from './DevicesCard';
import type { RegisteredDevice } from '../types';

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
    expect(without.root.findAllByType('button')).toHaveLength(0);

    const onChange = vi.fn();
    const withCallback = create(
      <DevicesCard devices={[device]} onTrustChange={onChange} />,
    );
    const buttons = withCallback.root.findAllByType('button');
    expect(buttons.length).toBeGreaterThan(0);
    buttons.find((b) => b.children.join('') === 'trusted')?.props.onClick();
    expect(onChange).toHaveBeenCalledWith('dev_abc', 'trusted');
  });

  it('handles an empty registry', () => {
    const view = create(<DevicesCard devices={[]} />);
    expect(JSON.stringify(view.toJSON())).toContain('No devices');
  });
});
