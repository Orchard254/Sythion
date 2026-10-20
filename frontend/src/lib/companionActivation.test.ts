import { describe, expect, it } from 'vitest';
import { shouldActivateCompanion } from './companionActivation';

/*
 * Regression coverage for the startup coordination defect.
 *
 * The Companion overlay is always-on-top and has no taskbar button. While the
 * first-run setup screen was still on screen it floated above the setup form
 * and obscured it. These assertions pin the rule that the overlay only
 * appears once the setup gate has been cleared.
 */
describe('companion activation', () => {
  it('stays hidden while first-run setup is required', () => {
    expect(
      shouldActivateCompanion({ isConnected: true, setupNeeded: true, textFirstMode: false })
    ).toBe(false);
  });

  it('stays hidden until readiness has been resolved', () => {
    expect(
      shouldActivateCompanion({ isConnected: true, setupNeeded: null, textFirstMode: false })
    ).toBe(false);
  });

  it('appears once the setup gate is cleared', () => {
    expect(
      shouldActivateCompanion({ isConnected: true, setupNeeded: false, textFirstMode: false })
    ).toBe(true);
  });

  it('appears immediately in the limited text-first mode', () => {
    expect(
      shouldActivateCompanion({ isConnected: true, setupNeeded: null, textFirstMode: true })
    ).toBe(true);
  });

  it('does not appear without a backend connection', () => {
    expect(
      shouldActivateCompanion({ isConnected: false, setupNeeded: false, textFirstMode: false })
    ).toBe(false);
  });

  it('does not appear without a backend connection even in limited mode', () => {
    expect(
      shouldActivateCompanion({ isConnected: false, setupNeeded: null, textFirstMode: true })
    ).toBe(false);
  });
});
