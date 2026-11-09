import { useMemo, useState } from 'react';
import { Icon } from '../components/icons';
import { GoalDetailDrawer } from '../components/GoalDetailDrawer';
import { TaskDetailDrawer } from '../components/TaskDetailDrawer';
import {
  Badge,
  Button,
  Panel,
  Rail,
  ScreenHead,
} from '../components/ui';
import { useAgent, useCapabilities, useHealth, useObjectives, useTasks } from '../hooks/useBackend';
import { dateTime, humanize, parseTimestamp, relTime, taskLabel, taskTone, OBJECTIVE_TONE } from '../lib/format';
import { useNavStore } from '../store/navStore';
import {
  assessCompletion,
  collectAttention,
  latestConfirmedStep,
  waitingCondition,
  type AttentionItem,
} from '../lib/observation';
import type { Objective } from '../types';

/* Operations — the Control Center landing view.
 *
 * It answers four questions, in order:
 *   1. What needs me?
 *   2. What is running?
 *   3. What finished?
 *   4. Where are my goals?
 *
 * System status is one plain-language line; readiness internals live on
 * System & Integrations. Every value comes from durable backend state. No
 * percentage, progress figure or health verdict is invented here.
 */

const RUNNING_STATES = new Set(['created', 'running', 'paused']);
const OPEN_STEP_STATES = new Set(['pending', 'ready', 'running', 'in_progress', 'blocked', 'failed']);

function resultSnippet(value: string | null): string {
  const text = value?.replace(/\s+/g, ' ').replace(/\*\*/g, '').trim();
  if (!text) return 'No result was recorded.';
  const sentence = text.split(/(?<=[.!?])\s+/)[0] ?? text;
  return sentence.length > 180 ? `${sentence.slice(0, 177)}…` : sentence;
}

export function OperationsScreen() {
  const tasks = useTasks();
  const objectives = useObjectives();
  const health = useHealth();
  const agent = useAgent();
  const capabilities = useCapabilities();

  const setRoute = useNavStore((s) => s.setRoute);

  const [openGoal, setOpenGoal] = useState<string | null>(null);
  const [openTask, setOpenTask] = useState<string | null>(null);

  const allTasks = useMemo(() => tasks.data ?? [], [tasks.data]);
  const allObjectives = useMemo(() => objectives.data ?? [], [objectives.data]);

  const attention = useMemo(
    () => collectAttention({ tasks: allTasks, objectives: allObjectives, now: Date.now() }),
    [allTasks, allObjectives]
  );

  const attentionTaskIds = useMemo(
    () => new Set(attention.map((item) => item.taskId).filter((id): id is string => Boolean(id))),
    [attention]
  );

  const runningTasks = useMemo(
    () =>
      allTasks
        .filter((t) => RUNNING_STATES.has(t.state) && !attentionTaskIds.has(t.id))
        .sort((a, b) => (parseTimestamp(b.updated_at) ?? 0) - (parseTimestamp(a.updated_at) ?? 0)),
    [allTasks, attentionTaskIds]
  );

  const finishedTasks = useMemo(() => {
    const objectiveById = new Map(allObjectives.map((o) => [o.id, o]));
    return [...allTasks]
      .filter((t) =>
        (t.state === 'completed' || t.state === 'cancelled') &&
        !attentionTaskIds.has(t.id)
      )
      .sort((a, b) => (parseTimestamp(b.updated_at) ?? 0) - (parseTimestamp(a.updated_at) ?? 0))
      .slice(0, 2)
      .map((t) => ({
        task: t,
        assessment: assessCompletion(t, t.objective_id ? objectiveById.get(t.objective_id) ?? null : null),
      }));
  }, [allTasks, allObjectives, attentionTaskIds]);

  const focusedGoals = useMemo(
    () =>
      [...allObjectives]
        .filter((o) => o.status === 'active' || o.status === 'paused')
        .sort((a, b) =>
          a.priority - b.priority ||
          (parseTimestamp(b.updated_at) ?? 0) - (parseTimestamp(a.updated_at) ?? 0)
        )
        .slice(0, 2),
    [allObjectives]
  );

  const objectiveById = useMemo(
    () => new Map(allObjectives.map((o) => [o.id, o])),
    [allObjectives]
  );

  const taskById = useMemo(() => new Map(allTasks.map((t) => [t.id, t])), [allTasks]);

  /* System status in plain language: only ever what the backend reported. */
  const systemStatus = useMemo(() => {
    const problems: string[] = [];
    const db = health.isError ? undefined : health.data?.database_connected;
    if (db === false) problems.push('database unreachable');
    const runtime = health.isError ? undefined : health.data?.status;
    if (runtime && runtime !== 'ok' && runtime !== 'healthy') problems.push('runtime degraded');
    const agentState = agent.data?.state;
    if (agentState && agentState !== 'active') problems.push(`agent ${agentState}`);
    const caps = capabilities.data ?? [];
    const unavailable = caps.filter((c) => c.status === 'UNAVAILABLE' || c.status === 'DISABLED').length;
    if (caps.length > 0 && unavailable > 0) {
      problems.push(`${unavailable} ${unavailable === 1 ? 'capability' : 'capabilities'} unavailable`);
    }
    if (problems.length) return { tone: 'warn' as const, text: problems.join(' · ') };
    if (caps.length === 0 || db === undefined || runtime === undefined || !agentState) {
      return { tone: 'unknown' as const, text: 'Status unknown' };
    }
    return { tone: 'ok' as const, text: 'Everything working' };
  }, [health.data, health.isError, agent.data, capabilities.data]);

  const summary = useMemo(() => {
    const hour = new Date().getHours();
    const greeting = hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening';
    const parts: string[] = [];
    if (attention.length) {
      parts.push(`${attention.length} ${attention.length === 1 ? 'thing needs' : 'things need'} your attention`);
    }
    if (runningTasks.length) {
      parts.push(`${runningTasks.length} ${runningTasks.length === 1 ? 'task is' : 'tasks are'} running`);
    }
    if (finishedTasks.length) {
      parts.push(`${finishedTasks.length} ${finishedTasks.length === 1 ? 'task has' : 'tasks have'} finished recently`);
    }
    if (!parts.length) return `${greeting}. Everything looks good.`;
    return `${greeting}. ${parts.join(' · ')}.`;
  }, [attention.length, runningTasks.length, finishedTasks.length]);

  return (
    <div className="screen screen--operations">
      <div className="screen__inner operations-layout">
        <ScreenHead
          eyebrow="Control Center"
          title="Operations"
          description={summary}
        />

        {/* 1. NEEDS YOU --------------------------------------------------- */}
        <section aria-label="Needs you" className="operations-needs">
          {attention.length > 0 ? (
            <div className="attention attention--rail operations-attention" role="region" aria-label="Needs attention">
              <div className="attention__rail-head">
                <span className="field__label">Needs you</span>
                <span className="t-faint">{attention.length} item{attention.length === 1 ? '' : 's'}</span>
              </div>
              <div className="attention__rail-list">
                {attention.slice(0, 2).map((item) => (
                  <AttentionCard
                    key={item.id}
                    item={item}
                    onOpenTask={setOpenTask}
                    onOpenGoal={setOpenGoal}
                  />
                ))}
                {attention.length > 2 && (
                  <div className="operations-more">
                    +{attention.length - 2} more need your attention
                  </div>
                )}
              </div>
            </div>
          ) : (
            <Panel title="Needs you" icon="check" tight className="operations-panel">
              <div className="quiet-ok">
                <span className="quiet-ok__dot" aria-hidden="true" />
                <span>Nothing needs you right now.</span>
              </div>
            </Panel>
          )}
        </section>

        {/* 2 + 3. RUNNING / FINISHED -------------------------------------- */}
        <div className="operations-grid">
          <div className="stack operations-column">
            <Panel
              title="Running now"
              icon="activity"
              flush
              className="operations-panel"
              actions={runningTasks.length > 0 ? (
                <span className="t-faint">{runningTasks.length} running</span>
              ) : undefined}
            >
              {tasks.isLoading ? (
                <div className="panel__body">
                  <div className="skeleton" style={{ height: 52 }} />
                </div>
              ) : runningTasks.length === 0 ? (
                <div className="panel__body operations-empty">
                  <span className="quiet-ok__dot" aria-hidden="true" />
                  <span>Nothing running</span>
                </div>
              ) : (
                <div className="list">
                  {runningTasks.slice(0, 2).map((t) => {
                    const step = latestConfirmedStep(t, allObjectives);
                    const waiting = waitingCondition(t);
                    const goal = t.objective_id ? objectiveById.get(t.objective_id) : undefined;
                    return (
                      <button
                        type="button"
                        key={t.id}
                        className="list__row list__row--clickable"
                        onClick={() => setOpenTask(t.id)}
                      >
                        <Rail tone={taskTone(t.state)} />
                        <div className="list__main">
                          <span className="t-title t-truncate" style={{ fontSize: 12.5 }}>
                            {t.normalized_goal || t.original_request}
                          </span>
                          <div className="list__meta">
                            <Badge tone={taskTone(t.state)}>{taskLabel(t.state)}</Badge>
                            {goal && <span className="t-truncate">{goal.title}</span>}
                            <span title={dateTime(t.updated_at)}>updated {relTime(t.updated_at)}</span>
                          </div>
                          {/* Latest confirmed step only. Never inferred from elapsed time. */}
                          {(step || waiting) && (
                            <span className="op-step">
                              <Icon name="layers" size={11} />
                              {step ? (
                                <>
                                  <span className="t-truncate">
                                    Step {step.step_order}: {step.description}
                                  </span>
                                  <span className={`op-step__state op-step__state--${step.status}`}>{step.status}</span>
                                </>
                              ) : (
                                <span className="t-warn t-truncate">{waiting}</span>
                              )}
                            </span>
                          )}
                        </div>
                        <div className="list__trail">
                          <span className="t-faint">Open</span>
                          <Icon name="chevronRight" size={13} style={{ color: 'var(--text-3)' }} />
                        </div>
                      </button>
                    );
                  })}
                </div>
              )}
              {runningTasks.length > 2 && (
                <div className="panel__foot operations-more">
                  +{runningTasks.length - 2} more running
                </div>
              )}
            </Panel>

            <Panel title="Finished lately" icon="check" flush className="operations-panel">
              {finishedTasks.length === 0 ? (
                <div className="panel__body operations-empty">
                  <span className="quiet-ok__dot" aria-hidden="true" />
                  <span>Nothing finished recently</span>
                </div>
              ) : (
                <div className="list">
                  {finishedTasks.map(({ task: t, assessment }) => (
                    <button
                      type="button"
                      key={t.id}
                      className="list__row list__row--clickable"
                      onClick={() => setOpenTask(t.id)}
                    >
                      <Rail tone={taskTone(t.state)} />
                      <div className="list__main">
                        <span className="t-title t-truncate" style={{ fontSize: 12.5 }}>
                          {t.normalized_goal || t.original_request}
                        </span>
                        <span className="t-muted t-clamp-2">
                          {resultSnippet(t.final_result || t.terminal_reason)}
                        </span>
                        <div className="list__meta">
                          <Badge tone={t.state === 'cancelled' ? 'idle' : assessment.verdict === 'verified' ? 'ok' : 'plain'}>
                            {t.state === 'cancelled' ? 'Cancelled' : assessment.verdict === 'verified' ? 'Verified' : 'Completed'}
                          </Badge>
                          <span title={dateTime(t.updated_at)}>{relTime(t.updated_at)}</span>
                        </div>
                      </div>
                      <div className="list__trail">
                        <span className="t-faint">Open</span>
                        <Icon name="chevronRight" size={13} style={{ color: 'var(--text-3)' }} />
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </Panel>
          </div>

          <div className="stack operations-column">
            <Panel
              title="Goals in focus"
              icon="target"
              flush
              className="operations-panel"
              actions={focusedGoals.length > 0 ? (
                <Button size="sm" variant="ghost" icon="chevronRight" onClick={() => setRoute('missions')}>
                  All goals
                </Button>
              ) : undefined}
            >
              {focusedGoals.length === 0 ? (
                <div className="panel__body operations-empty">
                  <span className="quiet-ok__dot" aria-hidden="true" />
                  <span>No active goals</span>
                </div>
              ) : (
                <div className="list">
                  {focusedGoals.map((o) => <GoalFocusRow key={o.id} objective={o} onOpen={setOpenGoal} />)}
                </div>
              )}
            </Panel>

            <Panel title="System status" icon="shield" tight className="operations-panel">
              <div className="row row--between" style={{ gap: 8 }}>
                <div className="row" style={{ gap: 8, alignItems: 'center' }}>
                  <span
                    className={`state-dot state-dot--${systemStatus.tone}`}
                    aria-hidden="true"
                  />
                  <span className="t-body">{systemStatus.text}</span>
                </div>
                <Button size="sm" variant="ghost" onClick={() => setRoute('system')}>
                  Details
                </Button>
              </div>
            </Panel>
          </div>
        </div>
      </div>

      {openGoal && (
        <GoalDetailDrawer
          goalId={openGoal}
          onClose={() => setOpenGoal(null)}
          onOpenTask={(id) => {
            setOpenGoal(null);
            setOpenTask(id);
          }}
        />
      )}
      {openTask && taskById.get(openTask) && (
        <TaskDetailDrawer
          task={taskById.get(openTask)!}
          onClose={() => setOpenTask(null)}
          onChanged={tasks.refetch}
        />
      )}
    </div>
  );
}

function GoalFocusRow({
  objective: o,
  onOpen,
}: {
  objective: Objective;
  onOpen: (id: string) => void;
}) {
  const steps = [...(o.steps ?? [])].sort((a, b) => a.step_order - b.step_order);
  const completedSteps = steps.filter((s) => s.status === 'completed').length;
  const nextStep = steps.find((s) => OPEN_STEP_STATES.has(s.status));

  return (
    <button
      type="button"
      className="list__row list__row--clickable"
      onClick={() => onOpen(o.id)}
    >
      <Rail tone={OBJECTIVE_TONE[o.status] ?? 'idle'} />
      <div className="list__main">
        <span className="t-title t-truncate" style={{ fontSize: 12.5 }}>
          {o.title}
        </span>
        <div className="list__meta">
          <Badge tone={OBJECTIVE_TONE[o.status] ?? 'idle'}>{humanize(o.status)}</Badge>
          <span>{steps.length ? `${completedSteps} of ${steps.length} steps complete` : 'No steps recorded'}</span>
          {o.criteria_verified && <span>Criteria verified</span>}
        </div>
        {nextStep && (
          <span className="op-step">
            <Icon name="layers" size={11} />
            <span className="t-truncate">Next: {nextStep.description}</span>
            {nextStep.status === 'blocked' && nextStep.blocker_reason && (
              <span className="t-warn">Blocked: {nextStep.blocker_reason}</span>
            )}
          </span>
        )}
        {!nextStep && o.current_summary && <span className="t-muted t-clamp-2">{o.current_summary}</span>}
      </div>
      <div className="list__trail">
        <Icon name="chevronRight" size={13} style={{ color: 'var(--text-3)' }} />
      </div>
    </button>
  );
}

function AttentionCard({
  item,
  onOpenTask,
  onOpenGoal,
}: {
  item: AttentionItem;
  onOpenTask: (id: string) => void;
  onOpenGoal: (id: string) => void;
}) {
  const KIND_LABEL: Record<string, string> = {
    approval: 'Approval',
    input: 'Needs input',
    failure: 'Failure',
    unverified: 'Unverified',
    stalled: 'Watch',
    blocked_step: 'Blocked step',
  };

  return (
    <div className={`attn attn--${item.kind}`}>
      <span className="attn__icon" aria-hidden="true">
        <Icon
          name={
            item.kind === 'approval'
              ? 'lock'
              : item.kind === 'failure'
                ? 'alert'
                : item.kind === 'stalled'
                  ? 'clock'
                  : item.kind === 'blocked_step'
                    ? 'layers'
                    : 'info'
          }
          size={14}
        />
      </span>
      <div className="attn__body">
        <div className="attn__head">
          {/* Kind is carried by a text label as well as colour and icon. */}
          <Badge tone={item.tone}>{KIND_LABEL[item.kind] ?? item.kind}</Badge>
          <span className="t-title t-truncate" style={{ fontSize: 12.5 }}>
            {item.title}
          </span>
        </div>
        <p className="attn__detail">{item.detail}</p>
      </div>
      {item.target && (
        <Button
          size="sm"
          variant="ghost"
          onClick={() =>
            item.target!.kind === 'task' ? onOpenTask(item.target!.id) : onOpenGoal(item.target!.id)
          }
        >
          {item.target.kind === 'task' ? 'Open task' : 'Open goal'}
        </Button>
      )}
    </div>
  );
}
