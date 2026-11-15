import { useEffect, useMemo, useState } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { SythionMark } from './components/icons';
import { Sidebar } from './components/Sidebar';
import { TopBar } from './components/TopBar';
import { Button, Notice, Panel } from './components/ui';
import { AssistantScreen } from './screens/Assistant';
import { CloudConversationOnlyScreen } from './screens/CloudConversationOnly';
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
import { useAppInit } from './hooks/useAppInit';
import { useAppStore } from './store/appStore';
import { assessRuntimeHealth } from './lib/runtimeHealth';
import { setupService } from './services/api';
import { canStartChat } from './lib/setupReadiness';
import { readProviderLane, saveProviderLane, cloudShellRoute } from './lib/providerLane';
import { useNavStore } from './store/navStore';
import { routeById, type RouteId } from './lib/routes';
import {
  useAgent,
  useAgentHealth,
  useHealth,
  useSkillProposals,
  useTasks,
  useToolProposals,
  useVersion,
} from './hooks/useBackend';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

function ScreenFor({
  route, cloudAnswerOnly, onCloudModelReady, onOllamaModelReady, onOpenModels,
}: {
  route: RouteId;
  cloudAnswerOnly: boolean;
  onCloudModelReady: () => void;
  onOllamaModelReady: () => void;
  onOpenModels: () => void;
}) {
  // No cloud model is routed through AssistantScreen (the task-capable
  // Ollama assistant). Cloud sends only to /cloud-conversation/turn.
  if (cloudAnswerOnly) {
    if (route === 'settings') {
      return <SettingsScreen cloudAnswerOnly onCloudReady={onCloudModelReady} onOllamaReady={onOllamaModelReady} />;
    }
    return <CloudConversationOnlyScreen
      key="cloud-answer-only"
      embedded requireToolEvidence
      onExit={onOpenModels}
    />;
  }

  switch (route) {
    case 'assistant':
      return <AssistantScreen />;
    case 'cloud':
      // Retained for old deep links; configuration now belongs in Settings.
      return <SettingsScreen onCloudReady={onCloudModelReady} onOllamaReady={onOllamaModelReady} />;
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
      return <SettingsScreen onCloudReady={onCloudModelReady} onOllamaReady={onOllamaModelReady} />;
    case 'overview':
    default:
      // Operations is the Control Center landing view.
      return <OperationsScreen />;
  }
}

function AppContent() {
  const { isInitialized, isBridgeReady, retry } = useAppInit();
  const isConnected = useAppStore((s) => s.isConnected);
  const error = useAppStore((s) => s.error);
  const setConnectedStore = useAppStore((s) => s.setConnected);
  const setErrorStore = useAppStore((s) => s.setError);
  const notify = useAppStore((s) => s.notify);

  const [setupNeeded, setSetupNeeded] = useState<boolean | null>(null);
  // Persist only the owner's UI choice. This is never permission evidence:
  // cloud conversations check the current backend/key/model each mount/send.
  const [cloudConversationOnly, setCloudConversationOnly] = useState(
    () => readProviderLane() === 'cloud-answer-only',
  );
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
    if (cloudConversationOnly ||
        !shouldActivateCompanion({ isConnected, setupNeeded, textFirstMode: false })) return;
    void activateCompanion();
  }, [isConnected, setupNeeded, cloudConversationOnly]);

  const route = useNavStore((s) => s.route);
  const setRoute = useNavStore((s) => s.setRoute);
  const selectCloud = () => {
    saveProviderLane('cloud-answer-only');
    setCloudConversationOnly(true);
    setRoute('assistant');
  };
  const selectOllama = () => {
    // Settings can switch back only after the existing Ollama readiness
    // requirement has been re-established, never merely by selecting a name.
    void setupService.readiness().then((ready) => {
      if (!canStartChat(ready)) {
        saveProviderLane('ollama');
        setCloudConversationOnly(false);
        setSetupNeeded(true);
        setRoute('assistant');
        return;
      }
      saveProviderLane('ollama');
      setCloudConversationOnly(false);
      setSetupNeeded(false);
      setRoute('assistant');
    }).catch(() => {
      // Unknown readiness must never unlock the task-capable Ollama UI.
      setSetupNeeded(true);
      saveProviderLane('ollama');
      setCloudConversationOnly(false);
      setRoute('assistant');
    });
  };
  // An existing hash may target tasks/automation. Cloud lane never renders
  // an executable route even if a hash change bypasses sidebar buttons.
  const shellRoute = cloudConversationOnly ? cloudShellRoute(route) :
    route === 'cloud' ? 'settings' : route;

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

  const [seenTaskStates, setSeenTaskStates] = useState<Record<string, string>>({});

  // Raise an in-app notification whenever a task enters an interesting
  // terminal state. Derived from polled backend state, not fabricated.
  useEffect(() => {
    if (cloudConversationOnly) return;
    const interesting = new Set(['failed', 'waiting_for_user', 'interrupted']);
    const next = { ...seenTaskStates };
    let changed = false;
    for (const t of tasks.data ?? []) {
      const prev = seenTaskStates[t.id];
      if (prev !== t.state) {
        next[t.id] = t.state;
        changed = true;
      }
      if (prev !== t.state && interesting.has(t.state)) {
        notify({
          tone: t.state === 'failed' ? 'danger' : 'warn',
          title: `Task ${t.state === 'failed' ? 'failed' : 'needs your input'}`,
          body: t.terminal_reason ?? t.pending_question ?? t.normalized_goal ?? t.original_request,
          route: 'assistant',
        });
      }
    }
    if (changed) {
      setSeenTaskStates(next);
    }
  }, [tasks.data, notify, seenTaskStates, cloudConversationOnly]);

  const attentionCount = useMemo(() => {
    const skillPending = (skillProposals.data ?? []).filter((p) => p.status === 'proposed' || p.status === 'pending_review').length;
    const toolPending = (toolProposals.data ?? []).filter((p) => p.status === 'pending_review').length;
    const held = (tasks.data ?? []).filter((t) => t.state === 'interrupted').length;
    return skillPending + toolPending + held;
  }, [skillProposals.data, toolProposals.data, tasks.data]);

  if (!isInitialized) {
    return (
      <div className="shell" style={singleScreenShellStyle()}>
        <div className="shell__main" style={singleScreenContentStyle({ placeItems: 'center' })}>
          <div className="stack" style={{ alignItems: 'center', gap: 14, maxWidth: 340 }}>
            <SythionMark size={40} />
            <div style={{ textAlign: 'center' }}>
              <div className="t-title">Connecting to Sythion</div>
              <div className="t-muted">Resolving the local runtime bridge…</div>
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

  if (isConnected && setupNeeded && !cloudConversationOnly) {
    return (
      <SetupScreen
        onComplete={(tf) => {
          localStorage.setItem('sythion.textFirst', tf ? '1' : '0');
          saveProviderLane('ollama');
          setCloudConversationOnly(false);
          setRoute('assistant');
          setSetupNeeded(false);
        }}
        onCloudConversationOnly={selectCloud}
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
                  Sythion backend unreachable
                </h1>
                <p className="t-muted" style={{ margin: '3px 0 0' }}>
                  Start the local backend to see live state.
                </p>              </div>
            </div>

            <Notice tone="danger" icon="alert">
              <span>{error || 'No response from the Sythion sidecar.'}</span>
            </Notice>

            <Panel title="What you can do" icon="info">
              <div className="stack stack--tight">
                <span className="t-muted">
                  <strong className="t-body" style={{ color: 'var(--text-0)' }}>
                    Start the backend
                  </strong>{' '}
                  — run <span className="t-mono">start_sythion.cmd</span> from the project root, or{' '}
                  <span className="t-mono">python -m uvicorn backend.main:app</span>.
                </span>
              </div>
              <div className="row" style={{ gap: 8, marginTop: 14 }}>
                <Button variant="primary" icon="refresh" onClick={() => void retry()}>
                  Retry connection
                </Button>
              </div>
            </Panel>
          </div>
        </div>
      </div>
    );
  }

  const def = routeById(shellRoute);

  return (
    <div className="shell">
      <Sidebar
        route={shellRoute}
        onNavigate={setRoute}
        cloudAnswerOnly={cloudConversationOnly}
        pendingApprovals={cloudConversationOnly ? 0 : attentionCount}
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
        cloudAnswerOnly={cloudConversationOnly}
        connected={isConnected}
        runtimeState={runtimeHealth.state}
        attentionCount={cloudConversationOnly ? 0 : attentionCount}
        onNavigate={setRoute}
      />

      <main className="shell__main">
        <ScreenFor
          route={shellRoute}
          cloudAnswerOnly={cloudConversationOnly}
          onCloudModelReady={selectCloud}
          onOllamaModelReady={selectOllama}
          onOpenModels={() => setRoute('settings')}
        />
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
