import type { Task } from '../types';

/** Keep the user's message visible from the first durable task response. */
export function conversationTasks(tasks: Task[], pinnedIds: Set<string>): Task[] {
  return tasks.filter((task) => !pinnedIds.has(task.id)).slice().sort((a, b) =>
    new Date(a.created_at).getTime() - new Date(b.created_at).getTime());
}

/** Only narrate completed replies submitted in this mounted conversation. */
export function nextSpokenReply(tasks: Task[], submitted: Set<string>, narrated: Set<string>): Task | undefined {
  return conversationTasks(tasks, new Set()).find((task) =>
    submitted.has(task.id) && !narrated.has(task.id) && task.state === 'completed' && Boolean(task.final_result?.trim()));
}
