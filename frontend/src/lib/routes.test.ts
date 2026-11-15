import { describe, expect, it } from 'vitest';
import { DEFAULT_ROUTE, ROUTES, resolveRoute, routeById } from './routes';

describe('AI model configuration belongs in Settings', () => {
  it('keeps the original Assistant and Settings, without a duplicate cloud route', () => {
    expect(routeById('assistant').label).toBe('AI Assistant');
    expect(routeById('assistant').blurb).toContain('task execution');
    expect(routeById('settings').label).toBe('Settings');
    expect(ROUTES.some((route) => route.id === 'cloud')).toBe(false);
  });

  it('redirects old cloud deep links to Settings, not an experimental chat screen', () => {
    expect(resolveRoute('cloud')).toBe('settings');
    expect(routeById('cloud').id).toBe('settings');
    expect(resolveRoute('assistant')).toBe('assistant');
    expect(resolveRoute('missing-route')).toBe(DEFAULT_ROUTE);
  });
});
