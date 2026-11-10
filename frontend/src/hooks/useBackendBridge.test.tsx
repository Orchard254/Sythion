/**
 * @vitest-environment jsdom
 */
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

import { act, create } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useHealth } from './useBackend';
import { useAppStore } from '../store/appStore';

const mock = vi.hoisted(() => ({
  healthCheck: vi.fn(async () => ({ status: 'healthy', version: 'test' })),
}));

vi.mock('../services/api', () => ({
  healthService: { check: mock.healthCheck },
}));

const Harness = () => {
  useHealth(); // Companion/useBackendLive path does not pass an explicit flag.
  return null;
};

beforeEach(() => {
  vi.clearAllMocks();
  useAppStore.setState({ isConnected: false, bridgeReady: false });
});

describe('companion health handshake gate', () => {
  it('does not contact the Vite proxy until this window configures its bridge', async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    let view: ReturnType<typeof create> | undefined;
    try {
      await act(async () => {
        view = create(
          <QueryClientProvider client={client}><Harness /></QueryClientProvider>
        );
      });
      expect(mock.healthCheck).not.toHaveBeenCalled();

      await act(async () => {
        useAppStore.getState().setBridgeReady(true);
        await Promise.resolve();
      });
      expect(mock.healthCheck).toHaveBeenCalledTimes(1);
      expect(useAppStore.getState().isConnected).toBe(false);
    } finally {
      await act(async () => { view?.unmount(); });
      client.clear();
    }
  });
});
