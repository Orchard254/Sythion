import { useCallback, useEffect, useState } from 'react';
import { useAppStore } from '../store/appStore';
import { healthService, taskService, configureBridge } from '../services/api';
import { resolveBridgeConfig, isTauri } from '../services/bridge';

const BRIDGE_TIMEOUT_MS = 60_000;

async function initBridge(): Promise<void> {
  if (!isTauri()) {
    configureBridge(await resolveBridgeConfig());
    return;
  }

  const deadline = Date.now() + BRIDGE_TIMEOUT_MS;
  while (Date.now() < deadline) {
    try {
      const config = await resolveBridgeConfig();
      if (config) {
        configureBridge(config);
        return;
      }
    } catch {
      // Sidecar not ready yet; poll again.
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error('Sythion (Synth) sidecar did not become ready');
}

export function useAppInit() {
  const setHealthStatus = useAppStore((s) => s.setHealthStatus);
  const setConnected = useAppStore((s) => s.setConnected);
  const setError = useAppStore((s) => s.setError);
  const setTasks = useAppStore((s) => s.setTasks);
  const setLoading = useAppStore((s) => s.setLoading);
  const setDataMode = useAppStore((s) => s.setDataMode);

  const [isInitialized, setIsInitialized] = useState(false);
  const [attempt, setAttempt] = useState(0);

  const connect = useCallback(async () => {
    setLoading(true);
    try {
      await initBridge();

      const health = await healthService.check();
      setHealthStatus(health);
      setConnected(true);
      setError(null);
      // A deep-linked ?preview=1 is honoured only while no backend answers.
      if (typeof window === 'undefined' || new URLSearchParams(window.location.search).get('preview') !== '1') {
        setDataMode('live');
      }

      const tasks = await taskService.list();
      setTasks(tasks);

      setIsInitialized(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to connect to backend');
      setConnected(false);
      // Mark initialised so the UI can offer the offline / preview paths
      // instead of an indefinite spinner.
      setIsInitialized(true);
    } finally {
      setLoading(false);
    }
  }, [setConnected, setDataMode, setError, setHealthStatus, setLoading, setTasks]);

  useEffect(() => {
    void connect();
  }, [connect, attempt]);

  const retry = useCallback(() => {
    setIsInitialized(false);
    setAttempt((n) => n + 1);
  }, []);

  return { isInitialized, retry };
}