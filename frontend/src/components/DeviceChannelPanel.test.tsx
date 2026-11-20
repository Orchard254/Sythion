import { act, create } from 'react-test-renderer';
import { describe, expect, it, vi } from 'vitest';
import { DeviceChannelPanel } from './DeviceChannelPanel';
import type { DeviceChannelConfig } from '../services/deviceChannel';

const base: DeviceChannelConfig = {
  version: 1,
  enabled: true,
  relay_enabled: true,
  ws_enabled: true,
};

const flush = async () => {
  await act(async () => { await Promise.resolve(); });
};

describe('DeviceChannelPanel', () => {
  it('loads and renders the shell config', async () => {
    const load = vi.fn().mockResolvedValue({ ...base, ws_enabled: false });
    let view!: ReturnType<typeof create>;
    await act(async () => { view = create(<DeviceChannelPanel load={load} />); });
    await flush();
    const rendered = JSON.stringify(view.toJSON());
    expect(rendered).toContain('Device channel');
    expect(view.root.findAllByProps({ className: 't-mono t-faint' })[0]
      .children.join('')).toBe('version 1');
    const switches = view.root.findAllByProps({ role: 'switch' });
    expect(switches.map((s) => s.props['aria-checked'])).toEqual([true, true, false]);
  });

  it('saves a disable change through the shell command', async () => {
    const load = vi.fn().mockResolvedValue(base);
    const save = vi.fn().mockImplementation(async (config: DeviceChannelConfig) => config);
    let view!: ReturnType<typeof create>;
    await act(async () => { view = create(<DeviceChannelPanel load={load} save={save} />); });
    await flush();

    const master = view.root.findAllByProps({ role: 'switch' })[0];
    await act(async () => { master.props.onClick(); });
    await flush();

    expect(save).toHaveBeenCalledWith({ ...base, enabled: false });
  });

  it('surfaces save errors without changing the view', async () => {
    const load = vi.fn().mockResolvedValue(base);
    const save = vi.fn().mockRejectedValue(new Error('shell refused'));
    let view!: ReturnType<typeof create>;
    await act(async () => { view = create(<DeviceChannelPanel load={load} save={save} />); });
    await flush();

    const relay = view.root.findAllByProps({ role: 'switch' })[1];
    await act(async () => { relay.props.onClick(); });
    await flush();

    expect(JSON.stringify(view.toJSON())).toContain('shell refused');
    const switches = view.root.findAllByProps({ role: 'switch' });
    expect(switches[1].props['aria-checked']).toBe(true);
  });
});
