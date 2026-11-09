import { useCallback, useEffect, useState } from 'react';
import { useAppStore } from '../store/appStore';
import { healthService, taskService, configureBridge } from '../services/api';
import { resolveBridgeConfig, isTauri } from '../services/bridge';

const BRIDGE_TIMEOUT_MS = 60_000;

async function initBridge(signal?: AbortSignal): Promise<void> {
  if (signal?.aborted) return;
  if (!isTauri()) {
    const config = await resolveBridgeConfig();
    // Suppress stale results before mutating global bridge state.
    if (signal?.aborted) return;
    configureBridge(config);
    return;
  }

  const deadline = Date.now() + BRIDGE_TIMEOUT_MS;
  while (Date.now() < deadline) {
    // Guard before each discovery so an aborted wait cannot start another invoke.
    if (signal?.aborted) return;
    try {
      const config = await resolveBridgeConfig();
      // Suppress stale results after each await boundary.
      if (signal?.aborted) return;
      if (config) {
        if (signal?.aborted) return;
        configureBridge(config);
        return;
      }
    } catch {
      // Sidecar not ready yet; poll again.
    }
    // Guard after catch before sleeping.
    if (signal?.aborted) return;
    await new Promise<void>((resolve) => setTimeout(resolve, 500));
    // Guard after the (possibly aborted) sleep.
    if (signal?.aborted) return;
  }
  // Do not throw once the run has been cancelled.
  if (signal?.aborted) return;
  throw new Error('Sythion (Synth) sidecar did not become ready');
}

export function useAppInit() {
  const setHealthStatus = useAppStore((s) => s.setHealthStatus);
  const setConnected = useAppStore((s) => s.setConnected);
  const setError = useAppStore((s) => s.setError);
  const setTasks = useAppStore((s) => s.setTasks);
  const setLoading = useAppStore((s) => s.setLoading);

  const [isInitialized, setIsInitialized] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();

    async function run() {
      if (controller.signal.aborted) return;
      setLoading(true);
      try {
        await initBridge(controller.signal);
        if (controller.signal.aborted) return;

        const health = await healthService.check();
        if (controller.signal.aborted) return;
        setHealthStatus(health);
        setConnected(true);
        setError(null);

        const tasks = await taskService.list();
        if (controller.signal.aborted) return;
        setTasks(tasks);

        if (!controller.signal.aborted) {
          setIsInitialized(true);
        }
      } catch (err) {
        if (controller.signal.aborted) return;
        setError(err instanceof Error ? err.message : 'Failed to connect to backend');
        setConnected(false);
        // Mark initialised so the UI can show the offline screen with a retry
        // instead of an indefinite spinner.
        setIsInitialized(true);
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
        }
      }
    }

    void run();
    return () => {
      controller.abort();
    };
  }, [attempt, setConnected, setError, setHealthStatus, setLoading, setTasks]);

  const retry = useCallback(() => {
    setIsInitialized(false);
    setAttempt((n) => n + 1);
  }, []);

  return { isInitialized, retry };
}
