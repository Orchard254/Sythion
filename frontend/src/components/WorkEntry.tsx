import { useRef, useState } from 'react';
import { Button } from './ui';
import type { Task } from '../types';

/**
 * User-initiated task creation, separate from normal conversation.
 *
 * This UI confirms the user's intent; it does not authorize tool calls,
 * bypass backend task classification, or enable background autonomy.
 * The backend can still classify a request as conversation (no Task row).
 */
export type CreateWorkTask = (
  request: string,
  interactionHint?: 'conversation' | 'task',
  idempotencyKey?: string
) => Promise<Task>;

const TEXT_KEY = 'sythion.taskComposer.pendingText';
const ID_KEY = 'sythion.taskComposer.pendingId';

function newRequestId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return 'work-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2);
}

/** Reject incomplete storage or a different unresolved task identity. */
function reserveRequestId(text: string): string {
  if (typeof sessionStorage === 'undefined') {
    throw new Error('Task request safety storage is unavailable.');
  }
  const savedText = sessionStorage.getItem(TEXT_KEY);
  const savedId = sessionStorage.getItem(ID_KEY);
  if ((savedText && !savedId) || (!savedText && savedId)) {
    throw new Error('A previous task identity is incomplete.');
  }
  if (savedText && savedId && savedText !== text) {
    throw new Error('A previous task outcome is unresolved.');
  }
  const id = savedText === text && savedId ? savedId : newRequestId();
  sessionStorage.setItem(TEXT_KEY, text);
  sessionStorage.setItem(ID_KEY, id);
  if (sessionStorage.getItem(TEXT_KEY) !== text || sessionStorage.getItem(ID_KEY) !== id) {
    throw new Error('Task request identity could not be stored.');
  }
  return id;
}

/** Clear only the exact reservation confirmed by the backend. */
function clearRequestId(text: string, id: string): boolean {
  try {
    if (sessionStorage.getItem(TEXT_KEY) !== text ||
        sessionStorage.getItem(ID_KEY) !== id) return false;
    sessionStorage.removeItem(TEXT_KEY);
    sessionStorage.removeItem(ID_KEY);
    if (sessionStorage.getItem(TEXT_KEY) === null &&
        sessionStorage.getItem(ID_KEY) === null) return true;
    // Some constrained webviews may silently ignore removeItem.
    sessionStorage.setItem(TEXT_KEY, '');
    sessionStorage.setItem(ID_KEY, '');
    return sessionStorage.getItem(TEXT_KEY) === '' &&
      sessionStorage.getItem(ID_KEY) === '';
  } catch {
    return false;
  }
}

export function WorkEntry({
  text,
  disabled,
  createTask,
  onCreated,
}: {
  text: string;
  disabled: boolean;
  createTask: CreateWorkTask;
  onCreated: (task: Task, submittedText: string) => void;
}) {
  const [candidate, setCandidate] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [conversationOnly, setConversationOnly] = useState<string | null>(null);
  const requestText = text.trim();
  const hasChanged = candidate !== null && candidate !== requestText;
  const ready = candidate !== null && !hasChanged && !disabled &&
    !busy && requestText.length > 0 && conversationOnly !== requestText;

  const confirm = async () => {
    if (!ready || !candidate || busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setFeedback(null);
    let id: string;
    try {
      id = reserveRequestId(candidate);
    } catch {
      setFeedback('Task safety storage is unavailable or a previous task is unresolved. Review Tasks before starting another request.');
      setBusy(false);
      busyRef.current = false;
      return;
    }
    try {
      // The backend may start execution immediately. Normal permission,
      // lease, intent and evidence gates remain enforced server-side.
      const result = await createTask(candidate, 'task', id);
      if (result &&
          (result.interaction_type?.toUpperCase() === 'CONVERSATION' ||
            (typeof result.id === 'string' && result.id.startsWith('conv-')))) {
        const cleared = clearRequestId(candidate, id);
        setConversationOnly(candidate);
        setFeedback(cleared
          ? 'The backend treated this as conversation. No executable task was created. Change the request to specify the work.'
          : 'No executable task was created. Task safety storage could not be cleared; review it before retrying.');
        return;
      }
      if (!result || typeof result.id !== 'string' || !result.id ||
          typeof result.state !== 'string') {
        throw new Error('Task creation response is missing required evidence.');
      }
      const cleared = clearRequestId(candidate, id);
      setCandidate(null);
      setFeedback(cleared ? null :
        'Task was recorded, but its local request identity could not be cleared. Review Tasks before making another request.');
      onCreated(result, candidate);
    } catch {
      // A failed HTTP exchange or lost connection can be an unknown outcome.
      // Never automatically retry or generate another id for the same text.
      setFeedback('Task outcome is not confirmed. Check Tasks before explicitly retrying. The same request identity will be reused.');
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  };

  return (
    <section className="ac-work" aria-label="Create executable work">
      {!candidate ? (
        <div className="ac-work__entry">
          <Button
            variant="subtle"
            size="sm"
            icon="layers"
            disabled={disabled || !requestText}
            aria-label="Create task from message"
            onClick={() => {
              if (disabled || !requestText) return;
              setCandidate(requestText);
              setFeedback(null);
            }}
          >
            Create task
          </Button>
          <span>Separate from Send message. Execution may start after confirmation and is subject to backend permissions.</span>
        </div>
      ) : (
        <div className="ac-work__confirm" role="group" aria-label="Confirm executable task">
          <strong>Start work with Synth?</strong>
          <p className="ac-work__request">{candidate}</p>
          <p>Creating a task can start execution immediately. Backend tool permissions and approval rules still apply.</p>
          {hasChanged && <p role="status">The message changed. Cancel and review the new draft before creating a task.</p>}
          {conversationOnly === requestText &&
            <p role="status">This request was already classified as conversation. Edit it to request a specific action.</p>}
          <div className="ac-work__controls">
            <Button
              variant="primary"
              size="sm"
              disabled={!ready}
              aria-label="Confirm create task"
              onClick={() => { void confirm(); }}
            >
              {busy ? 'Creating…' : 'Confirm task'}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              disabled={busy}
              aria-label="Cancel task creation"
              onClick={() => { setCandidate(null); setFeedback(null); }}
            >Cancel</Button>
          </div>
        </div>
      )}
      {feedback && <p className="ac-work__feedback" role="status">{feedback}</p>}
    </section>
  );
}
