import { useMemo, useState } from 'react';
import { Icon } from '../components/icons';
import { GoalDetailDrawer } from '../components/GoalDetailDrawer';
import { TaskDetailDrawer } from '../components/TaskDetailDrawer';
import {
  Badge,
  Button,
  DataBadge,
  DemoTag,
  EmptyState,
  Panel,
  Rail,
  ScreenHead,
} from '../components/ui';
import { useAgent, useCapabilities, useHealth, useObjectives, useTasks, useVersion } from '../hooks/useBackend';
import { useAppStore } from '../store/appStore';
import { dateTime, parseTimestamp, relTime, taskLabel, taskTone } from '../lib/format';
import {
  collectAttention,
  latestConfirmedStep,
  waitingCondition,
  type AttentionItem,
} from '../lib/observation';

/* Operations — the Control Center landing view.
 *
 * Strict content priority:
 *   1. Attention   — approvals, material failures, uncertain outcomes, and
 *                    things that genuinely need investigation.
 *   2. Active work — persisted state, latest confirmed step, waiting conditions.
 *   3. Readiness   — runtime/model/capability, compact when healthy.
 *
 * Every value comes from the observation layer, which follows
 * . Nothing here invents an alert,
 * a percentage, a progress figure or a health verdict. Where the backend cannot
 * answer a question the UI says so.
 *
 * Two things this screen deliberately does NOT do:
 *  - claim runner/executor liveness. No lease endpoint is exposed, so
 *    "executor might not be running" is unavailable rather than guessed;
 *  - treat a finished task as a verified outcome. */

export function OperationsScreen() {
  const tasks = useTasks();
  const objectives = useObjectives();
  const health = useHealth();
  const agent = useAgent();
  const capabilities = useCapabilities();
  const version = useVersion();

  const notify = useAppStore((s) => s.notify);
  const dataMode = useAppStore((s) => s.dataMode);

  const [openGoal, setOpenGoal] = useState<string | null>(null);
  const [openTask, setOpenTask] = useState<string | null>(null);
  const [readinessExpanded, setReadinessExpanded] = useState(false);

  const allTasks = useMemo(() => tasks.data ?? [], [tasks.data]);
  const allObjectives = useMemo(() => objectives.data ?? [], [objectives.data]);

  const attention = useMemo(
    () => collectAttention({ tasks: allTasks, objectives: allObjectives, now: Date.now() }),
    [allTasks, allObjectives]
  );

  const activeTasks = useMemo(
    () =>
      allTasks
        .filter((t) => ['running', 'created', 'waiting_for_user', 'interrupted', 'paused'].includes(t.state))
        .sort((a, b) => (parseTimestamp(b.updated_at) ?? 0) - (parseTimestamp(a.updated_at) ?? 0)),
    [allTasks]
  );

  const taskById = useMemo(() => new Map(allTasks.map((t) => [t.id, t])), [allTasks]);

  /* --- Readiness: only ever states what the backend reported. ---------- */

  const readiness = useMemo(() => {
    const checks: Array<{ id: string; label: string; state: 'ok' | 'warn' | 'unknown'; value: string }> = [];

    const dbConnected = health.isError ? undefined : health.data?.database_connected;
    checks.push({
      id: 'db',
      label: 'Database',
      state: dbConnected === undefined ? 'unknown' : dbConnected ? 'ok' : 'warn',
      value: dbConnected === undefined ? 'unknown' : dbConnected ? 'connected' : 'not connected',
    });

    const runtime = health.isError ? undefined : health.data?.status;
    checks.push({
      id: 'runtime',
      label: 'Runtime',
      state: runtime ? (runtime === 'ok' || runtime === 'healthy' ? 'ok' : 'warn') : 'unknown',
      value: runtime ?? 'unknown',
    });

    const agentState = agent.data?.state;
    checks.push({
      id: 'agent',
      label: 'Main agent',
      state: agentState ? (agentState === 'active' ? 'ok' : 'warn') : 'unknown',
      value: agentState ?? 'unknown',
    });

    const authLevel = agent.data?.policies?.autonomy_level;
    checks.push({
      id: 'authorization',
      label: 'Authorization',
      state: authLevel ? (authLevel === 'guarded' ? 'ok' : 'warn') : 'unknown',
      value: authLevel ?? 'not reported',
    });

    const caps = capabilities.data ?? [];
    const unavailable = caps.filter((c) => c.status === 'UNAVAILABLE' || c.status === 'DISABLED').length;
    const gated = caps.filter((c) => c.status === 'REQUIRES_PERMISSION').length;
    checks.push({
      id: 'capabilities',
      label: 'Capabilities',
      state: caps.length === 0 ? 'unknown' : unavailable > 0 ? 'warn' : 'ok',
      value: caps.length === 0 ? 'unknown' : `${caps.length - unavailable}/${caps.length} available`,
    });

    return {
      checks,
      degraded: checks.some((c) => c.state === 'warn'),
      unknown: checks.some((c) => c.state === 'unknown'),
      unavailableCaps: unavailable,
      gatedCaps: gated,
    };
  }, [health.data, health.isError, agent.data, capabilities.data]);

  /* --- Derived counts, stated only where supported. -------------------- */

  const goalCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const o of allObjectives) counts[o.status] = (counts[o.status] ?? 0) + 1;
    return counts;
  }, [allObjectives]);

  const isDemo = dataMode === 'preview';

  return (
    <div className="screen">
      <div className="screen__inner">
        <ScreenHead
          eyebrow="Control Center"
          title="Operations"
          description="What needs you, what is running, and whether the system is ready. Everything here is read from persisted backend state."
          actions={isDemo ? <DemoTag /> : <DataBadge origin="live" />}
        />

        {/* 1. ATTENTION ------------------------------------------------- */}
        {attention.length > 0 ? (
          <div className="attention attention--rail" role="region" aria-label="Needs attention">
            <div className="attention__rail-head">
              <span className="field__label">Attention</span>
              <span className="t-faint">{attention.length} item{attention.length === 1 ? '' : 's'}</span>
            </div>
            <div className="attention__rail-list">
              {attention.map((item) => (
                <AttentionCard
                  key={item.id}
                  item={item}
                  onOpenTask={setOpenTask}
                  onOpenGoal={setOpenGoal}
                />
              ))}
            </div>
          </div>
        ) : (
          <Panel title="Attention" icon="check" tight>
            <div className="quiet-ok">
              <span className="quiet-ok__dot" aria-hidden="true" />
              <span>
                Nothing requires attention. Approvals, failures and unverified outcomes would appear
                here.
              </span>
            </div>
          </Panel>
        )}

        {/* 2. ACTIVE OPERATIONS ------------------------------------------ */}
        <div className="grid grid--main">
          <Panel
            title="Active operations"
            icon="activity"
            flush
            actions={<span className="t-faint">{activeTasks.length} in flight or held</span>}
          >
            {tasks.isLoading ? (
              <div className="panel__body">
                <div className="skeleton" style={{ height: 90 }} />
              </div>
            ) : activeTasks.length === 0 ? (
              <EmptyState
                icon="inbox"
                title="No active operations"
                description="Nothing is running, queued or waiting. This reflects the persisted task list."
              />
            ) : (
              <div className="list">
                {activeTasks.map((t) => {
                  const step = latestConfirmedStep(t, allObjectives);
                  const waiting = waitingCondition(t);
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
                          <span title={dateTime(t.updated_at)}>updated {relTime(t.updated_at)}</span>
                          {t.objective_id && <span className="t-mono">goal {t.objective_id}</span>}
                        </div>
                        {/* Latest confirmed step only. Never inferred from elapsed time. */}
                        {step && (
                          <span className="op-step">
                            <Icon name="layers" size={11} />
                            <span className="t-truncate">
                              Step {step.step_order}: {step.description}
                            </span>
                            <span className={`op-step__state op-step__state--${step.status}`}>{step.status}</span>
                          </span>
                        )}
                        {waiting && <span className="t-warn t-clamp-2" style={{ fontSize: 11.5 }}>{waiting}</span>}
                      </div>
                      <div className="list__trail">
                        <Icon name="chevronRight" size={13} style={{ color: 'var(--text-3)' }} />
                      </div>
                    </button>
                  );
                })}
              </div>
            )}
          </Panel>

          <div className="stack">
            {/* 3. READINESS ------------------------------------------------ */}
            <Panel
              title="System readiness"
              icon="shield"
              tight
              actions={
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setReadinessExpanded((v) => !v)}
                  aria-expanded={readinessExpanded}
                >
                  {readinessExpanded ? 'Compact' : 'Details'}
                </Button>
              }
            >
              <div className="stack stack--tight">
                {readiness.checks.map((c) => (
                  <div className="kv" key={c.id}>
                    <span className="kv__k">{c.label}</span>
                    <span className="kv__v row" style={{ gap: 7 }}>
                      <span
                        className={`state-dot state-dot--${c.state}`}
                        aria-hidden="true"
                      />
                      <span>{c.value}</span>
                      <span className="sr-only">{c.state === 'unknown' ? 'unknown' : c.state}</span>
                    </span>
                  </div>
                ))}

                {readinessExpanded && (
                  <>
                    {version.data?.version && (
                      <div className="kv">
                        <span className="kv__k">Version</span>
                        <span className="kv__v t-mono">{version.data.version}</span>
                      </div>
                    )}
                    {readiness.gatedCaps > 0 && (
                      <div className="kv">
                        <span className="kv__k">Needs permission</span>
                        <span className="kv__v">{readiness.gatedCaps} capabilities</span>
                      </div>
                    )}
                    <div className="kv">
                      <span className="kv__k">Executor liveness</span>
                      <span className="kv__v t-faint">unavailable — no lease endpoint is exposed</span>
                    </div>
                    <p className="t-faint">
                      Every row above is a reported value. A blank or unknown row is not a healthy one.
                    </p>
                  </>
                )}
              </div>
            </Panel>

            <Panel title="Goals" icon="target" tight
              actions={
                <Button size="sm" variant="ghost" icon="chevronRight" onClick={() => notify({ tone: 'info', title: 'Open Goals in the sidebar' })}>
                  View all
                </Button>
              }
            >
              {allObjectives.length === 0 ? (
                <EmptyState icon="target" title="No goals" description="No goal has been created." />
              ) : (
                <div className="stack stack--tight">
                  {(['active', 'paused', 'completed', 'abandoned'] as const).map((status) =>
                    goalCounts[status] ? (
                      <div className="kv" key={status}>
                        <span className="kv__k">{status}</span>
                        <span className="kv__v">{goalCounts[status]}</span>
                      </div>
                    ) : null
                  )}
                </div>
              )}
            </Panel>
          </div>
        </div>
      </div>

      {openGoal && (
        <GoalDetailDrawer
          goalId={openGoal}
          origin={objectives.origin}
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
          origin={tasks.origin}
          onClose={() => setOpenTask(null)}
          onChanged={tasks.refetch}
        />
      )}
    </div>
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
          {item.derived && <span className="t-faint">derived</span>}
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