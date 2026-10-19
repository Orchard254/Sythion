import type { IconName } from '../components/icons';

/* The Sythion destination map. `data` records how much of each screen is
 * backed by a real endpoint today — surfaced in the UI, never hidden. */

export type RouteId =
  | 'overview'
  | 'assistant'
  | 'missions'
  | 'life'
  | 'devices'
  | 'comms'
  | 'memory'
  | 'automations'
  | 'security'
  | 'system'
  | 'settings'
  /** Development-only visual preview. Not part of the shipped navigation. */
  | 'companion-preview';

export type NavGroupId = 'operate' | 'personal' | 'environment' | 'control';

export interface RouteDef {
  id: RouteId;
  label: string;
  icon: IconName;
  group: NavGroupId;
  /** Short line shown in the top bar subtitle. */
  blurb: string;
  /** Whether this screen reads live backend data, demo data, or both. */
  data: 'live' | 'partial' | 'demo';
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
    data: 'live',
  },
  {
    id: 'assistant',
    label: 'AI Assistant',
    icon: 'assistant',
    group: 'operate',
    // Neutral wording: the screen hosts conversations and task execution in both
    // modes, but claiming "live" execution while preview mode is showing nothing
    // but demonstration data is exactly the contradiction to avoid.
    blurb: 'Conversations, voice input and task execution',
    data: 'live',
  },
  {
    id: 'missions',
    // "Goals" is the canonical user-facing term. The route id stays `missions`
    // for compatibility and `#/goals` redirects here — see ROUTE_ALIASES.
    label: 'Goals',
    icon: 'missions',
    group: 'operate',
    blurb: 'Goals, their versioned plans, steps and evidence',
    data: 'live',
  },
  {
    id: 'life',
    label: 'Life Management',
    icon: 'life',
    group: 'personal',
    blurb: 'Calendar, routines and personal administration',
    data: 'demo',
  },
  {
    id: 'devices',
    label: 'Devices & Home',
    icon: 'devices',
    group: 'environment',
    blurb: 'Connected machines, phones and smart-home inventory',
    data: 'demo',
  },
  {
    id: 'comms',
    label: 'Communications',
    icon: 'comms',
    group: 'personal',
    blurb: 'Messages, mail, notifications and call history',
    data: 'demo',
  },
  {
    id: 'memory',
    label: 'Memory & Knowledge',
    icon: 'memory',
    group: 'personal',
    blurb: 'What Sythion remembers, and your control over it',
    data: 'partial',
  },
  {
    id: 'automations',
    label: 'Automations',
    icon: 'automations',
    group: 'environment',
    blurb: 'Scheduled and event-triggered workflows',
    data: 'partial',
  },
  {
    id: 'security',
    label: 'Security & Control',
    icon: 'security',
    group: 'control',
    blurb: 'Autonomy mode, permissions, approvals and audit',
    data: 'live',
  },
  {
    id: 'system',
    label: 'System & Integrations',
    icon: 'system',
    group: 'control',
    blurb: 'Runtime, models, integrations and diagnostics',
    data: 'live',
  },
  {
    id: 'settings',
    label: 'Settings',
    icon: 'settings',
    group: 'control',
    blurb: 'Interface preferences and application settings',
    data: 'live',
  },
];

export const DEFAULT_ROUTE: RouteId = 'overview';

/**
 * Redirects for terminology changes and the development-only Companion preview.
 *
 * Keeping these here means a shared link or bookmark written before the rename
 * still lands on the right screen instead of silently falling back to the
 * default route.
 */
export const ROUTE_ALIASES: Record<string, RouteId> = {
  goals: 'missions',
  operations: 'overview',
  companion: 'companion-preview',
};

/**
 * The Companion preview is development-only. It is a route rather than a
 * feature flag so it can never be mistaken for a shipped overlay window: see
 * components/Companion.tsx for why a true transparent desktop overlay would
 * require Tauri window changes outside this task's scope.
 */
export const COMPANION_PREVIEW_ROUTE: RouteId = 'companion-preview';

/**
 * The preview's own RouteDef. Deliberately NOT in `ROUTES`, so it can never
 * appear in the sidebar navigation, but it exists so the top bar titles the
 * screen correctly instead of falling back to the first real route.
 */
const COMPANION_PREVIEW_DEF: RouteDef = {
  id: 'companion-preview',
  label: 'Companion',
  icon: 'assistant',
  group: 'operate',
  blurb: 'Development-only visual preview',
  data: 'demo',
};

export function routeById(id: RouteId): RouteDef {
  if (id === COMPANION_PREVIEW_ROUTE) return COMPANION_PREVIEW_DEF;
  return ROUTES.find((r) => r.id === id) ?? ROUTES[0];
}

export function isRouteId(value: string): value is RouteId {
  return ROUTES.some((r) => r.id === value) || value === COMPANION_PREVIEW_ROUTE;
}

/** Resolve a raw hash segment to a real route, following aliases. */
export function resolveRoute(raw: string): RouteId {
  if (isRouteId(raw)) return raw;
  return ROUTE_ALIASES[raw] ?? DEFAULT_ROUTE;
}