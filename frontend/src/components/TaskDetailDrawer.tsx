import { useState } from 'react';
import { Icon } from './icons';
import { Badge, Bar, Button, DataBadge, Drawer, KV, LoadingBlock, Notice, Rail } from './ui';
import { useActions, useTaskEvents, useDataMode } from '../hooks/useBackend';
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

export function TaskDetailDrawer({
  task,
  origin,
  onClose,
  onChanged,
}: {
  task: Task;
  origin: 'live' | 'demo';
  onClose: () => void;
  onChanged?: () => void;
}) {
  const events = useTaskEvents(task.id);
  const actions = useActions();
  const dataMode = useDataMode();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reply, setReply] = useState('');

  const tone = taskTone(task.state);
  const running = task.state === 'running' || task.state === 'waiting_for_user';
  const timeline = [...events.data ?? []].sort((a, b) => a.sequence_num - b.sequence_num);

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      onChanged?.();
      events.refetch();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Action failed');
    } finally {
      setBusy(false);
    }
  };

  const footer =
    dataMode === 'live' && running ? (
      <Button
        variant="danger"
        icon="stop"
        disabled={busy}
        onClick={() => void run(() => actions.cancelTask(task.id))}
      >
        {busy ? 'Cancelling…' : 'Interrupt task'}
      </Button>
    ) : dataMode === 'live' && task.state === 'interrupted' ? (
      <Button
        variant="primary"
        icon="play"
        disabled={busy}
        onClick={() => void run(() => actions.resumeTask(task.id))}
      >
        {busy ? 'Resuming…' : 'Resume task'}
      </Button>
    ) : undefined;

  return (
    <Drawer
      title={task.normalized_goal || task.original_request}
      subtitle={
        <span className="row" style={{ gap: 8 }}>
          <Badge tone={tone}>{taskLabel(task.state)}</Badge>
          <span className="t-mono">{task.id}</span>
          <DataBadge origin={origin} />
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
          {dataMode === 'live' && (
            <div className="row" style={{ marginTop: 9, gap: 7 }}>
              <input
                className="input"
                style={{ flex: 1 }}
                value={reply}
                placeholder="Your answer…"
                aria-label="Answer to Sythion"
                onChange={(e) => setReply(e.target.value)}
              />
              <Button
                variant="primary"
                size="sm"
                disabled={!reply.trim() || busy}
                onClick={() => void run(async () => actions.replyToTask(task.id, reply.trim()))}
              >
                Send
              </Button>
            </div>
          )}
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
              Held for authorization
            </span>
            <span>{task.terminal_reason}</span>
          </div>
        </Notice>
      )}

      <KV
        items={[
          { k: 'State', v: taskLabel(task.state) },
          { k: 'Started', v: dateTime(task.created_at) },
          { k: 'Updated', v: `${dateTime(task.updated_at)} · ${relTime(task.updated_at)}` },
          { k: 'Elapsed', v: duration(task.created_at, task.state === 'completed' || task.state === 'failed' ? task.updated_at : null) },
          { k: 'Current action', v: task.active_action_id || '—' },
          { k: 'Objective', v: task.objective_id ?? '—' },
          { k: 'Version', v: `v${task.version}` },
        ]}
      />

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
                  <div className="row row--between" style={{ gap: 8 }}>
                    <span className="t-body" style={{ color: 'var(--text-0)', fontSize: 12.5 }}>
                      {humanize(ev.event_type)}
                    </span>
                    <span className="t-faint t-mono">#{ev.sequence_num}</span>
                  </div>
                  <div className="list__meta">
                    <Icon name="clock" size={11} />
                    {clockTime(ev.created_at)}
                    {Object.keys(ev.event_data ?? {}).length > 0 && (
                      <span className="t-mono">
                        {Object.entries(ev.event_data)
                          .slice(0, 3)
                          .map(([k, v]) => `${k}=${shortValue(v)}`)
                          .join('  ')}
                      </span>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
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