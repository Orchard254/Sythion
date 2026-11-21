import { useCallback, useEffect, useState } from 'react';
import { useAppStore } from '../store/appStore';
import { healthService, taskService, configureBridge } from '../services/api';
import { resolveBridgeConfig, isTauri } from '../services/bridge';

/**
 * The packaged shell starts the sidecar itself in its Tauri `setup` hook and
 * publishes the bridge config once the 90s handshake completes (see
 * `src-tauri/src/sidecar.rs`: HANDSHAKE_TIMEOUT_SECS). Keep polling at least as
 * long so the UI never declares a definitive failure while the shell is still
 * starting the local runtime. The owner must never be asked to launch a
 * process manually.
 */
export const BRIDGE_TIMEOUT_MS = 95_000;

/** Read-only probe used after the startup window elapses, for self-healing. */
export const QUIET_RECHECK_MS = 5_000;

async function initBridge(signal?: AbortSignal, pollIntervalMs = 500): Promise<void> {
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
      // The shell is still resolving the sidecar handshake; poll again.
    }
    // Guard after catch before sleeping.
    if (signal?.aborted) return;
    await new Promise<void>((resolve) => setTimeout(resolve, pollIntervalMs));
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
  const setBridgeReady = useAppStore((s) => s.setBridgeReady);
  const setError = useAppStore((s) => s.setError);
  const setTasks = useAppStore((s) => s.setTasks);
  const setLoading = useAppStore((s) => s.setLoading);

  const [isInitialized, setIsInitialized] = useState(false);
  // The initial health poll must not use Vite's fixed-port fallback while
  // Tauri is still discovering its dynamically assigned sidecar port.
  const [isBridgeReady, setIsBridgeReady] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let recheckTimer: ReturnType<typeof setTimeout> | undefined;
    const isCurrent = () => !controller.signal.aborted;

    // Commit the post-handshake sequence. Shared by the initial bootstrap and
    // by the quiet background probe so both recover the exact same way.
    async function commitRuntime(): Promise<boolean> {
      if (!isCurrent()) return false;
      setIsBridgeReady(true);
      // Companion and main windows run their own React trees. The shared
      // store gate protects health hooks that mount before this effect.
      setBridgeReady(true);

      const health = await healthService.check();
      if (!isCurrent()) return false;
      setHealthStatus(health);
      setConnected(true);
      setError(null);

      const tasks = await taskService.list();
      if (!isCurrent()) return false;
      setTasks(tasks);
      return true;
    }

    function scheduleQuietRecheck() {
      if (!isCurrent()) return;
      recheckTimer = setTimeout(() => { void quietRecheck(); }, QUIET_RECHECK_MS);
    }

    // Read-only self-healing: never bills quota and never mutates consequential
    // state. It only re-reads the bridge config / health / tasks and commits
    // them if the shell published a working runtime in the meantime.
    async function quietRecheck() {
      if (!isCurrent()) return;
      try {
        await initBridge(controller.signal, 2_000);
        if (!isCurrent()) return;
        if (await commitRuntime()) {
          setIsInitialized(true);
          return;
        }
      } catch {
        // Still unreachable. Keep probing; the UI stays honest about the last
        // definitive check and does not hand the owner a manual retry.
      }
      scheduleQuietRecheck();
    }

    async function run() {
      if (!isCurrent()) return;
      setLoading(true);
      try {
        await initBridge(controller.signal);
        if (!(await commitRuntime())) return;
        if (!isCurrent()) return;
        setIsInitialized(true);
      } catch (err) {
        if (!isCurrent()) return;
        setError(err instanceof Error ? err.message : 'Failed to connect to backend');
        setConnected(false);
        // Initialised so the UI can show the honest offline state instead of an
        // indefinite spinner. Background probing continues automatically.
        setIsInitialized(true);
        scheduleQuietRecheck();
      } finally {
        if (isCurrent()) {
          setLoading(false);
        }
      }
    }

    void run();
    return () => {
      controller.abort();
      if (recheckTimer) clearTimeout(recheckTimer);
    };
  }, [attempt, setBridgeReady, setConnected, setError, setHealthStatus, setLoading, setTasks]);

  const retry = useCallback(() => {
    setBridgeReady(false);
    setIsBridgeReady(false);
    setIsInitialized(false);
    setAttempt((n) => n + 1);
  }, [setBridgeReady]);

  return { isInitialized, isBridgeReady, retry };
}
