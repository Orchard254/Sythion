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

export interface BriefStatusSummary {
  health: string | null;
  agentState: string | null;
  deviceTrust: string[];
  needsDecision: number;
}

/** Row kinds whose lines represent an owner decision still owed. */
const DECISION_KEYS = new Set(['reconciliation', 'blockers', 'permission_awaiting']);

/**
 * Extract a compact, read-only status summary from the brief's structured rows.
 * Values come only from data the Core assembled; this never infers or invents
 * state and never grants authority.
 */
export function briefStatusSummary(brief: StructuredBrief): BriefStatusSummary {
  let health: string | null = null;
  let agentState: string | null = null;
  let needsDecision = 0;
  const deviceTrust: string[] = [];

  for (const row of brief.rows) {
    if (row.key === 'system') {
      const match = row.detail.match(/Overall:\s*([A-Z_]+)/i);
      if (match) health = match[1].toUpperCase();
    } else if (row.key === 'agent') {
      const match = row.detail.match(/State:\s*([a-z_]+)/i);
      if (match) agentState = match[1].toLowerCase();
    } else if (row.key === 'devices') {
      // Each device line looks like "name [id]: trust=..., last_seen=..."
      for (const part of row.detail.split('·')) {
        const trust = part.match(/trust=([a-z_]+)/i);
        if (trust) deviceTrust.push(trust[1].toLowerCase());
      }
    }
    // Count decision rows, but only their real lines — "empty" marker lines
    // are not decisions and must not inflate the count.
    if (DECISION_KEYS.has(row.key)) {
      needsDecision += row.detail.split('·').filter((line) => {
        const trimmed = line.trim();
        return trimmed.length > 0 && !EMPTY_MARKERS.some((m) => trimmed.includes(m));
      }).length;
    }
  }
  return { health, agentState, deviceTrust, needsDecision };
}

/** Sections that need owner attention and where to handle them on the phone. */
const ACTIONS: Record<string, BriefAction> = {
  reconciliation: { label: 'Review', route: 'overview' },
  blockers: { label: 'Review', route: 'overview' },
  task_held: { label: 'Review', route: 'overview' },
  task_needs_input: { label: 'Review', route: 'overview' },
  goal_step_blocked: { label: 'Review', route: 'overview' },
  permission_awaiting: { label: 'Approvals', route: 'security' },
  // A heads-up "agent not active" item always needs the agent-status panel.
  agent_not_active: { label: 'Agent status', route: 'system' },
  devices: { label: 'Devices', route: 'system' },
  device_actions: { label: 'Device actions', route: 'overview' },
  goals: { label: 'Goals', route: 'missions' },
  tasks: { label: 'Goals', route: 'missions' },
};

const EMPTY_MARKERS = [
  'Nothing needs reconciliation', 'Nothing is blocked', 'None reported.',
  'No devices have registered', 'No device actions recorded',
];

/** Conditional actions: only offered when the section's detail needs attention. */
const CONDITIONAL: Record<string, { when: (detail: string) => boolean; action: BriefAction }> = {
  system: {
    when: (detail) => /DEGRADED|UNAVAILABLE|UNKNOWN|unavailable/i.test(detail),
    action: { label: 'Diagnostics', route: 'system' },
  },
  agent: {
    when: (detail) => /State:\s*(paused|degraded|shutdown|unknown)|not been initialized/i.test(detail),
    action: { label: 'Agent status', route: 'system' },
  },
};

/**
 * Derive distinct owner actions from a structured brief. A section marked with
 * an "empty" line is not actionable, and conditional sections (health, agent)
 * only produce an action when their detail actually needs attention. This is
 * read-only navigation metadata: it never approves, grants, or clears anything.
 */
export function briefActions(brief: StructuredBrief): BriefAction[] {
  const out: BriefAction[] = [];
  const seen = new Set<string>();
  const push = (action: BriefAction) => {
    const id = `${action.label}:${action.route}`;
    if (seen.has(id)) return;
    seen.add(id);
    out.push(action);
  };
  for (const row of brief.rows) {
    if (EMPTY_MARKERS.some((marker) => row.detail.includes(marker))) continue;
    const action = ACTIONS[row.key];
    if (action) {
      push(action);
      continue;
    }
    const conditional = CONDITIONAL[row.key];
    if (conditional && conditional.when(row.detail)) push(conditional.action);
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
