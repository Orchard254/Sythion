import { create } from 'zustand';
import { DEFAULT_ROUTE, resolveRoute, type RouteId } from '../lib/routes';

/* Lightweight hash routing — the project ships no router dependency, so this
 * keeps deep links working (`#/security`) without adding one.
 *
 * Legacy and alias segments resolve through `resolveRoute`, so `#/missions`,
 * `#/goals`, `#/overview` and `#/operations` all reach the same screen. */

function routeFromHash(): RouteId {
  if (typeof window === 'undefined') return DEFAULT_ROUTE;
  const raw = window.location.hash.replace(/^#\/?/, '').split('?')[0];
  return resolveRoute(raw);
}

interface NavState {
  route: RouteId;
  /** Transient context panel, e.g. the task detail drawer. */
  detailId: string | null;
  setRoute: (route: RouteId) => void;
  openDetail: (id: string | null) => void;
}

export const useNavStore = create<NavState>((set) => ({
  route: routeFromHash(),
  detailId: null,
  setRoute: (route) => {
    set({ route, detailId: null });
    if (typeof window !== 'undefined' && window.location.hash !== `#/${route}`) {
      window.location.hash = `#/${route}`;
    }
  },
  openDetail: (detailId) => set({ detailId }),
}));

if (typeof window !== 'undefined') {
  window.addEventListener('hashchange', () => {
    const next = routeFromHash();
    if (next !== useNavStore.getState().route) {
      useNavStore.setState({ route: next, detailId: null });
    }
  });
}