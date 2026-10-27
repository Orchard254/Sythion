import { useMemo, useState } from 'react';
import { Icon } from '../components/icons';
import { GoalDetailDrawer } from '../components/GoalDetailDrawer';
import { TaskDetailDrawer } from '../components/TaskDetailDrawer';
import {
  Badge,
  Bar,
  Chips,
  DataBadge,
  DemoTag,
  Drawer,
  EmptyState,
  Panel,
  ScreenHead,
} from '../components/ui';
import { useGoals, useObjectives, useTasks } from '../hooks/useBackend';
import { useAppStore } from '../store/appStore';
import { relTime } from '../lib/format';
import type { GoalSummary } from '../types';

/* Goals.
 *
 * "Goals" is the canonical user-facing term. The route id remains `missions`
 * and `#/goals` redirects to it, so existing links keep resolving.
 *
 * The model shown is exactly:
 *   Goal -> versioned Plan -> Plan Steps and associated Tasks -> evidence.
 * Progress comes from the backend's own derivation over persisted step state
 * (services/goal_manager.py `progress`), never from a model-reported number.
 * The richer per-goal view lives in GoalDetailDrawer: Plan, Activity, and
 * Results & Evidence.
 *
 * The previous Missions screen is left in place and unreferenced, because it
 * contains unrelated work that must not be discarded. */

const STATUS_TONE: Record<string, 'ok' | 'warn' | 'idle' | 'accent' | 'danger'> = {
  active: 'accent',
  paused: 'warn',
  completed: 'ok',
  abandoned: 'idle',
  failed: 'danger',
};

// `Chips` renders its own implicit "All" chip, so the sentinel is not listed
// here — including it would produce two identical "All" controls.
const FILTERS = [
  { value: 'active', label: 'Active' },
  { value: 'paused', label: 'Paused' },
  { value: 'completed', label: 'Completed' },
  { value: 'abandoned', label: 'Abandoned' },
];

export function GoalsScreen() {
  const goals = useGoals();
  const objectives = useObjectives();
  const tasks = useTasks();

  const dataMode = useAppStore((s) => s.dataMode);
  const [filter, setFilter] = useState('__all__');
  const [openGoal, setOpenGoal] = useState<string | null>(null);
  const [openTask, setOpenTask] = useState<string | null>(null);

  const all = useMemo(() => goals.data ?? [], [goals.data]);
  const list = useMemo(
    () => (filter === '__all__' ? all : all.filter((g) => g.status === filter)),
    [all, filter]
  );

  // Association is by the backend's own objective_id link. No tree is implied.
  const taskCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const t of tasks.data ?? []) {
      if (t.objective_id) counts.set(t.objective_id, (counts.get(t.objective_id) ?? 0) + 1);
    }
    return counts;
  }, [tasks.data]);

  const isDemo = dataMode === 'preview';
  const selectedTask = openTask ? (tasks.data ?? []).find((t) => t.id === openTask) : undefined;

  return (
    <div className="screen">
      <div className="screen__inner">
        <ScreenHead
          eyebrow="Operate"
          title="Goals"
          description="Each goal holds a versioned plan. Plans hold steps, steps hold tasks, and results are only called verified when the criteria evidence says so."
          actions={isDemo ? <DemoTag /> : <DataBadge origin="live" />}
        />

        <Chips value={filter} onChange={setFilter} options={FILTERS} />

        {goals.isLoading ? (
          <div className="skeleton" style={{ height: 140 }} />
        ) : list.length === 0 ? (
          <Panel title="Goals" icon="target" flush>
            <EmptyState
              icon="target"
              title={all.length === 0 ? 'No goals yet' : 'Nothing matches this filter'}
              description={
                all.length === 0
                  ? 'No goal exists. Goals are created through the backend; this screen only reads them.'
                  : 'Switch the filter to see other goals.'
              }
            />
          </Panel>
        ) : (
          <div className="grid grid--2">
            {list.map((g) => (
              <GoalCard
                key={g.id}
                goal={g}
                taskCount={taskCounts.get(g.id) ?? 0}
                objective={objectives.data?.find((o) => o.id === g.id) ?? null}
                onOpen={() => setOpenGoal(g.id)}
              />
            ))}
          </div>
        )}

        {/* Objectives remain reachable for anyone relying on that endpoint, and
            it is the source of the nested step view. */}
        {(objectives.data ?? []).length > 0 && (
          <Panel title="Plan steps by objective" icon="layers" flush>
            <div className="list">
              {(objectives.data ?? []).map((o) => (
                <button
                  type="button"
                  key={o.id}
                  className="list__row list__row--clickable"
                  onClick={() => setOpenGoal(o.id)}
                >
                  <Badge tone={STATUS_TONE[o.status] ?? 'idle'}>{o.status}</Badge>
                  <div className="list__main">
                    <span className="t-title t-truncate" style={{ fontSize: 12.5 }}>
                      {o.title}
                    </span>
                    <span className="list__meta">
                      <span>{(o.steps ?? []).length} steps</span>
                      <span>updated {relTime(o.updated_at)}</span>
                      {o.criteria_verified && <span>criteria verified</span>}
                    </span>
                  </div>
                  <div className="list__trail">
                    <Icon name="chevronRight" size={13} style={{ color: 'var(--text-3)' }} />
                  </div>
                </button>
              ))}
            </div>
          </Panel>
        )}
      </div>

      {openGoal && (
        <GoalDetailDrawer
          goalId={openGoal}
          origin={goals.origin}
          onClose={() => setOpenGoal(null)}
          onOpenTask={(id) => {
            setOpenGoal(null);
            setOpenTask(id);
          }}
        />
      )}
      {openTask && selectedTask ? (
        <TaskDetailDrawer
          task={selectedTask}
          origin={tasks.origin}
          onClose={() => setOpenTask(null)}
          onChanged={tasks.refetch}
        />
      ) : openTask ? (
        <Drawer title="Task unavailable" onClose={() => setOpenTask(null)}>
          <EmptyState icon="inbox" title="Task unavailable" description={`Task ${openTask} is not in the current task list.`} />
        </Drawer>
      ) : null}
    </div>
  );
}

function GoalCard({
  goal,
  taskCount,
  objective,
  onOpen,
}: {
  goal: GoalSummary;
  taskCount: number;
  objective: { completion_criteria: string | null; criteria_verified: boolean } | null;
  onOpen: () => void;
}) {
  const p = goal.progress;
  const pct = Math.round((p?.fraction ?? 0) * 100);
  const hasSteps = (p?.total_required_steps ?? 0) > 0;
  const blocked = (p?.open_step_states ?? []).includes('blocked');

  return (
    <Panel title={undefined} className="goal-card">
      <button type="button" className="goal-card__hit" onClick={onOpen} aria-label={`Open ${goal.title}`}>
        <div className="goal-card__head">
          <Badge tone={STATUS_TONE[goal.status] ?? 'idle'}>{goal.status}</Badge>
          <span className="t-faint">priority {goal.priority}</span>
          <span className="spacer" />
          <Icon name="chevronRight" size={14} style={{ color: 'var(--text-3)' }} />
        </div>

        <h3 className="goal-card__title">{goal.title}</h3>

        {objective?.completion_criteria && (
          <p className="t-muted t-clamp-2">{objective.completion_criteria}</p>
        )}

        <div className="stack stack--tight" style={{ marginTop: 'auto' }}>
          <div className="row row--between">
            <span className="t-faint">
              {hasSteps
                ? `${p?.completed_steps ?? 0} of ${p?.total_required_steps ?? 0} steps`
                : 'no plan steps recorded'}
            </span>
            <span className="t-faint t-mono">{hasSteps ? `${pct}%` : '—'}</span>
          </div>
          <Bar value={pct} tone={blocked ? 'warn' : 'accent'} />

          <div className="row row--wrap" style={{ gap: 7 }}>
            <span className="t-faint">{taskCount} associated task{taskCount === 1 ? '' : 's'}</span>
            {blocked && (
              <Badge tone="warn">
                <Icon name="alert" size={10} /> blocked step
              </Badge>
            )}
            {objective && (
              <Badge tone={objective.criteria_verified ? 'ok' : 'idle'}>
                {objective.criteria_verified ? 'criteria verified' : 'unverified'}
              </Badge>
            )}
          </div>
        </div>
      </button>
    </Panel>
  );
}
