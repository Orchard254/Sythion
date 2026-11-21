/**
 * @vitest-environment jsdom
 */
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

import React from 'react';
import { act, create } from 'react-test-renderer';
import type { ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { BRIDGE_TIMEOUT_MS, MAX_RECONNECT_ATTEMPTS, QUIET_RECHECK_MS, useAppInit } from './useAppInit';
import { useAppStore } from '../store/appStore';
import type { Task } from '../types';

const mock = vi.hoisted(() => ({
  configureBridge: vi.fn(),
  healthCheck: vi.fn(),
  taskList: vi.fn(),
  isTauri: vi.fn(() => false),
  resolveBridgeConfig: vi.fn(),
}));

const configureBridge = mock.configureBridge;
const healthCheck = mock.healthCheck;
const taskList = mock.taskList;
const isTauri = mock.isTauri;
const resolveBridgeConfig = mock.resolveBridgeConfig;

vi.mock('../services/api', () => ({
  configureBridge: mock.configureBridge,
  healthService: { check: mock.healthCheck },
  taskService: { list: mock.taskList },
}));
vi.mock('../services/bridge', () => ({
  isTauri: mock.isTauri,
  resolveBridgeConfig: mock.resolveBridgeConfig,
}));

function deferred<T = unknown>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

let hook: ReturnType<typeof useAppInit>;
let view: ReactTestRenderer | undefined;
const Harness = () => {
  hook = useAppInit();
  return null;
};

async function flush(times = 10) {
  for (let i = 0; i < times; i += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

beforeEach(() => {
  vi.clearAllMocks();
  useAppStore.setState({
    tasks: [],
    activeTaskId: null,
    isLoading: false,
    error: null,
    healthStatus: null,
    isConnected: false,
    bridgeReady: false,
  });
  isTauri.mockReturnValue(false);
  resolveBridgeConfig.mockResolvedValue({ baseUrl: '/api/v1', controlToken: '' });
  healthCheck.mockResolvedValue({ status: 'healthy' });
  taskList.mockResolvedValue([]);
});

afterEach(async () => {
  await act(async () => {
    view?.unmount();
  });
});

describe('useAppInit race guards', () => {

  it('keeps bridge readiness false until configuration, and resets it on retry', async () => {
    const pending = deferred<{ baseUrl: string; controlToken: string }>();
    resolveBridgeConfig.mockReturnValueOnce(pending.promise);
    await act(async () => { view = create(<Harness />); });
    expect(hook.isBridgeReady).toBe(false);
    expect(useAppStore.getState().bridgeReady).toBe(false);
    expect(healthCheck).not.toHaveBeenCalled();

    pending.resolve({ baseUrl: 'http://127.0.0.1:60333/api/v1', controlToken: 'synthetic-test-token' });
    await flush(12);
    expect(hook.isBridgeReady).toBe(true);
    expect(useAppStore.getState().bridgeReady).toBe(true);
    expect(configureBridge).toHaveBeenCalledTimes(1);

    // A fresh discovery attempt must not continue polling against an old bridge.
    const next = deferred<{ baseUrl: string; controlToken: string }>();
    resolveBridgeConfig.mockReturnValueOnce(next.promise);
    await act(async () => { hook.retry(); });
    expect(hook.isBridgeReady).toBe(false);
    expect(useAppStore.getState().bridgeReady).toBe(false);
    next.resolve({ baseUrl: 'http://127.0.0.1:62000/api/v1', controlToken: 'synthetic-next-token' });
    await flush(12);
    expect(hook.isBridgeReady).toBe(true);
    expect(useAppStore.getState().bridgeReady).toBe(true);
  });
  it('normal success configures bridge, health and tasks', async () => {
    await act(async () => {
      view = create(<Harness />);
    });
    await flush(12);
    expect(hook.isInitialized).toBe(true);
    expect(configureBridge).toHaveBeenCalled();
    expect(hook.isBridgeReady).toBe(true);
    expect(healthCheck).toHaveBeenCalled();
    expect(taskList).toHaveBeenCalled();
    expect(useAppStore.getState().isConnected).toBe(true);
    expect(useAppStore.getState().healthStatus).toEqual({ status: 'healthy' });
    expect(useAppStore.getState().isLoading).toBe(false);
  });

  it('offline bridge failure surfaces error semantics', async () => {
    resolveBridgeConfig.mockRejectedValue(new Error('bridge down'));
    await act(async () => {
      view = create(<Harness />);
    });
    await flush(12);
    expect(hook.isInitialized).toBe(true);
    expect(hook.isBridgeReady).toBe(false);
    expect(useAppStore.getState().error).toBe('bridge down');
    expect(useAppStore.getState().isConnected).toBe(false);
    expect(useAppStore.getState().isLoading).toBe(false);
  });

  it('newest retry wins and stale attempt has no side effects', async () => {
    const first = deferred<{ baseUrl: string; controlToken: string }>();
    resolveBridgeConfig.mockReturnValueOnce(first.promise);
    await act(async () => {
      view = create(<Harness />);
    });

    const second = deferred<{ baseUrl: string; controlToken: string }>();
    resolveBridgeConfig.mockReturnValueOnce(second.promise);
    await act(async () => {
      hook.retry();
    });
    await flush(4);

    first.resolve({ baseUrl: '/stale', controlToken: 'stale' });
    await flush(8);

    second.resolve({ baseUrl: '/fresh', controlToken: 'fresh' });
    await flush(12);

    expect(hook.isInitialized).toBe(true);
    expect(configureBridge).toHaveBeenCalledTimes(1);
    expect(configureBridge).toHaveBeenCalledWith({ baseUrl: '/fresh', controlToken: 'fresh' });
    expect(healthCheck).toHaveBeenCalledTimes(1);
    expect(taskList).toHaveBeenCalledTimes(1);
    expect(useAppStore.getState().healthStatus).toEqual({ status: 'healthy' });
  });

  it('unmount suppresses stale bridge/health/tasks side effects', async () => {
    const pending = deferred<{ baseUrl: string; controlToken: string }>();
    resolveBridgeConfig.mockReturnValue(pending.promise);
    await act(async () => {
      view = create(<Harness />);
    });
    await act(async () => {
      view?.unmount();
    });
    pending.resolve({ baseUrl: '/late', controlToken: '' });
    await flush(8);
    expect(configureBridge).not.toHaveBeenCalled();
    expect(healthCheck).not.toHaveBeenCalled();
    expect(taskList).not.toHaveBeenCalled();
  });

  it('StrictMode success', async () => {
    const v = create(<React.StrictMode><Harness /></React.StrictMode>);
    view = v;
    await flush(15);
    expect(hook.isInitialized).toBe(true);
    expect(configureBridge).toHaveBeenCalled();
  });
});

describe('useAppInit expanded deterministic guards', () => {
  it('tauri polling abort during sleep prevents extra invoke/configure', async () => {
    vi.useFakeTimers();
    try {
      isTauri.mockReturnValue(true);
      const first = deferred<unknown>();
      resolveBridgeConfig.mockReturnValueOnce(first.promise);
      await act(async () => {
        view = create(<Harness />);
      });
      first.resolve(undefined);
      await act(async () => {
        await Promise.resolve();
      });
      await act(async () => {
        view?.unmount();
      });
      await act(async () => {
        await Promise.resolve();
      });
      view = undefined;
      expect(resolveBridgeConfig).toHaveBeenCalledTimes(1);
      expect(configureBridge).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it('deferred health rejection after retry has no stale commit', async () => {
    const staleHealth = deferred<unknown>();
    healthCheck.mockReturnValueOnce(staleHealth.promise).mockResolvedValue({ status: 'fresh' });
    await act(async () => {
      view = create(<Harness />);
    });
    await flush(4);

    await act(async () => {
      hook.retry();
    });
    await flush(4);

    staleHealth.reject(new Error('stale health'));
    await flush(8);

    expect(useAppStore.getState().error).toBeNull();
    expect(useAppStore.getState().isConnected).toBe(true);
    expect(useAppStore.getState().healthStatus).toEqual({ status: 'fresh' });
    expect(useAppStore.getState().isLoading).toBe(false);
  });

  it('deferred task resolution after retry has no stale commit', async () => {
    healthCheck.mockResolvedValue({ status: 'healthy' });
    const staleTasks = deferred<Task[]>();
    const freshTask: Task = {
      id: 'fresh',
      original_request: '',
      normalized_goal: null,
      state: 'completed',
      version: 0,
      active_action_id: null,
      pending_question: null,
      final_result: null,
      created_at: '',
      updated_at: '',
      terminal_reason: null,
    };
    taskList.mockReturnValueOnce(staleTasks.promise).mockResolvedValue([freshTask]);
    await act(async () => {
      view = create(<Harness />);
    });
    await flush(4);

    await act(async () => {
      hook.retry();
    });
    await flush(4);

    staleTasks.resolve([{
      id: 'stale',
      original_request: '',
      normalized_goal: null,
      state: 'completed',
      version: 0,
      active_action_id: null,
      pending_question: null,
      final_result: null,
      created_at: '',
      updated_at: '',
      terminal_reason: null,
    }]);
    await flush(8);

    const tasks = useAppStore.getState().tasks as Array<{ id?: string }>;
    expect(tasks.some((t) => t.id === 'stale')).toBe(false);
    expect(useAppStore.getState().error).toBeNull();
    expect(useAppStore.getState().isLoading).toBe(false);
  });

  it('abort during polling sleep does not continue after timer fires', async () => {
    vi.useFakeTimers();
    try {
      isTauri.mockReturnValue(true);
      resolveBridgeConfig.mockResolvedValue(undefined);
      await act(async () => {
        view = create(<Harness />);
      });
      await act(async () => {
        await Promise.resolve();
      });
      expect(resolveBridgeConfig).toHaveBeenCalledTimes(1);

      await act(async () => {
        view?.unmount();
      });
      view = undefined;

      await act(async () => {
        await vi.advanceTimersByTimeAsync(500);
      });
      await act(async () => {
        await Promise.resolve();
      });

      expect(resolveBridgeConfig).toHaveBeenCalledTimes(1);
      expect(configureBridge).not.toHaveBeenCalled();
      expect(healthCheck).not.toHaveBeenCalled();
      expect(taskList).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it('polls at least as long as the shell handshake window', () => {
    // src-tauri/src/sidecar.rs gives the sidecar 90s to publish its handshake.
    expect(BRIDGE_TIMEOUT_MS).toBeGreaterThanOrEqual(90_000);
  });

  it('self-heals a transient bridge failure without a manual retry', async () => {
    vi.useFakeTimers();
    try {
      resolveBridgeConfig
        .mockRejectedValueOnce(new Error('bridge down'))
        .mockResolvedValue({ baseUrl: '/api/v1', controlToken: '' });
      await act(async () => { view = create(<Harness />); });
      for (let i = 0; i < 8; i += 1) {
        await act(async () => { await Promise.resolve(); });
      }
      expect(useAppStore.getState().isConnected).toBe(false);
      expect(useAppStore.getState().error).toBe('bridge down');

      await act(async () => { await vi.advanceTimersByTimeAsync(QUIET_RECHECK_MS); });
      for (let i = 0; i < 8; i += 1) {
        await act(async () => { await Promise.resolve(); });
      }

      // The background probe recovered the runtime on its own.
      expect(useAppStore.getState().isConnected).toBe(true);
      expect(useAppStore.getState().error).toBeNull();
      expect(configureBridge).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it('re-discovers a changed bridge after a later failure and reconnects without any manual action', async () => {
    vi.useFakeTimers();
    const logs: string[] = [];
    const record = (...args: unknown[]) => { logs.push(args.map(String).join(' ')); };
    const spyLog = vi.spyOn(console, 'log').mockImplementation(record);
    const spyError = vi.spyOn(console, 'error').mockImplementation(record);
    const spyWarn = vi.spyOn(console, 'warn').mockImplementation(record);
    try {
      // Successful boot against the original bridge.
      await act(async () => { view = create(<Harness />); });
      for (let i = 0; i < 8; i += 1) await act(async () => { await Promise.resolve(); });
      expect(useAppStore.getState().isConnected).toBe(true);
      expect(configureBridge).toHaveBeenCalledTimes(1);

      // The backend restarts on a new port with a rotated control token.
      const rotated = 'rotated-reconnect-token-9f3a';
      const changedConfig = { baseUrl: 'http://127.0.0.1:61999/api/v1', controlToken: rotated };
      resolveBridgeConfig.mockResolvedValue(changedConfig);

      // AppContent reports the honest offline state on the later health failure.
      useAppStore.setState({ isConnected: false, error: 'Network Error' });
      expect(useAppStore.getState().isConnected).toBe(false);

      await act(async () => { hook.requestRediscovery(); });
      for (let i = 0; i < 8; i += 1) await act(async () => { await Promise.resolve(); });
      // No silent claim: still offline until the quiet probe actually re-runs.
      expect(useAppStore.getState().isConnected).toBe(false);
      expect(healthCheck).toHaveBeenCalledTimes(1);

      await act(async () => { await vi.advanceTimersByTimeAsync(QUIET_RECHECK_MS); });
      for (let i = 0; i < 8; i += 1) await act(async () => { await Promise.resolve(); });

      // Same quiet machinery re-resolved the bridge, then re-probed health + tasks.
      expect(configureBridge).toHaveBeenCalledTimes(2);
      expect(configureBridge).toHaveBeenLastCalledWith(changedConfig);
      expect(healthCheck).toHaveBeenCalledTimes(2);
      expect(taskList).toHaveBeenCalledTimes(2);
      expect(useAppStore.getState().isConnected).toBe(true);
      expect(useAppStore.getState().error).toBeNull();

      // The rotated token is never logged or surfaced by the reconnect path.
      expect(logs.join('\n')).not.toContain(rotated);
    } finally {
      vi.useRealTimers();
      spyLog.mockRestore();
      spyError.mockRestore();
      spyWarn.mockRestore();
    }
  });

  it('bounds quiet re-discovery when the backend stays unreachable', async () => {
    vi.useFakeTimers();
    try {
      await act(async () => { view = create(<Harness />); });
      for (let i = 0; i < 8; i += 1) await act(async () => { await Promise.resolve(); });
      const afterBoot = configureBridge.mock.calls.length;
      expect(afterBoot).toBe(1);

      useAppStore.setState({ isConnected: false, error: 'Network Error' });
      healthCheck.mockRejectedValue(new Error('still down'));
      resolveBridgeConfig.mockResolvedValue({ baseUrl: 'http://127.0.0.1:61111/api/v1', controlToken: 'still-down-token' });

      await act(async () => { hook.requestRediscovery(); });
      for (let i = 0; i < MAX_RECONNECT_ATTEMPTS; i += 1) {
        await act(async () => { await vi.advanceTimersByTimeAsync(QUIET_RECHECK_MS); });
        for (let j = 0; j < 8; j += 1) await act(async () => { await Promise.resolve(); });
      }

      expect(configureBridge.mock.calls.length - afterBoot).toBe(MAX_RECONNECT_ATTEMPTS);
      expect(useAppStore.getState().isConnected).toBe(false);
      expect(useAppStore.getState().error).toBe('Network Error');

      // Budget exhausted: no further probes as more quiet intervals elapse.
      await act(async () => { await vi.advanceTimersByTimeAsync(QUIET_RECHECK_MS * 3); });
      expect(configureBridge.mock.calls.length - afterBoot).toBe(MAX_RECONNECT_ATTEMPTS);
    } finally {
      vi.useRealTimers();
    }
  });
});
