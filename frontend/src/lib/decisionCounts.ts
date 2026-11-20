/** Owner decision counts from durable read APIs (pure, testable). */

export interface DecisionInputs {
  proposalApprovals: number;
  ownerApprovals: number;
  needsReconciliation: number;
  awaitingYou: number;
}

/**
 * Everything requiring an owner decision, from all sources:
 * skill/tool proposals, trusted owner approval proposals, uncertain remote
 * actions needing reconciliation, and tasks waiting on the owner.
 */
export function countDecisionsDue(input: DecisionInputs): number {
  return (
    input.proposalApprovals +
    input.ownerApprovals +
    input.needsReconciliation +
    input.awaitingYou
  );
}

export function describeDecisionsDue(input: DecisionInputs): string {
  const total = countDecisionsDue(input);
  if (total === 0) return 'Nothing waiting on you';
  return `${input.ownerApprovals} approvals · ${input.needsReconciliation} reconciliations · ${input.proposalApprovals + input.awaitingYou} other`;
}
