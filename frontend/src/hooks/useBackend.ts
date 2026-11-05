import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import { useMemo } from 'react';
import { useAppStore } from '../store/appStore';
import type { DataOrigin, GoalDetail, GoalPlanVersion, GoalSummary, MemoryItemsPage } from '../types';
import * as api from '../services/api';
import * as demo from '../data/demo';

/* Data access layer.
 *
 * Every screen reads through here. In `live` mode it calls the real backend
 * contract defined in services/api.ts. In `preview` mode it serves the
 * demonstration dataset in data/demo.ts and reports origin='demo' so callers
 * can label it. No screen ever mixes the two for the same panel.
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

function usePreview() {
  return useAppStore((s) => s.dataMode === 'preview');
}

function useSource<T>(
  key: string,
  live: () => Promise<T>,
  demoValue: T,
  options: { enabled?: boolean; refetchInterval?: number; staleMs?: number; ignoreConnection?: boolean } = {}
): SourceResult<T> {
  const preview = usePreview();
  const connected = useAppStore((s) => s.isConnected);

  // A query only runs once the bridge handshake has succeeded. Without this,
  // every panel would fire at a dead proxy while the offline screen is showing.
  const canQuery = (options.ignoreConnection || connected) && !preview && (options.enabled ?? true);

  const query = useQuery<T>({
    queryKey: [key],
    queryFn: live,
    enabled: canQuery,
    refetchInterval: canQuery ? options.refetchInterval ?? false : false,
    staleTime: options.staleMs ?? 10_000,
    retry: 1,
  });

  return {
    data: preview ? demoValue : query.data,
    isLoading: preview ? false : query.isLoading,
    isFetching: query.isFetching,
    isError: preview ? false : query.isError,
    error: (query.error as Error | null) ?? null,
    dataUpdatedAt: preview ? Date.now() : query.dataUpdatedAt,
    origin: preview ? 'demo' : 'live',
    refetch: () => {
      void query.refetch();
    },
  };
}

/* --- Connectivity -------------------------------------------------------- */

/** True when real backend calls are permitted. */
export function useBackendLive(): boolean {
  const dataMode = useAppStore((s) => s.dataMode);
  const isConnected = useAppStore((s) => s.isConnected);
  const health = useHealth();
  // A failed health check is definitive enough to stop uncertain mutations even
  // when the original bridge handshake succeeded. Safe, read-only features that
  // tolerate partial degradation keep working while the check is only degraded.
  return dataMode === 'live' && isConnected && !health.isError;
}

export function useDataMode() {
  return useAppStore((s) => s.dataMode);
}

/* --- Runtime ------------------------------------------------------------- */

export function useHealth() {
  // The health check keeps running even after the handshake gate drops so that
  // a lost backend can be observed and a recovered one can flip the gate back.
  return useSource('health', api.healthService.check, demo.demoHealth, {
    refetchInterval: 20_000,
    ignoreConnection: true,
  });
}

export function useVersion() {
  return useSource('version', api.healthService.version, demo.demoVersion, { staleMs: 300_000 });
}

/* --- Tasks --------------------------------------------------------------- */

export function useTasks() {
  return useSource<TaskListShape>('tasks', api.taskService.list, demo.demoTasks, {
    refetchInterval: 5_000,
  });
}

type TaskListShape = Awaited<ReturnType<typeof api.taskService.list>>;

export function useTaskEvents(taskId: string | null) {
  return useSource(
    `task-events:${taskId ?? 'none'}`,
    () => (taskId ? api.taskService.events(taskId) : Promise.resolve([])),
    taskId ? demo.demoTaskEvents[taskId] ?? [] : [],
    { enabled: Boolean(taskId), refetchInterval: 3_000 }
  );
}

/* --- Objectives / goals -------------------------------------------------- */

export function useObjectives() {
  return useSource('objectives', api.objectiveService.list, demo.demoObjectives, {
    staleMs: 15_000,
  });
}

export function useGoals() {
  return useSource<GoalSummary[]>('goals', api.goalService.list, demo.demoGoals, {
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
    goalId ? demo.demoGoalDetail(goalId) : demo.demoGoalDetail(null),
    { enabled: Boolean(goalId), staleMs: 10_000, refetchInterval: 15_000 }
  );
}

/** Plan version history for one goal. Replanning is audited, so old versions persist. */
export function useGoalPlans(goalId: string | null) {
  return useSource<GoalPlanVersion[]>(
    `goal-plans:${goalId ?? 'none'}`,
    () => api.goalService.plans(goalId as string),
    goalId ? demo.demoGoalPlans[goalId] ?? [] : [],
    { enabled: Boolean(goalId), staleMs: 60_000 }
  );
}

/* --- Agent --------------------------------------------------------------- */

export function useAgent() {
  return useSource('agent', api.agentService.identity, demo.demoAgent, { refetchInterval: 15_000 });
}

export function useAgentHealth() {
  return useSource('agent-health', () => api.agentService.health(false), demo.demoAgentHealth, {
    refetchInterval: 30_000,
  });
}

export function useAgentActivity() {
  return useSource('agent-activity', () => api.agentService.activity(20), demo.demoActivity, {
    refetchInterval: 15_000,
  });
}

export function useBackgroundPolicy() {
  return useSource('agent-background', api.agentService.backgroundPolicy, demo.demoBackground, {
    refetchInterval: 30_000,
  });
}

export function useAgentRegistry() {
  return useSource('agents', api.agentRegistryService.list, demo.demoAgentRegistry, {
    staleMs: 30_000,
  });
}

export function useDelegations() {
  return useSource(
    'delegations',
    () => api.agentRegistryService.delegations(50),
    demo.demoDelegations,
    { staleMs: 15_000 }
  );
}

/* --- Capabilities, tools, skills ----------------------------------------- */

export function useCapabilities() {
  return useSource(
    'capabilities',
    () => api.capabilityService.list().then((r) => r.capabilities),
    demo.demoCapabilities,
    { staleMs: 30_000 }
  );
}

export function useCapabilityConsistency() {
  return useSource(
    'capabilities-consistency',
    () => api.capabilityService.list().then((r) => r.consistency_issues),
    ['browser.read_page references a tool that is not registered'] as string[],
    { staleMs: 60_000 }
  );
}

export function useTools() {
  return useSource('tools', api.toolService.list, demo.demoTools, { staleMs: 60_000 });
}

export function useSkills() {
  return useSource('skills', api.skillService.list, demo.demoSkills, { staleMs: 30_000 });
}

export function useSkillProposals() {
  return useSource('skill-proposals', api.skillService.proposals, demo.demoSkillProposals, {
    staleMs: 30_000,
  });
}

export function useToolProposals() {
  return useSource('tool-proposals', api.toolProposalService.list, demo.demoToolProposals, {
    staleMs: 30_000,
  });
}

/* --- Memory, settings, models -------------------------------------------- */

export function useMemoryReport() {
  return useSource('memory-report', api.memoryService.report, demo.demoMemoryReport, {
    staleMs: 30_000,
  });
}

export function useMemoryEdges() {
  return useSource('memory-edges', api.memoryService.edges, demo.demoMemoryEdges, {
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
  const demoValue: MemoryItemsPage = {
    items: demo.demoMemoryItems.map((m) => ({
      id: Number(String(m.id).replace('mem-', '')) || 0,
      kind: String(m.kind).toUpperCase(),
      scope: 'USER',
      key: m.key,
      value: m.value,
      confidence: m.confidence,
      verification_status: (m.confidence ?? 0) >= 0.95 ? 'VERIFIED_USER' : 'UNVERIFIED',
      source_type: 'USER_EXPLICIT',
      observed_at: m.updated,
      updated_at: m.updated,
      score: 0,
    })),
    has_more: false,
  };
  return useSource<MemoryItemsPage>(
    key,
    () => api.memoryService.items(params ?? {}),
    demoValue,
    { staleMs: 30_000 }
  );
}

export function useAppSettings() {
  return useSource('settings', api.settingsService.get, demo.demoSettings, { staleMs: 30_000 });
}

export function useSecrets() {
  return useSource('secrets', api.secretService.status, { configured: demo.demoSecrets }, {
    staleMs: 60_000,
  });
}

export function useModels() {
  return useSource('models', () => api.modelService.registry(), demo.demoModels, {
    staleMs: 120_000,
  });
}

export function useRetentionReceipts() {
  return useSource('retention-receipts', api.retentionService.receipts, demo.demoPurgeReceipts, {
    staleMs: 60_000,
  });
}

export function useVoiceAssets() {
  return useSource('voice-assets', api.voiceService.assets, demo.demoVoiceAssets, {
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
      async createTask(request: string, interactionHint?: 'conversation' | 'task') {
        if (!live) throw new Error('Not connected to a Sythion backend.');
        return api.taskService.create(request, interactionHint);
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
