import { useState } from 'react';
import { Badge, Button, ConfirmDialog, EmptyState, Panel } from './ui';
import type { PendingApproval } from '../types';

/** Generate a fresh decision id in the trusted UI, never from model output. */
export function newDecisionId(): string {
  const random = typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return `owner-ui-${random}`;
}

interface Props {
  proposals: PendingApproval[];
  busy?: string | null;
  onDecide: (proposalId: string, decisionId: string, decision: 'approve' | 'deny') => Promise<unknown>;
  onDecided?: () => void;
}

/**
 * Owner decision surface for exact permission proposals.
 *
 * Every decision carries a freshly generated id that is bound into the durable
 * receipt. Approve requires a confirmation; nothing here runs a tool itself.
 */
export function PendingApprovalsPanel({ proposals, busy, onDecide, onDecided }: Props) {
  const [confirming, setConfirming] = useState<PendingApproval | null>(null);
  const [acting, setActing] = useState<string | null>(null);

  const decide = async (proposal: PendingApproval, decision: 'approve' | 'deny') => {
    setActing(proposal.proposal_id);
    try {
      await onDecide(proposal.proposal_id, newDecisionId(), decision);
      onDecided?.();
    } finally {
      setActing(null);
    }
  };

  return (
    <Panel title="Owner decisions" icon="lock" hint="Exact permission proposals awaiting your decision">
      {proposals.length === 0 ? (
        <EmptyState title="Nothing to decide" description="No permission proposals are awaiting an owner decision." />
      ) : (
        <div className="list" data-testid="pending-approvals-panel">
          {proposals.map((proposal) => (
            <div className="list__row" key={proposal.proposal_id}>
              <div className="list__main">
                <span className="t-title t-truncate" style={{ fontSize: 12.5 }}>
                  {proposal.tool_name}
                </span>
                <span className="list__meta">
                  <Badge tone="warn">awaiting</Badge>
                  <span>· task {proposal.task_id}</span>
                </span>
                <span className="t-faint t-clamp-2">{proposal.target_canonical}</span>
              </div>
              <div className="list__trail">
                <Button
                  size="sm"
                  variant="primary"
                  disabled={Boolean(busy) || acting === proposal.proposal_id}
                  onClick={() => setConfirming(proposal)}
                >
                  Approve
                </Button>
                <Button
                  size="sm"
                  disabled={Boolean(busy) || acting === proposal.proposal_id}
                  onClick={() => void decide(proposal, 'deny')}
                >
                  Deny
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}

      {confirming && (
        <ConfirmDialog
          title={`Approve ${confirming.tool_name}?`}
          tone="danger"
          confirmLabel="Approve this action"
          busy={acting === confirming.proposal_id}
          body={
            <div className="stack stack--tight">
              <p>This records a trusted owner decision for one exact permission proposal.</p>
              <p className="t-muted">
                It does not execute the action itself. The resulting decision id is bound to the receipt.
              </p>
            </div>
          }
          onCancel={() => setConfirming(null)}
          onConfirm={() => {
            const proposal = confirming;
            setConfirming(null);
            void decide(proposal, 'approve');
          }}
        />
      )}
    </Panel>
  );
}
