import { useRef, useState } from 'react';
import {
  Badge,
  Button,
  Drawer,
  EmptyState,
  KV,
  LoadingBlock,
  Notice,
  Panel,
  Rail,
  ScreenHead,
  Stat,
} from '../components/ui';
import { useActions, useAgentActivity, useBackgroundPolicy, useSkills, useSkillProposals } from '../hooks/useBackend';
import { humanize, relTime } from '../lib/format';
import type { SkillProposal } from '../types';

/* Automations.
 *
 * Everything here reads the real backend: the backend registry of validated
 * skills, pending skill proposals, the background-orchestration switch that
 * causes event-driven work to run, and the resulting wake history. There is
 * no scheduler or workflow-definition endpoint, so there is no workflow
 * catalogue in the front end either.
 */

const isPending = (p: SkillProposal) => p.status === 'proposed' || p.status === 'pending_review';

export function AutomationsScreen() {
  const background = useBackgroundPolicy();
  const skills = useSkills();
  const proposals = useSkillProposals();
  const activity = useAgentActivity();

  const [openProposalId, setOpenProposalId] = useState<string | number | null>(null);

  const pendingProposals = (proposals.data ?? []).filter(isPending).length;
  const wakesFromTriggers = (activity.data?.wakes ?? []).filter((w) => w.trigger_type !== 'user.request');
  const enabledSkillCount = (skills.data ?? []).filter((s) => s.enabled).length;

  return (
    <div className="screen">
      <div className="screen__inner">
        <ScreenHead
          eyebrow="Environment"
          title="Automations"
          description="Scheduled and event-triggered work."
        />

        <div className="grid grid--4">
          <Stat
            label="Validated skills"
            icon="layers"
            value={skills.isLoading ? '—' : (skills.data ?? []).length}
            foot="From the backend registry"
          />
          <Stat
            label="Enabled"
            icon="check"
            value={skills.isLoading ? '—' : enabledSkillCount}
          />
          <Stat
            label="Pending review"
            icon="shield"
            value={pendingProposals}
            tone={pendingProposals > 0 ? 'accent' : undefined}
            foot="New skill proposals"
          />
          <Stat
            label="Triggered wakes"
            icon="power"
            value={wakesFromTriggers.length}
            foot="Started by a trigger, not by you"
          />
        </div>

        <div className="grid grid--2">
          <Panel
            title="Background orchestration"
            icon="power"
          >
            <div className="stack stack--tight">
              <div className="row row--between">
                <div style={{ minWidth: 0 }}>
                  <span className="t-title">Event-driven background work</span>
                  <p className="t-muted" style={{ marginTop: 3 }}>
                    The Main Agent wakes on registered triggers and advances active objectives.
                  </p>
                </div>
                <Badge tone={background.data?.enabled ? 'accent' : 'idle'}>
                  {background.data?.enabled ? 'Enabled' : 'Disabled'}
                </Badge>
              </div>
              <KV
                items={[
                  { k: 'Backend default', v: background.data?.default ? 'on' : 'off' },
                  { k: 'Current value', v: background.data ? String(background.data.enabled) : '—' },
                ]}
              />
            </div>
          </Panel>

          <Panel
            title="Pending review"
            icon="shield"
            flush
          >
            {(proposals.data ?? []).length === 0 ? (
              <EmptyState
                icon="check"
                title="Nothing awaiting review"
                description="Proposals from repeated successful runs appear here. You approve every one."
              />
            ) : (
              <div className="list">
                {(proposals.data ?? []).map((p) => (
                  <div className="list__row" key={String(p.id)}>
                    <Rail tone="warn" />
                    <div className="list__main">
                      <span className="t-title" style={{ fontSize: 12.5 }}>
                        {p.definition?.name || p.name || `Proposal ${p.id}`}
                      </span>
                      <span className="list__meta">
                        <Badge tone="warn">{humanize(p.status ?? 'unknown')}</Badge>
                        {typeof p.evidence_count === 'number' && (
                          <span>· {p.evidence_count} similar runs</span>
                        )}
                      </span>
                    </div>
                    <div className="list__trail">
                      <Button size="sm" variant="ghost" onClick={() => setOpenProposalId(p.id)}>
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

        <Panel
          title="Validated skills"
          icon="layers"
          flush
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

        <Panel title="Triggered wakes from the live ledger" icon="power" flush>
          {wakesFromTriggers.length === 0 ? (
            <EmptyState
              icon="power"
              title="No background wakes recorded"
              description="Wakes appear here when a trigger fires."
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

      {openProposalId !== null && (
        <ProposalDrawer
          proposal={(proposals.data ?? []).find((p) => String(p.id) === String(openProposalId))}
          refresh={() => { proposals.refetch(); skills.refetch(); }}
          onClose={() => setOpenProposalId(null)}
        />
      )}
    </div>
  );
}

function ProposalDrawer({ proposal, refresh, onClose }: { proposal: SkillProposal | undefined; refresh: () => void; onClose: () => void }) {
  const actions = useActions();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const inFlight = useRef(false);
  const canReview = Boolean(proposal && proposal.status === 'proposed' && actions.live);

  async function decide(decision: 'approve' | 'reject') {
    if (!proposal || !canReview || inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError('');
    try {
      if (decision === 'approve') await actions.approveSkillProposal(proposal.id);
      else await actions.rejectSkillProposal(proposal.id);
      refresh();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Review failed. Please try again.');
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  return (
    <Drawer
      title={proposal?.definition?.name || proposal?.name || `Skill proposal ${proposal?.id ?? ''}`}
          onClose={onClose}
      footer={
        <>
          <Button variant="subtle" onClick={onClose}>
            Close
          </Button>
          <Button variant="primary" disabled={!canReview || busy} onClick={() => void decide('approve')}>
            Approve
          </Button>
          <Button variant="danger" disabled={!canReview || busy} onClick={() => void decide('reject')}>
            Reject
          </Button>
        </>
      }
    >
      <Notice tone="warn" icon="alert"><span>
        {!actions.live ? 'Connect to the backend to review proposals.' :
          proposal && proposal.status !== 'proposed' ? 'This status cannot be decided by the backend.' :
          'Approval validates and enables this skill. Rejection declines it; neither decision runs the skill.'}
      </span></Notice>
      {!proposal && <p className="t-muted">This proposal is no longer available.</p>}
      {proposal && <>
        <KV items={[
          { k: 'Status', v: humanize(proposal.status ?? 'unknown') },
          { k: 'Evidence', v: typeof proposal.evidence_count === 'number' ? String(proposal.evidence_count) : Array.isArray(proposal.trace_ids) ? String(proposal.trace_ids.length) : '—' },
        ]} />
        {proposal.definition != null && <><span className="field__label">Proposed definition</span><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(proposal.definition, null, 2)}</pre></>}
        {Array.isArray(proposal.validation_errors) && proposal.validation_errors.length > 0 && <p className="t-warn">Validation: {proposal.validation_errors.map(String).join('; ')}</p>}
      </>}
      {error && <Notice tone="danger" icon="alert"><span>{error}</span></Notice>}
    </Drawer>
  );
}
