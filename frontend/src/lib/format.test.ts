import { afterEach, describe, expect, it, vi } from 'vitest';
import { relTime } from './format';

afterEach(() => { vi.useRealTimers(); });

describe('relative time wording', () => {
  it('formats moments without redundant suffixes', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-11-10T03:00:00Z'));
    expect(relTime('2026-11-10T03:00:00Z')).toBe('just now');
    expect(relTime('2026-11-10T02:59:40Z')).toBe('just now');
    expect(relTime('2026-11-10T03:00:20Z')).toBe('in a moment');
  });

  it('retains correct units and UTC parsing for older or future events', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-11-10T03:00:00Z'));
    expect(relTime('2026-11-10T02:58:00Z')).toBe('2m ago');
    expect(relTime('2026-11-10T03:02:00Z')).toBe('2m from now');
    // The backend sends naive UTC timestamps without Z.
    expect(relTime('2026-11-10T02:59:00')).toBe('1m ago');
  });
});
