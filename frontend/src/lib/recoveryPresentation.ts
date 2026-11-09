import type { TaskResumePreflight } from '../types';

/**
 * This is a display/interaction guard, NOT execution authority.
 *
 * A clear preflight is only a time-sensitive observation. The backend must
 * repeat admission under its writer lock when explicit resume is requested.
 * Unknown/older/inconsistent packets fail closed in the UI.
 */
export function mayOfferExplicitResume(value: TaskResumePreflight | null | undefined): boolean {
  if (!value) return false;
  const review = value.review;
  return (value.task_state === 'interrupted' || value.task_state === 'paused')
    && value.preflight_clear === true
    && value.reason === 'clear_for_explicit_resume'
    && value.review_required === false
    && value.automatic_replay_allowed === false
    && review?.status === 'preflight_clear_not_authorized'
    && review.blocker === 'clear_for_explicit_resume'
    && review.approved === false
    && review.authorization_issued === false
    && review.uncertainty_cleared === false
    && review.execution_started === false
    && review.automatic_replay_allowed === false;
}

const BLOCKER_LABELS: Record<string, string> = {
  external_effect_review_required: 'External action outcome needs independent verification',
  prior_mutating_effect_review_required: 'Earlier changes must be verified before resuming',
  pending_action_review_required: 'A pending action requires separate review',
  corrupt_reasoning_receipt_review_required: 'Reasoning history needs integrity review',
  unresolved_reasoning_review_required: 'A previous reasoning call has no recorded outcome',
  ownership_recovery_required: 'Task ownership needs reconciliation',
  task_not_resumable: 'This task cannot be resumed in its current state',
  inconsistent_preflight_review_required: 'Recovery information is inconsistent',
};

export function recoveryStatusLabel(value: TaskResumePreflight | null | undefined): string {
  if (mayOfferExplicitResume(value)) return 'No blockers found in the latest preflight';
  if (!value) return 'Recovery status unavailable — resume remains disabled';
  if (value.preflight_clear === true) return 'Recovery information is incomplete or inconsistent';
  return BLOCKER_LABELS[value.reason] ?? 'Recovery needs review before resuming';
}

/** Only render the server's bounded, fixed-template guidance, never raw logs. */
export function recoveryVerificationSteps(value: TaskResumePreflight | null | undefined): string[] {
  if (!value || !Array.isArray(value.review?.verification_steps)) return [];
  return value.review.verification_steps.filter(
    (step): step is string => typeof step === 'string' && step.length > 0 && step.length <= 400,
  ).slice(0, 4);
}
