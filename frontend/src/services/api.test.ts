import {
  AxiosError,
  type AxiosAdapter,
  type AxiosResponse,
  type InternalAxiosRequestConfig,
} from 'axios';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import apiClient from './api';

/**
 * The control token the request interceptor attaches. The response
 * interceptor must never put it into a message.
 */
const CONTROL_TOKEN = 'control-token-under-test';

/** An adapter that answers 200 with a JSON body, exactly as a server sends it. */
const okAdapter = (body: string): AxiosAdapter => (config) =>
  Promise.resolve({
    data: body,
    status: 200,
    statusText: 'OK',
    headers: {},
    config,
  } as unknown as AxiosResponse);

/** An adapter that answers `status` with a raw body, exactly as a server sends it. */
const errorAdapter = (status: number, rawBody: string): AxiosAdapter => (config) =>
  Promise.reject(
    new AxiosError(
      `Request failed with status code ${status}`,
      AxiosError.ERR_BAD_RESPONSE,
      config,
      undefined,
      {
        data: rawBody,
        status,
        statusText: '',
        headers: {},
        config,
      } as unknown as AxiosResponse
    )
  );

/** An adapter that fails the way a dead socket does: no response at all. */
const networkErrorAdapter: AxiosAdapter = (config) =>
  Promise.reject(new AxiosError('Network Error', AxiosError.ERR_NETWORK, config, undefined));

/** The rejection of a request, with the "it did not resolve" half asserted. */
async function rejectedWith(promise: Promise<unknown>): Promise<AxiosError> {
  const outcome: { resolved: boolean; error: unknown } = { resolved: false, error: null };
  await promise.then(
    () => {
      outcome.resolved = true;
    },
    (reason: unknown) => {
      outcome.error = reason;
    }
  );
  expect(outcome.resolved).toBe(false);
  expect(outcome.error).toBeInstanceOf(AxiosError);
  return outcome.error as AxiosError;
}

beforeEach(() => {
  // vitest's default environment is `node`, where `sessionStorage` does not
  // exist, and `getControlToken` reads it on every request.
  vi.stubGlobal('sessionStorage', {
    getItem: vi.fn(() => CONTROL_TOKEN),
    setItem: vi.fn(),
    removeItem: vi.fn(),
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('the axios response interceptor', () => {
  it('surfaces the backend detail for an error response', async () => {
    const adapter = errorAdapter(
      502,
      JSON.stringify({ detail: 'Inference returned an empty response' })
    );
    const error = await rejectedWith(
      apiClient.post('/onboarding/inference', { model_id: 'm1' }, { adapter })
    );
    expect(error.message).toBe('Inference returned an empty response');
    expect(error.response?.status).toBe(502);
  });

  it('prefers the detail over the error slug', async () => {
    const adapter = errorAdapter(
      500,
      JSON.stringify({ error: 'INTERNAL_ERROR', detail: 'boom', code: '500' })
    );
    const error = await rejectedWith(
      apiClient.post('/onboarding/inference', { model_id: 'm1' }, { adapter })
    );
    expect(error.message).toBe('boom');
  });

  it('falls back to the error slug when there is no detail', async () => {
    const adapter = errorAdapter(409, JSON.stringify({ error: 'VAULT_WRITE_BLOCKED', code: '409' }));
    const error = await rejectedWith(
      apiClient.post('/onboarding/inference', { model_id: 'm1' }, { adapter })
    );
    expect(error.message).toBe('VAULT_WRITE_BLOCKED');
  });

  it('falls back to the generic message when the body carries no detail', async () => {
    const adapter = errorAdapter(502, '');
    const error = await rejectedWith(
      apiClient.post('/onboarding/inference', { model_id: 'm1' }, { adapter })
    );
    expect(error.message).toBe('Request failed with status code 502');
  });

  it('never surfaces a raw body', async () => {
    const adapter = errorAdapter(
      500,
      'Traceback (most recent call last):\n  File "backend/main.py", line 1, in <module>'
    );
    const error = await rejectedWith(
      apiClient.post('/onboarding/inference', { model_id: 'm1' }, { adapter })
    );
    expect(error.message).toBe('Request failed with status code 500');
    expect(error.message).not.toContain('Traceback');
  });

  it('passes a successful response through unchanged', async () => {
    const body = { success: true, model: 'm1', response_preview: 'OK' };
    const adapter = okAdapter(JSON.stringify(body));
    const res = await apiClient.post('/onboarding/inference', { model_id: 'm1' }, { adapter });
    expect(res.status).toBe(200);
    expect(res.data).toEqual(body);
  });

  it('leaves a network failure without a response alone', async () => {
    const error = await rejectedWith(
      apiClient.post('/onboarding/inference', { model_id: 'm1' }, { adapter: networkErrorAdapter })
    );
    expect(error.message).toBe('Network Error');
    expect(error.response).toBeUndefined();
  });

  it('does not change the request side', async () => {
    const seen: InternalAxiosRequestConfig[] = [];
    const headersBefore: Array<Record<string, unknown>> = [];
    const adapter: AxiosAdapter = (config) => {
      seen.push(config);
      // A COPY of the headers as they are on the wire, taken BEFORE the
      // response interceptor runs.  Comparing against the same object's own
      // keys could never see a header VALUE mutated by the response side.
      headersBefore.push({ ...config.headers });
      return Promise.reject(
        new AxiosError(
          'Request failed with status code 502',
          AxiosError.ERR_BAD_RESPONSE,
          config,
          undefined,
          {
            data: JSON.stringify({ detail: 'Inference returned an empty response' }),
            status: 502,
            statusText: '',
            headers: {},
            config,
          } as unknown as AxiosResponse
        )
      );
    };
    const error = await rejectedWith(
      apiClient.post('/onboarding/inference', { model_id: 'm1' }, { adapter })
    );
    expect(error.message).toBe('Inference returned an empty response');
    expect(seen).toHaveLength(1);
    // The request interceptor still ran: the control token is on the way out.
    expect(seen[0].headers['X-Control-Token']).toBe(CONTROL_TOKEN);
    // The response interceptor changed nothing on the request: same headers,
    // same values, as they were when the request went out.
    expect({ ...seen[0].headers }).toEqual(headersBefore[0]);
  });

  it('never puts the control token in the message', async () => {
    const adapter = errorAdapter(
      502,
      JSON.stringify({ detail: 'Inference returned an empty response' })
    );
    const error = await rejectedWith(
      apiClient.post('/onboarding/inference', { model_id: 'm1' }, { adapter })
    );
    expect(error.message).not.toContain(CONTROL_TOKEN);
  });
});
