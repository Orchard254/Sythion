import { describe, expect, it } from 'vitest';
import { assessRuntimeHealth, runtimeHealthLabel } from './runtimeHealth';

describe('assessRuntimeHealth degraded backend state', () => {
  const now = 1_000_000;

  it('reports degraded when a successful health check says the startup is degraded', () => {
    const r = assessRuntimeHealth({
      isConnected: true,
      isFetching: false,
      isError: false,
      hasData: true,
      dataUpdatedAt: now - 1_000,
      now,
      isDegraded: true,
    });
    expect(r.state).toBe('degraded');
    expect(r.current).toBe(true);
  });

  it('treats a backend status string of degraded as degraded', () => {
    const r = assessRuntimeHealth({
      isConnected: true,
      isFetching: false,
      isError: false,
      hasData: true,
      dataUpdatedAt: now - 1_000,
      now,
      healthStatus: 'degraded',
    });
    expect(r.state).toBe('degraded');
  });

  it('does not flip a failed check to degraded', () => {
    const r = assessRuntimeHealth({
      isConnected: true,
      isFetching: false,
      isError: true,
      hasData: true,
      dataUpdatedAt: now - 1_000,
      now,
      isDegraded: true,
    });
    expect(r.state).toBe('stale');
  });

  it('keeps an unreachable backend unavailable', () => {
    const r = assessRuntimeHealth({
      isConnected: false,
      isFetching: false,
      isError: false,
      hasData: false,
      dataUpdatedAt: 0,
      now,
      isDegraded: true,
    });
    expect(r.state).toBe('unavailable');
  });
});

describe('runtimeHealthLabel degraded', () => {
  it('labels a degraded runtime', () => {
    expect(runtimeHealthLabel('degraded', 1000)).toBe('Runtime degraded');
  });
});