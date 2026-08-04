export interface BridgeConfig {
  baseUrl: string;
  controlToken: string;
}

export function isTauri(): boolean {
  return typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;
}

/**
 * Resolve the API base URL + per-launch control token.
 *
 * Packaged shell: the Rust bridge exposes the sidecar's bound port + handshake
 * token via the single `get_bridge_config` command. Browser dev: the Vite proxy
 * forwards `/api` to 127.0.0.1:8000 and the token comes from the environment
 * (set by the dev launcher), which is exactly the dev-only path.
 */
export async function resolveBridgeConfig(): Promise<BridgeConfig> {
  if (!isTauri()) {
    const controlToken = import.meta.env.DEV
      ? import.meta.env.VITE_SYTHION_CONTROL_TOKEN || ''
      : '';
    return { baseUrl: '/api/v1', controlToken };
  }

  const { invoke } = await import('@tauri-apps/api/core');
  const cfg = (await invoke('get_bridge_config')) as { base_url: string; control_token: string };
  return {
    baseUrl: `${cfg.base_url}/api/v1`,
    controlToken: cfg.control_token,
  };
}
