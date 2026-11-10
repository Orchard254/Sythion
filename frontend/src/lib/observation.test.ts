import { describe, expect, it } from 'vitest';
import {
  assessCompletion,
  assessStall,
  collectAttention,
  isBlockedOnOwner,
  isLegitimateWait,
  SUSPECTED_STALL_MS,
} from '../lib/observation';
import type { Objective, Task } from '../types';

/* Tests for the observation layer.
 *
 * These exist because this module is what stops the Control Center from making
 * claims the backend cannot support ().
 * It is pure, so the tests are fast and need no DOM or network.
 */

function task(overrides: Partial<Task> = {}): Task {
  return {
    id: 't1',
    original_request: 'Do the thing',
    normalized_goal: null,
    state: 'running',
    version: 1,
    active_action_id: null,
    objective_id: null,
    pending_question: null,
    final_result: null,
    created_at: '2026-10-19T10:00:00',
    updated_at: '2026-10-19T10:00:00',
    terminal_reason: null,
    ...overrides,
  };
}

function objective(overrides: Partial<Objective> = {}): Objective {
  return {
    id: 'o1',
    title: 'Goal',
    purpose: null,
    status: 'active',
    priority: 3,
    current_summary: null,
    last_task_id: null,
    completion_criteria: null,
    criteria_verified: false,
    parent_objective_id: null,
    created_at: '2026-10-19T10:00:00',
    updated_at: '2026-10-19T10:00:00',
    steps: [],
    sub_objectives: [],
    ...overrides,
  };
}

describe('assessCompletion', () => {
  it('never calls a finished task verified without criteria evidence', () => {
    const completed = task({ state: 'completed', final_result: 'did it' });
    const unlinked = assessCompletion(completed, null);
    expect(unlinked.verdict).toBe('verification_unavailable');
    expect(unlinked.uncertain).toBe(true);

    const withCriteria = assessCompletion(completed, {
      criteria_verified: false,
      completion_criteria: 'the report exists',
    });
    expect(withCriteria.verdict).toBe('completed_unverified');
    expect(withCriteria.uncertain).toBe(true);
  });

  it('reports verified only when the goal criteria are verified', () => {
    const verified = assessCompletion(task({ state: 'completed', final_result: 'x' }), {
      criteria_verified: true,
      completion_criteria: 'the report exists',
    });
    expect(verified.verdict).toBe('verified');
    expect(verified.uncertain).toBe(false);
  });

  it('treats a completed task with no final result as unverified, not successful', () => {
    const result = assessCompletion(task({ state: 'completed', final_result: null }), null);
    expect(result.verdict).toBe('verification_unavailable');
  });

  it('surfaces the recorded terminal reason on failure', () => {
    const result = assessCompletion(
      task({ state: 'failed', terminal_reason: 'drive not mounted' }),
      null
    );
    expect(result.verdict).toBe('failed');
    expect(result.limitation).toBe('drive not mounted');
  });

  it('marks interrupted as held rather than failed', () => {
    const result = assessCompletion(
      task({ state: 'interrupted', terminal_reason: 'awaiting owner approval' }),
      null
    );
    expect(result.verdict).toBe('held');
    expect(result.limitation).toContain('continuity');
  });

  it('handles paused as a legitimate hold, not a failure', () => {
    const result = assessCompletion(task({ state: 'paused' }), null);
    expect(result.verdict).toBe('held');
    expect(result.limitation).toContain('has not failed');
  });
});

describe('stall detection', () => {
  const now = Date.parse('2026-10-19T12:00:00Z');

  it('does not suspect a stall for a legitimate wait', () => {
    for (const state of ['waiting_for_user', 'paused', 'interrupted'] as const) {
      const stale = task({ state, updated_at: '2026-10-19T10:00:00' });
      expect(assessStall(stale, now)).toBeNull();
    }
  });

  it('does not suspect a stall for a terminal task', () => {
    const failed = task({ state: 'failed', updated_at: '2026-10-19T10:00:00' });
    expect(assessStall(failed, now)).toBeNull();
  });

  it('flags a long-silent running task as suspected only', () => {
    const silent = task({ state: 'running', updated_at: '2026-10-19T10:00:00' });
    const result = assessStall(silent, now);
    expect(result?.suspected).toBe(true);
    expect(result!.silentSince).toBe(Date.parse('2026-10-19T10:00:00Z'));
  });

  it('reads backend timestamps as UTC, not local', () => {
    // The backend emits naive UTC. If this were parsed as local time the task
    // would look hours staler than it is, on any machine not on UTC.
    const twoMinutesAgo = new Date(now - 2 * 60 * 1000)
      .toISOString()
      .replace('Z', ''); // naive, as the backend sends it
    expect(assessStall(task({ state: 'running', updated_at: twoMinutesAgo }), now)).toBeNull();
  });

  it('leaves a recently updated running task alone', () => {
    const fresh = task({ state: 'running', updated_at: '2026-10-19T11:59:00' });
    expect(assessStall(fresh, now)).toBeNull();
  });
});

describe('state predicates', () => {
  it('treats waiting_for_user and interrupted as blocked on the owner', () => {
    expect(isBlockedOnOwner(task({ state: 'waiting_for_user' }))).toBe(true);
    expect(isBlockedOnOwner(task({ state: 'interrupted' }))).toBe(true);
    expect(isBlockedOnOwner(task({ state: 'running' }))).toBe(false);
  });

  it('treats held states as legitimate waits', () => {
    expect(isLegitimateWait(task({ state: 'paused' }))).toBe(true);
    expect(isLegitimateWait(task({ state: 'failed' }))).toBe(false);
  });
});

describe('collectAttention', () => {
  const now = Date.parse('2026-10-19T12:00:00Z');

  it('raises an approval when a wait carries permission metadata, even without a question', () => {
    // Staged permissions are the only waits that may advertise approval. A null
    // question alone must not imply one.
    const items = collectAttention({
      tasks: [task({ state: 'waiting_for_user', pending_question: null, waiting_kind: 'permission' })],
      now,
    });
    expect(items).toHaveLength(1);
    expect(items[0].kind).toBe('approval');
    expect(items[0].detail).toContain('will not continue until you reply');
  });

  it('keeps an unclassified wait visible as a neutral input request', () => {
    // A model question with no pending_action must not be labelled approval.
    const items = collectAttention({
      tasks: [task({ state: 'waiting_for_user', pending_question: 'How many people?' })],
      now,
    });
    expect(items).toHaveLength(1);
    expect(items[0].kind).toBe('input');
    expect(items[0].title).toBe('Needs your answer');
  });

  it('classifies waiting states from explicit kind only', () => {
    const cases: Array<[Partial<Task>, 'approval' | 'input']> = [
      [{ waiting_kind: 'permission' }, 'approval'],
      [{ waiting_kind: 'clarification' }, 'input'],
      [{ waiting_kind: 'unknown' }, 'input'],
      [{}, 'input'],
      [{ pending_question: null }, 'input'],
    ];
    for (const [overrides, expected] of cases) {
      const items = collectAttention({
        tasks: [task({ state: 'waiting_for_user', pending_question: 'Question?', ...overrides })],
        now,
      });
      expect(items[0].kind).toBe(expected);
    }
  });

  it('classifies an interrupted wait with explicit permission metadata as an approval', () => {
    const items = collectAttention({
      tasks: [task({ state: 'interrupted', terminal_reason: 'awaiting owner approval', waiting_kind: 'permission' })],
      now,
    });
    expect(items[0].kind).toBe('approval');
    expect(items.some((i) => i.kind === 'failure')).toBe(false);
  });

  it('keeps an interrupted execution visible without approval claims', () => {
    const items = collectAttention({
      tasks: [task({ state: 'interrupted', terminal_reason: 'worker lost' })],
      now,
    });
    expect(items[0].kind).toBe('input');
    expect(items[0].title).toBe('Execution interrupted');
    expect(items.some((i) => i.kind === 'failure')).toBe(false);
  });

  it('reports completed-but-unverified as needing attention', () => {
    const items = collectAttention({
      tasks: [task({ state: 'completed', final_result: 'ok', objective_id: 'o1' })],
      objectives: [objective({ criteria_verified: false, completion_criteria: 'c' })],
      now,
    });
    expect(items.map((i) => i.kind)).toEqual(['unverified']);
  });

  it('stays quiet on routine completed work that has a result but no goal', () => {
    // Casual, completed, has a recorded result — this is the normal shape of
    // daily assistant work and must not fill the Attention rail.
    const items = collectAttention({
      tasks: [task({ state: 'completed', final_result: 'the answer' })],
      objectives: [],
      now,
    });
    expect(items).toHaveLength(0);
  });

  it('still reports a completed task whose result was never recorded', () => {
    const items = collectAttention({
      tasks: [task({ state: 'completed', final_result: null })],
      objectives: [],
      now,
    });
    expect(items.map((i) => i.kind)).toEqual(['unverified']);
  });

  it('stays quiet when a completed goal is fully verified', () => {
    const items = collectAttention({
      tasks: [task({ state: 'completed', final_result: 'ok', objective_id: 'o1' })],
      objectives: [objective({ criteria_verified: true, completion_criteria: 'c' })],
      now,
    });
    expect(items).toHaveLength(0);
  });

  it('labels a blocked plan step as step-level and links to the goal', () => {
    const items = collectAttention({
      tasks: [],
      objectives: [
        objective({
          steps: [
            {
              id: 1,
              objective_id: 'o1',
              step_order: 1,
              description: 'Collect inputs',
              status: 'blocked',
              linked_task_id: null,
              result_summary: null,
              blocker_reason: 'waiting on vendor',
              created_at: '2026-10-19T10:00:00',
              updated_at: '2026-10-19T10:00:00',
            },
          ],
        }),
      ],
      now,
    });
    expect(items[0].kind).toBe('blocked_step');
    expect(items[0].target).toEqual({ kind: 'goal', id: 'o1' });
    expect(items[0].derived).toBe(true);
  });

  it('does not show historical blocked steps from abandoned or completed goals', () => {
    const step = {
      id: 10, objective_id: 'o1', step_order: 1, description: 'Old dependency',
      status: 'blocked' as const, linked_task_id: null,
      result_summary: null, blocker_reason: 'old blocker',
      created_at: '2026-10-19T10:00:00', updated_at: '2026-10-19T10:00:00',
    };
    const items = collectAttention({
      tasks: [],
      objectives: [
        objective({ id: 'abandoned', status: 'abandoned', steps: [step] }),
        objective({ id: 'completed', status: 'completed', steps: [step] }),
      ],
      now,
    });
    expect(items).toHaveLength(0);
    const paused = collectAttention({
      tasks: [], objectives: [objective({ status: 'paused', steps: [step] })], now,
    });
    expect(paused.map((item) => item.kind)).toEqual(['blocked_step']);
  });

  it('orders approvals ahead of failures, unverified and stalls', () => {
    const items = collectAttention({
      tasks: [
        task({ id: 'stall', state: 'running', updated_at: '2026-10-19T09:00:00' }),
        task({ id: 'fail', state: 'failed', terminal_reason: 'boom' }),
        task({ id: 'appr', state: 'waiting_for_user', pending_question: 'which one?', waiting_kind: 'permission' }),
        task({
          id: 'unver',
          state: 'completed',
          final_result: 'x',
          objective_id: 'o1',
        }),
      ],
      objectives: [objective({ criteria_verified: false, completion_criteria: 'c' })],
      now,
    });
    expect(items.map((i) => i.kind)).toEqual([
      'approval',
      'failure',
      'unverified',
      'stalled',
    ]);
  });

  it('produces nothing for routine healthy work', () => {
    // Must be genuinely fresh: a stale running task would (correctly) be
    // reported as a suspected stall.
    const items = collectAttention({
      tasks: [task({ state: 'running', updated_at: new Date().toISOString() })],
      now: Date.now(),
    });
    expect(items).toHaveLength(0);
  });

  it('uses a fixed default stall window rather than per-task guessing', () => {
    const justUnder = Date.now() - SUSPECTED_STALL_MS + 60_000;
    const items = collectAttention({
      tasks: [task({ state: 'running', updated_at: new Date(justUnder).toISOString() })],
      now: Date.now(),
    });
    expect(items).toHaveLength(0);
  });
});