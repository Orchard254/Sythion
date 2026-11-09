import { describe, expect, it } from 'vitest';
import type { TaskResumePreflight } from '../types';
import { mayOfferExplicitResume, recoveryStatusLabel, recoveryVerificationSteps } from './recoveryPresentation';

function preflight(overrides: Partial<TaskResumePreflight> = {}): TaskResumePreflight {
  return {
    task_id: 't1',
    task_state: 'interrupted',
    owner_lease: 'released',
    unresolved_reasoning_calls: 0,
    reason: 'clear_for_explicit_resume',
    preflight_clear: true,
    review_required: false,
    automatic_replay_allowed: false,
    review: {
      status: 'preflight_clear_not_authorized',
      blocker: 'clear_for_explicit_resume',
      severity: 'none',
      observations: { task_state: 'interrupted', owner_lease: 'released', unresolved_reasoning_calls: 0 },
      evidence_source_classes: ['task_state'],
      verification_steps: ['A fresh backend check is always required.'],
      requires_authenticated_reconciliation: false,
      limited_to_current_snapshot: true,
      approved: false,
      authorization_issued: false,
      uncertainty_cleared: false,
      execution_started: false,
      automatic_replay_allowed: false,
      note: 'Not an approval',
    },
    ...overrides,
  };
}

describe('resume preflight UI is non-authorizing', () => {
  it('offers only explicit resume on a consistent clear, non-authorizing packet', () => {
    expect(mayOfferExplicitResume(preflight())).toBe(true);
    expect(mayOfferExplicitResume(preflight({ task_state: 'paused' }))).toBe(true);
    expect(recoveryStatusLabel(preflight())).toContain('No blockers');
  });

  it('fails closed on a missing review or missing preflight', () => {
    expect(mayOfferExplicitResume(undefined)).toBe(false);
    expect(mayOfferExplicitResume(preflight({ review: undefined as never }))).toBe(false);
    expect(recoveryStatusLabel(undefined)).toContain('disabled');
  });

  it.each([
    { reason: 'unresolved_reasoning_review_required', preflight_clear: false },
    { reason: 'ownership_recovery_required', preflight_clear: false },
    { reason: 'prior_mutating_effect_review_required', preflight_clear: false },
    { task_state: 'running' },
    { preflight_clear: false },
    { review_required: true },
    { automatic_replay_allowed: true },
  ])('does not offer resume for contradictory or blocked preflight %#', (patch) => {
    expect(mayOfferExplicitResume(preflight(patch as Partial<TaskResumePreflight>))).toBe(false);
  });

  it.each(['approved', 'authorization_issued', 'uncertainty_cleared', 'execution_started', 'automatic_replay_allowed'] as const)(
    'refuses a packet that claims %s', (key) => {
      const clean = preflight();
      expect(mayOfferExplicitResume({
        ...clean,
        review: { ...clean.review, [key]: true },
      })).toBe(false);
    },
  );

  it('renders only bounded verification steps', () => {
    const clean = preflight();
    const steps = recoveryVerificationSteps({
      ...clean,
      review: {
        ...clean.review,
        verification_steps: ['Check independent evidence', '', 'a'.repeat(500), 'Inspect ownership', 'Confirm state'],
      },
    });
    expect(steps).toEqual(['Check independent evidence', 'Inspect ownership', 'Confirm state']);
  });

  it('never interprets a clear flag as successful authorization', () => {
    const clean = preflight();
    expect(mayOfferExplicitResume({
      ...clean,
      review: { ...clean.review, status: 'review_required', blocker: 'clear_for_explicit_resume' },
    })).toBe(false);
  });
});
