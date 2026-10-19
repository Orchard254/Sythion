import { useMemo, useState } from 'react';
import { Icon } from '../components/icons';
import {
  Badge,
  Bar,
  Button,
  Chips,
  DataBadge,
  DemoTag,
  Drawer,
  EmptyState,
  KV,
  LoadingBlock,
  Notice,
  Panel,
  Rail,
  ScreenHead,
  SearchInput,
} from '../components/ui';
import { useGoals, useObjectives, useTasks } from '../hooks/useBackend';
import { useAppStore } from '../store/appStore';
import {
  OBJECTIVE_TONE,
  PLAN_STEP_TONE,
  dateTime,
  humanize,
  planProgress,
  relTime,
  taskLabel,
  taskTone,
} from '../lib/format';
import type { Objective, PlanStep } from '../types';

type Stage = 'planning' | 'approval' | 'execution' | 'verification';

function stageOf(o: Objective): { stage: Stage; label: string; tone: 'ok' | 'warn' | 'info' | 'idle' } {
  const blocked = o.steps.some((s) => s.status === 'blocked' || s.status === 'failed');
  const running = o.steps.some((s) => s.status === 'running' || s.status === 'in_progress');
  const allDone = o.steps.length > 0 && o.steps.every((s) => s.status === 'completed' || s.status === 'skipped');

  if (allDone) return { stage: 'verification', label: 'Verification', tone: 'ok' };
  if (blocked) return { stage: 'approval', label: 'Needs approval', tone: 'warn' };
  if (running) return { stage: 'execution', label: 'Executing', tone: 'info' };
  return { stage: 'planning', label: 'Planning', tone: 'idle' };
}

export function MissionsScreen() {
  const objectives = useObjectives();
  const goals = useGoals();
  const tasks = useTasks();
  const dataMode = useAppStore((s) => s.dataMode);

  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState<'__all__' | 'active' | 'paused' | 'completed'>('__all__');
  const [openId, setOpenId] = useState<string | null>(null);

  const all = useMemo(() => objectives.data ?? [], [objectives.data]);
  const list = useMemo(() => {
    const q = search.trim().toLowerCase();
    return all.filter((o) => {
      if (filter !== '__all__' && o.status !== filter) return false;
      if (!q) return true;
      return (
        o.title.toLowerCase().includes(q) ||
        (o.purpose ?? '').toLowerCase().includes(q) ||
        (o.current_summary ?? '').toLowerCase().includes(q)
      );
    });
  }, [all, search, filter]);

  const selected = all.find((o) => o.id === openId) ?? null;
  const totals = {
    active: all.filter((o) => o.status === 'active').length,
    paused: all.filter((o) => o.status === 'paused').length,
    completed: all.filter((o) => o.status === 'completed').length,
    blocked: all.filter((o) => o.steps.some((s) => s.status === 'blocked' || s.status === 'failed')).length,
  };

  return (
    <div className="screen">
      <div className="screen__inner">
        <ScreenHead
          eyebrow="Missions"
          title="Missions & Goals"
          description="Durable objectives with ordered plans. Progress is computed from verified steps, not self-reported progress."
          actions={
            dataMode === 'preview' ? (
              <DemoTag />
            ) : (
              <DataBadge origin="live" label={`${all.length} objectives`} />
            )
          }
        />

        <div className="grid grid--4">
          <div className="stat">
            <span className="stat__label">Active</span>
            <span className="stat__value">{totals.active}</span>
            <span className="stat__foot">Running against a live backend</span>
          </div>
          <div className="stat">
            <span className="stat__label">Paused</span>
            <span className="stat__value">{totals.paused}</span>
            <span className="stat__foot">Not being advanced</span>
          </div>
          <div className="stat">
            <span className="stat__label">Completed</span>
            <span className="stat__value">{totals.completed}</span>
            <span className="stat__foot">Criteria verified</span>
          </div>
          <div className="stat">
            <span className="stat__label">Blocked</span>
            <span className={`stat__value${totals.blocked > 0 ? ' t-danger' : ''}`}>{totals.blocked}</span>
            <span className="stat__foot">Waiting on approval or an external party</span>
          </div>
        </div>

        <div className="grid grid--aside">
          <div className="stack">
            <Panel title="Filter" icon="filter" tight>
              <div className="stack stack--tight">
                <SearchInput value={search} onChange={setSearch} placeholder="Search objectives…" />
                <Chips
                  value={filter}
                  onChange={setFilter}
                  options={[
                    { value: 'active', label: 'Active', count: totals.active },
                    { value: 'paused', label: 'Paused', count: totals.paused },
                    { value: 'completed', label: 'Completed', count: totals.completed },
                  ]}
                />
              </div>
            </Panel>

            <Panel title="Goal ledger" icon="target" flush>
              {(goals.data ?? []).length === 0 ? (
                <EmptyState icon="target" title="No goals recorded" />
              ) : (
                <div className="list">
                  {(goals.data ?? []).slice(0, 8).map((g) => (
                    <div className="list__row" key={g.id}>
                      <div className="list__main">
                        <span className="t-body t-truncate" style={{ color: 'var(--text-0)' }}>
                          {g.title}
                        </span>
                        {/* `progress` is the backend's derived object, not a scalar. */}
                        <Bar value={Math.round((g.progress?.fraction ?? 0) * 100)} tone="accent" />
                      </div>
                      <div className="list__trail t-faint t-mono">
                        {Math.round((g.progress?.fraction ?? 0) * 100)}%
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </Panel>
          </div>

          <Panel
            title="Objectives"
            icon="missions"
            flush
            actions={
              dataMode === 'preview' ? <DemoTag /> : <DataBadge origin="live" />
            }
          >
            {objectives.isLoading ? (
              <div className="panel__body">
                <LoadingBlock rows={4} />
              </div>
            ) : list.length === 0 ? (
              <EmptyState
                icon="missions"
                title={all.length === 0 ? 'No objectives yet' : 'No objectives match'}
                description={
                  all.length === 0
                    ? 'Objectives give Sythion a durable goal with its own plan, step verification and blockers.'
                    : 'Adjust the search or status filter.'
                }
              />
            ) : (
              <div className="list">
                {list.map((o) => {
                  const stage = stageOf(o);
                  const pct = planProgress(o.steps);
                  const blocked = o.steps.filter((s) => s.status === 'blocked' || s.status === 'failed');
                  return (
                    <button
                      type="button"
                      key={o.id}
                      className={`list__row list__row--clickable${o.id === openId ? ' list__row--selected' : ''}`}
                      onClick={() => setOpenId(o.id)}
                    >
                      <Rail tone={OBJECTIVE_TONE[o.status] ?? 'idle'} />
                      <div className="list__main">
                        <div className="row row--between" style={{ gap: 10 }}>
                          <span className="t-title t-truncate" style={{ fontSize: 13 }}>
                            {o.title}
                          </span>
                          <span className="t-faint t-mono" title={`Priority ${o.priority} (1 is highest)`}>
                            P{o.priority}
                          </span>
                        </div>

                        <span className="list__meta">
                          <Badge tone={OBJECTIVE_TONE[o.status] ?? 'idle'}>{o.status}</Badge>
                          <Badge tone={stage.tone}>{stage.label}</Badge>
                          <span>
                            {o.steps.filter((s) => s.status === 'completed').length}/{o.steps.length} steps
                          </span>
                          <span>· {relTime(o.updated_at)}</span>
                        </span>

                        {o.current_summary && (
                          <span className="t-muted t-clamp-2">{o.current_summary}</span>
                        )}

                        <div className="row" style={{ gap: 8, marginTop: 3 }}>
                          <div style={{ flex: 1, maxWidth: 260 }}>
                            <Bar value={pct} tone={blocked.length > 0 ? 'warn' : 'accent'} />
                          </div>
                          <span className="t-faint t-mono">{pct}%</span>
                          {o.criteria_verified && <Badge tone="ok" icon="check">verified</Badge>}
                        </div>

                        {blocked.length > 0 && (
                          <span className="t-warn t-clamp-2" style={{ fontSize: 11.5 }}>
                            Blocker: {blocked[0].blocker_reason ?? blocked[0].description}
                          </span>
                        )}
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
        </div>

        <Panel title="Recent task outcomes" icon="activity" flush>
          {(tasks.data ?? [])
            .filter((t) => t.state === 'completed' || t.state === 'failed')
            .slice(0, 5)
            .map((t) => (
              <div className="list__row" key={t.id}>
                <Rail tone={taskTone(t.state)} />
                <div className="list__main">
                  <span className="t-body t-clamp-2">{t.normalized_goal || t.original_request}</span>
                  <span className="list__meta">
                    <Badge tone={taskTone(t.state)}>{taskLabel(t.state)}</Badge>
                    <span>{relTime(t.updated_at)}</span>
                    {t.objective_id && <span className="t-mono">{t.objective_id}</span>}
                  </span>
                </div>
              </div>
            ))}
        </Panel>
      </div>

      {selected && (
        <ObjectiveDrawer objective={selected} tasks={tasks.data ?? []} onClose={() => setOpenId(null)} />
      )}
    </div>
  );
}

function ObjectiveDrawer({
  objective,
  tasks,
  onClose,
}: {
  objective: Objective;
  tasks: import('../types').Task[];
  onClose: () => void;
}) {
  const pct = planProgress(objective.steps);
  const linked = tasks.filter((t) => t.objective_id === objective.id);
  const stage = stageOf(objective);

  return (
    <Drawer
      title={objective.title}
      subtitle={
        <span className="row" style={{ gap: 8 }}>
          <Badge tone={OBJECTIVE_TONE[objective.status] ?? 'idle'}>{objective.status}</Badge>
          <Badge tone={stage.tone}>{stage.label}</Badge>
          <span className="t-mono">P{objective.priority}</span>
        </span>
      }
      onClose={onClose}
      width={600}
      footer={
        <Button variant="subtle" onClick={onClose}>
          Close
        </Button>
      }
    >
      {objective.purpose && (
        <div className="stack stack--tight">
          <span className="field__label">Purpose</span>
          <p className="t-body">{objective.purpose}</p>
        </div>
      )}

      {objective.current_summary && (
        <Notice tone="info" icon="info">
          {objective.current_summary}
        </Notice>
      )}

      <div className="stack stack--tight">
        <div className="row row--between">
          <span className="field__label">Plan progress</span>
          <span className="t-faint t-mono">{pct}%</span>
        </div>
        <Bar value={pct} tone={objective.steps.some((s) => s.status === 'blocked') ? 'warn' : 'accent'} />
      </div>

      {objective.completion_criteria && (
        <div className="stack stack--tight">
          <span className="field__label">Completion criteria</span>
          <p className="t-body">{objective.completion_criteria}</p>
          <span className="row" style={{ gap: 7 }}>
            <Badge tone={objective.criteria_verified ? 'ok' : 'idle'} icon={objective.criteria_verified ? 'check' : 'clock'}>
              {objective.criteria_verified ? 'Verified by read-back' : 'Not yet verified'}
            </Badge>
          </span>
        </div>
      )}

      <div className="stack stack--tight">
        <span className="field__label">Execution plan</span>
        <div>
          {objective.steps
            .slice()
            .sort((a, b) => a.step_order - b.step_order)
            .map((s: PlanStep) => (
              <div className="step" key={s.id}>
                <span
                  className={`step__marker${
                    s.status === 'completed'
                      ? ' step--done'
                      : s.status === 'blocked' || s.status === 'failed'
                        ? ' step--blocked'
                        : s.status === 'running' || s.status === 'in_progress'
                          ? ' step--active'
                          : ''
                  }`}
                >
                  {s.status === 'completed' ? (
                    <Icon name="check" size={9} strokeWidth={2.5} />
                  ) : s.status === 'blocked' || s.status === 'failed' ? (
                    <Icon name="close" size={9} strokeWidth={2.5} />
                  ) : (
                    s.step_order
                  )}
                </span>
                <div className="list__main">
                  <span className="t-body" style={{ color: 'var(--text-0)' }}>
                    {s.description}
                  </span>
                  <span className="list__meta">
                    <Badge tone={PLAN_STEP_TONE[s.status] ?? 'idle'}>{humanize(s.status)}</Badge>
                    {s.linked_task_id && <span className="t-mono">{s.linked_task_id}</span>}
                    <span>· {relTime(s.updated_at)}</span>
                  </span>
                  {s.result_summary && (
                    <span className="t-ok" style={{ fontSize: 11.5 }}>
                      {s.result_summary}
                    </span>
                  )}
                  {s.blocker_reason && (
                    <span className="t-danger" style={{ fontSize: 11.5 }}>
                      Blocked: {s.blocker_reason}
                    </span>
                  )}
                </div>
              </div>
            ))}
        </div>
      </div>

      {linked.length > 0 && (
        <div className="stack stack--tight">
          <span className="field__label">Linked tasks</span>
          {linked.map((t) => (
            <div className="row row--between" key={t.id}>
              <span className="t-muted t-truncate">{t.normalized_goal || t.original_request}</span>
              <Badge tone={taskTone(t.state)}>{taskLabel(t.state)}</Badge>
            </div>
          ))}
        </div>
      )}

      <KV
        items={[
          { k: 'Objective id', v: <span className="t-mono">{objective.id}</span> },
          { k: 'Created', v: dateTime(objective.created_at) },
          { k: 'Updated', v: `${dateTime(objective.updated_at)} · ${relTime(objective.updated_at)}` },
          { k: 'Last task', v: objective.last_task_id ?? '—' },
        ]}
      />
    </Drawer>
  );
}