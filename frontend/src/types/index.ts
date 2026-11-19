/* Shared domain types.
 *
 * The `Task`, `TaskState`, `TaskEvent` and `HealthStatus` shapes mirror the
 * FastAPI schemas in `backend/api/schemas.py`. Everything added here mirrors
 * an existing read-only or mutating endpoint under `backend/api/routes.py`;
 * nothing is invented.
 */

export interface Task {
  waiting_kind?: 'clarification' | 'permission' | 'unknown';
  id: string;
  original_request: string;
  normalized_goal: string | null;
  state: TaskState;
  version: number;
  active_action_id: string | null;
  objective_id?: string | null;
  pending_question: string | null;
  final_result: string | null;
  created_at: string;
  updated_at: string;
  terminal_reason: string | null;
  interaction_type?: string;
  conversation_id?: string | null;
  response?: string | null;
}

export type TaskState =
  | 'created'
  | 'running'
  | 'waiting_for_user'
  | 'queued'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'interrupted'
  /**
   * Present in the backend constraint (models/database.py Task.__table_args__)
   * and a legitimate transition from `running`, but missing from this union
   * previously — which made every `paused` comparison a compile error and hid a
   * real state from the observation layer.
   */
  | 'paused';

/** Read-only GET /api/v1/tasks/{task_id}/resume-preflight. */
export interface RecoveryReviewPacket {
  status: 'review_required' | 'preflight_clear_not_authorized';
  blocker: string;
  severity: 'high' | 'medium' | 'none';
  observations: {
    task_state: string;
    owner_lease: string;
    unresolved_reasoning_calls: number | null;
  };
  evidence_source_classes: string[];
  verification_steps: string[];
  requires_authenticated_reconciliation: boolean;
  limited_to_current_snapshot: boolean;
  approved: boolean;
  authorization_issued: boolean;
  uncertainty_cleared: boolean;
  execution_started: boolean;
  automatic_replay_allowed: boolean;
  note: string;
}

/** Non-authorizing server-side change detector for the observed task evidence. */
export interface RecoveryReviewBinding {
  schema: string;
  revision: string;
  task_version: number;
  last_event_sequence: number;
  binding_status: 'read_only_observation';
  authenticated_human: false;
  authorization_issued: false;
  uncertainty_cleared: false;
  automatic_replay_allowed: false;
  note: string;
}

/** A snapshot, not authorization to resume or replay consequential work. */
export interface TaskResumePreflight {
  task_id: string;
  task_state: TaskState;
  owner_lease: string;
  unresolved_reasoning_calls: number;
  reason: string;
  preflight_clear: boolean;
  review_required: boolean;
  automatic_replay_allowed: boolean;
  review: RecoveryReviewPacket;
  /** A revision label only, not proof of external state or human consent. */
  review_binding?: RecoveryReviewBinding;
}

export interface TaskEvent {
  id: number;
  task_id: string;
  event_type: string;
  event_data: Record<string, unknown>;
  sequence_num: number;
  created_at: string;
}

/** GET /api/v1/health */
export interface HealthStatus {
  status: string;
  version: string;
  database_connected: boolean;
  cloud_configured: boolean;
  selected_model?: string;
  // Truthful startup aggregate reported by /health. Optional for older payloads.
  startup_sequence_status?: string;
  startup_status?: string;
  startup_overall_status?: string;
  startup_aggregate_status?: string;
  startup_stage_statuses?: Record<string, string>;
  startup_boot?: Record<string, unknown>;
}

/** GET /api/v1/version */
export interface VersionInfo {
  version: string;
  api_version: string;
  platform: string;
  runtime: string;
}

export interface VoiceAssets {
  stt_installed: boolean;
  stt_model: string;
  stt_version: string;
  stt_license: string;
  stt_sha256_verified: boolean;
  tts_provider: string;
  tts_voice: string;
  assets_dir: string;
  downloads_on_import: boolean;
  downloads_on_startup: boolean;
}

export interface ToolInfo {
  name: string;
  description: string;
}

/* --- Objectives / plans (GET /api/v1/objectives) ------------------------- */

export type ObjectiveStatus = 'active' | 'paused' | 'completed' | 'abandoned';

export type PlanStepStatus =
  | 'pending'
  | 'ready'
  | 'running'
  | 'in_progress'
  | 'blocked'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'skipped';

export interface PlanStep {
  id: number;
  objective_id: string;
  step_order: number;
  description: string;
  status: PlanStepStatus;
  linked_task_id: string | null;
  result_summary: string | null;
  blocker_reason: string | null;
  created_at: string;
  updated_at: string;
}

export interface Objective {
  id: string;
  title: string;
  purpose: string | null;
  status: ObjectiveStatus;
  priority: number;
  current_summary: string | null;
  last_task_id: string | null;
  completion_criteria: string | null;
  criteria_verified: boolean;
  parent_objective_id: string | null;
  created_at: string;
  updated_at: string;
  steps: PlanStep[];
  sub_objectives: Objective[];
}

/** Read-only next-step diagnostic. It never starts work or authorizes tools. */
export interface GoalWorkPreview {
  goal_id: string;
  plan_version: number | null;
  state: string;
  goal_state: string;
  explanation: string;
  runnable: boolean;
  structurally_ready: boolean;
  in_backoff: boolean;
  next_step: {
    id: number;
    key: string;
    description: string;
    priority: number;
    persisted_status: string;
    would_unlock: boolean;
  } | null;
  counts: {
    total: number;
    completed: number;
    running: number;
    failed: number;
    blocked: number;
    effectively_ready: number;
  };
  blocked_steps: Array<{ step_key: string; waiting_for: string[] }>;
  read_only: true;
  execution_started: false;
  authorization_issued: false;
}

/** GET /api/v1/goals */
export interface GoalSummary {
  id: string;
  title: string;
  status: string;
  priority: number;
  /**
   * Derived by the backend from persisted step state only
   * (services/goal_manager.py `progress`). It is an object, not a number —
   * `GET /goals` returns this structure. Treating it as a scalar produced a
   * `NaN%` progress bar in live mode while preview data (a plain fraction)
   * rendered correctly and hid the defect.
   */
  progress: GoalProgress;
}

/** Derived purely from persisted plan-step state. No model-generated percentages. */
export interface GoalProgress {
  completed_steps: number;
  total_required_steps: number;
  fraction: number;
  open_step_states: string[];
}

/** A step inside the active plan version (GET /goals/{id} -> active_plan.steps). */
export interface GoalPlanStep {
  id: number;
  step_key: string;
  step_order: number;
  description: string;
  status: PlanStepStatus;
  priority?: number;
  depends_on: string[];
  attempt_count?: number;
  linked_task_id: string | null;
  /** The task whose result is this step's evidence. */
  evidence_task_id: string | null;
  result_summary: string | null;
  failure_reason: string | null;
}

/** The active, versioned plan for a goal. */
export interface GoalActivePlan {
  id: string;
  version: number;
  status: string;
  source: string | null;
  reason: string | null;
  steps: GoalPlanStep[];
}

/** GET /api/v1/goals/{goal_id} */
export interface GoalDetail {
  goal: {
    id: string;
    title: string;
    purpose: string | null;
    status: string;
    priority: number;
    current_summary: string | null;
    completion_criteria: string | null;
    criteria_verified: boolean;
  };
  progress: GoalProgress;
  /** null when the goal has no plan yet. Not an error. */
  active_plan: GoalActivePlan | null;
}

/** One entry of the plan version history (GET /goals/{id}/plans). */
export interface GoalPlanVersion {
  id: string;
  version: number;
  status: string;
  source: string | null;
  reason: string | null;
  created_at: string;
}

/* --- Agent runtime ------------------------------------------------------- */

/**
 * Execution-policy settings returned inside GET /api/v1/agent.
 * `autonomy_level` is the real authorization gate: "guarded" (default) or
 * "autonomous". See backend/core/config.py and core/importance_classifier.py.
 */
export interface AgentPolicies {
  background_orchestration_enabled: boolean;
  autonomy_level: 'guarded' | 'autonomous' | string;
  step_max_attempts: number;
  max_steps_per_background_cycle: number;
}

export interface AgentIdentity {
  agent_id: string;
  name: string;
  state: string;
  last_wake_at: string | null;
  last_session_outcome: string | null;
  last_wake_reason?: string | null;
  model_selected?: string | null;
  tools_installed?: number;
  goals_active?: number;
  policies?: AgentPolicies;
  [key: string]: unknown;
}

export type HealthLevel = 'HEALTHY' | 'DEGRADED' | 'UNAVAILABLE' | 'UNKNOWN';

export interface HealthCheck {
  name: string;
  status: HealthLevel;
  evidence: Record<string, unknown>;
}

/** GET /api/v1/agent/health */
export interface AgentHealthReport {
  overall: HealthLevel;
  checked_at: string;
  checks: HealthCheck[];
}

export interface AgentWake {
  id: number;
  wake_key: string;
  trigger_type: string;
  trigger_reference: string | null;
  started_at: string | null;
  finished_at: string | null;
  outcome: string | null;
  stop_reason: string | null;
  steps_attempted: number;
  steps_completed: number;
}

export interface AgentActivityEvent {
  event_type: string;
  event_data: Record<string, unknown>;
  sequence_num: number;
  created_at: string;
}

/** GET /api/v1/agent/activity */
export interface AgentActivity {
  wakes: AgentWake[];
  events: AgentActivityEvent[];
}

/** GET|PUT /api/v1/agent/background */
export interface BackgroundPolicy {
  enabled: boolean;
  default: boolean;
}

/* --- Capabilities (GET /api/v1/capabilities) ----------------------------- */

export type CapabilityStatus =
  | 'AVAILABLE'
  | 'UNAVAILABLE'
  | 'DISABLED'
  | 'DEGRADED'
  | 'REQUIRES_PERMISSION'
  | 'REQUIRES_CONFIGURATION';

export interface Capability {
  id: string;
  name: string;
  category: string;
  status: CapabilityStatus;
  risk_class: 'LOW' | 'MEDIUM' | 'HIGH' | string;
  requires_permission: boolean;
  requires_external_service: boolean;
  description?: string;
  environments?: string[];
  deterministic?: boolean;
  stateful?: boolean;
  tools_registered?: string[];
  tools_missing?: string[];
}

/* --- Agent registry (GET /api/v1/agents) --------------------------------- */

export interface SpecialistDefinition {
  id: string;
  role: string;
  name: string;
  enabled: boolean;
  capabilities: string[];
  model_requirements: Record<string, unknown>;
}

export interface AgentRecord {
  agent_id: string;
  role?: string;
  state?: string;
  [key: string]: unknown;
}

export interface AgentRegistry {
  main: AgentRecord;
  specialists: AgentRecord[];
  definitions: SpecialistDefinition[];
}

/** GET /api/v1/agents/delegations */
export interface Delegation {
  id: string;
  status: string;
  role?: string | null;
  objective?: string | null;
  attempt?: number | null;
  parent_agent_id?: string | null;
  specialist_agent_id?: string | null;
  [key: string]: unknown;
}

/* --- Skills / proposals (review queues) --------------------------------- */

export interface SkillSummary {
  id?: string;
  name?: string;
  status?: string;
  enabled?: boolean;
  version?: number | string;
  [key: string]: unknown;
}

export interface SkillProposal {
  id: number | string;
  name?: string;
  definition?: { name?: string; [key: string]: unknown } | null;
  status?: string;
  evidence_count?: number;
  created_at?: string | null;
  [key: string]: unknown;
}

/** GET /api/v1/tools/proposals */
export interface ToolProposal {
  id: number;
  name: string;
  description: string;
  status: string;
  enabled_mode: string | null;
  successful_uses: number;
  validation_errors: string[];
  sandbox_result: unknown;
  created_at: string | null;
  decided_at: string | null;
  enabled_at: string | null;
}

/* --- Memory -------------------------------------------------------------- */

export interface MemoryItem {
  id: number;
  kind: string;
  scope: string;
  key: string;
  value: string;
  confidence: number | null;
  verification_status: string;
  source_type: string;
  observed_at: string | null;
  updated_at: string | null;
  score: number;
}

export interface MemoryItemsPage {
  items: MemoryItem[];
  has_more: boolean;
}

export interface MemoryEdge {
  id: number;
  source_key: string;
  relation: string;
  target_key: string;
  weight: number;
  status: string;
  created_at: string | null;
  updated_at: string | null;
}

export interface MemoryReport {
  [key: string]: unknown;
}

/* --- Settings / secrets -------------------------------------------------- */

export interface AppSettings {
  host: string;
  port: number;
  ollama_cloud_base_url: string;
  selected_model: string;
  file_roots: string[];
  persisted_file_roots?: string[];
  environment_file_roots?: string[];
  effective_file_roots?: string[];
  fallback_file_roots?: string[];
  max_actions_per_task: number;
  max_cloud_requests_per_task: number;
  voice_enabled: boolean;
  voice_recording_enabled: boolean;
  voice_tts_voice: string;
  retention_days: number;
}

/** GET /api/v1/secrets — names of configured secrets only, never values. */
export interface SecretsList {
  configured: string[];
}

/** PUT /api/v1/secrets/{name} — write-only acknowledgement, never a value. */
export interface SecretStatusResponse {
  configured: boolean;
  name: string;
}

export interface ModelRegistryEntry {
  id?: string;
  role?: string;
  provider?: string;
  [key: string]: unknown;
}

/** GET /api/v1/retention/receipts */
export interface PurgeReceipt {
  task_id: string;
  reason: string;
  purged_at: string;
  event_count: number;
}

/* --- Presentation-only types -------------------------------------------- */

/** Where the data on screen came from. Surfaced in the UI, never hidden. */
export type DataOrigin = 'live' | 'demo';

export type Tone = 'ok' | 'warn' | 'danger' | 'info' | 'idle' | 'accent' | 'plain';

/* --- Assistant conversation turns (POST /assistant/turns) ---------------- */
export interface BriefingItem {
  kind: string;
  ref: string;
  title: string;
  reason: string;
  suggested_action: string;
  authority: 'none';
  approved: false;
}

export interface Briefing {
  headline: string;
  items: BriefingItem[];
  has_blockers: boolean;
  authorized: false;
  approval_issued: false;
  execution_started: false;
  note: string;
}

export interface AssistantTurn {
  exchange_id: string;
  user_text: string;
  assistant_text: string;
  status: string;
  background_task_ids: string[];
  background_task_scheduling_status?: string | null;
  scheduling_status?: string | null;
  task_scheduling_status?: string | null;
  dispatch_status?: string | null;
  scheduling_established?: boolean | null;
  briefing?: Briefing | null;
}
