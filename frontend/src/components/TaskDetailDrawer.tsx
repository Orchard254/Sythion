import { useState, type ReactNode } from 'react';
import { Icon } from './icons';
import { Badge, Bar, Button, Drawer, KV, LoadingBlock, Notice, Rail } from './ui';
import { useActions, useTaskEvents, useTaskResumePreflight } from '../hooks/useBackend';
import { taskService } from '../services/api';
import {
  mayOfferExplicitResume, recoveryStatusLabel, recoveryVerificationSteps,
} from '../lib/recoveryPresentation';
import { clockTime, dateTime, duration, humanize, relTime, taskLabel, taskTone } from '../lib/format';
import type { Task } from '../types';

const EVENT_TONE: Record<string, string> = {
  'task.created': 'idle',
  'task.completed': 'ok',
  'task.failed': 'danger',
  'task.cancelled': 'idle',
  'plan.created': 'info',
  'action.started': 'info',
  'action.completed': 'ok',
  'action.failed': 'danger',
  'question.asked': 'warn',
};

/**
 * Single collapsed "Advanced / for support" disclosure for this drawer. Raw
 * backend fields (task id, versions, evidence hashes, event-data dumps) live
 * here so the default view stays meaningful to an owner. Closed by default.
 */
function AdvancedDetails({ id, children }: { id: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="stack stack--tight" style={{ marginTop: 12 }}>
      <Button
        size="sm"
        variant="ghost"
        aria-expanded={open}
        data-testid={`advanced-toggle-${id}`}
        onClick={() => setOpen(!open)}
      >
        {open ? 'Hide advanced options' : 'Advanced / for support'}
      </Button>
      {open && (
        <div className="stack stack--tight" data-testid={`advanced-${id}`}>
          {children}
        </div>
      )}
    </div>
  );
}

export function TaskDetailDrawer({
  task,
  onClose,
  onChanged,
  onDismissFailed,
}: {
  task: Task;
  onClose: () => void;
  onChanged?: () => void;
  onDismissFailed?: () => void;
}) {
  const events = useTaskEvents(task.id);
  const actions = useActions();
  const resumeCandidate = task.state === 'interrupted' || task.state === 'paused';
  const resumePreflight = useTaskResumePreflight(resumeCandidate ? task.id : null);
  const resumeReady = resumeCandidate && !resumePreflight.isError && mayOfferExplicitResume(resumePreflight.data);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reply, setReply] = useState('');
  const [replyVersion, setReplyVersion] = useState<number | null>(null);

  const tone = taskTone(task.state);
  const running = task.state === 'created' || task.state === 'running' || task.state === 'waiting_for_user';
  // Only explicitly typed clarification waits can be resumed with a free-text
  // answer. Permission and unclassified/model questions stay visible but are
  // not routed through the clarification reply path.
  const canReplyByText = task.state === 'waiting_for_user' && task.waiting_kind === 'clarification';
  const timeline = [...events.data ?? []].sort((a, b) => a.sequence_num - b.sequence_num);

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      onChanged?.();
      events.refetch();
      if (resumeCandidate) resumePreflight.refetch();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Action failed');
    } finally {
      setBusy(false);
    }
  };

  const revision = resumePreflight.data?.review_binding;
  const hasSnapshot = revision?.binding_status === 'read_only_observation' &&
    /^sha256:[0-9a-f]{64}$/.test(revision.revision);
  // The preflight hook already re-polls on its own (staleMs: 0, 7s interval).
  // Derive a read-only freshness label from that poll's timestamp — no manual
  // recheck control and no extra timer of our own.
  const lastCheckedAt = resumePreflight.dataUpdatedAt;
  const lastCheckedSeconds = lastCheckedAt > 0
    ? Math.max(0, Math.floor((Date.now() - lastCheckedAt) / 1000))
    : null;

  const footer =
    running ? (
      <Button
        variant="danger"
        icon="stop"
        disabled={busy}
        onClick={() => void run(() => actions.cancelTask(task.id))}
      >
        {busy ? 'Cancelling…' : task.state === 'created' ? 'Cancel queued request' : 'Interrupt task'}
      </Button>
    ) : resumeCandidate ? (
      <div className="row row--between" style={{ gap: 8 }}>
        <span className="t-faint" style={{ fontSize: 12 }}>
          {resumePreflight.isFetching && !resumePreflight.data
            ? 'Checking recovery…'
            : lastCheckedSeconds === null
              ? 'Recovery status not yet checked'
              : lastCheckedSeconds === 0
                ? 'Recovery checked just now'
                : `Recovery checked ${lastCheckedSeconds}s ago`}
        </span>
        <Button
          variant="primary"
          icon="play"
          disabled={busy || !actions.live || resumePreflight.isFetching || resumePreflight.isError || !resumeReady}
          onClick={() => void run(async () => {
            // A displayed snapshot can become stale between refresh and click.
            // Re-read immediately before sending the explicit resume request.
            const latest = await taskService.resumePreflight(task.id);
            if (!mayOfferExplicitResume(latest)) {
              resumePreflight.refetch();
              throw new Error('Recovery conditions changed. Review the latest preflight before resuming.');
            }
            try {
              await actions.resumeTask(task.id);
            } catch {
              resumePreflight.refetch();
              throw new Error('The backend refused the resume request. Review the latest recovery status.');
            }
          })}
        >
          {busy ? 'Resuming…' : 'Resume task'}
        </Button>
      </div>
    ) : task.state === 'failed' ? (
      <Button variant="subtle" disabled={busy} onClick={() => {
        if (onDismissFailed) onDismissFailed();
        else void run(async () => { await taskService.acknowledgeFailure(task.id); onClose(); });
      }}>
        Dismiss failed task
      </Button>
    ) : undefined;

  return (
    <Drawer
      title={task.normalized_goal || task.original_request}
      subtitle={
        <span className="row" style={{ gap: 8 }}>
          <Badge tone={tone}>{taskLabel(task.state)}</Badge>
        </span>
      }
      onClose={onClose}
      footer={footer}
      width={560}
    >
      {error && (
        <Notice tone="danger" icon="alert">
          {error}
        </Notice>
      )}

      {resumeCandidate && (
        <Notice tone={resumeReady ? 'info' : 'warn'} icon={resumeReady ? 'info' : 'lock'}>
          <div className="stack stack--tight" role="status" aria-label="Recovery preflight">
            <strong>Recovery review</strong>
            <span>
              {resumePreflight.isError
                ? 'Recovery check failed. Resume is disabled until it can be verified.'
                : resumePreflight.isLoading && !resumePreflight.data
                  ? 'Checking the latest persisted recovery evidence…'
                  : recoveryStatusLabel(resumePreflight.data)}
            </span>
            {resumePreflight.data?.review && (
              <>
                {recoveryVerificationSteps(resumePreflight.data).length > 0 && (
                  <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
                    {recoveryVerificationSteps(resumePreflight.data).map((step, index) => (
                      <li key={index}>{step}</li>
                    ))}
                  </ul>
                )}
                <span className="t-faint">
                  Read-only guidance. This is not an approval or a guarantee that resumption is safe.
                </span>
              </>
            )}
            {!resumePreflight.data && (
              <span className="t-faint">
                This task cannot be resumed unless the recovery service returns a consistent preflight.
              </span>
            )}
          </div>
        </Notice>
      )}

      <div className="stack stack--tight">
        <span className="field__label">Requested</span>
        <p className="t-body clamp-wrap">{task.original_request}</p>
      </div>

      {task.state === 'running' && (
        <div className="stack stack--tight">
          <div className="row row--between">
            <span className="field__label">In progress</span>
            <span className="t-faint">{duration(task.created_at, task.updated_at)}</span>
          </div>
          <Bar indeterminate />
        </div>
      )}

      {task.pending_question && (
        <Notice tone="warn" icon="alert">
          <div className="stack stack--tight">
            <span className="t-title" style={{ fontSize: 12 }}>
              Sythion needs an answer
            </span>
            <span>{task.pending_question}</span>
          </div>
          {canReplyByText && (
            <div className="row" style={{ marginTop: 9, gap: 7 }}>
              <input
                className="input"
                style={{ flex: 1 }}
                value={reply}
                placeholder="Your answer…"
                aria-label="Answer to Sythion"
                disabled={busy}
                onChange={(e) => { if (!reply) setReplyVersion(task.version); setReply(e.target.value); }}
              />
              <Button
                variant="primary"
                size="sm"
                disabled={!reply.trim() || busy || replyVersion !== task.version}
                onClick={() => void run(async () => { await actions.replyToTask(task.id, reply.trim(), false, replyVersion ?? undefined); setReply(''); setReplyVersion(null); })}
              >
                Send
              </Button>
            </div>
          )}
          {!canReplyByText && (
            <p role="status" style={{ marginTop: 9 }}>
              This wait cannot be resumed by typing an answer here.
            </p>
          )}
          {reply && replyVersion !== task.version && canReplyByText && <p role="alert">This question changed. Review it before using your saved answer. <Button size="sm" variant="subtle" onClick={() => setReplyVersion(task.version)}>Use this answer for the current question</Button></p>}
        </Notice>
      )}

      {task.final_result && (
        <div className="stack stack--tight">
          <span className="field__label">Result</span>
          <div className="msg__bubble" style={{ maxWidth: 'none' }}>
            {task.final_result}
          </div>
        </div>
      )}

      {task.state === 'failed' && task.terminal_reason && (
        <Notice tone="danger" icon="alert">
          <div className="stack stack--tight">
            <span className="t-title" style={{ fontSize: 12 }}>
              Failed
            </span>
            <span>{task.terminal_reason}</span>
          </div>
        </Notice>
      )}

      {task.state === 'interrupted' && task.terminal_reason && (
        <Notice tone="warn" icon="lock">
          <div className="stack stack--tight">
            <span className="t-title" style={{ fontSize: 12 }}>
              {task.waiting_kind === 'permission' ? 'Held for authorization' : 'Execution interrupted'}
            </span>
            <span>{task.terminal_reason}</span>
          </div>
        </Notice>
      )}

      <div className="stack stack--tight">
        <div className="row row--between">
          <span className="field__label">Event timeline</span>
          <span className="t-faint">{timeline.length} events</span>
        </div>
        {events.isLoading ? (
          <LoadingBlock rows={3} />
        ) : timeline.length === 0 ? (
          <p className="t-faint" style={{ padding: '8px 0' }}>
            No events recorded yet.
          </p>
        ) : (
          <div className="timeline">
            {timeline.map((ev, i) => (
              <div className="timeline__item" key={ev.sequence_num}>
                <div className="timeline__marker">
                  <Rail tone={(EVENT_TONE[ev.event_type] ?? 'idle') as never} />
                  {i < timeline.length - 1 && <span className="timeline__line" />}
                </div>
                <div className="timeline__body">
                  <span className="t-body" style={{ color: 'var(--text-0)', fontSize: 12.5 }}>
                    {humanize(ev.event_type)}
                  </span>
                  <div className="list__meta">
                    <Icon name="clock" size={11} />
                    {clockTime(ev.created_at)}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <AdvancedDetails id="task-detail">
        <KV
          items={[
            { k: 'Task id', v: <span className="t-mono">{task.id}</span> },
            { k: 'State', v: taskLabel(task.state) },
            { k: 'Started', v: dateTime(task.created_at) },
            { k: 'Updated', v: `${dateTime(task.updated_at)} · ${relTime(task.updated_at)}` },
            { k: 'Elapsed', v: duration(task.created_at, task.state === 'completed' || task.state === 'failed' ? task.updated_at : null) },
            { k: 'Current action', v: task.active_action_id || '—' },
            { k: 'Objective', v: task.objective_id ?? '—' },
            { k: 'Version', v: `v${task.version}` },
          ]}
        />
        {hasSnapshot && revision && (
          <div className="stack stack--tight">
            <span className="t-faint t-mono" title="Snapshot revision, not an approval">
              Evidence snapshot {revision.revision.slice(7, 19)}
              {' · '}Task v{revision.task_version}
              {' · '}Event #{revision.last_event_sequence}
            </span>
            <span className="t-faint">
              The backend will verify again before changing task state.
            </span>
          </div>
        )}
        <span className="t-faint">Raw event data from the task event ledger.</span>
        {timeline.length === 0 ? (
          <span className="t-faint">No raw event data recorded.</span>
        ) : (
          <div className="stack stack--tight">
            {timeline.map((ev) => (
              <div key={`raw-${ev.sequence_num}`} className="stack stack--tight">
                <span className="t-mono">{`#${ev.sequence_num} ${humanize(ev.event_type)}`}</span>
                {Object.keys(ev.event_data ?? {}).length > 0 && (
                  <span className="t-mono">
                    {Object.entries(ev.event_data)
                      .slice(0, 3)
                      .map(([k, v]) => `${k}=${shortValue(v)}`)
                      .join('  ')}
                  </span>
                )}
              </div>
            ))}
          </div>
        )}
      </AdvancedDetails>
    </Drawer>
  );
}

function shortValue(v: unknown): string {
  if (typeof v === 'string') return v.length > 28 ? `${v.slice(0, 28)}…` : v;
  if (typeof v === 'number' || typeof v === 'boolean') return String(v);
  if (v === null || v === undefined) return '—';
  try {
    const s = JSON.stringify(v);
    return s.length > 28 ? `${s.slice(0, 28)}…` : s;
  } catch {
    return '…';
  }
}
