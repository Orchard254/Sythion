/* Surface detection.
 *
 * The Companion overlay and the Control Center are the SAME frontend build and
 * the SAME React entry point. They are distinguished at runtime by a single
 * `?surface=` query marker that the Rust side puts on the Companion window's
 * URL (src-tauri/src/main.rs -> create_companion_window).
 *
 * Why a query marker rather than the Tauri window label:
 *   - reading `location.search` needs no capability and no IPC, so adding the
 *     overlay required zero new permissions for surface identification;
 *   - it keeps the overlay debuggable in a plain browser, where the same route
 *     can be inspected with `?surface=companion`.
 *
 * `data-surface` is mirrored onto <html> so the stylesheet can make the page
 * background transparent for the overlay. That cannot be a CSS class alone,
 * because `body { background: var(--n-canvas) }` is global and the overlay must
 * not paint a canvas.
 *
 * Security note: this marker is not an access-control boundary. Supplying it in
 * the main window only removes chrome; it grants nothing and reveals nothing.
 */

export type Surface = 'control-center' | 'companion';

export function currentSurface(): Surface {
  if (typeof window === 'undefined') return 'control-center';
  const params = new URLSearchParams(window.location.search);
  return params.get('surface') === 'companion' ? 'companion' : 'control-center';
}

export function isCompanionSurface(): boolean {
  return currentSurface() === 'companion';
}

/** Mirror the surface onto <html> for CSS scoping. Idempotent. */
export function applySurfaceAttribute(): void {
  if (typeof document === 'undefined') return;
  document.documentElement.dataset.surface = currentSurface();
}