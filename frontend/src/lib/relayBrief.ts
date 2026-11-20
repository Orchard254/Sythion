import type { RouteId } from './routes';
import type { Briefing, MorningBrief } from '../types';

export interface BriefRow {
  key: string;
  label: string;
  detail: string;
}

export interface StructuredBrief {
  kind: 'briefing' | 'morning_brief';
  headline: string;
  rows: BriefRow[];
  note: string;
}

export interface BriefAction {
  label: string;
  route: RouteId;
}

/** Sections that need owner attention and where to handle them on the phone. */
const ACTIONS: Record<string, BriefAction> = {
  reconciliation: { label: 'Review', route: 'overview' },
  blockers: { label: 'Review', route: 'overview' },
  task_held: { label: 'Review', route: 'overview' },
  task_needs_input: { label: 'Review', route: 'overview' },
  goal_step_blocked: { label: 'Review', route: 'overview' },
  permission_awaiting: { label: 'Approvals', route: 'security' },
  devices: { label: 'Devices', route: 'system' },
  device_actions: { label: 'Device actions', route: 'overview' },
  goals: { label: 'Goals', route: 'missions' },
  tasks: { label: 'Goals', route: 'missions' },
};

const EMPTY_MARKERS = [
  'Nothing needs reconciliation', 'Nothing is blocked', 'None reported.',
  'No devices have registered', 'No device actions recorded',
];

/**
 * Derive distinct owner actions from a structured brief. A section marked with
 * an "empty" line is not actionable. This is read-only navigation metadata:
 * it never approves, grants, or clears anything.
 */
export function briefActions(brief: StructuredBrief): BriefAction[] {
  const out: BriefAction[] = [];
  const seen = new Set<string>();
  for (const row of brief.rows) {
    const action = ACTIONS[row.key];
    if (!action) continue;
    if (EMPTY_MARKERS.some((marker) => row.detail.includes(marker))) continue;
    const id = `${action.label}:${action.route}`;
    if (seen.has(id)) continue;
    seen.add(id);
    out.push(action);
  }
  return out;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/**
 * Parse a seeded relay message's JSON text into a structured brief.
 * Returns null when the text is not a recognizable brief, so the caller can
 * fall back to plain text. Never trusts the payload as authority — it only
 * extracts display fields from data the Core produced.
 */
export function parseRelayBrief(kind: string, text: string): StructuredBrief | null {
  if (kind !== 'briefing' && kind !== 'morning_brief') return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return null;
  }
  if (!isRecord(parsed)) return null;

  if (kind === 'morning_brief') {
    const brief = parsed as Partial<MorningBrief>;
    if (typeof brief.headline !== 'string' || !Array.isArray(brief.sections)) return null;
    const rows = brief.sections.filter(isRecord).map((section) => ({
      key: typeof section.key === 'string' ? section.key : '',
      label: typeof section.title === 'string' ? section.title : String(section.key ?? 'Section'),
      detail: Array.isArray(section.lines)
        ? section.lines.filter((line): line is string => typeof line === 'string').join(' · ')
        : '',
    }));
    return {
      kind, headline: brief.headline, rows,
      note: typeof brief.note === 'string' ? brief.note : '',
    };
  }

  const brief = parsed as Partial<Briefing>;
  if (typeof brief.headline !== 'string' || !Array.isArray(brief.items)) return null;
  const rows = brief.items.filter(isRecord).map((item) => ({
    key: typeof item.kind === 'string' ? item.kind : '',
    label: typeof item.title === 'string' ? item.title : String(item.kind ?? 'Item'),
    detail: typeof item.reason === 'string' ? item.reason : '',
  }));
  return {
    kind, headline: brief.headline, rows,
    note: typeof brief.note === 'string' ? brief.note : '',
  };
}
