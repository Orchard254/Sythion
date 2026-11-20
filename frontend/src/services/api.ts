/**
 * api.ts — API client + service facade.
 *
 * Section map (top to bottom; comment-only, no behavior/order change):
 * - imports (axios + ../types), API_BASE_URL, ConversationExchange
 * - conversationService (list) / assistantService (turn) — early services that
 *   reference apiClient at call time (first use in text above its creation)
 * - Bridge config + apiClient creation (configureBridge, getControlToken,
 *   axios.create interceptors) — apiClient intentionally stays where it is
 *   (bridge token/axios setup); services only call it after module init
 * - Interceptors: request (control-token header), response (backend
 *   error-message normalization via detailToText/errorReason)
 * - healthService, taskService
 * - Objectives/goals: objectiveService, goalService
 * - Agent runtime: agentService, agentRegistryService, capabilityService
 * - Skills/tools review queues: skillService, toolProposalService
 * - Memory: memoryService
 * - First-run setup: setupService
 * - Voice: Voice* interfaces, voiceService
 * - Settings/secrets/models/retention/tools: settingsService, secretService,
 *   modelService, retentionService, toolService
 * - Helpers (arrayBufferToBase64), SetupReadiness, default export (apiClient)
 */
import axios from 'axios';
import { mayOfferExplicitResume } from '../lib/recoveryPresentation';
import type {
  AgentActivity,
  AssistantTurn,
  AgentHealthReport,
  AgentIdentity,
  AgentRegistry,
  AppSettings,
  BackgroundPolicy,
  Capability,
  Delegation,
  GoalSummary,
  HealthStatus,
  MemoryEdge,
  MemoryItem,
  MemoryItemsPage,
  MemoryReport,
  Objective,
  PurgeReceipt,
  SecretStatusResponse,
  SecretsList,
  SkillProposal,
  SkillSummary,
  Task,
  TaskEvent,
  TaskResumePreflight,
  ToolInfo,
  ToolProposal,
  VersionInfo,
  VoiceAssets,
  GoalDetail,
  GoalPlanVersion,
  GoalWorkPreview,
} from '../types';

const API_BASE_URL = '/api/v1';

export interface ConversationExchange {
  id: string;
  session_id: string;
  user_text: string;
  assistant_text: string;
  created_at: string;
  background_task_ids?: string[];
}

export const conversationService = {
  async list(cursor?: string): Promise<{ conversations: ConversationExchange[]; next_cursor: string | null }> {
    const response = await apiClient.get<{ conversations: ConversationExchange[]; next_cursor: string | null }>(
      '/conversations', { params: cursor ? { cursor } : undefined }
    );
    return response.data;
  },
};

export const assistantService = {
  async turn(text: string, clientRequestId?: string): Promise<AssistantTurn> {
    const body: { text: string; client_request_id?: string } = { text };
    if (clientRequestId) {
      body.client_request_id = clientRequestId;
    }
    try {
      const response = await apiClient.post('/assistant/turns', body);
      return response.data;
    } catch (error) {
      // Only a rejection with no HTTP response is an unknown transport outcome:
      // there is no server verdict to surface, and no safe way to claim the work
      // stopped or was recorded. Known HTTP rejections (503, 401, ...) keep
      // their server-provided detail via the global interceptor.
      const hasResponse =
        typeof error === 'object' &&
        error !== null &&
        'response' in error &&
        (error as { response?: unknown }).response != null;
      if (!hasResponse) {
        throw new Error(
          'Connection lost. The request outcome is unknown; it was not automatically retried. Your draft is still here.'
        );
      }
      throw error;
    }
  },
};

// Runtime bridge configuration, set by the Tauri shell (or the dev launcher)
// before the first API call. baseURL defaults to the Vite-proxied path so the
// browser dev path works until the bridge config is resolved.
let bridgeBaseUrl: string | null = null;
let bridgeControlToken: string | null = null;

export function configureBridge(cfg: { baseUrl: string; controlToken: string }) {
  bridgeBaseUrl = cfg.baseUrl;
  bridgeControlToken = cfg.controlToken || null;
  apiClient.defaults.baseURL = bridgeBaseUrl;
}

// Get control token: bridge config first, then sessionStorage (Tauri preload),
// then the dev environment.
const getControlToken = () => {
  if (bridgeControlToken) {
    return bridgeControlToken;
  }

  const desktopToken = sessionStorage.getItem('sythion_control_token');
  if (desktopToken) {
    return desktopToken;
  }

  if (import.meta.env.DEV) {
    return import.meta.env.VITE_SYTHION_CONTROL_TOKEN || '';
  }

  return '';
};

// Create axios instance with authentication
const apiClient = axios.create({
  baseURL: bridgeBaseUrl || API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Add control token to all requests
apiClient.interceptors.request.use((config) => {
  const token = getControlToken();
  if (token) {
    config.headers['X-Control-Token'] = token;
  }
  return config;
});

// The parts of an axios rejection this module reads. The interceptor
// replaces the message on the SAME error object, so the status code, the
// response and the config stay reachable for any caller that wants them.
interface ApiRejection {
  message?: string;
  response?: { data?: unknown };
}

// The `msg` of one FastAPI validation entry, or '' when there is none.
const messageOf = (entry: unknown): string => {
  if (entry && typeof entry === 'object' && 'msg' in entry) {
    const msg = (entry as { msg?: unknown }).msg;
    return typeof msg === 'string' ? msg.trim() : '';
  }
  return '';
};

// A human-readable reason out of a structured error body. Only JSON fields
// are used: a raw body (an HTML error page, a Python traceback) is never
// surfaced, so the operator never sees a stack trace in the UI.
const detailToText = (detail: unknown): string => {
  if (typeof detail === 'string') {
    return detail.trim();
  }
  if (Array.isArray(detail)) {
    return detail
      .map((entry) => messageOf(entry))
      .filter((text) => text.length > 0)
      .join('; ');
  }
  return messageOf(detail);
};

// The reason to show for a failed request: the backend's `detail` first,
// then its `error` slug. An empty result means "say nothing", and the
// caller's own message (axios's "Request failed with status code NNN" or
// "Network Error") is kept.
const errorReason = (body: unknown): string => {
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    return '';
  }
  const record = body as Record<string, unknown>;
  const detail = detailToText(record.detail);
  if (detail) {
    return detail;
  }
  const slug = record.error;
  return typeof slug === 'string' ? slug.trim() : '';
};

// Surface the backend's own reason instead of axios's generic message. The
// error is never swallowed: the same error object is re-rejected with only
// its message replaced, and a successful response is passed through
// unchanged.
apiClient.interceptors.response.use(
  (response) => response,
  (error: ApiRejection) => {
    const reason = errorReason(error?.response?.data);
    if (reason) {
      error.message = reason;
    }
    return Promise.reject(error);
  }
);

export const healthService = {
  check: async (): Promise<HealthStatus> => {
    const response = await apiClient.get('/health');
    return response.data;
  },

  version: async (): Promise<VersionInfo> => {
    const response = await apiClient.get('/version');
    return response.data;
  },
};

export const taskService = {
  list: async (): Promise<Task[]> => {
    const response = await apiClient.get('/tasks');
    return response.data.tasks || [];
  },

  get: async (taskId: string): Promise<Task> => {
    const response = await apiClient.get(`/tasks/${taskId}`);
    return response.data;
  },

  acknowledgedFailures: async (): Promise<string[]> => {
    const response = await apiClient.get('/tasks/acknowledged-failures');
    const ids: unknown = response.data?.task_ids;
    if (!Array.isArray(ids) || ids.some((id) => typeof id !== 'string')) {
      throw new Error('Invalid task acknowledgement response');
    }
    return ids as string[];
  },
  acknowledgeFailure: async (taskId: string): Promise<void> => {
    const response = await apiClient.post(`/tasks/${encodeURIComponent(taskId)}/acknowledge-failure`);
    if (response.data?.task_id !== taskId ||
        response.data?.acknowledged !== true ||
        response.data?.audit_preserved !== true) {
      throw new Error('Task acknowledgement was not confirmed');
    }
  },

  interpret: async (originalRequest: string, ignorePending = false): Promise<{kind: 'conversation' | 'task' | 'reply' | 'ambiguous'; task_id: string | null}> => {
    const response = await apiClient.post('/assistant/interpret', { original_request: originalRequest, ignore_pending: ignorePending });
    return response.data;
  },
  create: async (
    originalRequest: string,
    interactionHint?: 'conversation' | 'task',
    idempotencyKey?: string
  ): Promise<Task> => {
    const response = await apiClient.post('/tasks', {
      original_request: originalRequest,
      interaction_hint: interactionHint,
      idempotency_key: idempotencyKey,
    });
    return response.data;
  },

  cancel: async (taskId: string): Promise<void> => {
    await apiClient.post(`/tasks/${taskId}/cancel`);
  },

  resumePreflight: async (taskId: string): Promise<TaskResumePreflight> => {
    const response = await apiClient.get<TaskResumePreflight>(`/tasks/${taskId}/resume-preflight`);
    return response.data;
  },

  resume: async (taskId: string): Promise<Task> => {
    // Every UI resume path uses a FRESH read-only preflight. A cached widget
    // can never authorize effects, and unknown/contradictory packets fail
    // closed. The backend repeats admission under its writer lock.
    const latest = await taskService.resumePreflight(taskId);
    if (!mayOfferExplicitResume(latest)) {
      throw new Error('Recovery review is required before this task can resume. Open task details to inspect the blocker.');
    }
    const response = await apiClient.post(`/tasks/${taskId}/resume`);
    return response.data;
  },

  reply: async (taskId: string, answer: string, inferred = false, expectedVersion?: number): Promise<Task> => {
    const response = await apiClient.post(`/tasks/${taskId}/reply`, { answer, inferred, expected_version: expectedVersion });
    return response.data;
  },

  events: async (taskId: string, afterSequence = 0): Promise<TaskEvent[]> => {
    const response = await apiClient.get(`/tasks/${taskId}/events`, {
      params: { after_sequence: afterSequence },
    });
    return response.data.events || [];
  },
};

/* --- Objectives and plans ------------------------------------------------ */

export const objectiveService = {
  list: async (): Promise<Objective[]> => {
    const response = await apiClient.get('/objectives');
    return response.data.objectives || [];
  },

  get: async (objectiveId: string): Promise<Objective> => {
    const response = await apiClient.get(`/objectives/${objectiveId}`);
    return response.data;
  },

  addStep: async (objectiveId: string, description: string): Promise<Objective> => {
    const response = await apiClient.post(`/objectives/${objectiveId}/steps`, { description });
    return response.data;
  },

  updateStep: async (
    stepId: number,
    updates: { status?: string; result_summary?: string; blocker_reason?: string }
  ) => {
    const response = await apiClient.patch(`/objectives/steps/${stepId}`, updates);
    return response.data;
  },

  verifyCriteria: async (objectiveId: string): Promise<Objective> => {
    const response = await apiClient.post(`/objectives/${objectiveId}/verify-criteria`);
    return response.data;
  },
};

export const goalService = {
  list: async (): Promise<GoalSummary[]> => {
    const response = await apiClient.get('/goals');
    return response.data.goals || [];
  },

  /** Goal, deterministic progress and the active plan version with its steps. */
  detail: async (goalId: string): Promise<GoalDetail> => {
    const response = await apiClient.get(`/goals/${goalId}`);
    return response.data;
  },

  /** Side-effect-free next-step preview, including dependency and backoff blockers. */
  nextWork: async (goalId: string): Promise<GoalWorkPreview> => {
    const response = await apiClient.get<GoalWorkPreview>(`/goals/${goalId}/next-work`);
    return response.data;
  },

  /** Plan version history. Replanning is audited, so earlier versions persist. */
  plans: async (goalId: string): Promise<GoalPlanVersion[]> => {
    const response = await apiClient.get(`/goals/${goalId}/plans`);
    return response.data.plans || [];
  },

  /**
   * Lifecycle action. The backend accepts exactly pause | resume | abandon and
   * rejects anything else with 404, so the union is closed on purpose.
   */
  lifecycle: async (goalId: string, action: 'pause' | 'resume' | 'abandon'): Promise<GoalDetail> => {
    const response = await apiClient.post(`/goals/${goalId}/${action}`);
    return response.data;
  },
};

/* --- Agent runtime ------------------------------------------------------- */

export const agentService = {
  identity: async (): Promise<AgentIdentity> => {
    const response = await apiClient.get('/agent');
    return response.data;
  },

  health: async (includeProvider = false): Promise<AgentHealthReport> => {
    const response = await apiClient.get('/agent/health', {
      params: { include_provider: includeProvider },
    });
    return response.data;
  },

  activity: async (limit = 20): Promise<AgentActivity> => {
    const response = await apiClient.get('/agent/activity', { params: { limit } });
    return { wakes: response.data.wakes || [], events: response.data.events || [] };
  },

  backgroundPolicy: async (): Promise<BackgroundPolicy> => {
    const response = await apiClient.get('/agent/background');
    return response.data;
  },

  setBackgroundPolicy: async (enabled: boolean): Promise<BackgroundPolicy> => {
    const response = await apiClient.put('/agent/background', { enabled });
    return response.data;
  },

  pause: async (): Promise<AgentIdentity> => {
    const response = await apiClient.post('/agent/pause');
    return response.data;
  },

  resume: async (): Promise<AgentIdentity> => {
    const response = await apiClient.post('/agent/resume');
    return response.data;
  },

  wake: async (): Promise<unknown> => {
    const response = await apiClient.post('/agent/wake');
    return response.data;
  },

  devices: async (): Promise<import('../types').DeviceList> => {
    const response = await apiClient.get('/devices');
    return response.data;
  },

  setDeviceTrust: async (
    deviceId: string,
    trustLevel: 'observed' | 'trusted' | 'privileged',
  ): Promise<import('../types').RegisteredDevice> => {
    const response = await apiClient.put(`/devices/${deviceId}/trust`, {
      trust_level: trustLevel,
    });
    return response.data;
  },
};

/** Owner device access management: credentials and capability grants.
 *  These are explicit owner actions; a device can never widen its own access. */
export const deviceAccessService = {
  issueCredential: async (deviceId: string): Promise<{ device_id: string; token: string }> => {
    const response = await apiClient.post(`/devices/${deviceId}/credential`);
    return response.data;
  },

  revokeCredential: async (deviceId: string): Promise<{ device_id: string; revoked: boolean }> => {
    const response = await apiClient.delete(`/devices/${deviceId}/credential`);
    return response.data;
  },

  addGrant: async (deviceId: string, capability: string) => {
    const response = await apiClient.put(`/devices/${deviceId}/grants/${capability}`);
    return response.data;
  },

  removeGrant: async (deviceId: string, capability: string) => {
    const response = await apiClient.delete(`/devices/${deviceId}/grants/${capability}`);
    return response.data;
  },
};

/** Per-device capability discovery (read-only, never grants). */
export const deviceCapabilityService = {
  get: async (deviceId: string): Promise<import('../types').DeviceCapabilities> => {
    const response = await apiClient.get(`/devices/${deviceId}/capabilities`);
    return response.data;
  },
};

/** Owner reconciliation of uncertain remote actions (never auto-replays). */
export const reconciliationService = {
  reconcile: async (
    actionId: string,
    decisionId: string,
    outcome: 'effect_confirmed' | 'effect_not_performed' | 'abandoned',
    evidenceRef?: string,
    newIdempotencyKey?: string,
  ) => {
    const response = await apiClient.post(
      `/remote-actions/${actionId}/reconcile`,
      {
        decision_id: decisionId,
        outcome,
        evidence_ref: evidenceRef,
        new_idempotency_key: newIdempotencyKey,
      },
    );
    return response.data;
  },

  list: async (actionId: string) => {
    const response = await apiClient.get(`/remote-actions/${actionId}/reconciliations`);
    return response.data;
  },
};

/** Trusted owner approvals. Decision ids are generated by the UI, never the model. */
export const approvalService = {
  pending: async (): Promise<{ proposals: import('../types').PendingApproval[] }> => {
    const response = await apiClient.get('/approvals/pending');
    return response.data;
  },

  decide: async (
    proposalId: string,
    decisionId: string,
    decision: 'approve' | 'deny',
  ): Promise<import('../types').ApprovalDecision> => {
    const response = await apiClient.post('/approvals/decide', {
      proposal_id: proposalId,
      decision_id: decisionId,
      decision,
    });
    return response.data;
  },
};

/** Owner-visible remote-action lifecycle (read-only; never approves). */
export const remoteActionService = {
  list: async (limit = 20): Promise<{ actions: import('../types').RemoteAction[] }> => {
    const response = await apiClient.get('/remote-actions', { params: { limit } });
    return response.data;
  },

  uncertain: async (): Promise<{ actions: import('../types').RemoteAction[] }> => {
    const response = await apiClient.get('/remote-actions/uncertain');
    return response.data;
  },
};

/** First-party relay: a device talks to the Core through a bound session.
 * No third party is involved; the device credential is passed per request. */
export const relayService = {
  open: async (deviceId: string, token: string) => {
    const response = await apiClient.post('/remote/sessions', null, {
      headers: {
        'X-Sythion-Device-Id': deviceId,
        'X-Sythion-Device-Token': token,
      },
    });
    return response.data as import('../types').RelaySession;
  },

  poll: async (sessionId: string, deviceId: string, token: string, afterId = 0) => {
    const response = await apiClient.get(
      `/remote/sessions/${sessionId}/messages`,
      {
        params: { after_id: afterId },
        headers: {
          'X-Sythion-Device-Id': deviceId,
          'X-Sythion-Device-Token': token,
        },
      },
    );
    return response.data.messages as import('../types').RelayMessage[];
  },

  send: async (
    sessionId: string, deviceId: string, token: string, text: string,
    idempotencyKey?: string,
  ) => {
    const response = await apiClient.post(
      `/remote/sessions/${sessionId}/messages`,
      { text, ...(idempotencyKey ? { idempotency_key: idempotencyKey } : {}) },
      {
        headers: {
          'X-Sythion-Device-Id': deviceId,
          'X-Sythion-Device-Token': token,
        },
      },
    );
    return response.data;
  },

  close: async (sessionId: string, deviceId: string, token: string) => {
    const response = await apiClient.delete(`/remote/sessions/${sessionId}`, {
      headers: {
        'X-Sythion-Device-Id': deviceId,
        'X-Sythion-Device-Token': token,
      },
    });
    return response.data;
  },
};

export const agentRegistryService = {
  list: async (): Promise<AgentRegistry> => {
    const response = await apiClient.get('/agents');
    return response.data;
  },

  delegations: async (limit = 50): Promise<Delegation[]> => {
    const response = await apiClient.get('/agents/delegations', { params: { limit } });
    return response.data.delegations || [];
  },
};

export const capabilityService = {
  list: async (): Promise<{ capabilities: Capability[]; consistency_issues: string[] }> => {
    const response = await apiClient.get('/capabilities', {
      params: { include_details: true },
    });
    return {
      capabilities: response.data.capabilities || [],
      consistency_issues: response.data.consistency_issues || [],
    };
  },
};

/* --- Skills / tools review queues ---------------------------------------- */

export const skillService = {
  list: async (): Promise<SkillSummary[]> => {
    const response = await apiClient.get('/skills');
    return Array.isArray(response.data) ? response.data : [];
  },

  proposals: async (): Promise<SkillProposal[]> => {
    const response = await apiClient.get('/skills/proposals');
    return Array.isArray(response.data) ? response.data : [];
  },

  approveProposal: async (proposalId: string | number): Promise<unknown> => {
    const response = await apiClient.post(`/skills/proposals/${proposalId}/approve`);
    return response.data;
  },

  rejectProposal: async (proposalId: string | number): Promise<unknown> => {
    const response = await apiClient.post(`/skills/proposals/${proposalId}/reject`);
    return response.data;
  },
};

export const toolProposalService = {
  list: async (): Promise<ToolProposal[]> => {
    const response = await apiClient.get('/tools/proposals');
    return Array.isArray(response.data) ? response.data : [];
  },

  approve: async (proposalId: number): Promise<unknown> => {
    const response = await apiClient.post(`/tools/proposals/${proposalId}/approve`);
    return response.data;
  },

  reject: async (proposalId: number): Promise<unknown> => {
    const response = await apiClient.post(`/tools/proposals/${proposalId}/reject`);
    return response.data;
  },
};

/* --- Memory -------------------------------------------------------------- */

export const memoryService = {
  report: async (): Promise<MemoryReport> => {
    const response = await apiClient.get('/memory/report');
    return response.data;
  },

  items: async (params?: {
    query?: string;
    kind?: string;
    kinds?: string[];
    limit?: number;
    offset?: number;
  }): Promise<MemoryItemsPage> => {
    const response = await apiClient.get('/memory/items', { params });
    const data = (response.data ?? {}) as { items?: unknown; has_more?: unknown };
    return {
      items: Array.isArray(data.items) ? (data.items as MemoryItem[]) : [],
      has_more: data.has_more === true,
    };
  },

  edges: async (): Promise<MemoryEdge[]> => {
    const response = await apiClient.get('/memory/edges');
    return Array.isArray(response.data) ? response.data : [];
  },

  reinforceEdge: async (edgeId: number): Promise<MemoryEdge> => {
    const response = await apiClient.post(`/memory/edges/${edgeId}/reinforce`);
    return response.data;
  },

  removeEdge: async (edgeId: number): Promise<MemoryEdge> => {
    const response = await apiClient.post(`/memory/edges/${edgeId}/remove`);
    return response.data;
  },
};

/* --- First-run setup ----------------------------------------------------- */

export const setupService = {
  readiness: async (): Promise<SetupReadiness> => {
    const response = await apiClient.get('/setup/readiness');
    return response.data;
  },

  testCredentials: async (value: string): Promise<{ valid: boolean; model_count: number; models: string[] }> => {
    const response = await apiClient.post('/onboarding/credentials/test', { value });
    return response.data;
  },

  setCredential: async (value: string): Promise<unknown> => {
    const response = await apiClient.put('/secrets/ollama_api_key', { value });
    return response.data;
  },

  inference: async (modelId?: string): Promise<{ success: boolean; model: string; response_preview: string }> => {
    const response = await apiClient.post('/onboarding/inference', modelId ? { model_id: modelId } : {});
    return response.data;
  },

  selectModel: async (modelId: string): Promise<{ selected_model: string; persisted: boolean }> => {
    const response = await apiClient.post('/onboarding/models', { model_id: modelId });
    return response.data;
  },

  applySafeSettings: async (): Promise<{ ok: boolean }> => {
    const response = await apiClient.post('/setup/safe-settings');
    return response.data;
  },
};

/* --- Inert, provider-aware onboarding preview (NOT active inference) ---- */

export type CloudPreviewProviderId = 'openrouter' | 'nvidia' | 'groq' | 'ollama-cloud';

/** Fixed order used by provider pickers; Ollama Cloud is no longer special. */
export const CLOUD_PREVIEW_PROVIDERS: readonly CloudPreviewProviderId[] = [
  'ollama-cloud', 'openrouter', 'nvidia', 'groq',
];

export const CLOUD_PROVIDER_LABELS: Record<CloudPreviewProviderId, string> = {
  'ollama-cloud': 'Ollama Cloud',
  openrouter: 'OpenRouter',
  nvidia: 'NVIDIA NIM',
  groq: 'Groq',
};

export function isCloudPreviewProviderId(value: unknown): value is CloudPreviewProviderId {
  return typeof value === 'string' &&
    CLOUD_PREVIEW_PROVIDERS.some((id) => id === value);
}

const CLOUD_SECRET_NAMES: Record<CloudPreviewProviderId, string> = {
  openrouter: 'openrouter_api_key',
  nvidia: 'nvidia_api_key',
  groq: 'groq_api_key',
  'ollama-cloud': 'ollama_api_key',
};

export interface CloudPreviewSelection {
  provider_id: CloudPreviewProviderId;
  model_id: string;
}

export interface CloudPreviewStatus {
  active_provider: string;
  staged: Partial<CloudPreviewSelection>;
  providers: Array<{ provider_id: CloudPreviewProviderId; credential_configured: boolean }>;
  tool_probe_verified: boolean;
  text_probe_verified: boolean;
  ready_for_execution: false;
}

export interface CloudCatalogModel {
  id: string;
  free_variant: boolean;
  tools_listed: boolean;
}
export interface CloudModelCatalog {
  provider_id: CloudPreviewProviderId;
  models: CloudCatalogModel[];
  truncated: boolean;
  ready_for_execution: false;
}

export interface CloudKeyCheckResult {
  provider_id: 'openrouter';
  credential_verified: true;
  active: false;
  ready_for_execution: false;
}

export interface CloudPreviewProbeResult extends CloudPreviewSelection {
  tool_probe_verified: boolean;
  active: false;
  ready_for_execution: false;
  evidence_persisted: true;
}

export interface CloudPreviewChatResult extends CloudPreviewSelection {
  assistant_text: string;
  active: false;
  ready_for_execution: false;
  persisted: false;
  task_executed: false;
}

export interface CloudConversationHistoryTurn {
  role: 'user' | 'assistant';
  content: string;
}

export interface CloudConversationOnlyResult extends CloudPreviewChatResult {
  conversation_only: true;
}

/** Actual Sythion reasoning processor; unlike inert preview staging. */
export const activeInferenceService = {
  activateOllama: async (modelId: string): Promise<{
    provider_id: 'ollama-cloud'; model_id: string;
    verified: true; permissions_unchanged: true;
  }> => {
    const response = await apiClient.post('/inference/ollama/activate', { model_id: modelId }, {
      timeout: 85000,
    });
    return response.data;
  },
  status: async (): Promise<{ provider_id: string; model_id: string; permissions_unchanged: true }> => {
    const response = await apiClient.get('/inference/active');
    return response.data;
  },
  activate: async (selection: CloudPreviewSelection): Promise<{ provider_id: string; model_id: string; permissions_unchanged: true }> => {
    // No provider HTTP request here: backend requires exact verified text and
    // synthetic tool evidence, and disallows changes while tasks are unfinished.
    const response = await apiClient.post('/inference/active', selection);
    return response.data;
  },
  restoreOllama: async (): Promise<{ provider_id: string; model_id: string; permissions_unchanged: true }> => {
    const response = await apiClient.delete('/inference/active');
    return response.data;
  },
};

export const cloudPreviewService = {
  status: async (): Promise<CloudPreviewStatus> => {
    const response = await apiClient.get('/inference-preview/providers');
    return response.data;
  },

  verifyStoredKey: async (): Promise<CloudKeyCheckResult> => {
    // Explicit POST; only the desktop's current vault key is used server-side.
    // Never receive or transmit the credential through the UI.
    const response = await apiClient.post('/inference-preview/verify-stored-key', {
      provider_id: 'openrouter',
      confirm_network_request: true,
    }, { timeout: 20000 });
    return response.data;
  },

  modelCatalog: async (providerId: CloudPreviewProviderId): Promise<CloudModelCatalog> => {
    const response = await apiClient.get('/inference-preview/model-catalog', {
      params: { provider_id: providerId }, timeout: 20000,
    });
    return response.data;
  },

  saveCredential: async (providerId: CloudPreviewProviderId, value: string): Promise<void> => {
    const secretName = CLOUD_SECRET_NAMES[providerId];
    if (!secretName || !value.trim()) throw new Error('Choose a provider and enter a key.');
    await apiClient.put('/secrets/' + secretName, { value: value.trim() });
    // A vault write is not validation of provider credentials.
  },

  stage: async (selection: CloudPreviewSelection): Promise<void> => {
    await apiClient.post('/inference-preview/stage', selection);
  },

  assistantContract: async (selection: CloudPreviewSelection): Promise<CloudPreviewSelection & {
    assistant_contract_verified: true; evidence_persisted: true;
    active: false; task_executed: false;
  }> => {
    const response = await apiClient.post('/inference-preview/assistant-contract', {
      ...selection, confirm_network_request: true,
    }, { timeout: 75000 });
    return response.data;
  },

  probe: async (selection: CloudPreviewSelection): Promise<CloudPreviewProbeResult> => {
    // Called only from the explicit, confirmed UI action. Never auto-probe.
    const response = await apiClient.post('/inference-preview/probe', {
      ...selection,
      confirm_network_request: true,
    });
    return response.data;
  },

  chat: async (
    selection: CloudPreviewSelection, text: string
  ): Promise<CloudPreviewChatResult> => {
    if (!text.trim() || text.trim().length > 1200) {
      throw new Error('Choose a short, nonempty test message.');
    }
    // One stateless, explicitly user-authorized model request, no tools.
    const response = await apiClient.post('/inference-preview/chat', {
      ...selection,
      text: text.trim(),
      confirm_network_request: true,
    });
    return response.data;
  },

  conversationTurn: async (
    selection: CloudPreviewSelection,
    text: string,
    history: CloudConversationHistoryTurn[],
  ): Promise<CloudConversationOnlyResult> => {
    if (!text.trim() || text.trim().length > 1200 ||
        !Array.isArray(history) || history.length > 6 ||
        history.some((turn, i) => !turn || !turn.content.trim() ||
          turn.content.length > 1200 || turn.role !== (i % 2 === 0 ? 'user' : 'assistant')) ||
        history.length % 2 !== 0) {
      throw new Error('Invalid bounded cloud conversation request.');
    }
    const response = await apiClient.post('/cloud-conversation/turn', {
      ...selection,
      text: text.trim(),
      history,
      confirm_network_request: true,
    });
    return response.data;
  },
};

/* --- Voice --------------------------------------------------------------- */

export interface VoiceSegment {
  segment_id: string;
  text: string;
  stop_detected: boolean;
  audio_deleted: boolean;
  stt_available: boolean;
}

export interface VoiceStartResult {
  session_id: string;
  status: string;
  stt_available: boolean;
  recording_enabled: boolean;
}

export interface VoiceAudioResult {
  session_id: string;
  segments: VoiceSegment[];
  is_speaking: boolean;
  stt_available: boolean;
}

export const voiceService = {
  startSession: async (): Promise<VoiceStartResult> => {
    const response = await apiClient.post('/voice/start');
    return response.data;
  },

  sendAudioFrame: async (sessionId: string, pcm16Mono: Uint8Array): Promise<VoiceAudioResult> => {
    const audioBase64 = arrayBufferToBase64(pcm16Mono);
    const response = await apiClient.post(`/voice/${sessionId}/audio`, {
      session_id: sessionId,
      audio_data: audioBase64,
      sample_rate: 16000,
      channels: 1,
    });
    return response.data;
  },

  endSession: async (sessionId: string): Promise<{ segments: VoiceSegment[] }> => {
    const response = await apiClient.post(`/voice/${sessionId}/end`);
    return response.data;
  },

  speak: async (text: string): Promise<{ success: boolean }> => {
    const response = await apiClient.post('/voice/speak', { text });
    return response.data;
  },

  assets: async (): Promise<VoiceAssets> => {
    const response = await apiClient.get('/voice/assets');
    return response.data;
  },

  installAssets: async (): Promise<VoiceAssets> => {
    const response = await apiClient.post('/voice/assets/install');
    return response.data;
  },
};

/* --- Settings, secrets, models, retention -------------------------------- */

export const settingsService = {
  get: async (): Promise<AppSettings> => {
    const response = await apiClient.get('/settings');
    return response.data;
  },

  update: async (updates: Record<string, unknown>): Promise<AppSettings> => {
    const response = await apiClient.put('/settings', updates);
    return response.data.settings || response.data;
  },
};

export const secretService = {
  status: async (): Promise<SecretsList> => {
    const response = await apiClient.get('/secrets');
    return { configured: response.data.configured || [] };
  },

  set: async (name: string, value: string): Promise<SecretStatusResponse> => {
    const response = await apiClient.put(`/secrets/${name}`, { value });
    return response.data;
  },
};

export const modelService = {
  available: async (): Promise<string[]> => {
    const response = await apiClient.get('/models');
    const models: unknown = response.data?.models;
    if (!Array.isArray(models)) return [];
    const isId = (value: unknown): value is string => typeof value === 'string' && value.trim().length > 0;
    return models.flatMap((entry: unknown) => {
      if (isId(entry)) return [entry];
      if (!entry || typeof entry !== 'object' || Array.isArray(entry)) return [];
      const { name, model } = entry as { name?: unknown; model?: unknown };
      if (isId(name)) return [name];
      return isId(model) ? [model] : [];
    });
  },

  registry: async (): Promise<Record<string, unknown>[]> => {
    const response = await apiClient.get('/models/registry');
    const payload = response.data;
    if (Array.isArray(payload)) return payload;
    if (payload && Array.isArray(payload.models)) return payload.models;
    if (payload && Array.isArray(payload.registry)) return payload.registry;
    return [];
  },
};

export const retentionService = {
  receipts: async (): Promise<PurgeReceipt[]> => {
    const response = await apiClient.get('/retention/receipts');
    return Array.isArray(response.data) ? response.data : [];
  },
};

export const toolService = {
  list: async (): Promise<ToolInfo[]> => {
    const response = await apiClient.get('/tools');
    return response.data.tools || [];
  },
};

function arrayBufferToBase64(buffer: Uint8Array): string {
  let binary = '';
  const chunkSize = 0x8000;
  for (let i = 0; i < buffer.length; i += chunkSize) {
    binary += String.fromCharCode(...buffer.subarray(i, i + chunkSize));
  }
  return btoa(binary);
}

export interface SetupReadiness {
  full_ready: boolean;
  text_first_available: boolean;
  active_provider_id?: string;
  active_provider_ready_ok?: boolean;
  checks: Record<string, boolean>;
  selected_model: string;
  tts_voice: string;
}

export default apiClient;
