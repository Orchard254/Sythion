import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import { useMemo } from 'react';
import { useAppStore } from '../store/appStore';
import type { DataOrigin, GoalDetail, GoalPlanVersion, GoalSummary, GoalWorkPreview, MemoryItemsPage } from '../types';
import * as api from '../services/api';

/* Data access layer.
 *
 * Every screen reads through here. All data comes from the real backend
 * contract defined in services/api.ts; every query is gated on the bridge
 * handshake so panels never fire at a dead proxy while the offline screen is
 * showing.
 */

export interface SourceResult<T> {
  data: T | undefined;
  isLoading: boolean;
  isFetching: boolean;
  isError: boolean;
  error: Error | null;
  dataUpdatedAt: number;
  origin: DataOrigin;
  refetch: () => void;
}

function useSource<T>(
  key: string,
  live: () => Promise<T>,
  options: { enabled?: boolean; refetchInterval?: number; staleMs?: number; ignoreConnection?: boolean } = {}
): SourceResult<T> {
  const connected = useAppStore((s) => s.isConnected);

  const canQuery = (options.ignoreConnection || connected) && (options.enabled ?? true);

  const query = useQuery<T>({
    queryKey: [key],
    queryFn: live,
    enabled: canQuery,
    refetchInterval: canQuery ? options.refetchInterval ?? false : false,
    staleTime: options.staleMs ?? 10_000,
    retry: 1,
  });

  return {
    data: query.data,
    isLoading: query.isLoading,
    isFetching: query.isFetching,
    isError: query.isError,
    error: (query.error as Error | null) ?? null,
    dataUpdatedAt: query.dataUpdatedAt,
    origin: 'live',
    refetch: () => {
      void query.refetch();
    },
  };
}

/* --- Connectivity -------------------------------------------------------- */

/** True when real backend calls are permitted. */
export function useBackendLive(): boolean {
  const isConnected = useAppStore((s) => s.isConnected);
  const health = useHealth();
  // A failed health check is definitive enough to stop uncertain mutations even
  // when the original bridge handshake succeeded. Safe, read-only features that
  // tolerate partial degradation keep working while the check is only degraded.
  return isConnected && !health.isError;
}

/* --- Runtime ------------------------------------------------------------- */

export function useHealth(bridgeReady?: boolean) {
  // The companion overlay mounts before its bridge bootstrap effect, while
  // the main window already passes explicit readiness. Both must wait until
  // configureBridge has replaced Vite's fixed-port fallback API base URL.
  const configured = useAppStore((s) => s.bridgeReady);
  return useSource('health', api.healthService.check, {
    refetchInterval: 20_000,
    ignoreConnection: true,
    // Keep polling after later disconnects; only gate bridge discovery/retry.
    enabled: bridgeReady ?? configured,
  });
}

export function useVersion() {
  return useSource('version', api.healthService.version, { staleMs: 300_000 });
}

/* --- Tasks --------------------------------------------------------------- */

export function useTasks() {
  return useSource<TaskListShape>('tasks', api.taskService.list, {
    refetchInterval: 5_000,
  });
}

type TaskListShape = Awaited<ReturnType<typeof api.taskService.list>>;

/** Read-only, task-scoped recovery preflight. Never an approval or resume action. */
export function useTaskResumePreflight(taskId: string | null) {
  return useSource<import('../types').TaskResumePreflight>(
    `task-resume-preflight:${taskId ?? 'none'}`,
    () => api.taskService.resumePreflight(taskId as string),
    { enabled: Boolean(taskId), staleMs: 0, refetchInterval: 7_000 },
  );
}

export function useTaskEvents(taskId: string | null) {
  return useSource(
    `task-events:${taskId ?? 'none'}`,
    () => (taskId ? api.taskService.events(taskId) : Promise.resolve([])),
    { enabled: Boolean(taskId), refetchInterval: 3_000 }
  );
}

/* --- Objectives / goals -------------------------------------------------- */

export function useObjectives() {
  return useSource('objectives', api.objectiveService.list, {
    staleMs: 15_000,
  });
}

export function useGoals() {
  return useSource<GoalSummary[]>('goals', api.goalService.list, {
    staleMs: 15_000,
    refetchInterval: 20_000,
  });
}

/**
 * One goal with its active plan version and deterministic progress.
 *
 * `enabled` is driven by the caller, so no request is made until a goal is
 * actually opened. `useSource` additionally gates on `isConnected`, so nothing
 * fires at a dead proxy while the offline screen is showing.
 */
export function useGoalDetail(goalId: string | null) {
  return useSource<GoalDetail>(
    `goal-detail:${goalId ?? 'none'}`,
    () => api.goalService.detail(goalId as string),
    { enabled: Boolean(goalId), staleMs: 10_000, refetchInterval: 15_000 }
  );
}

/** Preview only. Reading eligible work must never persist a readiness change. */
export function useGoalNextWork(goalId: string | null) {
  return useSource<GoalWorkPreview>(
    `goal-next-work:${goalId ?? 'none'}`,
    () => api.goalService.nextWork(goalId as string),
    { enabled: Boolean(goalId), staleMs: 0, refetchInterval: 12_000 },
  );
}

/** Plan version history for one goal. Replanning is audited, so old versions persist. */
export function useGoalPlans(goalId: string | null) {
  return useSource<GoalPlanVersion[]>(
    `goal-plans:${goalId ?? 'none'}`,
    () => api.goalService.plans(goalId as string),
    { enabled: Boolean(goalId), staleMs: 60_000 }
  );
}

/* --- Agent --------------------------------------------------------------- */

export function useAgent() {
  return useSource('agent', api.agentService.identity, { refetchInterval: 15_000 });
}

export function useAgentHealth() {
  return useSource('agent-health', () => api.agentService.health(false), {
    refetchInterval: 30_000,
  });
}

export function useAgentActivity() {
  return useSource('agent-activity', () => api.agentService.activity(20), {
    refetchInterval: 15_000,
  });
}

export function useBackgroundPolicy() {
  return useSource('agent-background', api.agentService.backgroundPolicy, {
    refetchInterval: 30_000,
  });
}

export function useDevices() {
  return useSource('devices', api.agentService.devices, {
    refetchInterval: 30_000,
  });
}

export function useAgentRegistry() {
  return useSource('agents', api.agentRegistryService.list, {
    staleMs: 30_000,
  });
}

export function useDelegations() {
  return useSource(
    'delegations',
    () => api.agentRegistryService.delegations(50),
    { staleMs: 15_000 }
  );
}

/* --- Capabilities, tools, skills ----------------------------------------- */

export function useCapabilities() {
  return useSource(
    'capabilities',
    () => api.capabilityService.list().then((r) => r.capabilities),
    { staleMs: 30_000 }
  );
}

export function useCapabilityConsistency() {
  return useSource(
    'capabilities-consistency',
    () => api.capabilityService.list().then((r) => r.consistency_issues),
    { staleMs: 60_000 }
  );
}

export function useTools() {
  return useSource('tools', api.toolService.list, { staleMs: 60_000 });
}

export function useSkills() {
  return useSource('skills', api.skillService.list, { staleMs: 30_000 });
}

export function useSkillProposals() {
  return useSource('skill-proposals', api.skillService.proposals, {
    staleMs: 30_000,
  });
}

export function useToolProposals() {
  return useSource('tool-proposals', api.toolProposalService.list, {
    staleMs: 30_000,
  });
}

/* --- Memory, settings, models -------------------------------------------- */

export function useMemoryReport() {
  return useSource('memory-report', api.memoryService.report, {
    staleMs: 30_000,
  });
}

export function useMemoryEdges() {
  return useSource('memory-edges', api.memoryService.edges, {
    staleMs: 30_000,
  });
}

export function useMemoryItems(params?: {
  query?: string;
  kind?: string;
  kinds?: string[];
  limit?: number;
  offset?: number;
}) {
  const key = `memory-items:${JSON.stringify(params ?? {})}`;
  return useSource<MemoryItemsPage>(
    key,
    () => api.memoryService.items(params ?? {}),
    { staleMs: 30_000 }
  );
}

export function useAppSettings() {
  return useSource('settings', api.settingsService.get, { staleMs: 30_000 });
}

export function useSecrets() {
  return useSource('secrets', api.secretService.status, {
    staleMs: 60_000,
  });
}

export function useModels() {
  return useSource('models', () => api.modelService.registry(), {
    staleMs: 120_000,
  });
}

export function useRetentionReceipts() {
  return useSource('retention-receipts', api.retentionService.receipts, {
    staleMs: 60_000,
  });
}

export function useVoiceAssets() {
  return useSource('voice-assets', api.voiceService.assets, {
    staleMs: 300_000,
  });
}

/* --- Mutating helpers (live only) ---------------------------------------- */

export function useActions() {
  const live = useBackendLive();
  const notify = useAppStore((s) => s.notify);

  return useMemo(
    () => ({
      live,

      async interpretMessage(request: string, ignorePending = false) {
        if (!live) throw new Error('Not connected to a Sythion backend.');
        return api.taskService.interpret(request, ignorePending);
      },
      async assistantTurn(text: string, clientRequestId?: string): Promise<import('../types').AssistantTurn> {
        if (!live) throw new Error('Not connected to a Sythion backend.');
        return api.assistantService.turn(text, clientRequestId);
      },
      async createTask(request: string, interactionHint?: 'conversation' | 'task', idempotencyKey?: string) {
        if (!live) throw new Error('Not connected to a Sythion backend.');
        return api.taskService.create(request, interactionHint, idempotencyKey);
      },
      async cancelTask(taskId: string) {
        if (!live) throw new Error('Not connected to a Sythion backend.');
        return api.taskService.cancel(taskId);
      },
      async resumeTask(taskId: string) {
        if (!live) throw new Error('Not connected to a Sythion backend.');
        return api.taskService.resume(taskId);
      },
      async replyToTask(taskId: string, answer: string, inferred = false, expectedVersion?: number) {
        if (!live) throw new Error('Not connected to a Sythion backend.');
        return api.taskService.reply(taskId, answer, inferred, expectedVersion);
      },
      async setBackground(enabled: boolean) {
        if (!live) throw new Error('Not connected to a Sythion backend.');
        return api.agentService.setBackgroundPolicy(enabled);
      },
      async pauseAgent() {
        if (!live) throw new Error('Not connected to a Sythion backend.');
        return api.agentService.pause();
      },
      async resumeAgent() {
        if (!live) throw new Error('Not connected to a Sythion backend.');
        return api.agentService.resume();
      },
      async wakeAgent() {
        if (!live) throw new Error('Not connected to a Sythion backend.');
        return api.agentService.wake();
      },
      async setDeviceTrust(
        deviceId: string,
        trustLevel: 'observed' | 'trusted' | 'privileged',
      ) {
        if (!live) throw new Error('Not connected to a Sythion backend.');
        return api.agentService.setDeviceTrust(deviceId, trustLevel);
      },
      async approveSkillProposal(id: string | number) {
        if (!live) throw new Error('Not connected to a Sythion backend.');
        return api.skillService.approveProposal(id);
      },
      async rejectSkillProposal(id: string | number) {
        if (!live) throw new Error('Not connected to a Sythion backend.');
        return api.skillService.rejectProposal(id);
      },
      async approveToolProposal(id: number) {
        if (!live) throw new Error('Not connected to a Sythion backend.');
        return api.toolProposalService.approve(id);
      },
      async rejectToolProposal(id: number) {
        if (!live) throw new Error('Not connected to a Sythion backend.');
        return api.toolProposalService.reject(id);
      },
      async reinforceEdge(id: number) {
        if (!live) throw new Error('Not connected to a Sythion backend.');
        return api.memoryService.reinforceEdge(id);
      },
      async removeEdge(id: number) {
        if (!live) throw new Error('Not connected to a Sythion backend.');
        return api.memoryService.removeEdge(id);
      },
      async updateSettings(updates: Record<string, unknown>) {
        if (!live) throw new Error('Not connected to a Sythion backend.');
        return api.settingsService.update(updates);
      },
      notify,
    }),
    [live, notify]
  );
}

export type Actions = ReturnType<typeof useActions>;

export type { UseQueryResult };
