import { AxiosError, type AxiosAdapter } from 'axios';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import apiClient, { modelService } from './api';

const originalAdapter = apiClient.defaults.adapter;
const models = [{ name: 'beam', model: 'beam' }, { model: 'other:exact-tag' }];

function respond(body: unknown) {
  const adapter: AxiosAdapter = async (config) => {
    expect(config.url).toBe('/models');
    expect(config.method).toBe('get');
    expect(config.headers['X-Control-Token']).toBe('inert-discovery-token');
    return { data: JSON.stringify(body), status: 200, statusText: 'OK', headers: {}, config };
  };
  apiClient.defaults.adapter = adapter;
}

beforeEach(() => {
  vi.stubGlobal('sessionStorage', { getItem: () => 'inert-discovery-token' });
});
afterEach(() => {
  apiClient.defaults.adapter = originalAdapter;
  vi.unstubAllGlobals();
});

describe('real model discovery service and axios client', () => {
  it('turns the unchanged HTTP provider payload into exact selector IDs', async () => {
    respond({ models });
    expect(await modelService.available()).toStrictEqual(['beam', 'other:exact-tag']);
  });

  it('prefers valid name, falls back to model, retains strings/order/duplicates without aliases', async () => {
    respond({ models: [
      { name: 'beam', model: 'not-beam' }, { name: '', model: 'fallback:tag' },
      { name: 42, model: 'numeric-name-fallback' }, { name: '   ', model: 'whitespace-fallback' },
      'legacy:exact', 'beam', { name: ' exact ID ' },
      '', '  ', null, false, 42, [], {}, { name: {} }, { model: 42 }, { name: null, model: '' },
    ] });
    expect(await modelService.available()).toStrictEqual([
      'beam', 'fallback:tag', 'numeric-name-fallback', 'whitespace-fallback',
      'legacy:exact', 'beam', ' exact ID ',
    ]);
  });

  it.each([{}, { models: [] }, { models: null }, { models: {} }, { models: 'beam' }, null])(
    'returns an empty list for absent/non-array payload %j', async (body) => {
      respond(body);
      expect(await modelService.available()).toStrictEqual([]);
    },
  );

  it('does not turn a discovery failure into empty success', async () => {
    apiClient.defaults.adapter = async (config) => {
      throw new AxiosError('Request failed with status code 503', AxiosError.ERR_BAD_RESPONSE,
        config, undefined, { data: { detail: 'Failed to fetch models: discovery unavailable' },
          status: 503, statusText: '', headers: {}, config });
    };
    await expect(modelService.available()).rejects.toMatchObject({
      message: 'Failed to fetch models: discovery unavailable', response: { status: 503 },
    });
  });
});
