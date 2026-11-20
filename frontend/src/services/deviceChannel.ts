import { isTauri } from './bridge';

export interface DeviceChannelConfig {
  version: number;
  enabled: boolean;
  relay_enabled: boolean;
  ws_enabled: boolean;
}

/** Current behavior when the shell has not written a config file yet. */
export const DEFAULT_DEVICE_CHANNEL: DeviceChannelConfig = {
  version: 1,
  enabled: true,
  relay_enabled: true,
  ws_enabled: true,
};

/**
 * Read the shell-owned device-channel config through the Tauri command.
 * Outside the packaged shell there is no device channel to configure, so the
 * safe browser fallback reports everything disabled (never assumes enabled).
 */
export async function getDeviceChannelConfig(): Promise<DeviceChannelConfig> {
  if (!isTauri()) {
    return { ...DEFAULT_DEVICE_CHANNEL, enabled: false };
  }
  const { invoke } = await import('@tauri-apps/api/core');
  return (await invoke('get_device_channel_config')) as DeviceChannelConfig;
}

/** Persist a validated config through the shell. Returns the stored value. */
export async function setDeviceChannelConfig(
  config: DeviceChannelConfig,
): Promise<DeviceChannelConfig> {
  if (!isTauri()) {
    throw new Error('Device channel can only be configured in the desktop app.');
  }
  if (config.version !== 1) {
    throw new Error('Unsupported device channel config version.');
  }
  const { invoke } = await import('@tauri-apps/api/core');
  return (await invoke('set_device_channel_config', { config })) as DeviceChannelConfig;
}
