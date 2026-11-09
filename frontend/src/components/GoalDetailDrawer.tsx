import { useMemo, useState } from 'react';
import { Icon } from './icons';
import { Badge, Button, EmptyState, LoadingBlock, Panel, Rail } from './ui';
import { useGoalDetail, useGoalPlans, useGoalNextWork, useTasks } from '../hooks/useBackend';
import { goalService } from '../services/api';
import { useAppStore } from '../store/appStore';
import { dateTime, parseTimestamp, taskLabel, taskTone } from '../lib/format';
import { assessCompletion } from '../lib/observation';
import type { GoalPlanStep, Task } from '../types';

/* Goal detail.
 *
 * Presents the canonical product model and nothing else:
 *   Goal -> versioned Plan -> Plan Steps (+ their Tasks) -> evidence -> outcome.
 *
 * There is no persisted Mission entity and Tasks have no general parent/child
 * hierarchy, so a Goal's tasks are shown as *associated* records (tasks whose
 * `objective_id` points here, plus steps that name a linked task) rather than
 * as a tree. Blocked work is labelled step-level, per the observation contract.
 *
 * The three tabs are the logical views the brief asks for: Plan, Activity and
 * Results & Evidence. */

type Tab = 'plan' | 'activity' | 'results';

const TABS: Array<{ id: Tab; label: string }> = [
  { id: 'plan', label: 'Plan' },
  { id: 'activity', label: 'Activity' },
  { id: 'results', label: 'Results & Evidence' },
];

const STEP_TONE: Record<string, 'ok' | 'warn' | 'danger' | 'info' | 'idle' | 'accent'> = {
  completed: 'ok',
  running: 'info',
  in_progress: 'info',
  ready: 'accent',
  blocked: 'warn',
  failed: 'danger',
  cancelled: 'idle',
  skipped: 'idle',
  pending: 'idle',
};

export function GoalDetailDrawer({
  goalId,
  onClose,
  onOpenTask,
}: {
  goalId: string;
  onClose: () => void;
  onOpenTask?: (taskId: string) => void;
}) {
  const [tab, setTab] = useState<Tab>('plan');

  const detail = useGoalDetail(goalId);
  const plans = useGoalPlans(goalId);
  const nextWork = useGoalNextWork(goalId);
  const tasks = useTasks();
  const notify = useAppStore((s) => s.notify);

  const goal = detail.data?.goal ?? null;
  const plan = detail.data?.active_plan ?? null;
  const progress = detail.data?.progress ?? null;

  // Associated tasks: any task the backend linked to this goal, plus the tasks
  // its steps point at. Presented flat and labelled "associated", never a tree.
  const associated = useMemo(() => {
    const all = tasks.data ?? [];
    const byId = new Map<string, Task>();
    for (const t of all) {
      if (t.objective_id === goalId) byId.set(t.id, t);
    }
    for (const step of plan?.steps ?? []) {
      if (step.linked_task_id && !byId.has(step.linked_task_id)) {
        const task = all.find((t) => t.id === step.linked_task_id);
        if (task) byId.set(step.linked_task_id, task);
      }
    }
    return Array.from(byId.values())
      .sort((a, b) => (parseTimestamp(b.updated_at) ?? 0) - (parseTimestamp(a.updated_at) ?? 0));
  }, [tasks.data, goalId, plan]);

  const assessment = goal
    ? assessCompletion(
        { state: goal.status === 'completed' ? 'completed' : 'running', final_result: goal.current_summary ?? '', terminal_reason: null } as Task,
        { criteria_verified: goal.criteria_verified, completion_criteria: goal.completion_criteria }
      )
    : null;

  const goalStatusTone =
    goal?.status === 'completed' ? 'ok' : goal?.status === 'abandoned' ? 'idle' : goal?.status === 'paused' ? 'warn' : 'accent';

  return (
    <>
      <div className="scrim" onClick={onClose} aria-hidden="true" />
      <aside className="drawer" role="dialog" aria-modal="true" aria-label={goal?.title ?? 'Goal detail'}>
        <header className="drawer__head">
          <div className="stack stack--tight" style={{ flex: 1, minWidth: 0 }}>
            <div className="row row--wrap" style={{ gap: 8 }}>
              <Badge tone={goalStatusTone}>{goal?.status ?? 'unknown'}</Badge>
              {goal?.priority !== undefined && <span className="t-faint">Priority {goal.priority}</span>}
            </div>
            <h2 className="t-title" style={{ fontSize: 15, lineHeight: 1.35 }}>
              {goal?.title ?? 'Goal'}
            </h2>
            {goal?.purpose && <p className="t-muted">{goal.purpose}</p>}
          </div>
          <button type="button" className="btn btn--icon btn--ghost" aria-label="Close goal detail" onClick={onClose}>
            <Icon name="close" size={15} />
          </button>
        </header>

        <div className="drawer__tabs" role="tablist" aria-label="Goal detail views">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={tab === t.id}
              className={`drawer__tab${tab === t.id ? ' drawer__tab--active' : ''}`}
              onClick={() => setTab(t.id)}
            >
              {t.label}
            </button>
          ))}
        </div>

        <div className="drawer__body">
          {detail.isLoading ? (
            <LoadingBlock rows={4} />
          ) : !goal ? (
            <EmptyState icon="target" title="Goal unavailable" description="The backend returned no goal for this id." />
          ) : tab === 'plan' ? (
            <>
              {/* Progress is derived by the backend from persisted step state. */}
              <div className="stack stack--tight">
                <div className="row row--between">
                  <span className="field__label">Progress</span>
                  <span className="t-muted">
                    {progress ? `${progress.completed_steps} of ${progress.total_required_steps} steps complete` : 'unknown'}
                  </span>
                </div>
                <div className="bar">
                  <div
                    className="bar__fill"
                    style={{ width: `${Math.round((progress?.fraction ?? 0) * 100)}%` }}
                    role="progressbar"
                    aria-valuenow={Math.round((progress?.fraction ?? 0) * 100)}
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-label="Goal step completion"
                  />
                </div>
              </div>

              <Panel title="Next work" icon="target" tight>
                {nextWork.isError ? (
                  <p className="t-warn">Could not inspect goal readiness. The preview is unavailable.</p>
                ) : nextWork.isLoading && !nextWork.data ? (
                  <LoadingBlock rows={2} />
                ) : nextWork.data ? (
                  <div className="stack stack--tight">
                    <div className="row row--wrap" style={{ gap: 8 }}>
                      <Badge tone={nextWork.data.runnable ? 'ok' : 'warn'}>
                        {nextWork.data.state.replace(/_/g, ' ')}
                      </Badge>
                      <span className="t-faint">
                        {nextWork.data.counts.completed} of {nextWork.data.counts.total} plan steps complete
                      </span>
                    </div>
                    <p className="t-body">{nextWork.data.explanation}</p>
                    {nextWork.data.next_step && (
                      <div className="stack stack--tight">
                        <span className="t-title" style={{ fontSize: 12.5 }}>
                          Next: {nextWork.data.next_step.description}
                        </span>
                        {nextWork.data.next_step.would_unlock && (
                          <p className="t-faint">
                            Dependencies are complete. The stored step will become ready only when execution begins.
                          </p>
                        )}
                      </div>
                    )}
                    {nextWork.data.blocked_steps.length > 0 && (
                      <div className="stack stack--tight">
                        {nextWork.data.blocked_steps.slice(0, 3).map((item) => (
                          <p className="t-muted" key={item.step_key}>
                            {item.step_key} is waiting for {item.waiting_for.join(', ')}
                          </p>
                        ))}
                      </div>
                    )}
                    <p className="t-faint">
                      This is a read-only preview, not an execution command. Runtime checks still apply.
                    </p>
                  </div>
                ) : (
                  <p className="t-muted">Next-work status is unavailable.</p>
                )}
              </Panel>

              {!plan ? (
                <EmptyState
                  icon="layers"
                  title="No active plan"
                  description="This goal has no plan version yet."
                />
              ) : (
                <Panel title={`Plan v${plan.version}`} icon="layers" tight
                  actions={<span className="t-faint">{plan.status}</span>}
                >
                  <div className="stack stack--tight">
                    {plan.source && (
                      <p className="t-faint">
                        Source: {plan.source}
                        {plan.reason ? ` — ${plan.reason}` : ''}
                      </p>
                    )}
                    {plan.steps.map((step) => (
                      <StepRow
                        key={step.id}
                        step={step}
                        taskLookup={new Map(associated.map((t) => [t.id, t]))}
                        onOpenTask={onOpenTask}
                      />
                    ))}
                  </div>
                </Panel>
              )}

              {/* Replanning is audited; earlier versions are never erased. */}
              <Panel title="Plan versions" icon="clock" tight>
                {plans.isLoading ? (
                  <div className="skeleton" style={{ height: 40 }} />
                ) : (plans.data ?? []).length === 0 ? (
                  <EmptyState icon="clock" title="No plan history" description="No plan version has been recorded." />
                ) : (
                  <div className="list list--dense">
                    {(plans.data ?? []).map((p) => (
                      <div className="list__row" key={p.id}>
                        <div className="list__main">
                          <span className="t-title" style={{ fontSize: 12.5 }}>
                            Version {p.version} <span className="t-faint">· {p.status}</span>
                          </span>
                          <span className="list__meta">
                            <span>{p.source ?? 'source unknown'}</span>
                            <span>{dateTime(p.created_at)}</span>
                          </span>
                          {p.reason && <span className="t-muted">{p.reason}</span>}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </Panel>
            </>
          ) : tab === 'activity' ? (
            <Panel title="Associated tasks" icon="activity" flush>
              {associated.length === 0 ? (
                <EmptyState
                  icon="inbox"
                  title="No associated tasks"
                  description="No linked task records. Plan steps may still reference tasks."
                />
              ) : (
                <div className="list">
                  {associated.map((t) => (
                    <button
                      type="button"
                      key={t.id}
                      className="list__row list__row--clickable"
                      onClick={() => onOpenTask?.(t.id)}
                    >
                      <Rail tone={taskTone(t.state)} />
                      <div className="list__main">
                        <span className="t-title t-truncate" style={{ fontSize: 12.5 }}>
                          {t.normalized_goal || t.original_request}
                        </span>
                        <span className="list__meta">
                          <Badge tone={taskTone(t.state)}>{taskLabel(t.state)}</Badge>
                          <span>{dateTime(t.updated_at)}</span>
                        </span>
                      </div>
                      <div className="list__trail">
                        <Icon name="chevronRight" size={13} style={{ color: 'var(--text-3)' }} />
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </Panel>
          ) : (
            <>
              <Panel title="Outcome assessment" icon="check" tight>
                <div className="stack stack--tight">
                  <div className="row row--wrap" style={{ gap: 8 }}>
                    <Badge tone={goal.criteria_verified ? 'ok' : goal.status === 'completed' ? 'warn' : 'idle'}>
                      {goal.criteria_verified ? 'criteria verified' : 'not verified'}
                    </Badge>
                    {assessment && <Badge tone={assessment.uncertain ? 'warn' : 'ok'}>{assessment.label}</Badge>}
                  </div>
                  {goal.current_summary && <p className="t-body">{goal.current_summary}</p>}
                  {/* The important negative statement, always shown. */}
                  {assessment?.limitation && <p className="t-muted">{assessment.limitation}</p>}
                </div>
              </Panel>

              <Panel title="Completion criteria" icon="target" tight>
                {goal.completion_criteria ? (
                  <p className="t-body">{goal.completion_criteria}</p>
                ) : (
                  <EmptyState
                    icon="target"
                    title="No criteria recorded"
                    description="No completion criteria stored — the outcome cannot be verified."
                  />
                )}
              </Panel>

              <Panel title="Step evidence" icon="shield" tight>
                {(plan?.steps ?? []).filter((s) => s.result_summary || s.evidence_task_id || s.failure_reason)
                  .length === 0 ? (
                  <EmptyState
                    icon="file"
                    title="No evidence recorded"
                    description="No step has recorded a result yet."
                  />
                ) : (
                  <div className="stack stack--tight">
                    {(plan?.steps ?? [])
                      .filter((s) => s.result_summary || s.evidence_task_id || s.failure_reason)
                      .map((s) => (
                        <div className="stack stack--tight" key={s.id}>
                          <span className="t-title" style={{ fontSize: 12 }}>
                            {s.description}
                          </span>
                          {s.result_summary && <p className="t-muted">{s.result_summary}</p>}
                          <span className="list__meta">
                            {s.evidence_task_id && (
                              <>
                                <Icon name="link" size={11} />
                                <span className="t-mono">evidence {s.evidence_task_id}</span>
                              </>
                            )}
                            {s.attempt_count !== undefined && s.attempt_count > 1 && (
                              <span>{s.attempt_count} attempts</span>
                            )}
                          </span>
                        </div>
                      ))}
                  </div>
                )}
              </Panel>
            </>
          )}
        </div>

        {goal && (
          <div className="drawer__foot">
            {goal.status === 'active' && (
              <Button
                size="sm"
                variant="subtle"
                onClick={() =>
                  void runLifecycle('pause', goal.id)
                }
              >
                Pause goal
              </Button>
            )}
            {goal.status === 'paused' && (
              <Button size="sm" variant="subtle" onClick={() => void runLifecycle('resume', goal.id)}>
                Resume goal
              </Button>
            )}
            {goal.status !== 'completed' && goal.status !== 'abandoned' && (
              <Button size="sm" variant="danger" onClick={() => void runLifecycle('abandon', goal.id)}>
                Abandon
              </Button>
            )}
          </div>
        )}
      </aside>
    </>
  );

  async function runLifecycle(action: 'pause' | 'resume' | 'abandon', id: string) {
    try {
      await goalService.lifecycle(id, action);
      detail.refetch();
      plans.refetch();
      nextWork.refetch();
      tasks.refetch();
      notify({ tone: 'ok', title: `Goal ${action}d` });
    } catch (err) {
      notify({
        tone: 'danger',
        title: 'Could not change the goal',
        body: err instanceof Error ? err.message : 'The backend rejected the transition.',
      });
    }
  }
}

function StepRow({
  step,
  taskLookup,
  onOpenTask,
}: {
  step: GoalPlanStep;
  taskLookup: Map<string, Task>;
  onOpenTask?: (id: string) => void;
}) {
  const task = step.linked_task_id ? taskLookup.get(step.linked_task_id) : undefined;
  // Dependencies are recorded as step keys. Resolving them is a lookup, not a
  // claim about ordering the backend has not confirmed.
  const blockers = (step.depends_on ?? []).filter(Boolean);

  return (
    <div className="step-row">
      <span className={`step-row__marker step-row__marker--${STEP_TONE[step.status] ?? 'idle'}`}>
        {step.status === 'completed' ? '✓' : step.step_order}
      </span>
      <div className="step-row__body">
        <span className="t-body">{step.description}</span>
        <div className="list__meta">
          <Badge tone={STEP_TONE[step.status] ?? 'idle'}>{step.status.replace('_', ' ')}</Badge>
          {blockers.length > 0 && (
            <span title="Recorded step dependencies">
              depends on {blockers.join(', ')}
            </span>
          )}
          {step.linked_task_id && task ? (
            <button type="button" className="link-chip" onClick={() => onOpenTask?.(step.linked_task_id!)}>
              <Icon name="link" size={11} />
              {taskLabel(task.state)}
            </button>
          ) : step.linked_task_id ? (
            <span className="t-muted">Linked task unavailable ({step.linked_task_id})</span>
          ) : null}
        </div>
        {step.status === 'blocked' && (
          <p className="t-warn" style={{ fontSize: 11.5 }}>
            Step-level blocked condition. The Goal itself is not blocked.
            {step.failure_reason ? ` ${step.failure_reason}` : ' No blocker reason was recorded.'}
          </p>
        )}
        {step.status !== 'blocked' && step.failure_reason && (
          <p className="t-muted" style={{ fontSize: 11.5 }}>
            {step.failure_reason}
          </p>
        )}
      </div>
      <span className="t-faint t-mono" aria-hidden="true">
        {step.priority ?? '—'}
      </span>
    </div>
  );
}
