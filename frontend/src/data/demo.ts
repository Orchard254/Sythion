/* Demonstration data.
 * ===========================================================================
 * NOTHING IN THIS FILE IS READ BY THE BACKEND. It exists so the interface can
 * be inspected and navigated when no Sythion backend is reachable, and so
 * screens without a backend endpoint yet (devices, calendar, messaging) show a
 * realistic shape instead of an empty box.
 *
 * Every panel fed from here is tagged `demo` in the UI via <DataBadge /> and the
 * application shows a persistent preview-mode banner. Values are invented and
 * must never be presented as observed system state.
 */

import type {
  AgentActivity,
  AgentHealthReport,
  AgentIdentity,
  AgentRegistry,
  BackgroundPolicy,
  Capability,
  Delegation,
  GoalDetail,
  GoalPlanVersion,
  GoalSummary,
  MemoryEdge,
  MemoryReport,
  Objective,
  PurgeReceipt,
  SkillProposal,
  SkillSummary,
  Task,
  TaskEvent,
  ToolInfo,
  ToolProposal,
} from '../types';

const now = Date.now();
const minsAgo = (m: number) => new Date(now - m * 60_000).toISOString();
const hoursAgo = (h: number) => new Date(now - h * 3_600_000).toISOString();
const daysAgo = (d: number) => new Date(now - d * 86_400_000).toISOString();
const inMins = (m: number) => new Date(now + m * 60_000).toISOString();

export const DEMO_BANNED = 'Demonstration data — not read from the Sythion backend.';

/* --- Tasks --------------------------------------------------------------- */

export const demoTasks: Task[] = [
  {
    id: 'demo-9f3a2c',
    original_request: 'Draft the Q4 infrastructure cost review and flag anything over budget',
    normalized_goal: 'Review Q4 infrastructure spend and flag budget overruns',
    state: 'running',
    version: 7,
    active_action_id: 'act-2f11',
    objective_id: 'obj-infra',
    pending_question: null,
    final_result: null,
    created_at: minsAgo(4),
    updated_at: minsAgo(1),
    terminal_reason: null,
  },
  {
    id: 'demo-41bd77',
    original_request: 'Which bills are due before Friday?',
    normalized_goal: 'Summarise upcoming bills due before Friday',
    state: 'waiting_for_user',
    version: 3,
    active_action_id: null,
    objective_id: null,
    pending_question:
      'Two billing sources matched "utilities". Should I include the mobile plan or keep it separate?',
    final_result: null,
    created_at: minsAgo(19),
    updated_at: minsAgo(6),
    terminal_reason: null,
  },
  {
    id: 'demo-7c02ae',
    original_request: 'Summarise the meeting notes from yesterday into action items',
    normalized_goal: 'Extract action items from yesterday’s meeting notes',
    state: 'completed',
    version: 12,
    active_action_id: null,
    objective_id: 'obj-notes',
    pending_question: null,
    final_result:
      'Three action items extracted. Two assigned to you (send the revised spec to Priya; confirm the Thursday demo slot). One unassigned: decide on the analytics vendor.',
    created_at: hoursAgo(5),
    updated_at: hoursAgo(4.6),
    terminal_reason: null,
  },
  {
    id: 'demo-1e55f0',
    original_request: 'Back up the project folder to the external drive',
    normalized_goal: 'Copy the project folder to the external backup drive',
    state: 'failed',
    version: 5,
    active_action_id: 'act-90cd',
    objective_id: null,
    pending_question: null,
    final_result: null,
    created_at: hoursAgo(9),
    updated_at: hoursAgo(8.7),
    terminal_reason:
      'Destination drive not found — volume E: is not mounted. Reconnect the drive and retry.',
  },
  {
    id: 'demo-58a9d0',
    original_request: 'Check which smart-home devices are offline',
    normalized_goal: 'Report offline smart-home devices',
    state: 'completed',
    version: 6,
    active_action_id: null,
    objective_id: null,
    pending_question: null,
    final_result: 'One device offline: Living-room thermostat (last seen 3h ago). Everything else responding.',
    created_at: hoursAgo(22),
    updated_at: hoursAgo(21.8),
    terminal_reason: null,
  },
  {
    id: 'demo-2b6f83',
    original_request: 'Cancel the duplicate dentist appointment',
    normalized_goal: 'Cancel the duplicate dentist appointment',
    state: 'interrupted',
    version: 4,
    active_action_id: null,
    objective_id: null,
    pending_question: null,
    final_result: null,
    created_at: daysAgo(1),
    updated_at: daysAgo(1),
    terminal_reason: 'Cancelling an appointment is an external communication — awaiting owner approval.',
  },
];

export const demoTaskEvents: Record<string, TaskEvent[]> = {
  'demo-9f3a2c': [
    {
      id: 1,
      task_id: 'demo-9f3a2c',
      event_type: 'task.created',
      event_data: { interaction_type: 'TASK' },
      sequence_num: 1,
      created_at: minsAgo(4),
    },
    {
      id: 2,
      task_id: 'demo-9f3a2c',
      event_type: 'plan.created',
      event_data: { steps: 3 },
      sequence_num: 2,
      created_at: minsAgo(4),
    },
    {
      id: 3,
      task_id: 'demo-9f3a2c',
      event_type: 'action.started',
      event_data: { action_id: 'act-2f10', tool: 'file_search', target: 'invoices/2024-q4' },
      sequence_num: 3,
      created_at: minsAgo(3.5),
    },
    {
      id: 4,
      task_id: 'demo-9f3a2c',
      event_type: 'action.completed',
      event_data: { action_id: 'act-2f10', duration_ms: 2410, files_found: 38 },
      sequence_num: 4,
      created_at: minsAgo(3.4),
    },
    {
      id: 5,
      task_id: 'demo-9f3a2c',
      event_type: 'action.started',
      event_data: { action_id: 'act-2f11', tool: 'file_read', target: 'invoices/2024-q4/cloud-summary.csv' },
      sequence_num: 5,
      created_at: minsAgo(1),
    },
  ],
  'demo-7c02ae': [
    {
      id: 1,
      task_id: 'demo-7c02ae',
      event_type: 'task.created',
      event_data: { interaction_type: 'TASK' },
      sequence_num: 1,
      created_at: hoursAgo(5),
    },
    {
      id: 2,
      task_id: 'demo-7c02ae',
      event_type: 'plan.created',
      event_data: { steps: 2 },
      sequence_num: 2,
      created_at: hoursAgo(5),
    },
    {
      id: 3,
      task_id: 'demo-7c02ae',
      event_type: 'action.started',
      event_data: { action_id: 'act-7a10', tool: 'file_search', target: 'notes/2026-10-17' },
      sequence_num: 3,
      created_at: hoursAgo(4.9),
    },
    {
      id: 4,
      task_id: 'demo-7c02ae',
      event_type: 'action.completed',
      event_data: { action_id: 'act-7a10', duration_ms: 1180, files_found: 4 },
      sequence_num: 4,
      created_at: hoursAgo(4.9),
    },
    {
      id: 5,
      task_id: 'demo-7c02ae',
      event_type: 'action.started',
      event_data: { action_id: 'act-7a11', tool: 'file_read', target: 'notes/2026-10-17/standup.md' },
      sequence_num: 5,
      created_at: hoursAgo(4.8),
    },
    {
      id: 6,
      task_id: 'demo-7c02ae',
      event_type: 'action.completed',
      event_data: { action_id: 'act-7a11', duration_ms: 402, bytes: 6120 },
      sequence_num: 6,
      created_at: hoursAgo(4.8),
    },
    {
      id: 7,
      task_id: 'demo-7c02ae',
      event_type: 'action.started',
      event_data: { action_id: 'act-7a12', tool: 'note_action_extract', target: '4 notes' },
      sequence_num: 7,
      created_at: hoursAgo(4.7),
    },
    {
      id: 8,
      task_id: 'demo-7c02ae',
      event_type: 'task.completed',
      event_data: { actions: 3, duration_ms: 184300 },
      sequence_num: 8,
      created_at: hoursAgo(4.6),
    },
  ],
  'demo-58a9d0': [
    {
      id: 1,
      task_id: 'demo-58a9d0',
      event_type: 'task.created',
      event_data: {},
      sequence_num: 1,
      created_at: hoursAgo(22),
    },
    {
      id: 2,
      task_id: 'demo-58a9d0',
      event_type: 'action.started',
      event_data: { action_id: 'act-5c22', tool: 'system_device_sweep', target: 'registered nodes' },
      sequence_num: 2,
      created_at: hoursAgo(22),
    },
    {
      id: 3,
      task_id: 'demo-58a9d0',
      event_type: 'action.failed',
      event_data: { action_id: 'act-5c23', tool: 'home_device_list', reason: 'no_fabric_integration' },
      sequence_num: 3,
      created_at: hoursAgo(21.9),
    },
    {
      id: 4,
      task_id: 'demo-58a9d0',
      event_type: 'task.completed',
      event_data: { actions: 2, degraded: true },
      sequence_num: 4,
      created_at: hoursAgo(21.8),
    },
  ],
};

/* --- Objectives and goals ------------------------------------------------ */

export const demoObjectives: Objective[] = [
  {
    id: 'obj-infra',
    title: 'Reduce Q4 infrastructure spend by 15%',
    purpose:
      'Identify and act on the largest recurring infrastructure cost drivers before the fiscal year closes.',
    status: 'active',
    priority: 1,
    current_summary:
      'Cost review under way. Two providers account for 61% of spend; both are candidates for renegotiation.',
    last_task_id: 'demo-9f3a2c',
    completion_criteria: 'Confirmed 15% reduction against the approved Q4 baseline.',
    criteria_verified: false,
    parent_objective_id: null,
    created_at: daysAgo(12),
    updated_at: minsAgo(4),
    steps: [
      {
        id: 901,
        objective_id: 'obj-infra',
        step_order: 1,
        description: 'Collect billing exports for all providers',
        status: 'completed',
        linked_task_id: 'demo-9f3a2c',
        result_summary: '38 invoice files collected across 5 providers.',
        blocker_reason: null,
        created_at: daysAgo(11),
        updated_at: hoursAgo(20),
      },
      {
        id: 902,
        objective_id: 'obj-infra',
        step_order: 2,
        description: 'Normalise and compare spend per service',
        status: 'running',
        linked_task_id: 'demo-9f3a2c',
        result_summary: null,
        blocker_reason: null,
        created_at: daysAgo(11),
        updated_at: minsAgo(1),
      },
      {
        id: 903,
        objective_id: 'obj-infra',
        step_order: 3,
        description: 'Draft renegotiation notes for the two largest providers',
        status: 'pending',
        linked_task_id: null,
        result_summary: null,
        blocker_reason: null,
        created_at: daysAgo(11),
        updated_at: daysAgo(11),
      },
      {
        id: 904,
        objective_id: 'obj-infra',
        step_order: 4,
        description: 'Verify reduction against baseline and close objective',
        status: 'blocked',
        linked_task_id: null,
        result_summary: null,
        blocker_reason: 'Depends on step 3 — Sythion cannot send supplier correspondence without approval.',
        created_at: daysAgo(11),
        updated_at: hoursAgo(20),
      },
    ],
    sub_objectives: [],
  },
  {
    id: 'obj-health',
    title: 'Rebuild a sustainable training routine',
    purpose: 'Three sessions a week, without losing the streak built in Q3.',
    status: 'active',
    priority: 3,
    current_summary: 'Two of four sessions complete this week. Evening slot still fragile.',
    last_task_id: null,
    completion_criteria: 'Twelve consecutive weeks at three sessions per week.',
    criteria_verified: false,
    parent_objective_id: null,
    created_at: daysAgo(31),
    updated_at: hoursAgo(30),
    steps: [
      {
        id: 911,
        objective_id: 'obj-health',
        step_order: 1,
        description: 'Establish baseline week',
        status: 'completed',
        linked_task_id: null,
        result_summary: 'Baseline recorded: 3 sessions/week, 45 min average.',
        blocker_reason: null,
        created_at: daysAgo(31),
        updated_at: daysAgo(24),
      },
      {
        id: 912,
        objective_id: 'obj-health',
        step_order: 2,
        description: 'Protect the Tuesday morning slot',
        status: 'in_progress',
        linked_task_id: null,
        result_summary: null,
        blocker_reason: null,
        created_at: daysAgo(24),
        updated_at: hoursAgo(30),
      },
      {
        id: 913,
        objective_id: 'obj-health',
        step_order: 3,
        description: 'Hold twelve consecutive weeks',
        status: 'pending',
        linked_task_id: null,
        result_summary: null,
        blocker_reason: null,
        created_at: daysAgo(24),
        updated_at: daysAgo(24),
      },
    ],
    sub_objectives: [],
  },
  {
    id: 'obj-notes',
    title: 'Migrate meeting notes to a searchable archive',
    purpose: 'Everything from the last two quarters, indexed and linked to projects.',
    status: 'completed',
    priority: 2,
    current_summary: 'Completed. 214 notes indexed, 38 linked to active projects.',
    last_task_id: 'demo-7c02ae',
    completion_criteria: 'All notes from the period indexed and deduplicated.',
    criteria_verified: true,
    parent_objective_id: null,
    created_at: daysAgo(45),
    updated_at: hoursAgo(5),
    steps: [
      {
        id: 921,
        objective_id: 'obj-notes',
        step_order: 1,
        description: 'Extract and normalise all notes',
        status: 'completed',
        linked_task_id: 'demo-7c02ae',
        result_summary: '214 notes extracted, 11 duplicates merged.',
        blocker_reason: null,
        created_at: daysAgo(45),
        updated_at: daysAgo(40),
      },
      {
        id: 922,
        objective_id: 'obj-notes',
        step_order: 2,
        description: 'Link notes to active projects',
        status: 'completed',
        linked_task_id: null,
        result_summary: '38 notes linked.',
        blocker_reason: null,
        created_at: daysAgo(40),
        updated_at: daysAgo(33),
      },
    ],
    sub_objectives: [],
  },
  {
    id: 'obj-garden',
    title: 'Finish the kitchen refit',
    purpose: null,
    status: 'paused',
    priority: 4,
    current_summary: 'Paused pending the contractor quote. No action for ~3 weeks.',
    last_task_id: null,
    completion_criteria: null,
    criteria_verified: false,
    parent_objective_id: null,
    created_at: daysAgo(96),
    updated_at: daysAgo(21),
    steps: [
      {
        id: 931,
        objective_id: 'obj-garden',
        step_order: 1,
        description: 'Get three quotes',
        status: 'blocked',
        linked_task_id: null,
        result_summary: null,
        blocker_reason: 'Awaiting contractor availability — outside Sythion’s reach.',
        created_at: daysAgo(96),
        updated_at: daysAgo(21),
      },
    ],
    sub_objectives: [],
  },
];

/* Mirrors backend GoalManager.progress(): derived only from persisted step
 * state, and shaped like the live response rather than a bare fraction. */
export const demoGoals: GoalSummary[] = demoObjectives.map((o) => {
  const steps = o.steps ?? [];
  const required = steps.filter((s) => s.status !== 'cancelled');
  const completed = required.filter((s) => s.status === 'completed').length;
  return {
    id: o.id,
    title: o.title,
    status: o.status,
    priority: o.priority,
    progress: {
      completed_steps: completed,
      total_required_steps: required.length,
      fraction: required.length ? completed / required.length : 0,
      open_step_states: Array.from(
        new Set(
          steps
            .filter((s) => ['pending', 'ready', 'running', 'in_progress', 'blocked'].includes(s.status))
            .map((s) => s.status)
        )
      ).sort(),
    },
  };
});

/* --- Goal detail (mirrors GET /goals/{id} and GET /goals/{id}/plans) -------
 * Derived from the same demo objectives, so preview shows the real response
 * shape rather than a different invented one. */

export function demoGoalDetail(goalId: string | null): GoalDetail {
  const objective = demoObjectives.find((o) => o.id === goalId) ?? null;
  if (!objective) {
    return {
      goal: {
        id: '',
        title: '',
        purpose: null,
        status: 'active',
        priority: 3,
        current_summary: null,
        completion_criteria: null,
        criteria_verified: false,
      },
      progress: { completed_steps: 0, total_required_steps: 0, fraction: 0, open_step_states: [] },
      active_plan: null,
    };
  }

  const summary = demoGoals.find((g) => g.id === objective.id);

  return {
    goal: {
      id: objective.id,
      title: objective.title,
      purpose: objective.purpose,
      status: objective.status,
      priority: objective.priority,
      current_summary: objective.current_summary,
      completion_criteria: objective.completion_criteria,
      criteria_verified: objective.criteria_verified,
    },
    progress: summary?.progress ?? {
      completed_steps: 0,
      total_required_steps: 0,
      fraction: 0,
      open_step_states: [],
    },
    active_plan: {
      id: `${objective.id}-plan`,
      version: 1,
      status: objective.status === 'completed' ? 'COMPLETED' : 'ACTIVE',
      source: 'manual',
      reason: null,
      steps: (objective.steps ?? []).map((s) => ({
        id: s.id,
        step_key: `step-${s.step_order}`,
        step_order: s.step_order,
        description: s.description,
        status: s.status,
        priority: 1,
        depends_on: s.step_order > 1 ? [`step-${s.step_order - 1}`] : [],
        attempt_count: 1,
        linked_task_id: s.linked_task_id,
        evidence_task_id: s.status === 'completed' ? s.linked_task_id : null,
        result_summary: s.result_summary,
        failure_reason: s.blocker_reason,
      })),
    },
  };
}

export const demoGoalPlans: Record<string, GoalPlanVersion[]> = {};
for (const o of demoObjectives) {
  demoGoalPlans[o.id] = [
    {
      id: `${o.id}-plan`,
      version: 1,
      status: o.status === 'completed' ? 'COMPLETED' : 'ACTIVE',
      source: 'manual',
      reason: null,
      created_at: o.created_at,
    },
  ];
}

/* --- Agent runtime ------------------------------------------------------- */

export const demoAgent: AgentIdentity = {
  agent_id: 'agent-demo-01',
  name: 'Sythion',
  state: 'active',
  last_wake_at: minsAgo(6),
  last_session_outcome: 'Completed infrastructure cost review — 2 steps executed, 1 blocked.',
  last_wake_reason: 'objective.ready',
  model_selected: 'qwen3:8b',
  tools_installed: 46,
  goals_active: 2,
  policies: {
    background_orchestration_enabled: true,
    autonomy_level: 'guarded',
    step_max_attempts: 3,
    max_steps_per_background_cycle: 5,
  },
};

export const demoBackground: BackgroundPolicy = { enabled: true, default: false };

export const demoAgentHealth: AgentHealthReport = {
  overall: 'DEGRADED',
  checked_at: minsAgo(1),
  checks: [
    { name: 'database', status: 'HEALTHY', evidence: { path: 'sythion.db' } },
    { name: 'main_agent', status: 'HEALTHY', evidence: { agent_id: 'agent-demo-01', state: 'active' } },
    { name: 'capability_consistency', status: 'DEGRADED', evidence: { issues: ['browser.read_page references a tool that is not registered'] } },
    { name: 'configuration', status: 'HEALTHY', evidence: { errors: [] } },
    { name: 'provider.ollama_cloud', status: 'UNKNOWN', evidence: { detail: 'Remote probe not run' } },
  ],
};

export const demoActivity: AgentActivity = {
  wakes: [
    {
      id: 3,
      wake_key: 'trigger:objective:obj-infra',
      trigger_type: 'objective.ready',
      trigger_reference: 'obj-infra',
      started_at: minsAgo(30),
      finished_at: minsAgo(26),
      outcome: 'completed',
      stop_reason: null,
      steps_attempted: 2,
      steps_completed: 2,
    },
    {
      id: 2,
      wake_key: 'trigger:schedule:daily-brief',
      trigger_type: 'schedule',
      trigger_reference: 'daily-brief',
      started_at: hoursAgo(7),
      finished_at: hoursAgo(7),
      outcome: 'completed',
      stop_reason: null,
      steps_attempted: 3,
      steps_completed: 3,
    },
    {
      id: 1,
      wake_key: 'trigger:user.request',
      trigger_type: 'user.request',
      trigger_reference: null,
      started_at: hoursAgo(9),
      finished_at: hoursAgo(8.6),
      outcome: 'failed',
      stop_reason: 'Destination volume not present',
      steps_attempted: 1,
      steps_completed: 0,
    },
  ],
  events: [
    {
      event_type: 'agent.background_policy_changed',
      event_data: { enabled: true },
      sequence_num: 92,
      created_at: minsAgo(180),
    },
    {
      event_type: 'task.completed',
      event_data: { task_id: 'demo-58a9d0', state: 'completed' },
      sequence_num: 91,
      created_at: hoursAgo(22),
    },
    {
      event_type: 'delegation.completed',
      event_data: { role: 'ANALYSIS', status: 'ACCEPT' },
      sequence_num: 90,
      created_at: hoursAgo(26),
    },
    {
      event_type: 'task.failed',
      event_data: { task_id: 'demo-1e55f0', reason: 'volume_missing' },
      sequence_num: 89,
      created_at: hoursAgo(9),
    },
  ],
};

/* --- Capabilities -------------------------------------------------------- */

export const demoCapabilities: Capability[] = [
  { id: 'file_read', name: 'Read files', category: 'Filesystem', status: 'AVAILABLE', risk_class: 'LOW', requires_permission: false, requires_external_service: false, deterministic: true, tools_registered: ['file_read'], tools_missing: [] },
  { id: 'file_write', name: 'Create and edit files', category: 'Filesystem', status: 'AVAILABLE', risk_class: 'MEDIUM', requires_permission: false, requires_external_service: false, deterministic: true, tools_registered: ['file_create', 'file_write', 'file_move'], tools_missing: [] },
  { id: 'file_delete', name: 'Delete files', category: 'Filesystem', status: 'REQUIRES_PERMISSION', risk_class: 'HIGH', requires_permission: true, requires_external_service: false, deterministic: true, tools_registered: ['file_delete'], tools_missing: [] },
  { id: 'gui_control', name: 'Control desktop windows', category: 'Desktop', status: 'REQUIRES_PERMISSION', risk_class: 'HIGH', requires_permission: true, requires_external_service: false, deterministic: false, tools_registered: ['gui_click', 'gui_type', 'gui_hotkey'], tools_missing: [] },
  { id: 'system_power', name: 'Power state', category: 'Desktop', status: 'REQUIRES_PERMISSION', risk_class: 'HIGH', requires_permission: true, requires_external_service: false, deterministic: true, tools_registered: ['system_power'], tools_missing: [] },
  { id: 'app_launch', name: 'Launch applications', category: 'Desktop', status: 'AVAILABLE', risk_class: 'MEDIUM', requires_permission: false, requires_external_service: false, deterministic: true, tools_registered: ['app_list', 'app_launch'], tools_missing: [] },
  { id: 'clipboard', name: 'Clipboard access', category: 'Desktop', status: 'AVAILABLE', risk_class: 'MEDIUM', requires_permission: false, requires_external_service: false, deterministic: true, tools_registered: ['clipboard_read'], tools_missing: [] },
  { id: 'browser_control', name: 'Browser automation', category: 'Web', status: 'DEGRADED', risk_class: 'MEDIUM', requires_permission: false, requires_external_service: true, deterministic: false, tools_registered: ['browser_tabs'], tools_missing: ['browser_read_page'] },
  { id: 'web_research', name: 'Web research', category: 'Web', status: 'UNAVAILABLE', risk_class: 'LOW', requires_permission: false, requires_external_service: true, deterministic: false, tools_registered: [], tools_missing: ['web_search', 'web_fetch'] },
  { id: 'smart_home', name: 'Smart-home control', category: 'Devices', status: 'UNAVAILABLE', risk_class: 'HIGH', requires_permission: true, requires_external_service: true, deterministic: false, tools_registered: [], tools_missing: ['home_device_list', 'home_device_set'] },
  { id: 'persistent_memory', name: 'Persistent memory', category: 'Memory', status: 'AVAILABLE', risk_class: 'LOW', requires_permission: false, requires_external_service: false, deterministic: true, tools_registered: ['memory_store', 'memory_recall'], tools_missing: [] },
  { id: 'email', name: 'Email', category: 'Communications', status: 'UNAVAILABLE', risk_class: 'HIGH', requires_permission: true, requires_external_service: true, deterministic: false, tools_registered: [], tools_missing: ['email_list', 'email_send'] },
  { id: 'telephony', name: 'Telephone calls', category: 'Communications', status: 'UNAVAILABLE', risk_class: 'HIGH', requires_permission: true, requires_external_service: true, deterministic: false, tools_registered: [], tools_missing: ['call_place'] },
  { id: 'voice_output', name: 'Voice output', category: 'Communications', status: 'AVAILABLE', risk_class: 'LOW', requires_permission: false, requires_external_service: false, deterministic: true, tools_registered: ['voice_speak'], tools_missing: [] },
  { id: 'package_install', name: 'Install software', category: 'System', status: 'REQUIRES_PERMISSION', risk_class: 'HIGH', requires_permission: true, requires_external_service: true, deterministic: false, tools_registered: [], tools_missing: ['package_install'] },
];

export const demoTools: ToolInfo[] = [
  { name: 'file_read', description: 'Read a UTF-8 text file, capped at 10 MB.' },
  { name: 'file_search', description: 'Recursive filename/extension search under an approved root.' },
  { name: 'system_time', description: 'Read the local date and time.' },
  { name: 'system_battery', description: 'Read battery charge state.' },
  { name: 'system_volume', description: 'Read or set system volume.' },
  { name: 'app_launch', description: 'Launch a registered application by id.' },
  { name: 'gui_screenshot', description: 'Capture a screenshot of a named window.' },
  { name: 'clipboard_read', description: 'Read the current clipboard text.' },
];

export const demoAgentRegistry: AgentRegistry = {
  main: { agent_id: 'agent-demo-01', role: 'MAIN', state: 'active' },
  specialists: [
    { agent_id: 'spec-demo-a1', role: 'ANALYSIS', state: 'COMPLETED' },
    { agent_id: 'spec-demo-a2', role: 'RESEARCH', state: 'READY' },
  ],
  definitions: [
    { id: 'def-research', role: 'RESEARCH', name: 'Research specialist', enabled: true, capabilities: ['web_research'], model_requirements: { context: 'long' } },
    { id: 'def-analysis', role: 'ANALYSIS', name: 'Analysis specialist', enabled: true, capabilities: ['file_read', 'file_write'], model_requirements: {} },
    { id: 'def-coding', role: 'CODING', name: 'Coding specialist', enabled: false, capabilities: ['file_read', 'file_write'], model_requirements: { tools: ['file_write'] } },
    { id: 'def-review', role: 'REVIEW', name: 'Review specialist', enabled: true, capabilities: ['file_read'], model_requirements: {} },
  ],
};

export const demoDelegations: Delegation[] = [
  { id: 'del-1', status: 'COMPLETED', role: 'ANALYSIS', objective: 'Compare provider spend per service', attempt: 1, parent_agent_id: 'agent-demo-01', specialist_agent_id: 'spec-demo-a1' },
  { id: 'del-2', status: 'RUNNING', role: 'RESEARCH', objective: 'Find current market rates for the two largest vendors', attempt: 1, parent_agent_id: 'agent-demo-01', specialist_agent_id: 'spec-demo-a2' },
  { id: 'del-3', status: 'FAILED', role: 'CODING', objective: 'Normalise the billing CSV schema', attempt: 2, parent_agent_id: 'agent-demo-01', specialist_agent_id: null },
];

export const demoSkills: SkillSummary[] = [
  { id: 'sk-daily-brief', name: 'daily_brief', status: 'validated', enabled: true, version: 4 },
  { id: 'sk-invoice-scan', name: 'invoice_scan', status: 'validated', enabled: true, version: 2 },
  { id: 'sk-note-extract', name: 'note_action_extract', status: 'validated', enabled: true, version: 7 },
];

export const demoSkillProposals: SkillProposal[] = [
  { id: 'sp-1', name: 'weekly_finance_recap', status: 'pending_review', evidence_count: 5 },
  { id: 'sp-2', name: 'meeting_followup_email', status: 'pending_review', evidence_count: 3 },
];

export const demoToolProposals: ToolProposal[] = [
  {
    id: 41,
    name: 'smart_home_device_set',
    description: 'Set a device property (power, level, temperature).',
    status: 'pending_review',
    enabled_mode: null,
    successful_uses: 0,
    validation_errors: ['Schema does not declare an integration target'],
    sandbox_result: null,
    created_at: daysAgo(2),
    decided_at: null,
    enabled_at: null,
  },
];

export const demoMemoryReport: MemoryReport = {
  generated_at: minsAgo(30),
  stores: {
    preferences: { count: 34 },
    facts: { count: 412 },
    decisions: { count: 57 },
    lessons: { count: 19 },
    projects: { count: 12 },
  },
  memory_context_injection: { runs_sampled: 50, mean_items_injected: 8.4 },
  relations: { edges: 96, stale: 7, removed: 3 },
};

export const demoMemoryEdges: MemoryEdge[] = [
  { id: 1, source_key: 'preference:email.tone', relation: 'supports', target_key: 'decision:vendor.emails.formal', weight: 0.92, status: 'active', created_at: daysAgo(30), updated_at: daysAgo(2) },
  { id: 2, source_key: 'project:infra-cost', relation: 'depends_on', target_key: 'fact:billing.exports.location', weight: 0.77, status: 'active', created_at: daysAgo(12), updated_at: hoursAgo(20) },
  { id: 3, source_key: 'preference:schedule.morning', relation: 'conflicts_with', target_key: 'routine:evening_training', weight: 0.21, status: 'stale', created_at: daysAgo(60), updated_at: daysAgo(34) },
  { id: 4, source_key: 'decision:stack.postgres', relation: 'supersedes', target_key: 'decision:stack.mysql', weight: 0.55, status: 'active', created_at: daysAgo(80), updated_at: daysAgo(75) },
  { id: 5, source_key: 'lesson:verify.before.deleting', relation: 'generalises_to', target_key: 'rule:no.unverified.writes', weight: 0.68, status: 'active', created_at: daysAgo(18), updated_at: daysAgo(9) },
];

export const demoMemoryItems = [
  { id: 'mem-1', kind: 'Preference', key: 'Email tone', value: 'Formal and direct; no exclamation marks.', source: 'Stated by owner 4 times', confidence: 0.94, updated: daysAgo(2), used: 31 },
  { id: 'mem-2', kind: 'Preference', key: 'Working hours', value: 'Do not schedule anything before 09:00 or after 18:00.', source: 'Stated by owner', confidence: 0.97, updated: daysAgo(11), used: 48 },
  { id: 'mem-3', kind: 'Project', key: 'Q4 cost review', value: 'Goal: 15% reduction. Baseline: €41,200/month.', source: 'Owner + task evidence', confidence: 0.88, updated: minsAgo(60), used: 17 },
  { id: 'mem-4', kind: 'Decision', key: 'Use Postgres over MySQL', value: 'Decided 2026-09-14 after load-test comparison.', source: 'Recorded decision', confidence: 1, updated: daysAgo(34), used: 9 },
  { id: 'mem-5', kind: 'Lesson', key: 'Verify before deleting', value: 'Always read back after a mutating tool call; silent success hid two bugs.', source: 'Post-task learning', confidence: 0.92, updated: daysAgo(9), used: 22 },
  { id: 'mem-6', kind: 'Fact', key: 'Billing exports location', value: 'C:\\Users\\owner\\Documents\\invoices\\2024-q4', source: 'Read from disk', confidence: 1, updated: daysAgo(12), used: 6 },
];

export const demoPurgeReceipts: PurgeReceipt[] = [
  { task_id: 'task-old-1', reason: 'retention window elapsed', purged_at: daysAgo(2), event_count: 142 },
  { task_id: 'task-old-2', reason: 'retention window elapsed', purged_at: daysAgo(6), event_count: 88 },
];

/* --- Devices (no device-registry endpoint exists) ------------------------- */

export const demoDevices = [
  {
    id: 'dev-pc',
    name: 'Sythion workstation',
    category: 'Computer',
    status: 'online' as const,
    lastSeen: 'Continuous',
    detail: 'Windows 11 · this machine · the host Sythion runs on',
    capabilities: ['Filesystem', 'Desktop control', 'Applications', 'Clipboard'],
    scope: 'Full owner scope, bounded by approved file roots',
    actions: ['Browse approved roots', 'Launch apps', 'Read system state'],
  },
  {
    id: 'dev-phone',
    name: 'Pixel 8 Pro',
    category: 'Phone',
    status: 'online' as const,
    lastSeen: '2m ago',
    detail: 'Android 15 · paired for notifications and voice',
    capabilities: ['Notifications', 'Voice input', 'Location (opt-in)'],
    scope: 'Read notifications; no remote control granted',
    actions: ['Send notification', 'Start voice session'],
  },
  {
    id: 'dev-laptop',
    name: 'Work laptop',
    category: 'Computer',
    status: 'offline' as const,
    lastSeen: '3d ago',
    detail: 'Windows 11 · remote execution not enabled',
    capabilities: ['Unknown — not reachable'],
    scope: 'None granted',
    actions: [],
  },
  {
    id: 'dev-nas',
    name: 'Home NAS',
    category: 'Storage',
    status: 'degraded' as const,
    lastSeen: '14m ago',
    detail: 'Synology · SMB reachable, 2 of 4 shares mounted',
    capabilities: ['File read', 'File write'],
    scope: 'Read/write on mounted shares only',
    actions: ['List shares', 'Backup to share'],
  },
  {
    id: 'dev-thermostat',
    name: 'Living-room thermostat',
    category: 'Climate',
    status: 'offline' as const,
    lastSeen: '3h ago',
    detail: 'Matter bridge · dropped off the fabric',
    capabilities: ['Read temperature', 'Set target'],
    scope: 'None — integration unavailable',
    actions: [],
  },
  {
    id: 'dev-lights',
    name: 'Downstairs lights',
    category: 'Lighting',
    status: 'online' as const,
    lastSeen: 'Live',
    detail: 'Matter · 4 lights grouped as one room',
    capabilities: ['Power', 'Brightness', 'Colour temperature'],
    scope: 'Read; write requires capability permission',
    actions: ['Read state'],
  },
  {
    id: 'dev-speaker',
    name: 'Kitchen speaker',
    category: 'Media',
    status: 'online' as const,
    lastSeen: 'Live',
    detail: 'Matter · supports voice output',
    capabilities: ['Play', 'Volume'],
    scope: 'Volume within 0–60',
    actions: ['Read state'],
  },
];

/* --- Life management (no calendar endpoint exists) ------------------------ */

export const demoCalendar = [
  { id: 'cal-1', time: '09:30', title: 'Team stand-up', kind: 'meeting', where: 'Video call', people: 6 },
  { id: 'cal-2', time: '11:00', title: '1:1 with Priya', kind: 'meeting', where: 'Kitchen table', people: 2 },
  { id: 'cal-3', time: '13:00', title: 'Deep work block', kind: 'focus', where: 'Do not disturb', people: 1 },
  { id: 'cal-4', time: '16:15', title: 'Supplier call — cloud vendor', kind: 'call', where: 'Phone', people: 3 },
];

export const demoReminders = [
  { id: 'rem-1', title: 'Send revised spec to Priya', due: inMins(95), priority: 'high', done: false, source: 'Meeting notes' },
  { id: 'rem-2', title: 'Renew the domain registration', due: inMins(60 * 30), priority: 'medium', done: false, source: 'Manual' },
  { id: 'rem-3', title: 'Confirm Thursday demo slot', due: inMins(60 * 8), priority: 'high', done: false, source: 'Meeting notes' },
  { id: 'rem-4', title: 'Replace the smoke alarm battery', due: daysAgo(1), priority: 'low', done: true, source: 'Routine' },
];

export const demoRoutines = [
  { id: 'rt-1', name: 'Morning briefing', cadence: 'Weekdays 07:30', lastRun: 'Today 07:30', status: 'ok' as const, owner: 'Sythion' },
  { id: 'rt-2', name: 'Evening review', cadence: 'Daily 20:00', lastRun: 'Yesterday 20:00', status: 'ok' as const, owner: 'Sythion' },
  { id: 'rt-3', name: 'Weekly finance recap', cadence: 'Sunday 18:00', lastRun: '—', status: 'blocked' as const, owner: 'Sythion' },
  { id: 'rt-4', name: 'Backup project folder', cadence: 'Daily 22:00', lastRun: '2d ago', status: 'failed' as const, owner: 'Sythion' },
];

export const demoAdmin = [
  { id: 'ad-1', item: 'Car insurance', due: daysAgo(-6), amount: '€412.00', status: 'upcoming', autopay: true },
  { id: 'ad-2', item: 'Broadband', due: daysAgo(-11), amount: '€54.00', status: 'upcoming', autopay: true },
  { id: 'ad-3', item: 'Mobile plan', due: daysAgo(-2), amount: '€29.50', status: 'due-soon', autopay: false },
  { id: 'ad-4', item: 'Gym membership', due: daysAgo(-14), amount: '€39.00', status: 'upcoming', autopay: true },
];

/* --- Communications (no messaging endpoint exists) ----------------------- */

export const demoThreads = [
  { id: 'th-1', channel: 'Email', title: 'Cloud vendor — Q4 usage review', preview: 'Attaching the usage breakdown we discussed…', when: '18m', unread: 2, tone: 'info' as const },
  { id: 'th-2', channel: 'Email', title: 'Priya Raman', preview: 'The spec looks good — one question on the rollout.', when: '1h', unread: 1, tone: 'info' as const },
  { id: 'th-3', channel: 'Message', title: 'Household group', preview: 'Who is picking up the parcel from the depot?', when: '3h', unread: 0, tone: 'accent' as const },
  { id: 'th-4', channel: 'Draft', title: 'Supplier renegotiation — vendor A', preview: 'Saved draft. Not sent. Requires approval before sending.', when: 'Yesterday', unread: 0, tone: 'warn' as const },
];

export const demoCalls = [
  { id: 'cl-1', direction: 'outbound', party: 'Cloud vendor — account team', started: minsAgo(190), duration: '11m 20s', outcome: 'completed', summary: 'Agreed to send the usage breakdown and revisit pricing in two weeks.' },
  { id: 'cl-2', direction: 'outbound', party: 'Contractor — kitchen quote', started: daysAgo(1), duration: '—', outcome: 'failed', summary: 'No answer. Contractor number rings to voicemail; retry next week.' },
  { id: 'cl-3', direction: 'inbound', party: 'Unknown', started: daysAgo(3), duration: '0m 04s', outcome: 'rejected', summary: 'Screening blocked the call — number not in the allowlist.' },
];

export const demoNotifications = [
  { id: 'nt-1', title: 'Backup failed', body: 'Volume E: was not mounted when the 22:00 backup ran.', when: minsAgo(180), tone: 'danger' as const, unread: true },
  { id: 'nt-2', title: 'Thermostat offline', body: 'Living-room thermostat has not reported for 3 hours.', when: hoursAgo(3), unread: true, tone: 'warn' as const },
  { id: 'nt-3', title: 'Objective advanced', body: 'Q4 cost review completed step 1 of 4.', when: hoursAgo(20), unread: false, tone: 'ok' as const },
  { id: 'nt-4', title: 'Approval needed', body: 'Cancelling an appointment requires your confirmation.', when: daysAgo(1), unread: false, tone: 'info' as const },
];

/* --- Automations --------------------------------------------------------- */

export const demoAutomations = [
  { id: 'au-1', name: 'Morning briefing', trigger: 'Schedule · weekdays 07:30', conditions: 'Only if nothing is already in flight', actions: 'Summarise calendar, mail and overdue tasks', enabled: true, lastRun: 'Today 07:30', lastOutcome: 'ok' as const, nextRun: 'Tomorrow 07:30', runs: 62 },
  { id: 'au-2', name: 'Bill payment reminder', trigger: 'Schedule · daily 09:00', conditions: 'Within 7 days of a due date', actions: 'Create reminder, never pay', enabled: true, lastRun: 'Today 09:00', lastOutcome: 'ok' as const, nextRun: 'Tomorrow 09:00', runs: 31 },
  { id: 'au-3', name: 'Device health sweep', trigger: 'Event · device heartbeat missing', conditions: 'No heartbeat for 30 minutes', actions: 'Report the device and its last known state', enabled: true, lastRun: '3h ago', lastOutcome: 'warn' as const, nextRun: 'On next trigger', runs: 118 },
  { id: 'au-4', name: 'Nightly backup', trigger: 'Schedule · daily 22:00', conditions: 'External volume present', actions: 'Copy approved project roots to the backup share', enabled: true, lastRun: '2d ago', lastOutcome: 'fail' as const, nextRun: 'Tonight 22:00', runs: 44 },
  { id: 'au-5', name: 'Weekly finance recap', trigger: 'Schedule · Sunday 18:00', conditions: 'Billing export newer than 7 days', actions: 'Draft recap for review — does not send', enabled: false, lastRun: 'Never', lastOutcome: 'idle' as const, nextRun: 'Paused', runs: 0 },
];

export const demoAutomationRuns = [
  { id: 'run-1', name: 'Morning briefing', started: 'Today 07:30', duration: '4s', outcome: 'ok' as const, detail: '6 items summarised, 0 escalations.' },
  { id: 'run-2', name: 'Device health sweep', started: '3h ago', duration: '1.1s', outcome: 'warn' as const, detail: '1 device reported offline.' },
  { id: 'run-3', name: 'Nightly backup', started: '2d ago', duration: '0.4s', outcome: 'fail' as const, detail: 'Volume E: not mounted — precondition failed, nothing copied.' },
  { id: 'run-4', name: 'Bill payment reminder', started: '2d ago', duration: '2.3s', outcome: 'ok' as const, detail: '1 reminder created for the mobile plan.' },
];

/* --- System -------------------------------------------------------------- */

export const demoIntegrations = [
  { id: 'int-1', name: 'Local backend (uvicorn)', kind: 'Runtime', state: 'connected' as const, detail: '127.0.0.1 · sidecar process', lastCheck: 'Continuous' },
  { id: 'int-2', name: 'Model provider', kind: 'Model', state: 'configured' as const, detail: 'Ollama Cloud · qwen3:8b selected', lastCheck: '12m ago' },
  { id: 'int-3', name: 'Email (IMAP/SMTP)', kind: 'External', state: 'unavailable' as const, detail: 'No credentials configured', lastCheck: 'Never' },
  { id: 'int-4', name: 'Messaging bridge', kind: 'External', state: 'unavailable' as const, detail: 'No bridge process registered', lastCheck: 'Never' },
  { id: 'int-5', name: 'Home automation fabric', kind: 'External', state: 'unavailable' as const, detail: 'No Matter or Home Assistant integration registered', lastCheck: 'Never' },
  { id: 'int-6', name: 'Speech to text (local)', kind: 'Local model', state: 'degraded' as const, detail: 'Model present, checksum not verified', lastCheck: '1h ago' },
  { id: 'int-7', name: 'Database', kind: 'Storage', state: 'connected' as const, detail: 'SQLite · WAL mode', lastCheck: 'Continuous' },
];

export const demoModels = [
  { id: 'qwen3:8b', role: 'primary', provider: 'Ollama Cloud', context: '32k', selected: true },
  { id: 'llama3.1:70b', role: 'analysis', provider: 'Ollama Cloud', context: '128k', selected: false },
  { id: 'phi4:14b', role: 'fast', provider: 'Ollama Cloud', context: '16k', selected: false },
];

export const demoSecrets = ['OLLAMA_API_KEY'];

export const demoSettings = {
  host: '127.0.0.1',
  port: 8000,
  ollama_cloud_base_url: 'https://ollama.com',
  selected_model: 'qwen3:8b',
  file_roots: ['C:\\Users\\owner\\Documents', 'C:\\Users\\owner\\Projects'],
  max_actions_per_task: 40,
  max_cloud_requests_per_task: 60,
  voice_enabled: true,
  voice_recording_enabled: false,
  voice_tts_voice: 'en-GB-SoniaNeural',
  retention_days: 90,
};

export const demoVoiceAssets = {
  stt_installed: true,
  stt_model: 'ggml-base.en',
  stt_version: '1.5.5',
  stt_license: 'MIT',
  stt_sha256_verified: false,
  tts_provider: 'piper',
  tts_voice: 'en-GB-SoniaNeural',
  assets_dir: '%LOCALAPPDATA%\\sythion\\voice',
  downloads_on_import: false,
  downloads_on_startup: false,
};

export const demoHealth = {
  status: 'preview',
  version: '0.1.0',
  database_connected: true,
  cloud_configured: true,
  selected_model: 'qwen3:8b',
};

export const demoVersion = {
  version: '0.1.0',
  api_version: 'v1',
  platform: 'windows',
  runtime: 'uvicorn',
};