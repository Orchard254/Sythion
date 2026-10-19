export type RuntimeHealthState = 'checking' | 'healthy' | 'unavailable' | 'stale';

export interface RuntimeHealthInput {
  /** Whether the initial bridge handshake succeeded. */
  isConnected: boolean;
  /** A health request is currently in flight. */
  isFetching: boolean;
  /** The most recent health request failed. */
  isError: boolean;
  /** A successful health result is available (possibly cached). */
  hasData: boolean;
  /** Epoch ms of the last successful health check (0 if none). */
  dataUpdatedAt: number;
  /** Current epoch ms. */
  now: number;
  /** How old a successful check may be before it is considered stale. */
  staleAfterMs?: number;
}

export interface RuntimeHealthResult {
  state: RuntimeHealthState;
  /** True only when the displayed health can be treated as current. */
  current: boolean;
  /** Age of the last successful check in ms, or null if none. */
  ageMs: number | null;
}

/** Default staleness window: a health check older than this is not "current". */
export const DEFAULT_STALE_AFTER_MS = 60_000;

/**
 * Derive the truthful runtime-health state from the latest reliable signals.
 *
 * Cached data is never presented as confirmed-current health: a failed check
 * yields `stale` (when cached data exists) or `unavailable` (when it does not),
 * and a successful check older than `staleAfterMs` is also `stale`.
 */
export function assessRuntimeHealth(input: RuntimeHealthInput): RuntimeHealthResult {
  const staleAfter = input.staleAfterMs ?? DEFAULT_STALE_AFTER_MS;
  const ageMs = input.dataUpdatedAt > 0 ? input.now - input.dataUpdatedAt : null;

  if (!input.isConnected) {
    return { state: 'unavailable', current: false, ageMs };
  }
  if (input.isFetching && !input.hasData) {
    return { state: 'checking', current: false, ageMs };
  }
  if (input.isError) {
    if (input.hasData) {
      return { state: 'stale', current: false, ageMs };
    }
    return { state: 'unavailable', current: false, ageMs };
  }
  if (input.hasData) {
    if (ageMs !== null && ageMs > staleAfter) {
      return { state: 'stale', current: false, ageMs };
    }
    return { state: 'healthy', current: true, ageMs };
  }
  return { state: 'checking', current: false, ageMs };
}

export function runtimeHealthLabel(state: RuntimeHealthState, ageMs: number | null): string {
  switch (state) {
    case 'healthy':
      return 'Runtime online';
    case 'checking':
      return 'Checking runtime…';
    case 'stale':
      return ageMs !== null ? `Runtime last seen ${Math.round(ageMs / 1000)}s ago` : 'Runtime status stale';
    case 'unavailable':
      return 'Backend offline';
  }
}