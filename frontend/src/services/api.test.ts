import {
  AxiosError,
  type AxiosAdapter,
  type AxiosResponse,
  type InternalAxiosRequestConfig,
} from 'axios';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import apiClient, { assistantService, goalService, taskService } from './api';

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
    // same values, as they were when the request went out.  ``toStrictEqual``
    // (not ``toEqual``) also pins the KEYS: ``toEqual`` ignores an
    // undefined-valued property, so a header added with an undefined value
    // would slip past it.
    expect({ ...seen[0].headers }).toStrictEqual(headersBefore[0]);
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

const TRANSPORT_MESSAGE =
  'Connection lost. The request outcome is unknown; it was not automatically retried. Your draft is still here.';

describe('assistantService.turn transport boundary', () => {
  afterEach(() => {
    delete apiClient.defaults.adapter;
  });

  it('maps a network failure with no response to the honest transport message', async () => {
    const adapter: AxiosAdapter = (config) =>
      Promise.reject(new AxiosError('Network Error', AxiosError.ERR_NETWORK, config, undefined));
    apiClient.defaults.adapter = adapter;
    await expect(assistantService.turn('hello', 'request-1')).rejects.toThrow(TRANSPORT_MESSAGE);
  });

  it('maps a timeout with no response to the honest transport message', async () => {
    const adapter: AxiosAdapter = (config) =>
      Promise.reject(new AxiosError('timeout of 5000ms exceeded', 'ECONNABORTED', config, undefined));
    apiClient.defaults.adapter = adapter;
    await expect(assistantService.turn('hello', 'request-1')).rejects.toThrow(TRANSPORT_MESSAGE);
  });

  it('preserves a known 503 detail unchanged', async () => {
    const adapter = errorAdapter(503, JSON.stringify({ detail: 'Service Unavailable' }));
    apiClient.defaults.adapter = adapter;
    await expect(assistantService.turn('hello', 'request-1')).rejects.toMatchObject({
      message: 'Service Unavailable',
      response: { status: 503 },
    });
  });

  it('does not rewrite a 401 with an HTTP response as a network failure', async () => {
    const adapter = errorAdapter(401, JSON.stringify({ detail: 'Authentication required' }));
    apiClient.defaults.adapter = adapter;
    let thrown: unknown = null;
    try {
      await assistantService.turn('hello', 'request-1');
    } catch (err) {
      thrown = err;
    }
    expect(thrown).toBeInstanceOf(Error);
    const error = thrown as { message: string; response?: { status?: number } };
    expect(error.message).not.toBe(TRANSPORT_MESSAGE);
    expect(error.message).toBe('Authentication required');
    expect(error.response?.status).toBe(401);
  });
});

describe('central taskService.resume recovery gate', () => {
  afterEach(() => {
    delete apiClient.defaults.adapter;
  });

  const clear = () => ({
    task_id: 't1',
    task_state: 'interrupted' as const,
    owner_lease: 'released',
    unresolved_reasoning_calls: 0,
    reason: 'clear_for_explicit_resume',
    preflight_clear: true,
    review_required: false,
    automatic_replay_allowed: false,
    review: {
      status: 'preflight_clear_not_authorized' as const,
      blocker: 'clear_for_explicit_resume',
      severity: 'none' as const,
      observations: { task_state: 'interrupted', owner_lease: 'released', unresolved_reasoning_calls: 0 },
      evidence_source_classes: [],
      verification_steps: [],
      requires_authenticated_reconciliation: false,
      limited_to_current_snapshot: true,
      approved: false,
      authorization_issued: false,
      uncertainty_cleared: false,
      execution_started: false,
      automatic_replay_allowed: false,
      note: 'Read-only',
    },
  });

  const adapterFor = (preflight: unknown, seen: string[]): AxiosAdapter => async (config) => {
    const endpoint = `${(config.method ?? '').toUpperCase()} ${config.url ?? ''}`;
    seen.push(endpoint);
    const data = config.method === 'get' ? preflight : { id: 't1', state: 'running' };
    return { data, status: 200, statusText: 'OK', headers: {}, config } as AxiosResponse;
  };

  it('blocks all POST attempts if the fresh preflight requires review', async () => {
    const seen: string[] = [];
    const blocked = {
      ...clear(), preflight_clear: false, review_required: true,
      reason: 'prior_mutating_effect_review_required',
      review: { ...clear().review, status: 'review_required' as const, blocker: 'prior_mutating_effect_review_required' },
    };
    apiClient.defaults.adapter = adapterFor(blocked, seen);
    await expect(taskService.resume('t1')).rejects.toThrow('Recovery review is required');
    expect(seen).toEqual(['GET /tasks/t1/resume-preflight']);
  });

  it('does not accept an older preflight that omits the non-authorization review', async () => {
    const seen: string[] = [];
    apiClient.defaults.adapter = adapterFor({ ...clear(), review: undefined }, seen);
    await expect(taskService.resume('t1')).rejects.toThrow('Recovery review is required');
    expect(seen).toEqual(['GET /tasks/t1/resume-preflight']);
  });

  it('does not turn a forged approval flag into permission to resume', async () => {
    const seen: string[] = [];
    apiClient.defaults.adapter = adapterFor({
      ...clear(), review: { ...clear().review, authorization_issued: true },
    }, seen);
    await expect(taskService.resume('t1')).rejects.toThrow('Recovery review is required');
    expect(seen).toEqual(['GET /tasks/t1/resume-preflight']);
  });

  it('reads a fresh preflight immediately before a single explicit POST', async () => {
    const seen: string[] = [];
    apiClient.defaults.adapter = adapterFor(clear(), seen);
    const result = await taskService.resume('t1');
    expect(result).toMatchObject({ id: 't1', state: 'running' });
    expect(seen).toEqual(['GET /tasks/t1/resume-preflight', 'POST /tasks/t1/resume']);
  });

  it('never posts when the read-only preflight request fails', async () => {
    const seen: string[] = [];
    apiClient.defaults.adapter = async (config) => {
      seen.push(`${(config.method ?? '').toUpperCase()} ${config.url}`);
      throw new AxiosError('Network Error', AxiosError.ERR_NETWORK, config);
    };
    await expect(taskService.resume('t1')).rejects.toThrow('Network Error');
    expect(seen).toEqual(['GET /tasks/t1/resume-preflight']);
  });
});

describe('goalService.nextWork read-only preview contract', () => {
  afterEach(() => { delete apiClient.defaults.adapter; });

  it('fetches next-work without POST or background execution', async () => {
    const seen: string[] = [];
    const preview = {
      goal_id: 'g1', plan_version: 1, state: 'ready', goal_state: 'active',
      explanation: 'A step is eligible', runnable: true, structurally_ready: true,
      in_backoff: false,
      next_step: { id: 7, key: 'next', description: 'Work', priority: 2, persisted_status: 'blocked', would_unlock: true },
      counts: { total: 2, completed: 1, running: 0, failed: 0, blocked: 0, effectively_ready: 1 },
      blocked_steps: [], read_only: true, execution_started: false, authorization_issued: false,
    };
    const adapter: AxiosAdapter = async (config) => {
      seen.push(`${(config.method ?? '').toUpperCase()} ${config.url}`);
      return { data: preview, status: 200, statusText: 'OK', headers: {}, config } as AxiosResponse;
    };
    apiClient.defaults.adapter = adapter;
    const result = await goalService.nextWork('g1');
    expect(result.next_step?.would_unlock).toBe(true);
    expect(result.read_only).toBe(true);
    expect(result.execution_started).toBe(false);
    expect(seen).toEqual(['GET /goals/g1/next-work']);
  });

  it('propagates failure rather than guessing next work', async () => {
    apiClient.defaults.adapter = networkErrorAdapter;
    await expect(goalService.nextWork('g1')).rejects.toThrow('Network Error');
  });
});
