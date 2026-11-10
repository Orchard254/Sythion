import { describe, expect, it } from 'vitest';
import { DEFAULT_ROUTE, ROUTES, resolveRoute, routeById } from './routes';

describe('cloud answer-only route registration', () => {
  it('exposes a distinct cloud chat destination outside the task-executing assistant', () => {
    const cloud = routeById('cloud');
    const assistant = routeById('assistant');
    expect(cloud.id).toBe('cloud');
    expect(cloud.group).toBe('operate');
    expect(cloud.label).toBe('Cloud Text Chat');
    expect(cloud.blurb.toLowerCase()).toContain('no tools');
    expect(assistant.id).toBe('assistant');
    expect(assistant.blurb).toContain('task execution');
    expect(ROUTES.filter((route) => route.id === 'cloud')).toHaveLength(1);
  });

  it('resolves explicit cloud navigation without becoming the default route', () => {
    expect(resolveRoute('cloud')).toBe('cloud');
    expect(resolveRoute('assistant')).toBe('assistant');
    expect(resolveRoute('missing-route')).toBe(DEFAULT_ROUTE);
    expect(DEFAULT_ROUTE).not.toBe('cloud');
  });
});
