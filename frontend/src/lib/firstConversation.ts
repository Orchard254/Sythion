import type { Task } from '../types';

/** Sort key for chronology. Background tasks are not shown at their scheduled
 *  time; their state changes (started/completed) happen after the conversation
 *  that initiated them, so their `updated_at` reflects that later event.
 *  Pure conversation exchanges only ever have a `created_at`. */
function chronologyTime(task: Task): number {
  const timestamp = task.interaction_type === 'CONVERSATION' ? task.created_at : task.updated_at;
  return new Date(timestamp || task.created_at).getTime();
}

/** Keep the user's message visible from the first durable task response. */
export function conversationTasks(tasks: Task[], pinnedIds: Set<string>): Task[] {
  return tasks
    .filter((task) => !pinnedIds.has(task.id))
    .slice()
    .sort((a, b) => {
      const timeDifference = chronologyTime(a) - chronologyTime(b);
      if (timeDifference !== 0) return timeDifference;
      // Keep an initiating conversation ahead of its tied background task
      // without inventing or changing either timestamp.
      const aConversation = a.interaction_type === 'CONVERSATION' ? 0 : 1;
      const bConversation = b.interaction_type === 'CONVERSATION' ? 0 : 1;
      if (aConversation !== bConversation) return aConversation - bConversation;
      return String(a.id).localeCompare(String(b.id));
    });
}

/** Only narrate completed replies submitted in this mounted conversation. */
export function nextSpokenReply(tasks: Task[], submitted: Set<string>, narrated: Set<string>): Task | undefined {
  return conversationTasks(tasks, new Set()).find((task) =>
    submitted.has(task.id) && !narrated.has(task.id) && task.state === 'completed' && Boolean(task.final_result?.trim()));
}