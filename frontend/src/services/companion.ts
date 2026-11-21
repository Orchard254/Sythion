import { isTauri } from './bridge';

const COMPANION_PREF_KEY = 'sythion.companionEnabled';

/**
 * The Settings checkbox is the SINGLE persisted source of truth for whether the
 * Companion should be revealed at startup. Every other show/hide path (the
 * global hotkey and the on-capsule close button) is session-only and must not
 * write here.
 *
 * Default off: only the explicit Settings choice can reveal Companion.
 */
export function companionEnabled(): boolean {
  try { return localStorage.getItem(COMPANION_PREF_KEY) === '1'; }
  catch { return false; }
}

/**
 * Persist the Settings preference and apply it to the current session.
 * The desktop command is a session-level show/hide; this is the only place that
 * commits the preference, so the two cannot drift apart.
 */
export async function setCompanionEnabled(enabled: boolean): Promise<void> {
  if (isTauri()) {
    const { invoke } = await import('@tauri-apps/api/core');
    await invoke('set_companion_visible', { visible: enabled });
  }
  // Commit preference only once the desktop acknowledged visibility.
  localStorage.setItem(COMPANION_PREF_KEY, enabled ? '1' : '0');
}

/**
 * Session-only hide. Used by the capsule close button; deliberately does NOT
 * touch the persisted preference, so the Companion still returns next launch
 * when the Settings checkbox remains on. The hotkey performs the same
 * session-only hide on the Rust side.
 */
export async function hideCompanion(): Promise<void> {
  if (!isTauri()) return;
  const { invoke } = await import('@tauri-apps/api/core');
  await invoke('set_companion_visible', { visible: false });
}

/**
 * Startup restore trigger. The caller (App.tsx) must invoke this only AFTER
 * `shouldActivateCompanion()` passes, so an always-on-top overlay can never be
 * shown over the first-run setup gate. This function adds a second guard by
 * requiring the persisted preference to be on; a returning user with the
 * preference set is therefore restored once setup is resolved.
 */
export async function activateCompanion(): Promise<void> {
  if (!companionEnabled() || !isTauri()) return;
  try {
    const { invoke } = await import('@tauri-apps/api/core');
    await invoke('set_companion_visible', { visible: true });
  } catch (error) {
    console.warn('[sythion] could not reveal Companion:', error);
  }
}
