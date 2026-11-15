import type { RouteId } from './routes';

export type ProviderLane = 'ollama' | 'cloud-answer-only';
const STORAGE_KEY = 'sythion.aiProviderLane';

/**
 * This is UI preference only. No local storage value ever authorizes
 * inference, tool execution, task creation, file access or memory writes.
 */
export function readProviderLane(): ProviderLane {
  try {
    return localStorage.getItem(STORAGE_KEY) === 'cloud-answer-only'
      ? 'cloud-answer-only' : 'ollama';
  } catch {
    return 'ollama';
  }
}

export function saveProviderLane(lane: ProviderLane): void {
  try {
    localStorage.setItem(STORAGE_KEY, lane);
  } catch {
    // Failure to store a preference must not weaken server authorization.
  }
}

/** Deep links to executable screens are never served while in cloud lane. */
export function cloudShellRoute(route: RouteId): RouteId {
  return route === 'settings' ? 'settings' : 'assistant';
}
