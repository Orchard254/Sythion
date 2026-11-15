import { isTauri } from './bridge';

const COMPANION_PREF_KEY = 'sythion.companionEnabled';

/** Default off: only the explicit Settings choice can reveal Companion. */
export function companionEnabled(): boolean {
  try { return localStorage.getItem(COMPANION_PREF_KEY) === '1'; }
  catch { return false; }
}

export async function setCompanionEnabled(enabled: boolean): Promise<void> {
  if (isTauri()) {
    const { invoke } = await import('@tauri-apps/api/core');
    await invoke('set_companion_visible', { visible: enabled });
  }
  // Commit preference only once the desktop acknowledged visibility.
  localStorage.setItem(COMPANION_PREF_KEY, enabled ? '1' : '0');
}

export async function activateCompanion(): Promise<void> {
  if (!companionEnabled() || !isTauri()) return;
  try {
    const { invoke } = await import('@tauri-apps/api/core');
    await invoke('set_companion_visible', { visible: true });
  } catch (error) {
    console.warn('[sythion] could not reveal Companion:', error);
  }
}
