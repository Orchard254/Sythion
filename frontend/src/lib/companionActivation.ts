/**
 * When may the Companion overlay appear?
 *
 * The overlay is an always-on-top, taskbar-less window. While the first-run
 * setup gate is active it floats above the setup form, obscures the very
 * screen the user has to complete, and invites interaction with a runtime
 * that is not configured yet. It must therefore stay out of the way until the
 * setup gate has been cleared.
 *
 * The decision lives here so it can be exercised without a Tauri runtime.
 */
export interface CompanionActivationInput {
  /** The Control Center has a live backend connection. */
  isConnected: boolean;
  /**
   * `null`  - readiness has not been resolved yet, the decision is pending.
   * `true`  - first-run setup is required.
   * `false` - the runtime is configured, no setup gate is active.
   */
  setupNeeded: boolean | null;
  /** The user chose the Limited (text-first) mode, which skips the gate. */
  textFirstMode: boolean;
}

export function shouldActivateCompanion(input: CompanionActivationInput): boolean {
  // Without a backend the overlay could not do anything at all, so it is not
  // created. It reappears on its own once the connection is restored.
  if (!input.isConnected) return false;

  // Limited mode is an explicit choice to run without the setup gate, so the
  // overlay is available immediately.
  if (input.textFirstMode) return true;

  // Readiness has not been answered yet. Activating now would put the overlay
  // back in front of the setup screen.
  if (input.setupNeeded === null) return false;

  // The setup gate is active: keep the overlay out of the way.
  return input.setupNeeded === false;
}
