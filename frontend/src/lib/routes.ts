import type { IconName } from '../components/icons';

/* The Sythion destination map. `data` records how much of each screen is
 * backed by a real endpoint today — surfaced in the UI, never hidden. */

export type RouteId =
  | 'overview'
  | 'assistant'
  | 'cloud'
  | 'missions'
  | 'memory'
  | 'automations'
  | 'security'
  | 'system'
  | 'settings';

export type NavGroupId = 'operate' | 'personal' | 'environment' | 'control';

export interface RouteDef {
  id: RouteId;
  label: string;
  icon: IconName;
  group: NavGroupId;
  /** Short line shown in the top bar subtitle. */
  blurb: string;
}

export const NAV_GROUPS: Array<{ id: NavGroupId; label: string }> = [
  { id: 'operate', label: 'Operate' },
  { id: 'personal', label: 'Personal' },
  { id: 'environment', label: 'Environment' },
  { id: 'control', label: 'Control' },
];

export const ROUTES: RouteDef[] = [
  {
    id: 'overview',
    // Operations is the Control Center landing view. The route id stays
    // `overview` so existing deep links keep working.
    label: 'Operations',
    icon: 'overview',
    group: 'operate',
    blurb: 'What needs attention, what is running, and system readiness',
  },
  {
    id: 'assistant',
    label: 'AI Assistant',
    icon: 'assistant',
    group: 'operate',
    // The screen hosts conversations and task execution against the real
    // backend; the wording stays neutral about the mode.
    blurb: 'Conversations, voice input and task execution',
  },
  {
    id: 'cloud',
    label: 'Cloud Text Chat',
    icon: 'assistant',
    group: 'operate',
    blurb: 'Experimental answer-only chat; no tools, tasks or memory writes',
  },
  {
    id: 'missions',
    // "Goals" is the canonical user-facing term. The route id stays `missions`
    // for compatibility and `#/goals` redirects here — see ROUTE_ALIASES.
    label: 'Goals',
    icon: 'missions',
    group: 'operate',
    blurb: 'Goals, their versioned plans, steps and evidence',
  },
  {
    id: 'memory',
    label: 'Memory & Knowledge',
    icon: 'memory',
    group: 'personal',
    blurb: 'What Sythion remembers, and your control over it',
  },
  {
    id: 'automations',
    label: 'Automations',
    icon: 'automations',
    group: 'environment',
    blurb: 'Scheduled and event-triggered workflows',
  },
  {
    id: 'security',
    label: 'Security & Control',
    icon: 'security',
    group: 'control',
    blurb: 'Autonomy mode, permissions, approvals and audit',
  },
  {
    id: 'system',
    label: 'System & Integrations',
    icon: 'system',
    group: 'control',
    blurb: 'Runtime, models, integrations and diagnostics',
  },
  {
    id: 'settings',
    label: 'Settings',
    icon: 'settings',
    group: 'control',
    blurb: 'Interface preferences and application settings',
  },
];

export const DEFAULT_ROUTE: RouteId = 'overview';

/**
 * Redirects for terminology changes.
 *
 * Keeping these here means a shared link or bookmark written before the rename
 * still lands on the right screen instead of silently falling back to the
 * default route.
 */
export const ROUTE_ALIASES: Record<string, RouteId> = {
  goals: 'missions',
  operations: 'overview',
  companion: 'assistant',
};

export function routeById(id: RouteId): RouteDef {
  return ROUTES.find((r) => r.id === id) ?? ROUTES[0];
}

export function isRouteId(value: string): value is RouteId {
  return ROUTES.some((r) => r.id === value);
}

/** Resolve a raw hash segment to a real route, following aliases. */
export function resolveRoute(raw: string): RouteId {
  if (isRouteId(raw)) return raw;
  return ROUTE_ALIASES[raw] ?? DEFAULT_ROUTE;
}