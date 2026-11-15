import { afterEach, describe, expect, it, vi } from 'vitest';
import { cloudShellRoute, readProviderLane, saveProviderLane } from './providerLane';

afterEach(() => { vi.unstubAllGlobals(); });

describe('cloud provider lane is preference, never authorization', () => {
  it('defaults to Ollama if storage is missing or inaccessible', () => {
    vi.stubGlobal('localStorage', { getItem: () => { throw new Error('unavailable'); } });
    expect(readProviderLane()).toBe('ollama');
    expect(() => saveProviderLane('cloud-answer-only')).not.toThrow();
  });

  it('recognizes only the exact cloud preference', () => {
    const setItem = vi.fn();
    const getItem = vi.fn(() => 'cloud-answer-only');
    vi.stubGlobal('localStorage', { getItem, setItem });
    expect(readProviderLane()).toBe('cloud-answer-only');
    saveProviderLane('cloud-answer-only');
    expect(setItem).toHaveBeenCalledWith('sythion.aiProviderLane', 'cloud-answer-only');
    getItem.mockReturnValue('invalid');
    expect(readProviderLane()).toBe('ollama');
  });

  it('allows only Assistant and Settings destinations while in cloud mode', () => {
    expect(cloudShellRoute('assistant')).toBe('assistant');
    expect(cloudShellRoute('settings')).toBe('settings');
    for (const route of [
      'overview', 'cloud', 'missions', 'memory', 'automations', 'security', 'system',
    ] as const) {
      expect(cloudShellRoute(route)).toBe('assistant');
    }
  });
});
