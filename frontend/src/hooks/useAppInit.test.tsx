/**
 * @vitest-environment jsdom
 */
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

import React from 'react';
import { act, create } from 'react-test-renderer';
import type { ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useAppInit } from './useAppInit';
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
    expect(healthCheck).not.toHaveBeenCalled();

    pending.resolve({ baseUrl: 'http://127.0.0.1:60333/api/v1', controlToken: 'synthetic-test-token' });
    await flush(12);
    expect(hook.isBridgeReady).toBe(true);
    expect(configureBridge).toHaveBeenCalledTimes(1);

    // A fresh discovery attempt must not continue polling against an old bridge.
    const next = deferred<{ baseUrl: string; controlToken: string }>();
    resolveBridgeConfig.mockReturnValueOnce(next.promise);
    await act(async () => { hook.retry(); });
    expect(hook.isBridgeReady).toBe(false);
    next.resolve({ baseUrl: 'http://127.0.0.1:62000/api/v1', controlToken: 'synthetic-next-token' });
    await flush(12);
    expect(hook.isBridgeReady).toBe(true);
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
});
