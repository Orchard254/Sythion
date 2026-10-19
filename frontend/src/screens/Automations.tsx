import { useMemo, useState } from 'react';
import { Icon } from '../components/icons';
import {
  Badge,
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
  Stat,
  Switch,
} from '../components/ui';
import { useAgentActivity, useBackgroundPolicy, useSkills, useSkillProposals } from '../hooks/useBackend';
import { useAppStore } from '../store/appStore';
import { demoAutomationRuns, demoAutomations } from '../data/demo';
import { humanize, relTime } from '../lib/format';

/* Automations.
 *
 * Live: the skill registry (GET /api/v1/skills), pending skill proposals, and
 * the background-orchestration switch that actually causes event-driven work
 * to run (GET|PUT /api/v1/agent/background).
 * Demo: the schedule/event workflow catalogue — there is no scheduler or
 * workflow-definition endpoint in the backend.
 */

const RUN_TONE: Record<string, 'ok' | 'warn' | 'danger' | 'idle' | 'info'> = {
  ok: 'ok',
  warn: 'warn',
  fail: 'danger',
  failed: 'danger',
  idle: 'idle',
  blocked: 'warn',
};

type Automation = (typeof demoAutomations)[number];

export function AutomationsScreen() {
  const background = useBackgroundPolicy();
  const skills = useSkills();
  const proposals = useSkillProposals();
  const activity = useAgentActivity();
  const dataMode = useAppStore((s) => s.dataMode);

  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState<'__all__' | 'enabled' | 'disabled' | 'failing'>('__all__');
  const [openId, setOpenId] = useState<string | null>(null);
  const [localEnabled, setLocalEnabled] = useState<Record<string, boolean>>(() =>
    Object.fromEntries(demoAutomations.map((a) => [a.id, a.enabled]))
  );

  const list = useMemo(() => {
    const q = search.trim().toLowerCase();
    return demoAutomations.filter((a) => {
      const enabled = localEnabled[a.id] ?? a.enabled;
      if (filter === 'enabled' && !enabled) return false;
      if (filter === 'disabled' && enabled) return false;
      if (filter === 'failing' && !['fail', 'failed', 'warn', 'blocked'].includes(a.lastOutcome)) return false;
      if (!q) return true;
      return a.name.toLowerCase().includes(q) || a.trigger.toLowerCase().includes(q);
    });
  }, [search, filter, localEnabled]);

  const enabledCount = Object.values(localEnabled).filter(Boolean).length;
  const failing = demoAutomations.filter((a) =>
    ['fail', 'failed', 'warn', 'blocked'].includes(a.lastOutcome)
  ).length;
  const pendingProposals = (proposals.data ?? []).filter((p) => (p.status ?? '').includes('pending')).length;

  const wakesFromTriggers = (activity.data?.wakes ?? []).filter((w) => w.trigger_type !== 'user.request');

  return (
    <div className="screen">
      <div className="screen__inner">
        <ScreenHead
          eyebrow="Environment"
          title="Automations"
          description="Scheduled and event-triggered work, the switch that lets it run unattended, and the record of what it actually did."
          actions={dataMode === 'preview' ? <DemoTag /> : <DataBadge origin="live" />}
        />

        <Notice tone="warn" icon="alert">
          <span>
            <strong>The workflow catalogue is a demonstration.</strong> Sythion has no scheduler or workflow-definition
            endpoint. What <em>is</em> real on this screen: the background-orchestration switch, the validated skill
            registry, and the agent wake history it produces.
          </span>
        </Notice>

        <div className="grid grid--4">
          <Stat
            label="Enabled"
            icon="automations"
            value={`${enabledCount}/${demoAutomations.length}`}
            foot="Toggle state is local to this session"
          />
          <Stat
            label="Needing attention"
            icon="alert"
            value={failing}
            tone={failing > 0 ? 'accent' : undefined}
            foot="Last run warned or failed"
          />
          <Stat
            label="Validated skills"
            icon="layers"
            value={skills.isLoading ? '—' : (skills.data ?? []).length}
            foot="Real backend registry"
          />
          <Stat
            label="Triggered wakes"
            icon="power"
            value={wakesFromTriggers.length}
            foot="Started by a trigger, not by you"
          />
        </div>

        {/* --- Live controls ------------------------------------------ */}
        <div className="grid grid--2">
          <Panel
            title="Background orchestration"
            icon="power"
            actions={dataMode === 'preview' ? <DemoTag /> : <DataBadge origin="live" />}
          >
            <div className="stack stack--tight">
              <div className="row row--between">
                <div style={{ minWidth: 0 }}>
                  <span className="t-title">Event-driven background work</span>
                  <p className="t-muted" style={{ marginTop: 3 }}>
                    When enabled, the Main Agent may wake on registered triggers and advance active objectives without
                    being asked.
                  </p>
                </div>
                <Badge tone={background.data?.enabled ? 'accent' : 'idle'}>
                  {background.data?.enabled ? 'Enabled' : 'Disabled'}
                </Badge>
              </div>
              <KV
                items={[
                  { k: 'Endpoint', v: <span className="t-mono">PUT /api/v1/agent/background</span> },
                  { k: 'Backend default', v: background.data?.default ? 'on' : 'off' },
                  { k: 'Current value', v: background.data ? String(background.data.enabled) : '—' },
                ]}
              />
              {dataMode === 'preview' ? (
                <Notice tone="warn" icon="lock">
                  <span>Read-only in preview mode — no backend is attached.</span>
                </Notice>
              ) : null}
            </div>
          </Panel>

          <Panel
            title="Pending review"
            icon="shield"
            actions={dataMode === 'preview' ? <DemoTag /> : <DataBadge origin="live" />}
            flush
          >
            {(proposals.data ?? []).length === 0 ? (
              <EmptyState
                icon="check"
                title="Nothing awaiting review"
                description="Sythion proposes new skills after seeing repeated successful traces. Approval is always yours."
              />
            ) : (
              <div className="list">
                {(proposals.data ?? []).map((p) => (
                  <div className="list__row" key={String(p.id)}>
                    <Rail tone="warn" />
                    <div className="list__main">
                      <span className="t-title" style={{ fontSize: 12.5 }}>
                        {p.name ?? `Proposal ${p.id}`}
                      </span>
                      <span className="list__meta">
                        <Badge tone="warn">{humanize(p.status ?? 'unknown')}</Badge>
                        {typeof p.evidence_count === 'number' && (
                          <span>· {p.evidence_count} similar runs</span>
                        )}
                      </span>
                    </div>
                    <div className="list__trail">
                      <Button size="sm" variant="ghost" onClick={() => setOpenId(`skill:${p.id}`)}>
                        Review
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            )}
            {pendingProposals === 0 && (proposals.data ?? []).length > 0 && (
              <div className="panel__foot t-faint">No proposals are awaiting your decision.</div>
            )}
          </Panel>
        </div>

        {/* --- Workflow catalogue ------------------------------------- */}
        <div className="grid grid--aside">
          <div className="stack">
            <Panel title="Filter" icon="filter" tight>
              <div className="stack stack--tight">
                <SearchInput value={search} onChange={setSearch} placeholder="Search workflows…" />
                <Chips
                  value={filter}
                  onChange={setFilter}
                  options={[
                    { value: 'enabled', label: 'Enabled', count: enabledCount },
                    { value: 'disabled', label: 'Disabled', count: demoAutomations.length - enabledCount },
                    { value: 'failing', label: 'Failing', count: failing },
                  ]}
                />
              </div>
            </Panel>

            <Panel
              title="Validated skills"
              icon="layers"
              flush
              actions={dataMode === 'preview' ? <DemoTag /> : <DataBadge origin="live" />}
            >
              {skills.isLoading ? (
                <div className="panel__body">
                  <LoadingBlock rows={3} />
                </div>
              ) : (skills.data ?? []).length === 0 ? (
                <EmptyState icon="layers" title="No skills registered" />
              ) : (
                <div className="list">
                  {(skills.data ?? []).map((s, i) => (
                    <div className="list__row" key={String(s.id ?? s.name ?? i)}>
                      <Rail tone={s.enabled ? 'ok' : 'idle'} />
                      <div className="list__main">
                        <span className="t-mono t-title" style={{ fontSize: 12 }}>
                          {s.name ?? `skill-${i}`}
                        </span>
                        <span className="list__meta">
                          <Badge tone={s.enabled ? 'ok' : 'idle'}>{s.enabled ? 'enabled' : 'disabled'}</Badge>
                          {s.version !== undefined && <span>· v{String(s.version)}</span>}
                          {s.status && <span>· {humanize(String(s.status))}</span>}
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </Panel>
          </div>

          <Panel title="Workflows" icon="automations" flush actions={<DemoTag />}>
            {list.length === 0 ? (
              <EmptyState icon="automations" title="No workflows match" description="Adjust the filters." />
            ) : (
              <div className="list">
                {list.map((a) => (
                  <div className="list__row" key={a.id}>
                    <Rail tone={RUN_TONE[a.lastOutcome] ?? 'idle'} />
                    <div className="list__main">
                      <div className="row row--between" style={{ gap: 10 }}>
                        <span className="t-title t-truncate" style={{ fontSize: 12.5 }}>
                          {a.name}
                        </span>
                        <span className="t-faint t-mono">{a.runs} runs</span>
                      </div>
                      <span className="list__meta">
                        <Badge tone="plain">{a.trigger}</Badge>
                        <Badge tone={RUN_TONE[a.lastOutcome] ?? 'idle'}>{a.lastOutcome}</Badge>
                      </span>
                      <span className="t-muted t-clamp-2">{a.actions}</span>
                      <span className="list__meta">
                        <span>Last run {a.lastRun}</span>
                        <span>· next {a.nextRun}</span>
                      </span>
                    </div>
                    <div className="list__trail">
                      <Switch
                        checked={localEnabled[a.id] ?? a.enabled}
                        onChange={(v) => setLocalEnabled((prev) => ({ ...prev, [a.id]: v }))}
                      />
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => setOpenId(a.id)}
                        aria-label={`Inspect ${a.name}`}
                      >
                        <Icon name="chevronRight" size={12} />
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Panel>
        </div>

        <Panel title="Recent runs" icon="activity" flush actions={<DemoTag />}>
          <div className="list">
            {demoAutomationRuns.map((r) => (
              <div className="list__row" key={r.id}>
                <Rail tone={RUN_TONE[r.outcome]} />
                <div className="list__main">
                  <span className="t-body" style={{ color: 'var(--text-0)' }}>
                    {r.name}
                  </span>
                  <span className="list__meta">
                    <span>{r.started}</span>
                    <span>· {r.duration}</span>
                  </span>
                  <span className="t-muted">{r.detail}</span>
                </div>
                <div className="list__trail">
                  <Badge tone={RUN_TONE[r.outcome]}>{r.outcome}</Badge>
                </div>
              </div>
            ))}
          </div>
        </Panel>

        <Panel title="Triggered wakes from the live ledger" icon="power" flush>
          {wakesFromTriggers.length === 0 ? (
            <EmptyState
              icon="power"
              title="No background wakes recorded"
              description="When background orchestration is enabled and a trigger fires, the wake appears here with its outcome."
            />
          ) : (
            <div className="list">
              {wakesFromTriggers.map((w) => (
                <div className="list__row" key={w.id}>
                  <Rail tone={w.outcome === 'failed' ? 'danger' : w.outcome === 'completed' ? 'ok' : 'info'} />
                  <div className="list__main">
                    <span className="t-body">{humanize(w.trigger_type)}</span>
                    <span className="list__meta">
                      <span>{relTime(w.started_at)}</span>
                      {w.trigger_reference && <span className="t-mono">· {w.trigger_reference}</span>}
                      <span>
                        · {w.steps_completed}/{w.steps_attempted} steps
                      </span>
                    </span>
                  </div>
                  <div className="list__trail">
                    <Badge tone={w.outcome === 'failed' ? 'danger' : 'ok'}>
                      {humanize(w.outcome ?? 'unknown')}
                    </Badge>
                  </div>
                </div>
              ))}
            </div>
          )}
        </Panel>
      </div>

      {openId?.startsWith('skill:') && (
        <ProposalDrawer
          proposalId={openId.slice('skill:'.length)}
          onClose={() => setOpenId(null)}
        />
      )}
      {openId && !openId.startsWith('skill:') && (
        <AutomationDrawer
          automation={demoAutomations.find((a) => a.id === openId)!}
          onClose={() => setOpenId(null)}
        />
      )}
    </div>
  );
}

function AutomationDrawer({
  automation,
  onClose,
}: {
  automation: Automation;
  onClose: () => void;
}) {
  return (
    <Drawer
      title={automation.name}
      subtitle={
        <span className="row" style={{ gap: 8 }}>
          <Badge tone={RUN_TONE[automation.lastOutcome]}>{automation.lastOutcome}</Badge>
          <DemoTag compact />
        </span>
      }
      onClose={onClose}
      footer={
        <Button variant="subtle" onClick={onClose}>
          Close
        </Button>
      }
    >
      <Notice tone="warn" icon="info">
        <span>
          This workflow is a demonstration record. Editing it would not create a real schedule — Sythion has no
          workflow-definition endpoint to write to.
        </span>
      </Notice>

      <div className="stack stack--tight">
        <span className="field__label">Pipeline</span>
        <div className="step">
          <span className="step__marker">
            <Icon name="clock" size={9} />
          </span>
          <div className="list__main">
            <span className="t-body" style={{ color: 'var(--text-0)' }}>
              When
            </span>
            <span className="t-muted">{automation.trigger}</span>
          </div>
        </div>
        <div className="step">
          <span className="step__marker">
            <Icon name="filter" size={9} />
          </span>
          <div className="list__main">
            <span className="t-body" style={{ color: 'var(--text-0)' }}>
              Only if
            </span>
            <span className="t-muted">{automation.conditions}</span>
          </div>
        </div>
        <div className="step">
          <span className="step__marker">
            <Icon name="play" size={9} />
          </span>
          <div className="list__main">
            <span className="t-body" style={{ color: 'var(--text-0)' }}>
              Then
            </span>
            <span className="t-muted">{automation.actions}</span>
          </div>
        </div>
      </div>

      <KV
        items={[
          { k: 'State', v: automation.enabled ? 'Enabled' : 'Paused' },
          { k: 'Last run', v: automation.lastRun },
          { k: 'Last outcome', v: automation.lastOutcome },
          { k: 'Next run', v: automation.nextRun },
          { k: 'Total runs', v: String(automation.runs) },
        ]}
      />

      <Notice tone="info" icon="shield">
        <span>
          A real workflow would still be gated by capability policy: each action resolves to a capability that must be
          AVAILABLE before it can run.
        </span>
      </Notice>
    </Drawer>
  );
}

function ProposalDrawer({ proposalId, onClose }: { proposalId: string; onClose: () => void }) {
  return (
    <Drawer
      title={`Skill proposal ${proposalId}`}
      subtitle={<DemoTag />}
      onClose={onClose}
      footer={
        <>
          <Button variant="subtle" onClick={onClose}>
            Close
          </Button>
          <Button variant="primary" disabled>
            Approve
          </Button>
          <Button variant="danger" disabled>
            Reject
          </Button>
        </>
      }
    >
      <Notice tone="warn" icon="alert">
        <span>
          Approve and reject for skill proposals call{' '}
          <span className="t-mono">POST /api/v1/skills/proposals/&#123;id&#125;/approve</span>. They are disabled here
          because no backend is attached — approving a proposal is a genuine authorization decision and must not be
          performable against demonstration data.
        </span>
      </Notice>
      <p className="t-muted">
        Sythion proposes a skill once it has seen several materially similar successful traces. Approval records your
        review; nothing is executed automatically.
      </p>
    </Drawer>
  );
}