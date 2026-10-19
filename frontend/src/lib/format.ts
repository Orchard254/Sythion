import type { Tone } from '../types';

/* Presentation-only helpers: formatting and status derivation.
 * No data fetching, no backend coupling. */

/**
 * Parse a backend timestamp to epoch milliseconds.
 *
 * The backend serialises timestamps as naive UTC — `2026-10-18T08:51:39`,
 * with no `Z` and no `±HH:MM` offset. `new Date()` on such a string treats it
 * as LOCAL time, so on a UTC+3 machine a task created three minutes ago reads
 * as three hours old. Every relative time, clock and duration in the app was
 * wrong by exactly the UTC offset.
 *
 * A string with an explicit zone is already unambiguous and is passed through
 * untouched; only a zone-less string gets `Z` appended. Fixing this here
 * rather than in the backend keeps the API contract unchanged.
 */
export function parseTimestamp(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(iso.trim());
  const ts = new Date(hasZone ? iso.trim() : `${iso.trim()}Z`).getTime();
  return Number.isNaN(ts) ? null : ts;
}

export function relTime(iso: string | null | undefined): string {
  const ts = parseTimestamp(iso);
  if (ts === null) return '—';
  const diff = Date.now() - ts;
  const future = diff < 0;
  const s = Math.abs(diff) / 1000;
  let out: string;
  if (s < 45) out = future ? 'in a moment' : 'just now';
  else if (s < 3600) out = `${Math.round(s / 60)}m`;
  else if (s < 86400) out = `${Math.round(s / 3600)}h`;
  else if (s < 604800) out = `${Math.round(s / 86400)}d`;
  else out = new Date(ts).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  return future ? `${out} from now` : `${out} ago`;
}

export function clockTime(iso: string | null | undefined): string {
  const ts = parseTimestamp(iso);
  if (ts === null) return '—';
  return new Date(ts).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
}

export function dateTime(iso: string | null | undefined): string {
  const ts = parseTimestamp(iso);
  if (ts === null) return '—';
  return new Date(ts).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function duration(fromIso: string | null, toIso: string | null): string {
  const start = parseTimestamp(fromIso);
  if (start === null) return '—';
  const end = toIso ? parseTimestamp(toIso) : Date.now();
  if (end === null) return '—';
  const s = Math.max(0, Math.floor((end - start) / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

export function shortId(id: string, keep = 6): string {
  return id.length <= keep + 2 ? id : `${id.slice(0, keep)}…`;
}

/* --- Tone derivation ----------------------------------------------------- */

const TASK_TONE: Record<string, Tone> = {
  created: 'idle',
  running: 'info',
  waiting_for_user: 'warn',
  completed: 'ok',
  failed: 'danger',
  cancelled: 'idle',
  interrupted: 'warn',
};

const TASK_LABEL: Record<string, string> = {
  created: 'queued',
  running: 'running',
  waiting_for_user: 'needs input',
  completed: 'completed',
  failed: 'failed',
  cancelled: 'cancelled',
  interrupted: 'interrupted',
};

export function taskTone(state: string): Tone {
  return TASK_TONE[state] ?? 'idle';
}

export function taskLabel(state: string): string {
  return TASK_LABEL[state] ?? state.replace(/_/g, ' ');
}

export const HEALTH_TONE: Record<string, Tone> = {
  HEALTHY: 'ok',
  DEGRADED: 'warn',
  UNAVAILABLE: 'danger',
  UNKNOWN: 'idle',
};

export const CAPABILITY_TONE: Record<string, Tone> = {
  AVAILABLE: 'ok',
  DEGRADED: 'warn',
  UNAVAILABLE: 'danger',
  DISABLED: 'idle',
  REQUIRES_PERMISSION: 'warn',
  REQUIRES_CONFIGURATION: 'info',
};

export const RISK_TONE: Record<string, Tone> = {
  LOW: 'ok',
  MEDIUM: 'warn',
  HIGH: 'danger',
};

export const AGENT_STATE_TONE: Record<string, Tone> = {
  active: 'ok',
  paused: 'warn',
  degraded: 'danger',
  shutdown: 'idle',
};

export const PLAN_STEP_TONE: Record<string, Tone> = {
  completed: 'ok',
  running: 'info',
  in_progress: 'info',
  ready: 'accent',
  pending: 'idle',
  blocked: 'danger',
  failed: 'danger',
  cancelled: 'idle',
  skipped: 'idle',
};

export const OBJECTIVE_TONE: Record<string, Tone> = {
  active: 'accent',
  paused: 'warn',
  completed: 'ok',
  abandoned: 'idle',
};

export function humanize(value: string): string {
  return value
    .replace(/[_.]+/g, ' ')
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/^./, (c) => c.toUpperCase());
}

export function pct(value: number | null | undefined): number {
  if (typeof value !== 'number' || Number.isNaN(value)) return 0;
  // Backend goal progress is a 0..1 ratio; objectives use step counts.
  return Math.round(Math.max(0, Math.min(1, value)) * 100);
}

export function planProgress(steps: Array<{ status: string }>): number {
  if (steps.length === 0) return 0;
  const done = steps.filter((s) => s.status === 'completed' || s.status === 'skipped').length;
  return Math.round((done / steps.length) * 100);
}

export function initials(label: string): string {
  const parts = label.replace(/[^a-zA-Z0-9 ]/g, ' ').split(/\s+/).filter(Boolean);
  if (parts.length === 0) return '?';
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[1][0]).toUpperCase();
}