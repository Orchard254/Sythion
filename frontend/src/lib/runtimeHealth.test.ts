import { describe, expect, it } from 'vitest';
import { assessRuntimeHealth, runtimeHealthLabel, DEFAULT_STALE_AFTER_MS } from './runtimeHealth';

describe('assessRuntimeHealth', () => {
  const now = 1_000_000;

  it('reports unavailable when never connected', () => {
    const r = assessRuntimeHealth({
      isConnected: false, isFetching: false, isError: false, hasData: false,
      dataUpdatedAt: 0, now,
    });
    expect(r.state).toBe('unavailable');
    expect(r.current).toBe(false);
  });

  it('reports checking on initial fetch before any data', () => {
    const r = assessRuntimeHealth({
      isConnected: true, isFetching: true, isError: false, hasData: false,
      dataUpdatedAt: 0, now,
    });
    expect(r.state).toBe('checking');
    expect(r.current).toBe(false);
  });

  it('reports healthy on a fresh successful check', () => {
    const r = assessRuntimeHealth({
      isConnected: true, isFetching: false, isError: false, hasData: true,
      dataUpdatedAt: now - 5_000, now,
    });
    expect(r.state).toBe('healthy');
    expect(r.current).toBe(true);
    expect(r.ageMs).toBe(5_000);
  });

  it('does not leave cached health as current after a failed check', () => {
    const r = assessRuntimeHealth({
      isConnected: true, isFetching: false, isError: true, hasData: true,
      dataUpdatedAt: now - 5_000, now,
    });
    expect(r.state).toBe('stale');
    expect(r.current).toBe(false);
  });

  it('reports unavailable when a check fails with no cached data', () => {
    const r = assessRuntimeHealth({
      isConnected: true, isFetching: false, isError: true, hasData: false,
      dataUpdatedAt: 0, now,
    });
    expect(r.state).toBe('unavailable');
    expect(r.current).toBe(false);
  });

  it('marks an old successful check as stale', () => {
    const r = assessRuntimeHealth({
      isConnected: true, isFetching: false, isError: false, hasData: true,
      dataUpdatedAt: now - (DEFAULT_STALE_AFTER_MS + 1_000), now,
    });
    expect(r.state).toBe('stale');
    expect(r.current).toBe(false);
  });

  it('restores healthy after a later successful check', () => {
    const r = assessRuntimeHealth({
      isConnected: true, isFetching: false, isError: false, hasData: true,
      dataUpdatedAt: now - 1_000, now,
    });
    expect(r.state).toBe('healthy');
    expect(r.current).toBe(true);
  });

  it('initial loading does not masquerade as healthy', () => {
    const r = assessRuntimeHealth({
      isConnected: true, isFetching: true, isError: false, hasData: false,
      dataUpdatedAt: 0, now,
    });
    expect(r.state).not.toBe('healthy');
    expect(r.current).toBe(false);
  });
});

describe('runtimeHealthLabel', () => {
  it('labels each state honestly', () => {
    expect(runtimeHealthLabel('healthy', 1000)).toBe('Runtime online');
    expect(runtimeHealthLabel('checking', null)).toBe('Checking runtime…');
    expect(runtimeHealthLabel('unavailable', null)).toBe('Backend offline');
    expect(runtimeHealthLabel('stale', 5000)).toBe('Runtime last seen 5s ago');
  });
});