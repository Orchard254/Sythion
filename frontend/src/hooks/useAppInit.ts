import { useState, useEffect } from 'react';
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
  const { 
    setHealthStatus, 
    setConnected, 
    setError, 
    setTasks,
    setLoading 
  } = useAppStore();

  const [isInitialized, setIsInitialized] = useState(false);

  useEffect(() => {
    const initializeApp = async () => {
      setLoading(true);
      
      try {
        await initBridge();

        // Check health
        const health = await healthService.check();
        setHealthStatus(health);
        setConnected(true);
        
        // Load tasks
        const tasks = await taskService.list();
        setTasks(tasks);
        
        setIsInitialized(true);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to connect to backend');
        setConnected(false);
      } finally {
        setLoading(false);
      }
    };

    initializeApp();
    // Mount-once initialization; the zustand setters are stable references.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return { isInitialized };
}
