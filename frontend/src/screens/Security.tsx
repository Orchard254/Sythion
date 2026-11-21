import { Fragment, useEffect, useMemo, useState, type ReactNode } from 'react';
import { Icon } from '../components/icons';
import {
  Badge,
  Button,
  ConfirmDialog,
  EmptyState,
  KV,
  LoadingBlock,
  Notice,
  Panel,
  Rail,
  ScreenHead,
  Stat,
  Switch,
} from '../components/ui';
import {
  useActions,
  useAgent,
  useAgentActivity,
  useDevices,
  useAgentHealth,
  useBackgroundPolicy,
  useCapabilities,
  useCapabilityConsistency,
  useHealth,
  useMemoryEdges,
  usePendingApprovals,
  useSkillProposals,
  useSkills,
  useTasks,
  useTools,
  useToolProposals,
  useUncertainRemoteActions,
} from '../hooks/useBackend';
import { useAppStore } from '../store/appStore';
import {
  AUTONOMY_MODES,
  capabilityCounts,
  deriveAuthorization,
  deriveAutonomy,
  type AutonomyModeId,
} from '../lib/autonomy';
import {
  AGENT_STATE_TONE,
  CAPABILITY_TONE,
  RISK_TONE,
  humanize,
  relTime,
  taskLabel,
  taskTone,
} from '../lib/format';
import { useNavStore } from '../store/navStore';
import { countDecisionsDue } from '../lib/decisionCounts';
import { DevicesCard } from '../components/DevicesCard';
import { PendingApprovalsPanel } from '../components/PendingApprovalsPanel';

/**
 * One collapsed "Advanced / for support" disclosure per panel. Raw backend
 * fields (ids, enums, event data, versions) and implementation prose live in
 * here so the default view stays meaningful to an owner. Closed by default.
 */
function AdvancedDetails({ id, children }: { id: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="stack stack--tight" style={{ marginTop: 10 }}>
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

export function SecurityScreen() {
  const agent = useAgent();
  const background = useBackgroundPolicy();
  const capabilities = useCapabilities();
  const capabilityConsistency = useCapabilityConsistency();
  const tools = useTools();
  const skills = useSkills();
  const agentHealth = useAgentHealth();
  const health = useHealth();
  const tasks = useTasks();
  const activity = useAgentActivity();
  const edges = useMemoryEdges();
  const skillProposals = useSkillProposals();
  const toolProposals = useToolProposals();
  const devices = useDevices();
  const pendingApprovals = usePendingApprovals();
  const uncertainActions = useUncertainRemoteActions();
  const actions = useActions();
  const notify = useAppStore((s) => s.notify);
  const navigate = useNavStore((s) => s.setRoute);

  const [busy, setBusy] = useState<string | null>(null);
  const [confirmPause, setConfirmPause] = useState(false);
  // Which tool proposal is expanded into its review/detail view (if any).
  const [reviewingTool, setReviewingTool] = useState<number | null>(null);
  // Optimistic mirror of the background switch so the control responds to the
  // click immediately instead of waiting for the refetch round-trip.
  const [bgOptimistic, setBgOptimistic] = useState<boolean | null>(null);

  const autonomy = deriveAutonomy(agent.data, background.data);
  const authorization = deriveAuthorization(agent.data);
  const autonomyMeta = AUTONOMY_MODES.find((m) => m.id === autonomy.mode)!;
  const caps = useMemo(() => capabilities.data ?? [], [capabilities.data]);
  const counts = useMemo(() => capabilityCounts(caps), [caps]);

  const gated = caps.filter((c) => c.requires_permission || c.risk_class === 'HIGH');
  const unavailable = caps.filter((c) => c.status === 'UNAVAILABLE' || c.status === 'DISABLED');

  const pendingSkill = (skillProposals.data ?? []).filter((p) => p.status === 'proposed' || p.status === 'pending_review');
  const pendingTools = (toolProposals.data ?? []).filter((p) => p.status === 'pending_review');
  const heldTasks = (tasks.data ?? []).filter((t) => t.state === 'interrupted');
  const awaitingInput = (tasks.data ?? []).filter((t) => t.state === 'waiting_for_user');
  const sensitive = (tasks.data ?? []).filter((t) => t.state === 'failed' || t.state === 'cancelled');

  const permissionProposals = pendingApprovals.data?.proposals ?? [];
  const uncertain = uncertainActions.data?.actions ?? [];
  // Same decision inputs as the navigation badge in App.tsx: proposals,
  // permission requests, held/waiting tasks, and uncertain remote actions.
  const pendingCount = countDecisionsDue({
    proposalApprovals: pendingSkill.length + pendingTools.length,
    ownerApprovals: permissionProposals.length,
    needsReconciliation: uncertain.length,
    awaitingYou: heldTasks.length + awaitingInput.length,
  });
  const staleEdges = (edges.data ?? []).filter((e) => e.status === 'stale');

  // Plain, owner-meaningual health status for the default view. A degraded or
  // failed report is NEVER presented as healthy; raw health enums live in the
  // Runtime identity panel's Advanced disclosure only.
  const healthOverall = agentHealth.data?.overall;
  const hasHealthReport = healthOverall !== undefined;
  const degradedHealth =
    healthOverall === 'DEGRADED' ||
    healthOverall === 'UNAVAILABLE' ||
    healthOverall === 'UNKNOWN' ||
    health.isError;
  const attention = degradedHealth;
  const statusHeadline = attention
    ? 'Synth needs attention'
    : !hasHealthReport
      ? "Checking Synth's health"
      : pendingCount > 0
        ? 'Synth needs your review'
        : 'Synth is healthy';
  const statusDetail = attention
    ? 'Something in the runtime needs a closer look. The raw health details are under Advanced / for support.'
    : !hasHealthReport
      ? 'The health report is still loading.'
      : pendingCount > 0
        ? `${pendingCount} decision${pendingCount === 1 ? '' : 's'} ${pendingCount === 1 ? 'is' : 'are'} waiting for you below.`
        : 'No decisions are waiting and the agent reports healthy.';
  const statusTone = attention ? 'warn' : pendingCount > 0 ? 'accent' : 'info';
  const statusIcon = attention ? 'alert' : pendingCount > 0 ? 'lock' : 'check';

  const run = async (key: string, fn: () => Promise<unknown>, onOk: () => void) => {
    setBusy(key);
    try {
      await fn();
      onOk();
      agent.refetch();
      background.refetch();
      agentHealth.refetch();
    } catch (err) {
      notify({
        tone: 'danger',
        title: 'Change rejected',
        body: err instanceof Error ? err.message : 'The backend refused the change.',
      });
    } finally {
      setBusy(null);
    }
  };

  // A decision changes backend capability/tool/skill state. These queries share
  // keys with the System screen panels, so refetching here refreshes every
  // consumer. Call this ONLY after the backend confirmed the decision (from an
  // onOk handler) so a failed request never disturbs the live panels.
  const refreshRegistryPanels = () => {
    capabilities.refetch();
    capabilityConsistency.refetch();
    tools.refetch();
    skills.refetch();
  };

  // Once the backend confirms the new value, drop the optimistic mirror so the
  // control falls back to being driven purely by server state.
  useEffect(() => {
    if (bgOptimistic !== null && background.data?.enabled === bgOptimistic) {
      setBgOptimistic(null);
    }
  }, [background.data?.enabled, bgOptimistic]);

  const setMode = (mode: AutonomyModeId) => {
    // Advisory/Proactive map onto the two real switches. Bounded has no
    // backend representation, so it is never offered as a target here.
    if (mode === 'advisory') {
      void run('mode', async () => {
        await actions.setBackground(false);
        await actions.pauseAgent();
      }, () =>
        notify({ tone: 'ok', title: 'Switched to Advisory', body: 'Agent paused, background work disabled.' })
      );
    } else if (mode === 'proactive') {
      void run('mode', async () => {
        await actions.resumeAgent();
        await actions.setBackground(true);
      }, () =>
        notify({
          tone: 'warn',
          title: 'Switched to Proactive',
          body: 'Sythion may now wake on triggers. Capability permissions still apply.',
        })
      );
    } else {
      void run('mode', async () => {
        await actions.setBackground(false);
        await actions.resumeAgent();
      }, () =>
        notify({ tone: 'ok', title: 'Switched to Assisted', body: 'Agent active, background work disabled.' })
      );
    }
  };

  const emergencyStop = () => {
    setConfirmPause(true);
  };

  const audit = [
    ...(activity.data?.events ?? [])
      .slice()
      .sort((a, b) => b.sequence_num - a.sequence_num)
      .slice(0, 10)
      .map((e) => ({
        id: `act-${e.sequence_num}`,
        at: e.created_at,
        title: humanize(e.event_type),
        detail: Object.entries(e.event_data ?? {})
          .slice(0, 3)
          .map(([k, v]) => `${k}: ${String(v).slice(0, 40)}`)
          .join('  ·  '),
        tone: e.event_type.includes('fail')
          ? ('danger' as const)
          : e.event_type.includes('background')
            ? ('warn' as const)
            : e.event_type.includes('complet')
              ? ('ok' as const)
              : ('info' as const),
      })),
    ...sensitive.slice(0, 5).map((t) => ({
      id: t.id,
      at: t.updated_at,
      title: `Task ${taskLabel(t.state).toLowerCase()}`,
      detail: t.terminal_reason ?? t.normalized_goal ?? t.original_request,
      tone: 'danger' as const,
    })),
  ].sort((a, b) => new Date(b.at).getTime() - new Date(a.at).getTime());

  return (
    <div className="screen">
      <div className="screen__inner">
        <ScreenHead
          eyebrow="Control"
          title="Security & Control"
          description="What Sythion may do, must ask about, and never does."
          actions={<Badge tone="accent">{pendingCount} awaiting you</Badge>}
        />

        {/* --- Plain status first ------------------------------------- */}
        <Notice tone={statusTone} icon={statusIcon}>
          <div className="stack stack--tight">
            <span className="t-title">{statusHeadline}</span>
            <span>{statusDetail}</span>
          </div>
        </Notice>

        {/* --- Emergency controls ------------------------------------- */}
        <Panel
          title="Emergency control"
          icon="power"
          actions={
            <Badge tone={AGENT_STATE_TONE[autonomy.agentState] ?? 'idle'}>
              Agent {humanize(autonomy.agentState).toLowerCase()}
            </Badge>
          }
        >
          <div className="grid grid--2">
            <div className="stack stack--tight">
              <div className="row row--between">
                <span className="t-title">Pause all agent work</span>
                <Button
                  variant="danger-solid"
                  icon="stop"
                  disabled={
                    busy === 'pause' || autonomy.agentState === 'paused'
                  }
                  title={
                    autonomy.agentState === 'paused'
                      ? 'The Main Agent is already paused'
                      : 'Pause the Main Agent so it starts no new work'
                  }
                  onClick={emergencyStop}
                >
                  {busy === 'pause'
                    ? 'Pausing…'
                    : autonomy.agentState === 'paused'
                      ? 'Agent paused'
                      : 'Pause everything'}
                </Button>
              </div>
              <p className="t-muted">
                Running tasks are not destroyed, but no new work starts or triggers until you resume.
              </p>
              <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
                <Button
                  variant="subtle"
                  icon="play"
                  disabled={
                    busy === 'resume' ||
                    autonomy.agentState === 'active'
                  }
                  title={
                    autonomy.agentState === 'active'
                      ? 'The Main Agent is already active'
                      : 'Resume the Main Agent'
                  }
                  onClick={() =>
                    void run('resume', actions.resumeAgent, () =>
                      notify({ tone: 'ok', title: 'Agent resumed' })
                    )
                  }
                >
                  {busy === 'resume' ? 'Resuming…' : 'Resume agent'}
                </Button>
                {autonomy.agentState === 'active' && (
                  <span className="t-faint">Already active — nothing to resume.</span>
                )}
              </div>
            </div>

            <div className="stack stack--tight">
              <div className="row row--between">
                <div>
                  <span className="t-title">Background orchestration</span>
                  <p className="t-faint" style={{ marginTop: 3 }}>
                    Lets the agent wake on triggers without being asked.
                  </p>
                </div>
                <Switch
                  checked={bgOptimistic ?? background.data?.enabled ?? false}
                  disabled={busy === 'bg'}
                  onChange={(v) => {
                    setBgOptimistic(v);
                    void run(
                      'bg',
                      () => actions.setBackground(v),
                      () =>
                        notify({
                          tone: v ? 'warn' : 'ok',
                          title: v ? 'Background work enabled' : 'Background work disabled',
                          body: v
                            ? 'Sythion may now act on its own initiative.'
                            : undefined,
                        })
                    );
                  }}
                />
              </div>
              <KV
                items={[
                  { k: 'Effect', v: 'Recorded in the agent event ledger' },
                  { k: 'Scope', v: 'Does not change any capability permission' },
                ]}
              />
            </div>
          </div>
        </Panel>

        {/* --- Devices (Synth OS endpoints) ---------------------------- */}
        <DevicesCard
          devices={devices.data?.devices ?? []}
          onTrustChange={(deviceId, level) => {
            void actions.setDeviceTrust(deviceId, level)
              .then(() => devices.refetch())
              .catch((error) => notify({
                tone: 'danger',
                title: 'Could not change device trust',
                body: String(error),
              }));
          }}
        />

        {/* --- Autonomy modes ----------------------------------------- */}
        <Panel title="Autonomy mode" icon="key">
          {/* --- Real authorization axis, reported read-only ----------- */}
          <div className="notice notice--accent" style={{ marginBottom: 14 }}>
            <span className="notice__icon">
              <Icon name="shield" size={14} />
            </span>
            <div className="notice__body">
              <div className="row row--wrap" style={{ gap: 8, marginBottom: 4 }}>
                <span className="t-title" style={{ fontSize: 12.5 }}>
                  Authorization level
                </span>
                <Badge tone={authorization.tone} icon={authorization.tone === 'ok' ? 'lock' : 'unlock'}>
                  {authorization.known ? authorization.label : 'Unknown'}
                </Badge>
              </div>
              <p>{authorization.summary}</p>
            </div>
          </div>

          <div className="grid grid--4">
            {AUTONOMY_MODES.map((m) => {
              const active = m.id === autonomy.mode;
              return (
                <button
                  type="button"
                  key={m.id}
                  className={`stat stat--clickable${active ? ' stat--active' : ''}`}
                  style={{
                    cursor: m.enforced ? 'pointer' : 'not-allowed',
                    opacity: m.enforced ? 1 : 0.72,
                  }}
                  onClick={() => m.enforced && setMode(m.id)}
                  disabled={!m.enforced}
                  aria-pressed={active}
                >
                  <span className="stat__label">
                    {active && <Icon name="check" size={12} />}
                    {m.label}
                    {!m.enforced && (
                      <Badge tone="warn">proposed</Badge>
                    )}
                  </span>
                  <span className="stat__value" style={{ fontSize: 14, lineHeight: 1.35 }}>
                    {m.tagline}
                  </span>
                  <span className="stat__foot" style={{ display: 'block', lineHeight: 1.5 }}>
                    {m.enforcementNote}
                  </span>
                </button>
              );
            })}
          </div>

          <div className="grid grid--3" style={{ marginTop: 14 }}>
            <div className="stack stack--tight">
              <span className="field__label">
                <Icon name="unlock" size={11} style={{ verticalAlign: '-1px', marginRight: 5 }} />
                Can act independently
              </span>
              {autonomyMeta.can.map((c) => (
                <span key={c} className="row t-muted" style={{ gap: 7, alignItems: 'flex-start' }}>
                  <Icon name="check" size={12} style={{ color: 'var(--ok)', flex: '0 0 auto', marginTop: 2 }} />
                  <span>{c}</span>
                </span>
              ))}
            </div>
            <div className="stack stack--tight">
              <span className="field__label">
                <Icon name="lock" size={11} style={{ verticalAlign: '-1px', marginRight: 5 }} />
                Requires confirmation
              </span>
              {autonomyMeta.mustAsk.map((c) => (
                <span key={c} className="row t-muted" style={{ gap: 7, alignItems: 'flex-start' }}>
                  <Icon name="lock" size={12} style={{ color: 'var(--warn)', flex: '0 0 auto', marginTop: 2 }} />
                  <span>{c}</span>
                </span>
              ))}
            </div>
            <div className="stack stack--tight">
              <span className="field__label">
                <Icon name="close" size={11} style={{ verticalAlign: '-1px', marginRight: 5 }} />
                Never permitted
              </span>
              {autonomyMeta.cannot.map((c) => (
                <span key={c} className="row t-muted" style={{ gap: 7, alignItems: 'flex-start' }}>
                  <Icon name="close" size={12} style={{ color: 'var(--danger)', flex: '0 0 auto', marginTop: 2 }} />
                  <span>{c}</span>
                </span>
              ))}
            </div>
          </div>

          <AdvancedDetails id="autonomy">
            <span className="t-faint">
              Derived from two backend switches. Cards without a backing endpoint are labelled proposed and cannot be selected.
            </span>
            <KV
              items={[
                { k: 'Authorization level (raw)', v: <span className="t-mono">{authorization.level}</span> },
                { k: 'Source', v: authorization.readOnlyReason },
              ]}
            />
            {agent.data?.policies && (
              <KV
                items={[
                  {
                    k: 'Step attempts',
                    v: String(agent.data.policies.step_max_attempts),
                  },
                  {
                    k: 'Steps / background cycle',
                    v: String(agent.data.policies.max_steps_per_background_cycle),
                  },
                  {
                    k: 'Background policy',
                    v: agent.data.policies.background_orchestration_enabled ? 'enabled' : 'disabled',
                  },
                ]}
              />
            )}
            <span className="t-muted">
              Authorization is enforced by backend execution policy — no card or toggle here grants a capability.
            </span>
          </AdvancedDetails>
        </Panel>

        {/* --- Summary ------------------------------------------------ */}
        <div className="grid grid--4">
          <Stat
            label="Awaiting your decision"
            icon="lock"
            value={pendingCount}
            tone={pendingCount > 0 ? 'accent' : undefined}
            foot="Proposals and held tasks"
          />
          <Stat
            label="Permission-gated"
            icon="key"
            value={counts.gated}
            foot="Need your approval first"
          />
          <Stat label="High risk" icon="alert" value={caps.filter((c) => c.risk_class === 'HIGH').length} foot="Require owner authorization" />
          <Stat
            label="Unavailable"
            icon="plug"
            value={counts.unavailable}
            foot="No integration behind them"
          />
        </div>

        {/* --- Trusted owner decisions (real proposal/receipt flow) ----- */}
        <PendingApprovalsPanel
          proposals={permissionProposals}
          busy={busy}
          onDecide={(proposalId, decisionId, decision) =>
            run('owner-decision', () =>
              actions.decideApproval(proposalId, decisionId, decision), () =>
              notify({ tone: 'ok', title: 'Decision recorded' })
            )
          }
          onDecided={() => pendingApprovals.refetch()}
        />

        {/* --- Approvals ---------------------------------------------- */}
        <div className="grid grid--2">
          <Panel
            title="Pending authorization"
            icon="lock"
            flush
          >
            {pendingSkill.length + pendingTools.length + heldTasks.length === 0 ? (
              <EmptyState
                icon="check"
                title="Nothing pending"
                description="Proposals and held tasks queue here."
              />
            ) : (
              <div className="list">
                {heldTasks.map((t) => (
                  <div className="list__row" key={t.id}>
                    <Rail tone="warn" />
                    <div className="list__main">
                      <span className="t-title t-truncate" style={{ fontSize: 12.5 }}>
                        {t.normalized_goal || t.original_request}
                      </span>
                      <span className="list__meta">
                        <Badge tone="warn">held</Badge>
                        <span>· {relTime(t.updated_at)}</span>
                      </span>
                      {t.terminal_reason && <span className="t-warn t-clamp-2">{t.terminal_reason}</span>}
                      <span className="t-faint">
                        Resuming is not permission approval. Uncertain effects require independent review.
                      </span>
                    </div>
                    <div className="list__trail">
                      <Button
                        size="sm"
                        variant="primary"
                        disabled={busy === 'resume-t'}
                        onClick={() =>
                          void run('resume-t', () => actions.resumeTask(t.id), () =>
                            notify({ tone: 'ok', title: 'Task resumed' })
                          )
                        }
                      >
                        Resume if safe
                      </Button>
                    </div>
                  </div>
                ))}
                {pendingSkill.map((p) => (
                  <div className="list__row" key={`sk-${p.id}`}>
                    <Rail tone="warn" />
                    <div className="list__main">
                      <span className="t-body t-truncate" style={{ color: 'var(--text-0)' }}>
                        Skill: {p.definition?.name || p.name || `Proposal ${p.id}`}
                      </span>
                      <span className="list__meta">
                        <Badge tone="warn">proposal</Badge>
                        {typeof p.evidence_count === 'number' && <span>· {p.evidence_count} runs</span>}
                      </span>
                    </div>
                    <div className="list__trail">
                      <Button
                        size="sm"
                        variant="primary"
                        disabled={!actions.live || p.status !== 'proposed' || busy !== null}
                        onClick={() =>
                          actions.live && p.status === 'proposed' && busy === null && void run(`sk-${p.id}`, () => actions.approveSkillProposal(p.id), () => {
                            skillProposals.refetch();
                            refreshRegistryPanels();
                            notify({ tone: 'ok', title: 'Proposal approved' });
                          })
                        }
                      >
                        Approve
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={!actions.live || p.status !== 'proposed' || busy !== null}
                        onClick={() =>
                          actions.live && p.status === 'proposed' && busy === null && void run(`sk-${p.id}`, () => actions.rejectSkillProposal(p.id), () => {
                            skillProposals.refetch();
                            refreshRegistryPanels();
                            notify({ tone: 'ok', title: 'Proposal rejected' });
                          })
                        }
                      >
                        Reject
                      </Button>
                    </div>
                  </div>
                ))}
                {pendingTools.map((p) => {
                  const expanded = reviewingTool === p.id;
                  return (
                    <Fragment key={`tp-${p.id}`}>
                      <div className="list__row">
                        <Rail tone="danger" />
                        <div className="list__main">
                          <span className="t-body t-truncate" style={{ color: 'var(--text-0)' }}>
                            Tool: {p.name}
                          </span>
                          <span className="t-muted t-clamp-2">{p.description}</span>
                          <span className="list__meta">
                            <Badge tone="danger">new capability</Badge>
                          </span>
                        </div>
                        <div className="list__trail">
                          <Button
                            size="sm"
                            variant="subtle"
                            disabled={!actions.live || busy !== null}
                            title="Open the proposal details, then approve or reject explicitly"
                            onClick={() => setReviewingTool(expanded ? null : p.id)}
                          >
                            {expanded ? 'Hide details' : 'Review'}
                          </Button>
                        </div>
                      </div>
                      {expanded && (
                        <div className="panel__body" data-testid={`tool-review-${p.id}`}>
                          <div className="stack stack--tight">
                            <span className="t-title">Review tool proposal · {p.name}</span>
                            <p className="t-muted">
                              {p.description || 'No description was supplied with this proposal.'}
                            </p>
                            <p className="t-warn">
                              Approving registers this tool as an approved capability; rejecting refuses the proposal.
                              No tool runs from this screen.
                            </p>
                            <div className="row" style={{ gap: 8 }}>
                              <Button
                                size="sm"
                                variant="primary"
                                disabled={!actions.live || busy !== null}
                                onClick={() =>
                                  void run(`tp-approve-${p.id}`, () => actions.approveToolProposal(p.id), () => {
                                    setReviewingTool(null);
                                    toolProposals.refetch();
                                    refreshRegistryPanels();
                                    notify({ tone: 'ok', title: 'Tool proposal approved' });
                                  })
                                }
                              >
                                Approve tool
                              </Button>
                              <Button
                                size="sm"
                                variant="ghost"
                                disabled={!actions.live || busy !== null}
                                onClick={() =>
                                  void run(`tp-reject-${p.id}`, () => actions.rejectToolProposal(p.id), () => {
                                    setReviewingTool(null);
                                    toolProposals.refetch();
                                    refreshRegistryPanels();
                                    notify({ tone: 'ok', title: 'Tool proposal rejected' });
                                  })
                                }
                              >
                                Reject tool
                              </Button>
                            </div>
                          </div>
                        </div>
                      )}
                    </Fragment>
                  );
                })}
              </div>
            )}
            <AdvancedDetails id="proposals">
              <span className="t-faint">Raw backend fields for the proposals and held tasks listed above.</span>
              {heldTasks.map((t) => (
                <KV key={`raw-t-${t.id}`} items={[
                  { k: 'Held task', v: <span className="t-mono">{t.id}</span> },
                  { k: 'State', v: t.state },
                ]} />
              ))}
              {pendingSkill.map((p) => (
                <KV key={`raw-s-${p.id}`} items={[
                  { k: 'Skill proposal', v: <span className="t-mono">{p.id}</span> },
                  { k: 'Status', v: String(p.status) },
                  { k: 'Evidence', v: String(p.evidence_count ?? '—') },
                ]} />
              ))}
              {pendingTools.map((p) => (
                <KV key={`raw-p-${p.id}`} items={[
                  { k: 'Tool proposal', v: <span className="t-mono">{p.id}</span> },
                  { k: 'Status', v: String(p.status) },
                  { k: 'Enabled mode', v: p.enabled_mode ?? '—' },
                  { k: 'Successful uses', v: String(p.successful_uses) },
                  { k: 'Created', v: relTime(p.created_at) },
                  { k: 'Validation problems', v: p.validation_errors.join('; ') || 'none' },
                ]} />
              ))}
              {pendingSkill.some((p) => p.status !== 'proposed') && (
                <span className="t-warn">This status cannot be decided by the backend.</span>
              )}
            </AdvancedDetails>
          </Panel>

          <Panel
            title="Privacy controls"
            icon="eye"
          >
            <div className="stack stack--tight">
              <KV
                items={[
                  { k: 'Retention', v: `${health.data ? 'configured' : '—'} · purge is not automatic` },
                  { k: 'Voice recording', v: 'off by default; never auto-enabled' },
                  { k: 'Cloud requests', v: 'bounded per task by backend budget' },
                  { k: 'Secret values', v: 'write-only — the API never returns them' },
                  { k: 'Stale relations', v: `${staleEdges.length} awaiting your review` },
                ]}
              />
              <div className="row row--wrap" style={{ gap: 7, marginTop: 4 }}>
                <Button size="sm" variant="subtle" onClick={() => navigate('memory')}>
                  Review memory
                </Button>
                <Button size="sm" variant="subtle" onClick={() => navigate('system')}>
                  Retention receipts
                </Button>
              </div>
              <p className="t-faint" style={{ marginTop: 4 }}>
                Editing or deleting individual memory items is not available in this version. Relations can be
                reinforced or withdrawn on the Memory screen.
              </p>
            </div>
          </Panel>
        </div>

        {/* --- Capabilities ------------------------------------------- */}
        <Panel
          title="Capability permissions"
          icon="shield"
          flush
        >
          {capabilities.isLoading ? (
            <div className="panel__body">
              <LoadingBlock rows={5} />
            </div>
          ) : (
            <>
              <p className="t-muted" style={{ marginTop: 0 }}>
                {caps.length === 0
                  ? 'No capabilities have been reported by the backend.'
                  : `${caps.length} capabilities reported. ${counts.gated} need your approval first; ${caps.filter((c) => c.risk_class === 'HIGH').length} are high risk; ${counts.unavailable} are unavailable.`}
              </p>
              <AdvancedDetails id="capabilities">
                <span className="t-faint">Reported by the backend; this table only displays it.</span>
                <div className="table__scroll" style={{ maxHeight: 420 }}>
                  <table className="table">
                    <thead>
                      <tr>
                        <th>Capability</th>
                        <th>Status</th>
                        <th>Risk</th>
                        <th>Permission</th>
                        <th>External service</th>
                      </tr>
                    </thead>
                    <tbody>
                      {caps.map((c) => (
                        <tr key={c.id}>
                          <td>
                            <div className="t-body" style={{ color: 'var(--text-0)' }}>
                              {c.name}
                            </div>
                            <div className="t-faint">{c.category}</div>
                          </td>
                          <td>
                            <Badge tone={CAPABILITY_TONE[c.status] ?? 'idle'}>{humanize(c.status)}</Badge>
                          </td>
                          <td>
                            <Badge tone={RISK_TONE[c.risk_class] ?? 'idle'}>{c.risk_class}</Badge>
                          </td>
                          <td>
                            {c.requires_permission ? (
                              <Badge tone="warn" icon="lock">
                                required
                              </Badge>
                            ) : (
                              <Badge tone="ok">open</Badge>
                            )}
                          </td>
                          <td>
                            {c.requires_external_service ? (
                              <Badge tone="info">yes</Badge>
                            ) : (
                              <Badge tone="idle">no</Badge>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </AdvancedDetails>
            </>
          )}
          {unavailable.length > 0 && (
            <div className="panel__foot">
              <span className="t-muted">
                {unavailable.length} capabilit{unavailable.length === 1 ? 'y is' : 'ies are'} unavailable — Sythion
                cannot use them regardless of autonomy mode. {gated.length} other
                {gated.length === 1 ? '' : 's'} would require explicit owner authorization first.
              </span>
            </div>
          )}
        </Panel>

        {/* --- Audit log ---------------------------------------------- */}
        <Panel
          title="Activity & audit log"
          icon="activity"
          flush
        >
          {audit.length === 0 ? (
            <EmptyState
              icon="activity"
              title="No recorded actions"
              description="Events appear here as they happen."
            />
          ) : (
            <div className="list" style={{ maxHeight: 380, overflowY: 'auto' }}>
              {audit.slice(0, 16).map((a) => (
                <div className="list__row" key={a.id}>
                  <Rail tone={a.tone} />
                  <div className="list__main">
                    <span className="t-body" style={{ color: 'var(--text-0)' }}>
                      {a.title}
                    </span>
                  </div>
                  <div className="list__trail t-faint">{relTime(a.at)}</div>
                </div>
              ))}
            </div>
          )}
          <AdvancedDetails id="audit">
            <span className="t-faint">Raw event data from the backend event ledger.</span>
            {audit.length === 0 ? (
              <span className="t-faint">No raw event data recorded.</span>
            ) : (
              <div className="stack stack--tight">
                {audit.slice(0, 16).map((a) => (
                  <div key={`raw-${a.id}`}>
                    <span className="t-mono t-clamp-2">{a.detail}</span>
                  </div>
                ))}
              </div>
            )}
          </AdvancedDetails>
        </Panel>

        {awaitingInput.length > 0 && (
          <Panel title="Tasks waiting on your answer" icon="inbox" flush>
            <div className="list">
              {awaitingInput.map((t) => (
                <div className="list__row" key={t.id}>
                  <Rail tone={taskTone(t.state)} />
                  <div className="list__main">
                    <span className="t-body t-clamp-2">{t.pending_question ?? t.original_request}</span>
                    <span className="list__meta">
                      <span>{relTime(t.updated_at)}</span>
                    </span>
                  </div>
                  <div className="list__trail">
                    <Button size="sm" variant="subtle" onClick={() => navigate('assistant')}>
                      Answer
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          </Panel>
        )}

        <div className="grid grid--2">
          <Panel title="Runtime identity" icon="server">
            <div className="stack stack--tight">
              <span className="t-title">{statusHeadline}</span>
              <span className="t-muted">{statusDetail}</span>
            </div>
            <AdvancedDetails id="runtime">
              <KV
                items={[
                  { k: 'Agent id', v: <span className="t-mono">{agent.data?.agent_id ?? '—'}</span> },
                  { k: 'State', v: humanize(autonomy.agentState) },
                  { k: 'Backend', v: health.data && !health.isError ? `v${health.data.version}` : 'unreachable' },
                  { k: 'Health', v: humanize(agentHealth.data?.overall ?? 'UNKNOWN') },
                  { k: 'Last wake', v: relTime(agent.data?.last_wake_at) },
                ]}
              />
            </AdvancedDetails>
          </Panel>

          <Panel title="What this screen cannot do" icon="info">
            <span className="t-muted">
              This screen shows status and records decisions; it cannot widen what the backend permits.
            </span>
            <AdvancedDetails id="limits">
              <div className="stack stack--tight">
                <span className="t-muted">
                  Permissions are declared by the backend capability registry and enforced by the executor — this interface
                  cannot widen them.
                </span>
                <span className="t-muted">
                  Device credentials and trust grants are listed in the Devices panel above and can be changed there.
                  There is no separate browser-style session list to revoke — Sythion runs as a single local process.
                </span>
                <span className="t-muted">
                  Pausing stops new work; it does not abort a tool already executing.
                </span>
              </div>
            </AdvancedDetails>
          </Panel>
        </div>
      </div>

      {confirmPause && (
        <ConfirmDialog
          title="Pause all agent work?"
          tone="danger"
          confirmLabel="Pause everything"
          busy={busy === 'pause'}
          body={
            <div className="stack stack--tight">
              <p>
                Sythion will stop starting new tasks and will not respond to triggers until you resume it.
              </p>
              <p className="t-muted">
                Work already in flight is <strong>not</strong> destroyed — its outcome still applies — and a tool call
                already executing is not interrupted.
              </p>
            </div>
          }
          onCancel={() => setConfirmPause(false)}
          onConfirm={() => {
            setConfirmPause(false);
            void run('pause', actions.pauseAgent, () =>
              notify({
                tone: 'ok',
                title: 'All agent work paused',
                body: 'Background orchestration was left as-is; resume when ready.',
              })
            );
          }}
        />
      )}
    </div>
  );
}
