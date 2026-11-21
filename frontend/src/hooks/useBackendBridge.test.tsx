/**
 * @vitest-environment jsdom
 */
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

import { act, create } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useHealth, useObjectives, useSkillProposals, useToolProposals } from './useBackend';
import { useAppStore } from '../store/appStore';

const mock = vi.hoisted(() => ({
  healthCheck: vi.fn(async () => ({ status: 'healthy', version: 'test' })),
  listObjectives: vi.fn(async () => []),
  listSkillProposals: vi.fn(async () => []),
  listToolProposals: vi.fn(async () => []),
}));

vi.mock('../services/api', () => ({
  healthService: { check: mock.healthCheck },
  objectiveService: { list: mock.listObjectives },
  skillService: { proposals: mock.listSkillProposals },
  toolProposalService: { list: mock.listToolProposals },
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

describe('bounded polling for stale-prone summary queries', () => {
  it('polls objectives and proposal badges at bounded intervals', async () => {
    // With refetchOnWindowFocus disabled globally, these summaries can only
    // stay fresh while a screen is open via an explicit refetchInterval.
    useAppStore.setState({ isConnected: true, bridgeReady: true });
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const PollHarness = () => {
      useObjectives();
      useSkillProposals();
      useToolProposals();
      return null;
    };
    let view: ReturnType<typeof create> | undefined;
    try {
      await act(async () => {
        view = create(
          <QueryClientProvider client={client}><PollHarness /></QueryClientProvider>
        );
      });
      const intervalFor = (key: string) =>
        (client.getQueryCache().find({ queryKey: [key] })?.options as
          | { refetchInterval?: number }
          | undefined)?.refetchInterval;
      expect(intervalFor('objectives')).toBe(20_000);
      expect(intervalFor('skill-proposals')).toBe(15_000);
      expect(intervalFor('tool-proposals')).toBe(15_000);
    } finally {
      await act(async () => { view?.unmount(); });
      client.clear();
    }
  });
});
