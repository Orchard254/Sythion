import { describe, expect, it } from 'vitest';
import {
  countDecisionsDue, decisionDestination, describeDecisionsDue,
} from './decisionCounts';

describe('owner decision counts', () => {
  it('sums all durable decision sources', () => {
    expect(countDecisionsDue({
      proposalApprovals: 2,
      ownerApprovals: 3,
      needsReconciliation: 1,
      awaitingYou: 4,
    })).toBe(10);
  });

  it('is zero when nothing needs a decision', () => {
    expect(countDecisionsDue({
      proposalApprovals: 0, ownerApprovals: 0,
      needsReconciliation: 0, awaitingYou: 0,
    })).toBe(0);
  });

  it('describes reconciliation and approval counts in the stat foot', () => {
    const text = describeDecisionsDue({
      proposalApprovals: 1, ownerApprovals: 2,
      needsReconciliation: 3, awaitingYou: 4,
    });
    expect(text).toContain('2 approvals');
    expect(text).toContain('3 reconciliations');
    expect(text).toContain('5 other');
  });

  it('reports nothing waiting when all sources are empty', () => {
    expect(describeDecisionsDue({
      proposalApprovals: 0, ownerApprovals: 0,
      needsReconciliation: 0, awaitingYou: 0,
    })).toBe('Nothing waiting on you');
  });
});

describe('decision destination', () => {
  it('routes dominant reconciliations to the Control Center overview', () => {
    expect(decisionDestination({
      proposalApprovals: 0, ownerApprovals: 0,
      needsReconciliation: 3, awaitingYou: 1,
    })).toBe('overview');
  });

  it('routes approvals to security', () => {
    expect(decisionDestination({
      proposalApprovals: 1, ownerApprovals: 2,
      needsReconciliation: 1, awaitingYou: 0,
    })).toBe('security');
  });

  it('routes waiting tasks to missions when nothing else dominates', () => {
    expect(decisionDestination({
      proposalApprovals: 0, ownerApprovals: 0,
      needsReconciliation: 0, awaitingYou: 2,
    })).toBe('missions');
  });

  it('defaults to security with nothing pending', () => {
    expect(decisionDestination({
      proposalApprovals: 0, ownerApprovals: 0,
      needsReconciliation: 0, awaitingYou: 0,
    })).toBe('security');
  });

  it('routes a lone reconciliation to overview', () => {
    expect(decisionDestination({
      proposalApprovals: 0, ownerApprovals: 0,
      needsReconciliation: 1, awaitingYou: 0,
    })).toBe('overview');
  });
});
