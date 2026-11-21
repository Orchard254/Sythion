import { useEffect, useMemo, useState } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { SythionMark } from './components/icons';
import { Sidebar } from './components/Sidebar';
import { TopBar } from './components/TopBar';
import { Notice, Panel } from './components/ui';
import { AssistantScreen } from './screens/Assistant';
import { AutomationsScreen } from './screens/Automations';
import { MemoryScreen } from './screens/Memory';
import { CompanionSurface } from './components/CompanionSurface';
import { GoalsScreen } from './screens/Goals';
import { isCompanionSurface } from './lib/surface';
import { shouldActivateCompanion } from './lib/companionActivation';
import { singleScreenContentStyle, singleScreenShellStyle } from './lib/shellLayout';
import { activateCompanion } from './services/companion';
import { OperationsScreen } from './screens/Operations';
import { SecurityScreen } from './screens/Security';
import { SettingsScreen } from './screens/Settings';
import { SetupScreen } from './screens/Setup';
import { SystemScreen } from './screens/SystemIntegrations';
import { PhoneRelayScreen } from './screens/PhoneRelay';
import { useAppInit } from './hooks/useAppInit';
import { useAppStore } from './store/appStore';
import { assessRuntimeHealth } from './lib/runtimeHealth';
import { setupService } from './services/api';
import { canStartChat } from './lib/setupReadiness';
import { useNavStore } from './store/navStore';
import { routeById, type RouteId } from './lib/routes';
import {
  useAgent,
  useAgentHealth,
  useHealth,
  usePendingApprovals,
  useSkillProposals,
  useTasks,
  useToolProposals,
  useUncertainRemoteActions,
  useVersion,
} from './hooks/useBackend';
import { countDecisionsDue } from './lib/decisionCounts';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

function ScreenFor({ route }: { route: RouteId }) {
  switch (route) {
    case 'assistant':
      return <AssistantScreen />;
    case 'cloud':
      // Legacy bookmarked preview routes open the real AI model settings.
      return <SettingsScreen />;
    case 'missions':
      // Canonical user-facing term is Goals. `missions` is retained as the route
      // id so existing deep links keep resolving (see ROUTE_ALIASES).
      return <GoalsScreen />;
    case 'memory':
      return <MemoryScreen />;
    case 'automations':
      return <AutomationsScreen />;
    case 'security':
      return <SecurityScreen />;
    case 'system':
      return <SystemScreen />;
    case 'settings':
      return <SettingsScreen />;
    case 'phone':
      return <PhoneRelayScreen />;
    case 'overview':
    default:
      // Operations is the Control Center landing view.
      return <OperationsScreen />;
  }
}

function AppContent() {
  const { isInitialized, isBridgeReady } = useAppInit();
  const isConnected = useAppStore((s) => s.isConnected);
  const error = useAppStore((s) => s.error);
  const setConnectedStore = useAppStore((s) => s.setConnected);
  const setErrorStore = useAppStore((s) => s.setError);
  const notify = useAppStore((s) => s.notify);

  const [setupNeeded, setSetupNeeded] = useState<boolean | null>(null);
  useEffect(() => {
    if (!isInitialized || !isConnected) return;
    setSetupNeeded(null);
    let cancelled = false;
    void setupService.readiness().then((r) => {
      if (!cancelled) setSetupNeeded(!canStartChat(r));
    }).catch(() => { if (!cancelled) setSetupNeeded(true); });
    return () => { cancelled = true; };
  }, [isInitialized, isConnected]);

  /* Companion overlay coordination.
   *
   * The overlay is an always-on-top window with no taskbar button. It is
   * created by the shell on request, and the request is withheld while the
   * first-run setup gate is active - otherwise it floats above the setup form
   * and hides the screen the user still has to complete.
   *
   * The decision is re-evaluated whenever the connection or the gate changes,
   * so the overlay also comes back on its own once the backend is reachable
   * again after a failed start. */
  useEffect(() => {
    if (!shouldActivateCompanion({ isConnected, setupNeeded, textFirstMode: false })) return;
    void activateCompanion();
  }, [isConnected, setupNeeded]);

  const route = useNavStore((s) => s.route);
  const setRoute = useNavStore((s) => s.setRoute);

  const health = useHealth(isBridgeReady);

  // Keep the sticky handshake gate honest: a failed health check means the
  // backend is no longer reachable, while a later successful one means it has
  // recovered. Partial degradation (a successful check reporting degraded)
  // does not drop the gate, so safe features stay available. The health query
  // itself keeps polling (ignoreConnection) to observe both directions.
  useEffect(() => {
    if (health.isError) {
      setConnectedStore(false);
      if (health.error?.message) setErrorStore(health.error.message);
    } else if (health.data !== undefined) {
      setConnectedStore(true);
      setErrorStore(null);
    }
  }, [health.isError, health.data, health.error, setConnectedStore, setErrorStore]);

  const runtimeHealth = useMemo(
    () =>
      assessRuntimeHealth({
        isConnected,
        isFetching: health.isFetching,
        isError: health.isError,
        hasData: health.data !== undefined,
        dataUpdatedAt: health.dataUpdatedAt,
        now: Date.now(),
        // Backend can answer (HTTP success) while still reporting a degraded
        // startup; liveness is unaffected, but it must not be called healthy.
        isDegraded:
          health.data?.status === 'degraded' ||
          health.data?.startup_sequence_status === 'degraded' ||
          health.data?.startup_sequence_status === 'failed' ||
          health.data?.startup_sequence_status === 'error' ||
          health.data?.startup_status === 'degraded' ||
          health.data?.startup_status === 'failed' ||
          health.data?.startup_status === 'error' ||
          health.data?.startup_overall_status === 'degraded' ||
          health.data?.startup_overall_status === 'failed' ||
          health.data?.startup_overall_status === 'error' ||
          health.data?.startup_aggregate_status === 'degraded' ||
          health.data?.startup_aggregate_status === 'failed' ||
          health.data?.startup_aggregate_status === 'error',
      }),
    [isConnected, health.isFetching, health.isError, health.data, health.dataUpdatedAt]
  );
  const agent = useAgent();
  const agentHealth = useAgentHealth();
  const version = useVersion();
  const tasks = useTasks();
  const skillProposals = useSkillProposals();
  const toolProposals = useToolProposals();
  const pendingApprovals = usePendingApprovals();
  const uncertainActions = useUncertainRemoteActions();

  // null = baseline not yet established. Historical terminal states that are
  // already present on first load are NOT new events and must not raise a
  // notification on every page load.
  const [seenTaskStates, setSeenTaskStates] = useState<Record<string, string> | null>(null);

  // Raise an in-app notification when a task ENTERS an interesting state
  // during this session. Derived from polled backend state, not fabricated.
  useEffect(() => {
    const interesting = new Set(['failed', 'waiting_for_user', 'interrupted']);
    const baseline = seenTaskStates ?? {};
    const next: Record<string, string> = { ...baseline };
    let changed = false;
    for (const t of tasks.data ?? []) {
      const prev = baseline[t.id];
      if (prev !== t.state) {
        next[t.id] = t.state;
        changed = true;
        // Only notify on a real transition seen live; the first poll seeds the
        // baseline and stays silent for pre-existing terminal states.
        if (seenTaskStates !== null && interesting.has(t.state)) {
          notify({
            tone: t.state === 'failed' ? 'danger' : 'warn',
            title: `Task ${t.state === 'failed' ? 'failed' : 'needs your input'}`,
            body: t.terminal_reason ?? t.pending_question ?? t.normalized_goal ?? t.original_request,
            route: 'assistant',
          });
        }
      }
    }
    if (changed || seenTaskStates === null) {
      setSeenTaskStates(next);
    }
  }, [tasks.data, notify, seenTaskStates]);

  // Every decision that genuinely waits on the owner, from all durable read
  // APIs: skill/tool proposals, exact permission proposals, tasks held or
  // waiting for an answer, and remote actions whose outcome is uncertain and
  // needs reconciliation. Uses the same inputs as Security's "awaiting you"
  // count so the two can never disagree.
  const attentionCount = useMemo(() => {
    const skillPending = (skillProposals.data ?? []).filter((p) => p.status === 'proposed' || p.status === 'pending_review').length;
    const toolPending = (toolProposals.data ?? []).filter((p) => p.status === 'pending_review').length;
    const permissionProposals = pendingApprovals.data?.proposals ?? [];
    const uncertain = uncertainActions.data?.actions ?? [];
    const awaiting = (tasks.data ?? []).filter(
      (t) => t.state === 'waiting_for_user' || t.state === 'interrupted'
    ).length;
    return countDecisionsDue({
      proposalApprovals: skillPending + toolPending,
      ownerApprovals: permissionProposals.length,
      needsReconciliation: uncertain.length,
      awaitingYou: awaiting,
    });
  }, [skillProposals.data, toolProposals.data, pendingApprovals.data, uncertainActions.data, tasks.data]);

  if (!isInitialized) {
    return (
      <div className="shell" style={singleScreenShellStyle()}>
        <div className="shell__main" style={singleScreenContentStyle({ placeItems: 'center' })}>
          <div className="stack" style={{ alignItems: 'center', gap: 14, maxWidth: 340 }}>
            <SythionMark size={40} />
            <div style={{ textAlign: 'center' }}>
              <div className="t-title">Checking connection…</div>
              <div className="t-muted">Sythion is resolving its local runtime bridge.</div>
              <div className="t-muted">It keeps checking on its own — no action is needed.</div>
            </div>
            <div className="bar bar--indeterminate" style={{ maxWidth: 200 }}>
              <div className="bar__fill" />
            </div>
          </div>
        </div>
      </div>
    );
  }

  if (isInitialized && isConnected && setupNeeded === null) {
    return (
      <div className="shell" style={singleScreenShellStyle()}>
        <div className="shell__main" style={singleScreenContentStyle({ placeItems: 'center' })}>
          <div className="stack" style={{ alignItems: 'center', gap: 14, maxWidth: 340 }}>
            <SythionMark size={40} />
            <div style={{ textAlign: 'center' }}>
              <div className="t-title">Checking setup</div>
              <div className="t-muted">Verifying first-run readiness…</div>
            </div>
            <div className="bar bar--indeterminate" style={{ maxWidth: 200 }}>
              <div className="bar__fill" />
            </div>
          </div>
        </div>
      </div>
    );
  }

  if (isConnected && setupNeeded) {
    return (
      <SetupScreen
        onComplete={(tf) => {
          localStorage.setItem('sythion.textFirst', tf ? '1' : '0');
          setRoute('assistant');
          setSetupNeeded(false);
        }}
      />
    );
  }

  if (!isConnected) {
    return (
      <div className="shell" style={singleScreenShellStyle()}>
        <div className="shell__main" style={singleScreenContentStyle({ overflowY: 'auto', padding: 40 })}>
          <div className="stack" style={{ maxWidth: 560, margin: '0 auto' }}>
            <div className="row" style={{ gap: 12 }}>
              <SythionMark size={34} />
              <div>
                <h1 className="screen-head__title" style={{ fontSize: 19 }}>
                  Sythion is still unreachable
                </h1>
                <p className="t-muted" style={{ margin: '3px 0 0' }}>
                  The local runtime did not respond during startup.
                </p>
              </div>
            </div>

            <Notice tone="danger" icon="alert">
              <span>{error || 'No response from the Sythion runtime.'}</span>
            </Notice>

            <Panel title="Connection status" icon="info">
              <div className="stack stack--tight">
                <span className="t-muted">
                  Sythion is checking quietly in the background and will reconnect on its own if the
                  runtime becomes available. Your settings and data are unchanged.
                </span>
                <span className="t-muted">
                  If it stays unreachable, the startup log records why:{' '}
                  <span className="t-mono">sidecar.err.log</span> or{' '}
                  <span className="t-mono">sythion-shell.log</span> in the Sythion profile.
                </span>
              </div>
            </Panel>
          </div>
        </div>
      </div>
    );
  }

  const def = routeById(route);

  return (
    <div className="shell">
      <Sidebar
        route={route}
        onNavigate={setRoute}
        pendingApprovals={attentionCount}
        agent={agent.data}
        overallHealth={agentHealth.data?.overall}
        connected={isConnected}
        runtimeState={runtimeHealth.state}
        runtimeAgeMs={runtimeHealth.ageMs}
        version={version.data?.version ?? health.data?.version}
        onOpenSettings={() => setRoute('settings')}
      />

      <TopBar
        route={def}
        connected={isConnected}
        runtimeState={runtimeHealth.state}
        attentionCount={attentionCount}
        onNavigate={setRoute}
      />

      <main className="shell__main">
        <ScreenFor route={route} />
      </main>
    </div>
  );
}

function App() {
  // The Companion overlay is the same bundle mounted without the Control Center
  // chrome. This branch must come BEFORE AppContent, because the boot and
  // offline shells paint an opaque full-screen background that a transparent
  // desktop window cannot tolerate.
  const companion = isCompanionSurface();

  return (
    <QueryClientProvider client={queryClient}>
      {companion ? (
        <>
          <CompanionBridge />
          <CompanionSurface />
        </>
      ) : (
        <AppContent />
      )}
    </QueryClientProvider>
  );
}

/**
 * Runs the same bridge bootstrap as the Control Center, but its result never
 * replaces the overlay. If the sidecar is unavailable the capsule reports an
 * honest error on submission rather than covering the desktop with a
 * connection screen.
 */
function CompanionBridge() {
  useAppInit();
  return null;
}

export default App;
