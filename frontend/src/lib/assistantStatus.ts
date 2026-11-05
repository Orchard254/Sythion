import type { Task, Tone } from '../types';

/**
 * Assistant status model.
 *
 * One pure function turns the application's real signals into a single
 * assistant state. Nothing here invents activity: every state is justified by
 * a value that actually arrived from the backend, from the voice service, or
 * from the local transport.
 *
 * The eight states required of the Assistant surface map to real sources:
 *
 *   idle        transport connected, nothing in flight
 *   listening   useVoiceCapture holds a live microphone session
 *   speaking    the sidecar VAD reported is_speaking on a real audio frame
 *   thinking    the user submitted and we are waiting for the task record
 *   executing   a task exists and is created/running
 *   awaiting    a task is waiting_for_user or interrupted with a question —
 *               this is the safety-critical state and outranks everything
 *               except an actual error
 *   offline     preview mode, or no backend attached
 *   error       a real error surfaced from voice or the task query
 *
 * `speaking` means the *user* is speaking. The backend exposes
 * POST /voice/speak, which returns only { success } and reports no playback
 * events, so there is no honest way to show "Sythion is speaking" — and we
 * therefore do not pretend to have one.
 */

export type AssistantState =
  | 'idle'
  | 'listening'
  | 'speaking'
  | 'thinking'
  | 'executing'
  | 'awaiting'
  | 'offline'
  | 'error';

export interface AssistantStatus {
  state: AssistantState;
  tone: Tone;
  /** Short, factual label. Must not imply work that is not happening. */
  label: string;
  /** One line of context, or '' when the label already says everything. */
  detail: string;
  /**
   * True only when the state describes real backend activity. Preview mode is
   * always false. Anything that makes an activity claim must gate on this.
   */
  live: boolean;
  /** The task responsible for the state, when the state came from a task. */
  taskId: string | null;
  /**
   * Normalised microphone level in [0, 1], or null when nothing is measuring
   * one. The sidecar returns no amplitude, so this is null today. It exists so
   * a future analyser can drive animation without changing the interface —
   * and components must render identically when it is null.
   */
  audioLevel: number | null;
}

export interface AssistantStatusInput {
  dataMode: 'live' | 'preview';
  isConnected: boolean;
  isListening: boolean;
  /** Real VAD flag from the audio-frame response. */
  isSpeaking: boolean;
  /** True from the moment the user submits until the task record appears. */
  submitting: boolean;
  tasks: Task[];
  error?: string | null;
  /** Clamped to [0, 1]. Null when no analyser is attached. */
  audioLevel?: number | null;
}

/**
 * A task is awaiting its owner purely because of its state. It must NOT also
 * require a `pending_question`: a live backend can report `waiting_for_user`
 * with an empty question, and classifying on the presence of the text made
 * such a task vanish from the conversation entirely — a real approval request
 * rendered as nothing at all.
 */
export function isAwaitingTask(task: Task): boolean {
  return task.state === 'waiting_for_user' || task.state === 'interrupted';
}

/**
 * The best available explanation of why a task is blocked.
 *
 * Two different fields carry it: `waiting_for_user` uses the backend's
 * question in `pending_question`, while `interrupted` records the
 * authorization hold in `terminal_reason` and often has no question at all.
 * Conflating them would report a task held for approval as "this did not
 * finish", which is exactly the safety information this screen must not
 * distort. Falls back to the task's own goal so the card is never blank.
 */
export function awaitingReason(task: Task): string {
  if (!isAwaitingTask(task)) return '';
  return (
    task.pending_question ??
    task.terminal_reason ??
    task.normalized_goal ??
    task.original_request
  );
}

/**
 * Tasks that are blocked on the owner, oldest first. They stay pinned above
 * the conversation: an approval request hidden behind a tab or below the fold
 * is a safety problem, not a UX one.
 */
export function findAwaitingTasks(tasks: Task[]): Task[] {
  return tasks
    .filter(isAwaitingTask)
    .sort((a, b) => new Date(a.updated_at).getTime() - new Date(b.updated_at).getTime());
}

/** Tasks currently doing work, newest first. */
export function findRunningTask(tasks: Task[]): Task | null {
  const running = tasks
    .filter((t) => t.state === 'running' || t.state === 'created')
    .sort((a, b) => Number(b.state === 'running') - Number(a.state === 'running') || new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime());
  return running[0] ?? null;
}

function clampLevel(level: number | null | undefined): number | null {
  if (typeof level !== 'number' || Number.isNaN(level)) return null;
  return Math.max(0, Math.min(1, level));
}

export function deriveAssistantStatus(input: AssistantStatusInput): AssistantStatus {
  const {
    dataMode,
    isConnected,
    isListening,
    isSpeaking,
    submitting,
    tasks,
    error,
  } = input;

  const audioLevel = clampLevel(input.audioLevel);
  const preview = dataMode === 'preview';

  // A real error outranks everything: the user needs to know why.
  if (error) {
    return {
      state: 'error',
      tone: 'danger',
      label: 'Something went wrong',
      detail: error,
      live: !preview,
      taskId: null,
      audioLevel: null,
    };
  }

  // Offline outranks the voice and task states, because in preview mode none of
  // them can truthfully occur. Labelling demo data as "executing" is exactly the
  // contradiction this screen must not have.
  if (preview || !isConnected) {
    return {
      state: 'offline',
      tone: 'warn',
      label: preview ? 'Preview mode' : 'Backend offline',
      detail: preview
        ? 'Nothing here runs. Messages are not sent and no task will be created.'
        : 'Reconnect to a Sythion runtime to send messages.',
      live: false,
      taskId: null,
      audioLevel: null,
    };
  }

  const awaiting = findAwaitingTasks(tasks)[0] ?? null;
  if (awaiting) {
    return {
      state: 'awaiting',
      tone: 'warn',
      label: awaiting.state === 'interrupted' ? 'Held for your approval' : 'Needs your answer',
      detail: awaitingReason(awaiting),
      live: true,
      taskId: awaiting.id,
      audioLevel: null,
    };
  }

  // `speaking` is only meaningful while a microphone session is open; the VAD
  // flag can otherwise be stale from a frame that arrived just before teardown.
  if (isListening && isSpeaking) {
    return {
      state: 'speaking',
      tone: 'accent',
      label: 'Listening — you are speaking',
      detail: '',
      live: true,
      taskId: null,
      audioLevel,
    };
  }

  if (isListening) {
    return {
      state: 'listening',
      tone: 'accent',
      label: 'Listening',
      detail: '',
      live: true,
      taskId: null,
      audioLevel,
    };
  }

  if (submitting) {
    return {
      state: 'thinking',
      tone: 'accent',
      label: 'Sending…',
      detail: '',
      live: true,
      taskId: null,
      audioLevel: null,
    };
  }

  const running = findRunningTask(tasks);
  if (running) {
    return {
      state: 'executing',
      tone: 'info',
      label: running.state === 'created' ? 'Queued — not started' : 'Working on it',
      detail: running.normalized_goal || running.original_request,
      live: true,
      taskId: running.id,
      audioLevel: null,
    };
  }

  return {
    state: 'idle',
    tone: 'idle',
    label: 'Ready',
    detail: '',
    live: true,
    taskId: null,
    audioLevel: null,
  };
}

/** True for states where the microphone session is genuinely open. */
export function isVoiceState(state: AssistantState): boolean {
  return state === 'listening' || state === 'speaking';
}

/** True when the microphone button should read as active. */
export function isVoiceActive(status: AssistantStatus): boolean {
  return isVoiceState(status.state);
}

/** True when the user cannot send anything. */
export function isComposerBlocked(status: AssistantStatus): boolean {
  return status.state === 'offline';
}
