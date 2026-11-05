import { describe, expect, it } from 'vitest';
import { conversationTasks, nextSpokenReply } from './firstConversation';
import type { Task } from '../types';

const task = (id: string, state: Task['state'], time = '2026-10-22T00:00:00Z'): Task => ({
  id, state, original_request: 'Hello Synth', normalized_goal: null, version: 1,
  active_action_id: null, pending_question: null, final_result: null,
  created_at: time, updated_at: time, terminal_reason: null,
});

describe('first message and reply', () => {
  it('shows queued and running requests before a reply exists, in conversation order', () => {
    const first = task('first', 'created');
    const second = task('second', 'running', '2026-10-22T00:01:00Z');
    expect(conversationTasks([second, first], new Set())).toEqual([first, second]);
  });
  it('keeps approval requests pinned rather than duplicating them', () => {
    expect(conversationTasks([task('approval', 'waiting_for_user')], new Set(['approval']))).toEqual([]);
  });
  it('speaks only a completed reply sent in this conversation', () => {
    const old = { ...task('old', 'completed'), final_result: 'An old reply' };
    const queued = task('new', 'running');
    expect(nextSpokenReply([old, queued], new Set(['new']), new Set())).toBeUndefined();
    const completed = { ...queued, state: 'completed' as const, final_result: 'Hello, how can I help?' };
    expect(nextSpokenReply([old, completed], new Set(['new']), new Set())).toEqual(completed);
    expect(nextSpokenReply([completed], new Set(['new']), new Set(['new']))).toBeUndefined();
  });
  it('does not narrate failed, cancelled, or blank responses as successful replies', () => {
    for (const state of ['failed', 'cancelled', 'running'] as const) {
      expect(nextSpokenReply([{ ...task('new', state), final_result: 'partial' }], new Set(['new']), new Set())).toBeUndefined();
    }
    expect(nextSpokenReply([{ ...task('new', 'completed'), final_result: ' ' }], new Set(['new']), new Set())).toBeUndefined();
  });

  it('shows a background task result after its initiating conversation', () => {
    const background = {
      ...task('background', 'completed', '2026-10-22T00:00:00Z'),
      interaction_type: 'task',
      updated_at: '2026-10-22T00:02:00Z',
      final_result: 'Background work finished.',
    };
    const initiating = {
      ...task('conversation', 'completed', '2026-10-22T00:01:00Z'),
      interaction_type: 'CONVERSATION',
      final_result: "I'll queue that background work.",
    };
    expect(conversationTasks([background, initiating], new Set())).toEqual([initiating, background]);
  });

  it('does not change tie ordering by inventing timestamps when a conversation and task share one instant', () => {
    const taskAtInstant = { ...task('task', 'completed', '2026-10-22T00:00:00Z'), interaction_type: 'task', updated_at: '2026-10-22T00:00:00Z' };
    const conversationAtInstant = { ...task('conversation', 'completed', '2026-10-22T00:00:00Z'), interaction_type: 'CONVERSATION' };
    expect(conversationTasks([taskAtInstant, conversationAtInstant], new Set())).toEqual([conversationAtInstant, taskAtInstant]);
  });
});
