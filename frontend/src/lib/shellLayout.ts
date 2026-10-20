import type { CSSProperties } from 'react';

/**
 * Grid contract for the single-screen shell.
 *
 * `.shell` (see index.css) is a TWO-row grid:
 *
 *   grid-template-rows: var(--topbar-h) 1fr;
 *   grid-template-areas: "brand topbar" "sidebar main";
 *
 * The single-screen layouts (boot, first-run setup, offline notice) replace
 * the areas with a ONE-row template. A one-row area template does NOT reset
 * the row track list: the explicit grid still has two rows, so the named area
 * lands in ROW 1 - the fixed `--topbar-h` track (60px) - and ROW 2 (`1fr`)
 * is left empty.
 *
 * The visible result is a screen clipped to the top of the window with the
 * rest of the viewport blank, and the content only reachable through a
 * scrollbar that is itself 60px tall.
 *
 * The row template therefore has to be overridden TOGETHER with the areas.
 * `gridTemplateRows` is the piece that is easy to forget, so it lives here
 * next to the areas and columns instead of being re-typed at every call site.
 */
export const SINGLE_SCREEN_AREA = 'boot';

/**
 * The grid definition for a single-screen shell.
 *
 * `gridTemplateRows: '1fr'` is the load-bearing part: it gives the screen the
 * whole shell height instead of the 60px topbar track.
 */
export function singleScreenShellStyle(): CSSProperties {
  return {
    gridTemplateColumns: '1fr',
    gridTemplateRows: '1fr',
    gridTemplateAreas: `"${SINGLE_SCREEN_AREA}"`,
  };
}

/**
 * The grid placement for the content inside a single-screen shell.
 *
 * `gridArea` must name the area declared by `singleScreenShellStyle`, so the
 * two are produced from the same constant.
 */
export function singleScreenContentStyle(style?: CSSProperties): CSSProperties {
  return { gridArea: SINGLE_SCREEN_AREA, ...style };
}
