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

/**
 * Where the decision stat should take the owner.
 *
 * Reconciliation items are handled in the Control Center overview (where the
 * reconciliation panel lives); approvals are handled in Security. When neither
 * dominates, tasks waiting on the owner go to the missions view. Security is
 * the default destination.
 */
export function decisionDestination(input: DecisionInputs): 'overview' | 'security' | 'missions' {
  const decisionCount = input.ownerApprovals + input.proposalApprovals;
  // Reconciliations are the dominant source when they outnumber every other
  // decision source combined.
  if (input.needsReconciliation > decisionCount + input.awaitingYou) return 'overview';
  if (decisionCount > 0) return 'security';
  if (input.needsReconciliation > 0) return 'overview';
  if (input.awaitingYou > 0) return 'missions';
  return 'security';
}
