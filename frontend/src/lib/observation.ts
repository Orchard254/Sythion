import type { Objective, PlanStep, Task, Tone } from '../types';
import { parseTimestamp } from './format';

/* Observation layer.
 *
 * Every value produced here is DERIVED from durable backend state. This module
 * implements the rules in :
 *
 *  - Report persisted state and sequences as fact; derive everything else and
 *    label it as derived.
 *  - `completed` is an execution terminal state, NOT a verified outcome.
 *  - Never infer success, progress or health where evidence is insufficient —
 *    say `unknown` instead.
 *  - Do not invent a task-level `blocked` state. Blocked work is step-level and
 *    must be labelled as such.
 *  - `waiting_for_user` and `paused` are legitimate non-progress, not stalls.
 *  - Warnings use tentative language.
 *
 * Deliberately NOT implemented, because the contract records them as gaps and
 * this UI must not fabricate them:
 *  - runner-lease / executor-liveness detection (no lease endpoint is exposed);
 *  - delegation supervision linkage (contract section 10.2, future task).
 * Both are surfaced in the UI as unavailable rather than guessed.
 */

/* --- Persisted task states ---------------------------------------------- */

/** `Task.state` as constrained by backend/models/database.py. */
export type PersistedTaskState =
  | 'created'
  | 'running'
  | 'waiting_for_user'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'interrupted'
  | 'paused';

export const TERMINAL_TASK_STATES: ReadonlySet<string> = new Set([
  'completed',
  'failed',
  'cancelled',
]);

/** States where the task cannot progress without the owner. */
export function isBlockedOnOwner(task: Pick<Task, 'state'>): boolean {
  return task.state === 'waiting_for_user' || task.state === 'interrupted';
}

/** A task is held on purpose, so silence is expected rather than suspicious. */
export function isLegitimateWait(task: Pick<Task, 'state'>): boolean {
  return task.state === 'waiting_for_user' || task.state === 'paused' || task.state === 'interrupted';
}

/* --- Completion assessment (contract 3.4) ------------------------------- */

export type CompletionVerdict =
  | 'not_started'
  | 'in_progress'
  | 'awaiting_owner'
  | 'held'
  | 'verified'
  | 'completed_unverified'
  | 'verification_unavailable'
  | 'failed'
  | 'cancelled';

export interface CompletionAssessment {
  verdict: CompletionVerdict;
  label: string;
  /** True when this verdict depends on something we could not observe. */
  uncertain: boolean;
  /** Why the verdict is not stronger. Shown in Results & Evidence. */
  limitation: string | null;
}

/**
 * Assess a task's outcome.
 *
 * `objective` is optional: a task need not be linked to a goal, and when it is
 * not we must not imply the goal's criteria were evaluated.
 */
export function assessCompletion(
  task: Task,
  objective?: Pick<Objective, 'criteria_verified' | 'completion_criteria'> | null
): CompletionAssessment {
  switch (task.state) {
    case 'created':
      return {
        verdict: 'not_started',
        label: 'Queued',
        uncertain: false,
        limitation: 'Created but not yet started executing.',
      };

    case 'running':
      return {
        verdict: 'in_progress',
        label: 'Running',
        uncertain: false,
        limitation: null,
      };

    case 'waiting_for_user':
      return {
        verdict: 'awaiting_owner',
        label: 'Needs your answer',
        uncertain: false,
        limitation: null,
      };

    case 'paused':
      return {
        verdict: 'held',
        label: 'Paused',
        uncertain: false,
        limitation: 'Paused by choice. It has not failed.',
      };

    case 'interrupted':
      return {
        verdict: 'held',
        label: task.waiting_kind === 'permission' ? 'Held for authorization' : 'Execution interrupted',
        uncertain: false,
        limitation:
          'Execution continuity was lost. The recorded reason is not proof of the current outcome.',
      };

    case 'cancelled':
      return {
        verdict: 'cancelled',
        label: 'Cancelled',
        uncertain: false,
        limitation: 'Terminal. It will not run again.',
      };

    case 'failed':
      return {
        verdict: 'failed',
        label: 'Failed',
        uncertain: false,
        limitation: task.terminal_reason ?? 'No failure reason was recorded.',
      };

    case 'completed': {
      if (!task.final_result) {
        return {
          verdict: 'verification_unavailable',
          label: 'Completed, unverified',
          uncertain: true,
          limitation: 'Terminal state is completed but no final result was recorded.',
        };
      }
      // No goal link: there are no goal criteria to evaluate, so the outcome is
      // complete as an execution record but its verification is not applicable.
      if (!objective) {
        return {
          verdict: 'verification_unavailable',
          label: 'Completed, not goal-verified',
          uncertain: true,
          limitation: 'Not linked to a Goal, so there are no completion criteria to verify against.',
        };
      }
      if (objective.criteria_verified) {
        return {
          verdict: 'verified',
          label: 'Verified',
          uncertain: false,
          limitation: null,
        };
      }
      return {
        verdict: 'completed_unverified',
        label: 'Completed but unverified',
        uncertain: true,
        limitation: objective.completion_criteria
          ? 'Goal completion criteria exist but have not been verified.'
          : 'The Goal records no completion criteria, so verification cannot be assessed.',
      };
    }

    default:
      return {
        verdict: 'verification_unavailable',
        label: 'Unknown state',
        uncertain: true,
        limitation: `Unrecognised persisted state "${String(task.state)}".`,
      };
  }
}

/* --- Attention ---------------------------------------------------------- */

export type AttentionKind = 'approval' | 'input' | 'failure' | 'unverified' | 'stalled' | 'blocked_step';

export interface AttentionItem {
  id: string;
  kind: AttentionKind;
  tone: Tone;
  /** Factual one-line summary. Never speculative. */
  title: string;
  /** Supporting evidence or the explicit reason it is unknown. */
  detail: string;
  /** Derived values carry this so the UI can mark them as such. */
  derived: boolean;
  taskId: string | null;
  goalId: string | null;
  /** Where the operator should go to act on this. */
  target: { kind: 'task' | 'goal'; id: string } | null;
}

/** A running task with no recorded change for this long is *suspected* stalled. */
export const SUSPECTED_STALL_MS = 20 * 60 * 1000;

/**
 * Suspected stall, not a failure claim.
 *
 * Deliberately conservative: never fires for a legitimate wait, and the wording
 * the UI shows is tentative by construction.
 */
export function assessStall(
  task: Task,
  now: number,
  windowMs: number = SUSPECTED_STALL_MS
): { suspected: boolean; silentSince: number } | null {
  if (TERMINAL_TASK_STATES.has(task.state)) return null;
  if (isLegitimateWait(task)) return null;
  if (task.state !== 'running' && task.state !== 'created') return null;

  // Backend timestamps are naive UTC; parseTimestamp normalises them. A raw
  // Date.parse here would make every task look hours older than it is.
  const updated = parseTimestamp(task.updated_at);
  if (updated === null) return null;

  const silentMs = now - updated;
  if (silentMs < windowMs) return null;

  return { suspected: true, silentSince: updated };
}

/**
 * The Attention rail.
 *
 * Ordered by what genuinely needs the operator: approvals first (they block
 * progress), then failures, then unverified outcomes, then stalls. Routine work
 * produces nothing here.
 */
export function collectAttention(input: {
  tasks: Task[];
  objectives?: Objective[];
  now: number;
  stallWindowMs?: number;
}): AttentionItem[] {
  const { tasks, now, stallWindowMs } = input;
  const objectives = input.objectives ?? [];
  const objectiveById = new Map(objectives.map((o) => [o.id, o]));
  const items: AttentionItem[] = [];

  for (const task of tasks) {
    // 1. Blocked on the owner. `interrupted` carries its reason in
    //    terminal_reason; `waiting_for_user` in pending_question.
    if (task.state === 'waiting_for_user' || task.state === 'interrupted') {
      const held = task.state === 'interrupted';
      // Approval is a permission property, not a state property. An interrupted
      // worker is a lost execution, not itself evidence of an authorization
      // request; only explicit permission metadata may advertise approval.
      const approval = task.waiting_kind === 'permission';
      const reason = task.pending_question ?? task.terminal_reason;
      items.push({
        id: `${approval ? 'approval' : 'input'}:${task.id}`,
        kind: approval ? 'approval' : 'input',
        tone: held ? 'danger' : approval ? 'warn' : 'info',
        title: held
          ? (approval ? 'Held for your approval' : 'Execution interrupted')
          : (approval ? 'Needs your answer' : 'Needs your answer'),
        detail: reason ?? 'Sythion is paused on this and will not continue until you reply.',
        derived: false,
        taskId: task.id,
        goalId: task.objective_id ?? null,
        target: { kind: 'task', id: task.id },
      });
      continue;
    }

    // 2. Terminal failure with a recorded reason.
    if (task.state === 'failed') {
      items.push({
        id: `failure:${task.id}`,
        kind: 'failure',
        tone: 'danger',
        title: 'Failed',
        detail: task.terminal_reason ?? 'No failure reason was recorded.',
        derived: false,
        taskId: task.id,
        goalId: task.objective_id ?? null,
        target: task.objective_id
          ? { kind: 'goal', id: task.objective_id }
          : { kind: 'task', id: task.id },
      });
      continue;
    }

    // 3. Completed but the outcome is not verified. A routine task that left a
    //    result and simply has no Goal to verify against produces no Attention
    //    item — that is the expected shape of casual work, not a problem.
    //    Missing evidence (no final result) and goal-linked-but-unverified do.
    if (task.state === 'completed') {
      const objective = task.objective_id ? objectiveById.get(task.objective_id) : null;
      const assessment = assessCompletion(task, objective ?? null);
      const missingResult = !task.final_result;
      const goalLinked = !!objective;
      const attentionWorthy =
        assessment.verdict === 'completed_unverified' ||
        (assessment.verdict === 'verification_unavailable' && (missingResult || goalLinked));
      if (attentionWorthy) {
        items.push({
          id: `unverified:${task.id}`,
          kind: 'unverified',
          tone: 'warn',
          title: assessment.label,
          detail: assessment.limitation ?? 'Verification evidence is incomplete.',
          derived: true,
          taskId: task.id,
          goalId: task.objective_id ?? null,
          target: task.objective_id
            ? { kind: 'goal', id: task.objective_id }
            : { kind: 'task', id: task.id },
        });
      }
      continue;
    }

    // 4. Suspected stall. Tentative by contract.
    const stall = assessStall(task, now, stallWindowMs);
    if (stall?.suspected) {
      items.push({
        id: `stalled:${task.id}`,
        kind: 'stalled',
        tone: 'warn',
        title: 'No recorded progress',
        detail: `Nothing recorded since ${new Date(stall.silentSince).toISOString()}. Not necessarily failed.`,
        derived: true,
        taskId: task.id,
        goalId: task.objective_id ?? null,
        target: { kind: 'task', id: task.id },
      });
    }
  }

  // 5. Blocked plan steps. Only active/paused goals are candidates for
  // current attention; completed or abandoned goals are historical records.
  // Keeping their old blocked steps in "Needs you" contradicts the goal
  // lifecycle and makes abandoned work appear actionable.
  for (const objective of objectives) {
    if (objective.status !== 'active' && objective.status !== 'paused') continue;
    for (const step of objective.steps ?? []) {
      if (step.status !== 'blocked') continue;
      items.push({
        id: `blocked_step:${objective.id}:${step.id}`,
        kind: 'blocked_step',
        tone: 'warn',
        title: 'Plan step blocked',
        detail: `${step.description}${
          step.blocker_reason ? ` — ${step.blocker_reason}` : ' — no blocker reason was recorded.'
        }`,
        derived: true,
        taskId: step.linked_task_id ?? null,
        goalId: objective.id,
        target: { kind: 'goal', id: objective.id },
      });
    }
  }

  const order: Record<AttentionKind, number> = {
    approval: 0,
    input: 1,
    failure: 2,
    unverified: 3,
    blocked_step: 4,
    stalled: 5,
  };
  return items.sort((a, b) => order[a.kind] - order[b.kind]);
}

/* --- Active operations -------------------------------------------------- */

/**
 * The latest confirmed step for a task, derived from linked plan steps.
 * Never inferred from elapsed time.
 */
export function latestConfirmedStep(
  task: Task,
  objectives: Objective[]
): PlanStep | null {
  if (!task.objective_id) return null;
  const objective = objectives.find((o) => o.id === task.objective_id);
  if (!objective) return null;
  const steps = objective.steps ?? [];
  if (!steps.length) return null;

  // Prefer the step this task is linked to; otherwise the lowest-ordered
  // non-terminal step, which is the only defensible "current" claim.
  if (task.id) {
    const linked = steps.find((s) => s.linked_task_id === task.id);
    if (linked) return linked;
  }
  const open = steps
    .filter((s) => ['pending', 'ready', 'running', 'in_progress', 'blocked'].includes(s.status))
    .sort((a, b) => a.step_order - b.step_order);
  return open[0] ?? steps[steps.length - 1];
}

/** Waiting condition text for a task, or null when it is not waiting. */
export function waitingCondition(task: Task): string | null {
  if (task.state === 'waiting_for_user') {
    return task.pending_question ?? 'Waiting for your answer.';
  }
  if (task.state === 'interrupted') {
    return task.terminal_reason ?? 'Held; execution continuity was lost.';
  }
  if (task.state === 'paused') return 'Paused.';
  return null;
}