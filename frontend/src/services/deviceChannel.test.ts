import { beforeEach, describe, expect, it, vi } from 'vitest';

const invoke = vi.fn();
const isTauri = vi.fn(() => true);

vi.mock('./bridge', () => ({ isTauri: () => isTauri() }));
vi.mock('@tauri-apps/api/core', () => ({ invoke: (...args: unknown[]) => invoke(...args) }));

import {
  DEFAULT_DEVICE_CHANNEL,
  getDeviceChannelConfig,
  setDeviceChannelConfig,
} from './deviceChannel';

beforeEach(() => {
  invoke.mockReset();
  isTauri.mockReturnValue(true);
});

describe('deviceChannel service', () => {
  it('reads config through the Tauri command', async () => {
    const config = { version: 1, enabled: false, relay_enabled: false, ws_enabled: true };
    invoke.mockResolvedValue(config);
    expect(await getDeviceChannelConfig()).toEqual(config);
    expect(invoke).toHaveBeenCalledWith('get_device_channel_config');
  });

  it('fails closed outside the desktop shell', async () => {
    isTauri.mockReturnValue(false);
    const config = await getDeviceChannelConfig();
    expect(config.enabled).toBe(false);
    expect(invoke).not.toHaveBeenCalled();
  });

  it('writes config through the Tauri command and returns stored value', async () => {
    const next = { version: 1, enabled: true, relay_enabled: true, ws_enabled: false };
    invoke.mockResolvedValue(next);
    expect(await setDeviceChannelConfig(next)).toEqual(next);
    expect(invoke).toHaveBeenCalledWith('set_device_channel_config', { config: next });
  });

  it('rejects invalid versions and browser writes', async () => {
    await expect(setDeviceChannelConfig({ ...DEFAULT_DEVICE_CHANNEL, version: 99 }))
      .rejects.toThrow('Unsupported device channel config version');
    isTauri.mockReturnValue(false);
    await expect(setDeviceChannelConfig(DEFAULT_DEVICE_CHANNEL))
      .rejects.toThrow('desktop app');
  });
});
