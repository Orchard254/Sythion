import { describe, expect, it } from 'vitest';
import { GENERIC_MODEL_TEST_FAILURE, modelTestFailureMessage } from './modelTestFailure';

describe('bounded inference failure text', () => {
  it('recognizes the live 402 wrapped in backend HTTP 502 without leaking raw text', () => {
    const raw = 'Inference failed: HTTP error 402 (private-provider-trace)';
    const error = {
      message: raw,
      response: { status: 502, data: { detail: raw } },
    };
    const display = modelTestFailureMessage(error);
    expect(display).toContain('Ollama Cloud returned HTTP 402');
    expect(display).toContain('account credit');
    expect(display).not.toContain('private-provider-trace');
    expect(display).not.toContain('API key is invalid');
  });

  it('handles a direct 402 status without guessing that credentials are invalid', () => {
    expect(modelTestFailureMessage({
      message: 'opaque',
      response: { status: 402 },
    })).toContain('payment required');
  });

  it('does not reinterpret other errors or display untrusted provider text', () => {
    for (const error of [
      null,
      new Error('API KEY secret-123'),
      { message: 'HTTP error 401: credential secret-123' },
      { message: 'HTTP error 429 with private details' },
      { response: { status: 502, data: { detail: 'internal stack secret-123' } } },
    ]) {
      expect(modelTestFailureMessage(error)).toBe(GENERIC_MODEL_TEST_FAILURE);
    }
  });
});
