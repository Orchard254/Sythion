import type { AgentIdentity, BackgroundPolicy, Capability } from '../types';

/* Autonomy model.
 *
 * IMPORTANT TRUTHFULNESS CONTRACT
 * -------------------------------
 * Sythion does not define a single "autonomy level" setting. It exposes two
 * real, independently-persisted runtime switches:
 *
 *   - Main Agent state  (active | paused | degraded | shutdown)
 *     POST /api/v1/agent/pause , POST /api/v1/agent/resume
 *   - Background orchestration policy (boolean)
 *     GET|PUT /api/v1/agent/background
 *
 * The four mode labels below are UI vocabulary projected onto that real state.
 * `enforced` records whether a backend control actually backs the label; a
 * false value means the label is aspirational and the UI must say so.
 * Nothing in the UI may imply that a toggle alone makes an action safe —
 * authorization is enforced by the executor's policy layer, not by this app.
 */

export type AutonomyModeId = 'advisory' | 'assisted' | 'bounded' | 'proactive';

export interface AutonomyMode {
  id: AutonomyModeId;
  label: string;
  tagline: string;
  /** What Sythion may do without asking. */
  can: string[];
  /** What still requires explicit confirmation. */
  mustAsk: string[];
  /** Never performed in this mode. */
  cannot: string[];
  /** True when a backend switch (not just a UI label) backs this mode. */
  enforced: boolean;
  /** Shown verbatim in the UI so the label is never overstated. */
  enforcementNote: string;
}

export const AUTONOMY_MODES: AutonomyMode[] = [
  {
    id: 'advisory',
    label: 'Advisory',
    tagline: 'Answer and plan only',
    can: [
      'Answer questions and reason over stored memory',
      'Draft plans, options and recommendations',
      'Read health and status information',
    ],
    mustAsk: [
      'Every state change on this machine',
      'Every outbound message or call',
    ],
    cannot: ['Running tools', 'Starting background work'],
    enforced: true,
    enforcementNote:
      'Enforced by the backend: Main Agent is paused and background orchestration is disabled.',
  },
  {
    id: 'assisted',
    label: 'Assisted',
    tagline: 'Acts on what you ask for, in this session',
    can: [
      'Execute tools inside a task you explicitly start',
      'Use capabilities the executor policy allows',
      'Cancel, resume and report on its own work',
    ],
    mustAsk: [
      'High-risk capabilities are gated by capability policy',
      'Anything beyond the approved file roots',
    ],
    cannot: ['Starting work on its own initiative'],
    enforced: true,
    enforcementNote:
      'Enforced by the backend: Main Agent is active with background orchestration disabled.',
  },
  {
    id: 'bounded',
    label: 'Bounded autonomy',
    tagline: 'PROPOSED — no backend enforcement today',
    can: ['Everything in Assisted'],
    mustAsk: ['Anything exceeding a declared budget'],
    cannot: ['Unbounded recurring work'],
    enforced: false,
    enforcementNote:
      'Not implemented as a mode. The backend does expose an execution gate — autonomy_level, guarded or autonomous — but that is a separate axis, not a budget-bounded mode. The only real limits today are the per-task budgets in Settings (max actions per task, max cloud requests per task), which bound a single task and not the system.',
  },
  {
    id: 'proactive',
    label: 'Proactive',
    tagline: 'Runs approved background workflows',
    can: [
      'Wake on registered triggers and advance active objectives',
      'Delegate to specialist agents within budgets',
      'Report outcomes without prompting',
    ],
    mustAsk: ['Capabilities flagged requires_permission', 'External services not configured'],
    cannot: ['Bypassing capability-level authorization'],
    enforced: true,
    enforcementNote:
      'Enforced by the backend: Main Agent is active with background orchestration enabled. Individual capability permissions still apply.',
  },
];

export interface DerivedAutonomy {
  mode: AutonomyModeId;
  agentState: string;
  backgroundEnabled: boolean;
  /** True when the combination is one the backend can actually represent. */
  recognised: boolean;
  description: string;
}

/* ---------------------------------------------------------------------------
 * Authorization level — a SEPARATE, real axis.
 *
 * `backend/core/config.py` defines `autonomy_level` with exactly two values:
 *   "guarded"    (default / contract) — consequential actions need approval
 *   "autonomous" (accessibility / unattended)
 *
 * It is read by the importance classifier (an action is auto-approved when
 * `autonomy_level == "autonomous"`), by the model engine (tool visibility) and
 * by the task execution loop. It is NOT exposed as a REST setting, so this
 * interface reports it read-only and never offers a toggle for it.
 * ------------------------------------------------------------------------- */

export type AuthorizationLevel = 'guarded' | 'autonomous';

export interface AuthorizationAxis {
  level: string;
  known: boolean;
  label: string;
  summary: string;
  tone: 'ok' | 'warn';
  /** Why the UI cannot change it. */
  readOnlyReason: string;
}

export function deriveAuthorization(agent: AgentIdentity | null | undefined): AuthorizationAxis {
  const raw = agent?.policies?.autonomy_level;
  const known = raw === 'guarded' || raw === 'autonomous';

  if (raw === 'autonomous') {
    return {
      level: raw,
      known,
      label: 'Autonomous',
      summary:
        'Consequential actions can be auto-approved by the importance classifier without asking you first.',
      tone: 'warn',
      readOnlyReason:
        'Set by the backend configuration (autonomy_level / SYTHION_AUTONOMY_LEVEL). No REST endpoint changes it, so this interface does not offer a control.',
    };
  }

  return {
    level: raw ?? 'unknown',
    known,
    label: 'Guarded',
    summary:
      known
        ? 'Consequential actions require your approval. Sythion proposes, you authorize.'
        : 'The backend did not report an authorization level. Treat every consequential action as guarded.',
    tone: 'ok',
    readOnlyReason:
      'Set by the backend configuration (autonomy_level / SYTHION_AUTONOMY_LEVEL). No REST endpoint changes it, so this interface does not offer a control.',
  };
}

export function deriveAutonomy(
  agent: AgentIdentity | null | undefined,
  background: BackgroundPolicy | null | undefined
): DerivedAutonomy {
  const agentState = agent?.state ?? 'unknown';
  const backgroundEnabled = Boolean(background?.enabled);

  let mode: AutonomyModeId;
  let recognised = true;

  if (agentState === 'paused') {
    mode = 'advisory';
  } else if (agentState === 'shutdown') {
    mode = 'advisory';
  } else if (backgroundEnabled) {
    mode = 'proactive';
  } else {
    mode = 'assisted';
  }

  if (agentState === 'degraded') {
    // A degraded agent is not a distinct mode; report it without inventing one.
    recognised = true;
  }

  const meta = AUTONOMY_MODES.find((m) => m.id === mode)!;
  return {
    mode,
    agentState,
    backgroundEnabled,
    recognised,
    description: meta.tagline,
  };
}

/** Capabilities that would need explicit owner authorization today. */
export function gatedCapabilities(capabilities: Capability[]): Capability[] {
  return capabilities.filter((c) => c.requires_permission || c.risk_class === 'HIGH');
}

/** Capabilities that cannot currently be exercised at all. */
export function unavailableCapabilities(capabilities: Capability[]): Capability[] {
  return capabilities.filter((c) => c.status === 'UNAVAILABLE' || c.status === 'DISABLED');
}

export function capabilityCounts(capabilities: Capability[]) {
  return {
    total: capabilities.length,
    available: capabilities.filter((c) => c.status === 'AVAILABLE').length,
    gated: capabilities.filter((c) => c.status === 'REQUIRES_PERMISSION').length,
    unavailable: capabilities.filter((c) => c.status === 'UNAVAILABLE' || c.status === 'DISABLED').length,
    degraded: capabilities.filter((c) => c.status === 'DEGRADED').length,
    misconfigured: capabilities.filter((c) => c.status === 'REQUIRES_CONFIGURATION').length,
  };
}