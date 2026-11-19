/**
 * Provider inference errors are untrusted (potentially contain backend
 * internals). Return bounded, actionable text and never leak raw details.
 * A provider's account 402 can be nested inside the backend's HTTP 502.
 */
export const GENERIC_MODEL_TEST_FAILURE =
  'The model test did not finish. Check your API key and internet connection, then try again or choose another model.';

export function modelTestFailureMessage(error: unknown): string {
  if (!error || typeof error !== 'object') return GENERIC_MODEL_TEST_FAILURE;
  const e = error as Record<string, unknown>;
  const response = e.response && typeof e.response === 'object'
    ? e.response as Record<string, unknown>
    : null;
  const data = response?.data && typeof response.data === 'object'
    ? response.data as Record<string, unknown>
    : null;
  const detail = typeof data?.detail === 'string' ? data.detail : '';
  const message = typeof e.message === 'string' ? e.message : '';
  const provider402 = response?.status === 402 ||
    /(?:HTTP\s+(?:error|status)|status\s+code)\s*:?\s*402\b/i.test(message) ||
    /(?:HTTP\s+(?:error|status)|status\s+code)\s*:?\s*402\b/i.test(detail);

  if (provider402) {
    return 'The provider returned HTTP 402 (payment required). Check that provider account’s credit, billing and usage limits. Your API key may be valid, but Sythion cannot continue until a real model test succeeds.';
  }
  return GENERIC_MODEL_TEST_FAILURE;
}
