import { describe, expect, it } from 'vitest';
import {
  SINGLE_SCREEN_AREA,
  singleScreenContentStyle,
  singleScreenShellStyle,
} from './shellLayout';

/*
 * Regression coverage for the startup rendering defect.
 *
 * The `.shell` stylesheet rule declares TWO rows (`--topbar-h` and `1fr`).
 * A single-screen layout used to override only `grid-template-columns` and
 * `grid-template-areas`, so the named area landed in the 60px topbar track
 * and the whole setup screen was clipped to the top of the window while the
 * rest of the viewport stayed blank.
 *
 * These assertions fail if the row template is dropped again.
 */
describe('single-screen shell layout', () => {
  it('gives the screen the full shell height instead of the topbar track', () => {
    const style = singleScreenShellStyle();
    expect(style.gridTemplateRows).toBe('1fr');
  });

  it('declares a single column and a single named area', () => {
    const style = singleScreenShellStyle();
    expect(style.gridTemplateColumns).toBe('1fr');
    expect(style.gridTemplateAreas).toBe('"boot"');
  });

  it('overrides every grid axis the .shell stylesheet rule sets', () => {
    // index.css sets grid-template-columns, grid-template-rows and
    // grid-template-areas on .shell. A single-screen layout has to override
    // all three, otherwise it inherits a track list it did not ask for.
    const style = singleScreenShellStyle();
    expect(style.gridTemplateColumns).toBeDefined();
    expect(style.gridTemplateRows).toBeDefined();
    expect(style.gridTemplateAreas).toBeDefined();
  });

  it('places content in the same named area the shell declares', () => {
    expect(singleScreenContentStyle().gridArea).toBe(SINGLE_SCREEN_AREA);
  });

  it('keeps caller supplied content styles', () => {
    const style = singleScreenContentStyle({ overflowY: 'auto', padding: 40 });
    expect(style.overflowY).toBe('auto');
    expect(style.padding).toBe(40);
    expect(style.gridArea).toBe(SINGLE_SCREEN_AREA);
  });
});
