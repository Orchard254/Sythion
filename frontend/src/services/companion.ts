import { isTauri } from './bridge';

/**
 * Ask the desktop shell to reveal the Companion overlay.
 *
 * The overlay is created hidden at startup: while the first-run setup gate is
 * active it would float above the setup form, because it is an always-on-top
 * window with no taskbar button. The Control Center calls this once the gate
 * has been cleared.
 *
 * Outside the Tauri shell (browser development, preview mode) there is no
 * overlay to reveal, so this is a no-op.
 */
export async function activateCompanion(): Promise<void> {
  if (!isTauri()) return;
  try {
    const { invoke } = await import('@tauri-apps/api/core');
    await invoke('set_companion_visible', { visible: true });
  } catch (err) {
    // The overlay is an additional surface, not a dependency of the Control
    // Center. A failure here must not take the app down, but it must not be
    // silent either - the shell records the reason in its log file.
    console.warn('[sythion] could not reveal the Companion overlay:', err);
  }
}
