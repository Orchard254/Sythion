import { type AxiosAdapter, type AxiosResponse } from 'axios';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import apiClient, { cloudPreviewService, type CloudPreviewProviderId } from './api';

const savedAdapter = apiClient.defaults.adapter;
let calls: Array<{ method: string; path: string; payload: unknown }>;

beforeEach(() => {
  calls = [];
  vi.stubGlobal('sessionStorage', { getItem: vi.fn(() => 'synthetic-control-token') });
  apiClient.defaults.adapter = ((config) => {
    calls.push({
      method: String(config.method || '').toUpperCase(),
      path: config.url || '',
      payload: config.data ? JSON.parse(String(config.data)) : undefined,
    });
    return Promise.resolve({
      data: { providers: [], staged: {}, ready_for_execution: false },
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    } as AxiosResponse);
  }) as AxiosAdapter;
});

afterEach(() => {
  apiClient.defaults.adapter = savedAdapter;
  vi.unstubAllGlobals();
});

describe('cloud preview API security and wire contracts', () => {
  it('fetches only non-executable preview status', async () => {
    const status = await cloudPreviewService.status();
    expect(status.ready_for_execution).toBe(false);
    expect(calls).toStrictEqual([
      { method: 'GET', path: '/inference-preview/providers', payload: undefined },
    ]);
  });

  it.each([
    ['openrouter', '/secrets/openrouter_api_key'],
    ['nvidia', '/secrets/nvidia_api_key'],
    ['groq', '/secrets/groq_api_key'],
  ] as const)('maps only %s into its exact vault key', async (provider, expectedPath) => {
    await cloudPreviewService.saveCredential(provider, '    synthetic-secret  ');
    expect(calls).toStrictEqual([
      { method: 'PUT', path: expectedPath, payload: { value: 'synthetic-secret' } },
    ]);
  });

  it('does not construct arbitrary credential paths from untrusted provider names', async () => {
    await expect(cloudPreviewService.saveCredential(
      'https://attacker.example' as CloudPreviewProviderId, 'synthetic-secret'
    )).rejects.toThrow('Choose a provider');
    expect(calls).toStrictEqual([]);
  });

  it('stages only the explicit candidate, with no active-provider mutation', async () => {
    const selection = { provider_id: 'openrouter' as const, model_id: 'google/gemma-4-26b-a4b-it:free' };
    await cloudPreviewService.stage(selection);
    expect(calls).toStrictEqual([
      { method: 'POST', path: '/inference-preview/stage', payload: selection },
    ]);
  });

  it('routes one short text message only to the isolated preview endpoint', async () => {
    const selection = { provider_id: 'openrouter' as const, model_id: 'unit/model:free' };
    await cloudPreviewService.chat(selection, '  Hello there  ');
    expect(calls).toStrictEqual([{
      method: 'POST', path: '/inference-preview/chat',
      payload: { ...selection, text: 'Hello there', confirm_network_request: true },
    }]);
  });

  it('rejects an empty preview message before making any request', async () => {
    await expect(cloudPreviewService.chat({
      provider_id: 'groq', model_id: 'unit'
    }, '    ')).rejects.toThrow('Choose a short');
    expect(calls).toStrictEqual([]);
  });

  it('sends explicit network consent only on the probe endpoint', async () => {
    const selection = { provider_id: 'groq' as const, model_id: 'test/functional:free' };
    await cloudPreviewService.probe(selection);
    expect(calls).toStrictEqual([
      { method: 'POST', path: '/inference-preview/probe', payload: {
        ...selection, confirm_network_request: true,
      } },
    ]);
  });
});
