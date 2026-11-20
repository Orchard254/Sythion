import { useEffect, useMemo, useState } from 'react';
import { Icon } from '../components/icons';
import { CommandComposer } from '../components/CommandComposer';
import { TaskDetailDrawer } from '../components/TaskDetailDrawer';
import {
  Badge,
  Bar,
  Button,
  EmptyState,
  KV,
  LoadingBlock,
  Notice,
  Panel,
  Rail,
  ScreenHead,
  Stat,
} from '../components/ui';
import {
  useActions,
  useAgent,
  useAgentActivity,
  useAgentHealth,
  useBackgroundPolicy,
  useCapabilities,
  useDelegations,
  useGoals,
  useHealth,
  useObjectives,
  usePendingApprovals,
  useSkillProposals,
  useUncertainRemoteActions,
  useTasks,
  useToolProposals,
} from '../hooks/useBackend';
import { useAppStore } from '../store/appStore';
import { useNavStore } from '../store/navStore';
import type { RouteId } from '../lib/routes';
import { AUTONOMY_MODES, capabilityCounts, deriveAuthorization, deriveAutonomy } from '../lib/autonomy';
import {
  countDecisionsDue, decisionDestination, describeDecisionsDue,
} from '../lib/decisionCounts';
import {
  AGENT_STATE_TONE,
  HEALTH_TONE,
  clockTime,
  dateTime,
  duration,
  humanize,
  relTime,
  taskLabel,
  taskTone,
} from '../lib/format';
import type { Task } from '../types';

function greeting(d = new Date()): string {
  const h = d.getHours();
  if (h < 5) return 'Still up';
  if (h < 12) return 'Good morning';
  if (h < 18) return 'Good afternoon';
  return 'Good evening';
}

export function OverviewScreen() {
  const navigate = useNavStore((s) => s.setRoute);

  const health = useHealth();
  const agent = useAgent();
  const agentHealth = useAgentHealth();
  const background = useBackgroundPolicy();
  const tasks = useTasks();
  const objectives = useObjectives();
  const goals = useGoals();
  const capabilities = useCapabilities();
  const skillProposals = useSkillProposals();
  const toolProposals = useToolProposals();
  const pendingApprovalsApi = usePendingApprovals();
  const uncertainActions = useUncertainRemoteActions();
  const delegations = useDelegations();
  const activity = useAgentActivity();
  const actions = useActions();

  const [selectedTask, setSelectedTask] = useState<Task | null>(null);
  const [pendingOpenId, setPendingOpenId] = useState<string | null>(null);
  const notify = useAppStore((s) => s.notify);

  // A freshly started task is not in the polled list yet. Refresh, then open its
  // detail panel as soon as the backend reports it.
  const autonomy = deriveAutonomy(agent.data, background.data);
  const authorization = deriveAuthorization(agent.data);
  const autonomyMeta = AUTONOMY_MODES.find((m) => m.id === autonomy.mode)!;
  const caps = useMemo(() => capabilityCounts(capabilities.data ?? []), [capabilities.data]);

  const allTasks = useMemo(() => tasks.data ?? [], [tasks.data]);
  const liveTasks = allTasks.filter((t) => t.state !== 'cancelled');
  const runningTasks = liveTasks.filter((t) => t.state === 'running' || t.state === 'waiting_for_user');
  const failedTasks = liveTasks.filter((t) => t.state === 'failed');
  const awaitingYou = liveTasks.filter((t) => t.state === 'waiting_for_user' || t.state === 'interrupted');
  const recentCompleted = liveTasks.filter((t) => t.state === 'completed');

  // Priority order is what needs attention first, then what just finished.
  // Completed work is included so a task submitted seconds ago does not appear
  // to vanish the moment it succeeds.
  const featuredTasks = [
    ...runningTasks,
    ...awaitingYou,
    ...failedTasks,
    ...recentCompleted
      .slice()
      .sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()),
  ]
    .filter((t, i, arr) => arr.findIndex((x) => x.id === t.id) === i)
    .slice(0, 6);

  // A freshly started task is not in the polled list yet. Refresh, then open its
  // detail panel as soon as the backend reports it.
  useEffect(() => {
    if (!pendingOpenId) return;
    const found = allTasks.find((t) => t.id === pendingOpenId);
    if (found) {
      setSelectedTask(found);
      setPendingOpenId(null);
      return;
    }
    const timer = setTimeout(() => tasks.refetch(), 1200);
    return () => clearTimeout(timer);
  }, [pendingOpenId, allTasks, tasks]);

  const pendingApprovals =
    (skillProposals.data ?? []).filter((p) => (p.status ?? '').includes('pending')).length +
    (toolProposals.data ?? []).filter((p) => p.status === 'pending_review').length;
  const ownerApprovals = (pendingApprovalsApi.data?.proposals ?? []).length;
  const needsReconciliation = (uncertainActions.data?.actions ?? []).length;
  const decisionInputs = {
    proposalApprovals: pendingApprovals,
    ownerApprovals,
    needsReconciliation,
    awaitingYou: awaitingYou.length,
  };
  // Everything that needs a decision from the owner, from all durable sources.
  const decisionsDue = countDecisionsDue(decisionInputs);

  const activeObjectives = (objectives.data ?? []).filter((o) => o.status === 'active');
  const objectiveProgress = (id: string) => {
    const o = (objectives.data ?? []).find((x) => x.id === id);
    if (!o || o.steps.length === 0) return null;
    return Math.round((o.steps.filter((s) => s.status === 'completed').length / o.steps.length) * 100);
  };

  const checks = agentHealth.data?.checks ?? [];
  const warnings = checks.filter((c) => c.status === 'DEGRADED' || c.status === 'UNAVAILABLE');
  const recentEvents = [...(activity.data?.events ?? [])]
    .sort((a, b) => b.sequence_num - a.sequence_num)
    .slice(0, 6);

  const runtimeTone =
    !health.isLoading && !health.data
      ? 'danger'
      : HEALTH_TONE[agentHealth.data?.overall ?? 'UNKNOWN'];

  const runtimeLabel =
    agentHealth.data?.overall === 'HEALTHY'
      ? 'Operational'
      : agentHealth.data?.overall === 'DEGRADED'
        ? 'Degraded'
        : agentHealth.data
          ? 'Checking'
          : 'Unavailable';

  const refreshAll = () => {
    health.refetch();
    agent.refetch();
    agentHealth.refetch();
    tasks.refetch();
    objectives.refetch();
    capabilities.refetch();
    activity.refetch();
    background.refetch();
  };

  return (
    <div className="screen">
      <div className="screen__inner">
        {/* ---- Header ------------------------------------------------- */}
        <ScreenHead
          eyebrow={new Date().toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' })}
          title={`${greeting()}. Sythion is ${runtimeLabel.toLowerCase()}.`}
          description="Live state of the runtime, what it is working on, and anything waiting on your decision."
          actions={
            <>
              
              <Badge tone={runtimeTone === 'warn' ? 'warn' : runtimeTone}>
                <span className="dot" style={{ background: 'currentColor' }} />
                {runtimeLabel}
              </Badge>
              <Button icon="refresh" variant="subtle" size="sm" onClick={refreshAll}>
                Refresh
              </Button>
            </>
          }
        />

        {/* ---- System summary ---------------------------------------- */}
        <div className="grid grid--4">
          <Stat
            label="Active missions"
            icon="missions"
            value={runningTasks.length}
            foot={
              activeObjectives.length > 0 ? (
                <span className="row" style={{ gap: 5 }}>
                  <Rail tone="accent" /> {activeObjectives.length} objectives tracked
                </span>
              ) : runningTasks.length > 0 ? (
                'Ad-hoc, no objective attached'
              ) : (
                'No tasks in flight'
              )
            }
            onClick={() => navigate('missions')}
          />
          <Stat
            label="Needs your decision"
            icon="shield"
            value={decisionsDue}
            tone={decisionsDue > 0 ? 'accent' : undefined}
            foot={describeDecisionsDue(decisionInputs)}
            onClick={() => navigate(decisionDestination(decisionInputs))}
          />
          <Stat
            label="Capabilities online"
            icon="plug"
            value={`${caps.available}/${caps.total}`}
            foot={
              caps.unavailable > 0 ? (
                <span className="row" style={{ gap: 5 }}>
                  <Rail tone="danger" /> {caps.unavailable} unavailable
                </span>
              ) : (
                'All registered capabilities responding'
              )
            }
            onClick={() => navigate('system')}
          />
          <Stat
            label="Autonomy mode"
            icon="key"
            value={autonomyMeta.label}
            foot={
              <span className="row" style={{ gap: 5 }}>
                <Rail tone={AGENT_STATE_TONE[autonomy.agentState] ?? 'idle'} />
                Agent {humanize(autonomy.agentState).toLowerCase()}
              </span>
            }
            onClick={() => navigate('security')}
          />
        </div>

        {/* ---- Main workspace ----------------------------------------- */}
        <div className="grid grid--main">
          <div className="stack">
            <Panel
              title="Delegate work to Sythion"
              icon="assistant"
              hint="Describe an outcome. Sythion plans, asks for authorization where required, and reports what it did."
             
            >
              <CommandComposer onStarted={(id) => setPendingOpenId(id)} />
            </Panel>

            <Panel
              title="Mission activity"
              icon="activity"
              flush
              actions={
                <>
                  <Badge tone={runningTasks.length > 0 ? 'info' : 'idle'}>
                    {runningTasks.length} running
                  </Badge>
                  <Button size="sm" variant="ghost" onClick={() => navigate('missions')}>
                    All missions
                    <Icon name="chevronRight" size={12} />
                  </Button>
                </>
              }
            >
              {tasks.isLoading ? (
                <div className="panel__body">
                  <LoadingBlock rows={4} />
                </div>
              ) : liveTasks.length === 0 ? (
                <EmptyState
                  icon="missions"
                  title="No missions yet"
                  description="Start one above and Sythion will plan it, report its stages and flag anything it cannot complete."
                />
              ) : (
                <div className="list">
                  {featuredTasks.slice(0, 6).map((task) => {
                    const progress = objectiveProgress(task.objective_id ?? '');
                    return (
                      <button
                        type="button"
                        key={task.id}
                        className="list__row list__row--clickable"
                        onClick={() => setSelectedTask(task)}
                      >
                        <Rail tone={taskTone(task.state)} />
                        <div className="list__main">
                          <span className="t-title t-truncate" style={{ fontSize: 12.5 }}>
                            {task.normalized_goal || task.original_request}
                          </span>
                          <span className="list__meta">
                            <Badge tone={taskTone(task.state)}>{taskLabel(task.state)}</Badge>
                            <span>{relTime(task.created_at)}</span>
                            {task.active_action_id && <span className="t-mono">{task.active_action_id}</span>}
                          </span>
                          {task.pending_question && (
                            <span className="t-warn t-clamp-2" style={{ fontSize: 11.5 }}>
                              Waiting on you: {task.pending_question}
                            </span>
                          )}
                          {task.terminal_reason && task.state === 'failed' && (
                            <span className="t-danger t-clamp-2" style={{ fontSize: 11.5 }}>
                              {task.terminal_reason}
                            </span>
                          )}
                          {progress !== null && (
                            <div style={{ marginTop: 5, maxWidth: 240 }}>
                              <Bar value={progress} tone="accent" />
                            </div>
                          )}
                        </div>
                        <div className="list__trail">
                          {(task.state === 'running' || task.state === 'waiting_for_user') ? (
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={(e) => {
                                e.stopPropagation();
                                void actions.cancelTask(task.id).catch((err: Error) =>
                                  notify({ tone: 'danger', title: 'Interrupt failed', body: err.message })
                                );
                              }}
                            >
                              Stop
                            </Button>
                          ) : null}
                          <Icon name="chevronRight" size={13} style={{ color: 'var(--text-3)' }} />
                        </div>
                      </button>
                    );
                  })}
                </div>
              )}
            </Panel>

            <Panel
              title="Recent activity"
              icon="clock"
             
            >
              {activity.isLoading ? (
                <LoadingBlock rows={3} />
              ) : recentEvents.length === 0 ? (
                <EmptyState
                  icon="activity"
                  title="No recorded activity"
                  description="Agent events appear here once Sythion has woken for a task or a background trigger."
                />
              ) : (
                <div className="timeline">
                  {recentEvents.map((ev, i) => (
                    <div className="timeline__item" key={`${ev.sequence_num}-${i}`}>
                      <div className="timeline__marker">
                        <span
                          className="dot"
                          style={{
                            background:
                              ev.event_type.includes('fail')
                                ? 'var(--danger)'
                                : ev.event_type.includes('complet')
                                  ? 'var(--ok)'
                                  : 'var(--info)',
                          }}
                        />
                        {i < recentEvents.length - 1 && <span className="timeline__line" />}
                      </div>
                      <div className="timeline__body">
                        <span className="t-body" style={{ color: 'var(--text-0)', fontSize: 12.5 }}>
                          {humanize(ev.event_type)}
                        </span>
                        <div className="list__meta">
                          <span>{relTime(ev.created_at)}</span>
                          <span className="t-mono">{summarise(ev.event_data)}</span>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </Panel>
          </div>

          {/* ---- Contextual column ----------------------------------- */}
          <div className="stack">
            <Panel
              title="Autonomy & permissions"
              icon="key"
              actions={
                <Button size="sm" variant="ghost" onClick={() => navigate('security')}>
                  Control
                  <Icon name="chevronRight" size={12} />
                </Button>
              }
            >
              <div className="stack stack--tight">
                <div className="row row--between">
                  <span className="t-title">{autonomyMeta.label}</span>
                  <Badge tone={autonomy.backgroundEnabled ? 'accent' : 'idle'}>
                    Background {autonomy.backgroundEnabled ? 'on' : 'off'}
                  </Badge>
                </div>
                <p className="t-muted">{autonomyMeta.tagline}</p>

                <div className="row row--between" style={{ marginTop: 6 }}>
                  <span className="t-muted">Authorization level</span>
                  <Badge tone={authorization.tone} icon={authorization.tone === 'ok' ? 'lock' : 'unlock'}>
                    {authorization.known ? authorization.label : 'Unknown'}
                  </Badge>
                </div>

                <div className="stack stack--tight" style={{ marginTop: 6 }}>
                  <span className="field__label">Acts without asking</span>
                  {autonomyMeta.can.map((c) => (
                    <span key={c} className="row t-muted" style={{ gap: 7, alignItems: 'flex-start' }}>
                      <Icon name="check" size={12} style={{ color: 'var(--ok)', flex: '0 0 auto', marginTop: 2 }} />
                      <span>{c}</span>
                    </span>
                  ))}
                </div>

                <div className="stack stack--tight" style={{ marginTop: 6 }}>
                  <span className="field__label">Needs your confirmation</span>
                  {autonomyMeta.mustAsk.map((c) => (
                    <span key={c} className="row t-muted" style={{ gap: 7, alignItems: 'flex-start' }}>
                      <Icon name="lock" size={12} style={{ color: 'var(--warn)', flex: '0 0 auto', marginTop: 2 }} />
                      <span>{c}</span>
                    </span>
                  ))}
                </div>
              </div>
            </Panel>

            <Panel
              title="System health"
              icon="activity"
              actions={
                <>
                      <Button size="sm" variant="ghost" onClick={() => navigate('system')}>
                    Diagnostics
                  </Button>
                </>
              }
            >
              {agentHealth.isLoading ? (
                <LoadingBlock rows={4} />
              ) : (
                <div className="stack stack--tight">
                  <KV
                    items={[
                      {
                        k: 'Backend',
                        v: (
                          <span className="row" style={{ gap: 6 }}>
                            <span
                              className={`dot dot--${health.data && !health.isError ? (health.data.status === 'degraded' ? 'warn' : 'ok') : 'danger'}`}
                            />
                            {health.data && !health.isError ? `${health.data.status === 'degraded' ? 'degraded · ' : ''}v${health.data.version}` : 'unreachable'}
                          </span>
                        ),
                      },
                      {
                        k: 'Database',
                        v: health.isError ? 'Unknown' : (health.data?.database_connected ? 'Connected' : 'Not connected'),
                      },
                      {
                        k: 'Network',
                        v: health.data?.cloud_configured ? 'Model provider configured' : 'Provider not configured',
                      },
                      {
                        k: 'Data stores',
                        v: `${(objectives.data ?? []).length} objectives · ${caps.total} capabilities registered`,
                      },
                    ]}
                  />

                  <div style={{ marginTop: 8 }}>
                    <span className="field__label">Checks</span>
                    <div className="stack stack--tight" style={{ marginTop: 5 }}>
                      {checks.map((c) => (
                        <div className="row row--between" key={c.name}>
                          <span className="t-muted t-truncate">{humanize(c.name)}</span>
                          <Badge tone={HEALTH_TONE[c.status] ?? 'idle'}>{c.status}</Badge>
                        </div>
                      ))}
                    </div>
                  </div>

                  {warnings.length > 0 && (
                    <Notice tone="warn" icon="alert">
                      <span>
                        {warnings.length} check{warnings.length === 1 ? '' : 's'} need attention. Open diagnostics
                        for evidence.
                      </span>
                    </Notice>
                  )}
                </div>
              )}
            </Panel>

            <Panel
              title="Objectives in flight"
              icon="target"
              actions={
                <Button size="sm" variant="ghost" onClick={() => navigate('missions')}>
                  Open
                  <Icon name="chevronRight" size={12} />
                </Button>
              }
            >
              {objectives.isLoading ? (
                <LoadingBlock rows={3} />
              ) : activeObjectives.length === 0 ? (
                <EmptyState
                  icon="target"
                  title="No active objectives"
                  description="Create one to give Sythion a long-running goal with its own plan and verification."
                  action={
                    <Button size="sm" variant="subtle" onClick={() => navigate('missions')}>
                      Go to Missions
                    </Button>
                  }
                />
              ) : (
                <div className="stack stack--tight">
                  {activeObjectives.slice(0, 4).map((o) => {
                    const done = o.steps.filter((s) => s.status === 'completed').length;
                    const pct = o.steps.length ? Math.round((done / o.steps.length) * 100) : 0;
                    const blocked = o.steps.filter((s) => s.status === 'blocked').length;
                    return (
                      <button
                        type="button"
                        key={o.id}
                        className="list__row list__row--clickable"
                        style={{ padding: '9px 0', borderRadius: 'var(--r-md)' }}
                        onClick={() => navigate('missions' as RouteId)}
                      >
                        <div className="list__main">
                          <span className="t-body t-truncate" style={{ color: 'var(--text-0)' }}>
                            {o.title}
                          </span>
                          <div className="list__meta">
                            <span>
                              {done}/{o.steps.length} steps
                            </span>
                            {blocked > 0 && <Badge tone="danger">{blocked} blocked</Badge>}
                          </div>
                          <Bar value={pct} tone={blocked > 0 ? 'warn' : 'accent'} />
                        </div>
                        <div className="list__trail t-faint t-mono">{pct}%</div>
                      </button>
                    );
                  })}
                  {(goals.data ?? []).length > 0 && (
                    <p className="t-faint" style={{ marginTop: 4 }}>
                      {(goals.data ?? []).length} goals tracked in the goal ledger.
                    </p>
                  )}
                </div>
              )}
            </Panel>

            {(delegations.data ?? []).length > 0 && (
              <Panel
                title="Specialist delegations"
                icon="layers"
               
              >
                <div className="stack stack--tight">
                  {(delegations.data ?? []).slice(0, 4).map((d) => (
                    <div className="row row--between" key={d.id}>
                      <span className="t-muted t-truncate">{d.objective ?? d.role ?? d.id}</span>
                      <Badge
                        tone={
                          d.status === 'COMPLETED'
                            ? 'ok'
                            : d.status === 'FAILED'
                              ? 'danger'
                              : d.status === 'RUNNING'
                                ? 'info'
                                : 'idle'
                        }
                      >
                        {humanize(d.status ?? 'unknown')}
                      </Badge>
                    </div>
                  ))}
                </div>
              </Panel>
            )}
          </div>
        </div>

        {/* Wakes detail — hidden unless there is something to show */}
        {(activity.data?.wakes ?? []).length > 0 && (
          <Panel title="Recent wakes" icon="power" flush>
            <div className="list">
              {(activity.data?.wakes ?? []).slice(0, 4).map((w) => (
                <div className="list__row" key={w.id}>
                  <Rail
                    tone={
                      w.outcome === 'failed' ? 'danger' : w.outcome === 'completed' ? 'ok' : 'info'
                    }
                  />
                  <div className="list__main">
                    <span className="t-body t-truncate">{humanize(w.trigger_type)}</span>
                    <span className="list__meta">
                      <span>{clockTime(w.started_at)}</span>
                      {w.started_at && <span>{duration(w.started_at, w.finished_at)}</span>}
                      <span>
                        {w.steps_completed}/{w.steps_attempted} steps
                      </span>
                    </span>
                  </div>
                  <div className="list__trail">
                    <Badge
                      tone={
                        w.outcome === 'failed'
                          ? 'danger'
                          : w.outcome === 'completed'
                            ? 'ok'
                            : 'idle'
                      }
                    >
                      {humanize(w.outcome ?? 'unknown')}
                    </Badge>
                  </div>
                </div>
              ))}
            </div>
          </Panel>
        )}

        <p className="t-faint" style={{ textAlign: 'center', paddingTop: 4 }}>
          Last checked {dateTime(new Date().toISOString())} · autonomy is enforced by the backend execution policy,
          not by this interface
        </p>
      </div>

      {selectedTask && (
        <TaskDetailDrawer
          task={selectedTask}
          onClose={() => setSelectedTask(null)}
          onChanged={tasks.refetch}
        />
      )}
    </div>
  );
}

function summarise(data: Record<string, unknown>): string {
  const entries = Object.entries(data ?? {});
  if (entries.length === 0) return '';
  return entries
    .slice(0, 2)
    .map(([k, v]) => `${k}=${typeof v === 'string' ? v.slice(0, 20) : String(v).slice(0, 20)}`)
    .join('  ');
}