import { useState } from 'react';
import { Icon } from '../components/icons';
import {
  Badge,
  DataBadge,
  DemoTag,
  EmptyState,
  KV,
  LoadingBlock,
  Notice,
  Panel,
  Rail,
  ScreenHead,
  Segmented,
  Stat,
} from '../components/ui';
import {
  useAgentHealth,
  useAppSettings,
  useCapabilityConsistency,
  useHealth,
  useModels,
  useRetentionReceipts,
  useSecrets,
  useSkills,
  useTools,
  useVersion,
  useVoiceAssets,
} from '../hooks/useBackend';
import { useAppStore } from '../store/appStore';
import { demoIntegrations } from '../data/demo';
import { dateTime, humanize, relTime } from '../lib/format';

type Tab = 'runtime' | 'models' | 'integrations' | 'diagnostics';

const INTEGRATION_TONE: Record<string, 'ok' | 'warn' | 'danger' | 'idle' | 'info'> = {
  connected: 'ok',
  configured: 'info',
  degraded: 'warn',
  unavailable: 'danger',
};

export function SystemScreen() {
  const [tab, setTab] = useState<Tab>('runtime');
  const health = useHealth();
  const version = useVersion();
  const agentHealth = useAgentHealth();
  const settings = useAppSettings();
  const models = useModels();
  const secrets = useSecrets();
  const tools = useTools();
  const skills = useSkills();
  const consistency = useCapabilityConsistency();
  const receipts = useRetentionReceipts();
  const assets = useVoiceAssets();
  const dataMode = useAppStore((s) => s.dataMode);

  const checks = agentHealth.data?.checks ?? [];
  const receiptList = receipts.data ?? [];

  return (
    <div className="screen">
      <div className="screen__inner">
        <ScreenHead
          eyebrow="Control"
          title="System & Integrations"
          description="Runtime state, the models it can reach, what is connected, and the evidence behind every claim on this screen."
          actions={
            <>
              {dataMode === 'preview' ? <DemoTag /> : <DataBadge origin="live" />}
              <Segmented
                value={tab}
                onChange={setTab}
                ariaLabel="System view"
                options={[
                  { value: 'runtime', label: 'Runtime' },
                  { value: 'models', label: 'Models' },
                  { value: 'integrations', label: 'Integrations' },
                  { value: 'diagnostics', label: 'Diagnostics' },
                ]}
              />
            </>
          }
        />

        <div className="grid grid--4">
          <Stat
            label="Backend"
            icon="server"
            value={health.data && !health.isError ? 'Online' : 'Offline'}
            tone={health.data ? undefined : 'accent'}
            foot={health.data && !health.isError ? `v${health.data.version} · ${version.data?.runtime ?? 'uvicorn'}` : 'No response'}
          />
          <Stat
            label="Health"
            icon="activity"
            value={agentHealth.data ? humanize(agentHealth.data.overall) : '—'}
            foot={`${checks.length} checks · ${checks.filter((c) => c.status !== 'HEALTHY').length} not healthy`}
          />
          <Stat label="Tools" icon="layers" value={tools.data?.length ?? '—'} foot="Registered in the executor" />
          <Stat label="Skills" icon="automations" value={skills.data?.length ?? '—'} foot="Validated procedures" />
        </div>

        {tab === 'runtime' && (
          <div className="grid grid--2">
            <Panel
              title="Process"
              icon="server"
              actions={dataMode === 'preview' ? <DemoTag /> : <DataBadge origin="live" />}
            >
              {health.isLoading ? (
                <LoadingBlock rows={4} />
              ) : (
                <KV
                  items={[
                    { k: 'Status', v: health.isError ? 'unreachable' : (health.data?.status ?? 'unreachable') },
                    { k: 'Version', v: version.data ? `${version.data.version} (api ${version.data.api_version})` : health.data?.version ?? '—' },
                    { k: 'Platform', v: version.data?.platform ?? '—' },
                    { k: 'Runtime', v: version.data?.runtime ?? '—' },
                    { k: 'Bind address', v: settings.data ? `${settings.data.host}:${settings.data.port}` : '—' },
                    { k: 'Database', v: health.isError ? 'Unknown' : (health.data?.database_connected ? 'Connected' : 'Not connected') },
                    { k: 'Checked at', v: agentHealth.data ? relTime(agentHealth.data.checked_at) : '—' },
                  ]}
                />
              )}
            </Panel>

            <Panel
              title="Execution limits"
              icon="shield"
              actions={dataMode === 'preview' ? <DemoTag /> : <DataBadge origin="live" />}
            >
              {settings.isLoading ? (
                <LoadingBlock rows={3} />
              ) : (
                <>
                  <KV
                    items={[
                      { k: 'Max actions / task', v: String(settings.data?.max_actions_per_task ?? '—') },
                      { k: 'Max cloud requests / task', v: String(settings.data?.max_cloud_requests_per_task ?? '—') },
                      { k: 'Effective file-tool roots', v: settings.data?.effective_file_roots ? `${settings.data.effective_file_roots.length} reported` : 'not reported' },
                      { k: 'Retention', v: `${settings.data?.retention_days ?? '—'} days` },
                    ]}
                  />
                  <p className="t-faint" style={{ marginTop: 10 }}>
                    These are the only real bounds the backend applies today. They limit a single task, not the system —
                    which is why bounded autonomy is labelled proposed rather than offered.
                  </p>
                  {(settings.data?.effective_file_roots?.length ?? 0) > 0 && (
                    <div className="chips" style={{ marginTop: 8 }}>
                      {settings.data?.effective_file_roots?.map((r) => (
                        <span className="chip" key={r}>
                          <Icon name="file" size={11} style={{ marginRight: 5 }} />
                          {r}
                        </span>
                      ))}
                    </div>
                  )}
                </>
              )}
            </Panel>

            <Panel
              title="Voice pipeline"
              icon="microphone"
              actions={dataMode === 'preview' ? <DemoTag /> : <DataBadge origin="live" />}
            >
              {assets.isLoading ? (
                <LoadingBlock rows={3} />
              ) : (
                <>
                  <KV
                    items={[
                      { k: 'Speech to text', v: assets.data?.stt_installed ? `installed (${assets.data.stt_model})` : 'not installed' },
                      { k: 'Model checksum', v: assets.data?.stt_sha256_verified ? 'verified' : 'not verified' },
                      { k: 'Text to speech', v: `${assets.data?.tts_provider ?? '—'} · ${assets.data?.tts_voice || 'no voice'}` },
                      { k: 'Downloads', v: assets.data?.downloads_on_startup ? 'on startup' : 'never automatic' },
                      { k: 'Recording', v: settings.data?.voice_recording_enabled ? 'enabled' : 'disabled' },
                    ]}
                  />
                  {!assets.data?.stt_sha256_verified && assets.data?.stt_installed && (
                    <Notice tone="warn" icon="alert">
                      <span>
                        The speech model is present but its checksum has not been verified. Sythion will not download it
                        for you.
                      </span>
                    </Notice>
                  )}
                </>
              )}
            </Panel>

            <Panel
              title="Health checks"
              icon="activity"
              actions={dataMode === 'preview' ? <DemoTag /> : <DataBadge origin="live" />}
              flush
            >
              {agentHealth.isLoading ? (
                <div className="panel__body">
                  <LoadingBlock rows={4} />
                </div>
              ) : (
                <div className="list">
                  {checks.map((c) => (
                    <div className="list__row" key={c.name}>
                      <Rail
                        tone={c.status === 'HEALTHY' ? 'ok' : c.status === 'DEGRADED' ? 'warn' : c.status === 'UNAVAILABLE' ? 'danger' : 'idle'}
                      />
                      <div className="list__main">
                        <span className="t-body" style={{ color: 'var(--text-0)' }}>
                          {humanize(c.name)}
                        </span>
                        {Object.keys(c.evidence ?? {}).length > 0 && (
                          <span className="t-mono t-clamp-2">
                            {JSON.stringify(c.evidence).slice(0, 160)}
                          </span>
                        )}
                      </div>
                      <div className="list__trail">
                        <Badge
                          tone={
                            c.status === 'HEALTHY'
                              ? 'ok'
                              : c.status === 'DEGRADED'
                                ? 'warn'
                                : c.status === 'UNAVAILABLE'
                                  ? 'danger'
                                  : 'idle'
                          }
                        >
                          {c.status}
                        </Badge>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </Panel>
          </div>
        )}

        {tab === 'models' && (
          <div className="grid grid--2">
            <Panel
              title="Model registry"
              icon="cpu"
              actions={dataMode === 'preview' ? <DemoTag /> : <DataBadge origin="live" />}
              flush
            >
              {models.isLoading ? (
                <div className="panel__body">
                  <LoadingBlock rows={3} />
                </div>
              ) : (models.data ?? []).length === 0 ? (
                <EmptyState
                  icon="cpu"
                  title="No models registered"
                  description="The registry is empty. Select a model in Settings once a provider is configured."
                />
              ) : (
                <div className="list">
                  {(models.data ?? []).map((m, i) => {
                    const id = String(m.id ?? m.model ?? m.name ?? `model-${i}`);
                    const selected = health.data?.selected_model === id || settings.data?.selected_model === id;
                    return (
                      <div className="list__row" key={id}>
                        <Rail tone={selected ? 'accent' : 'idle'} />
                        <div className="list__main">
                          <span className="t-mono t-title" style={{ fontSize: 12 }}>
                            {id}
                          </span>
                          <span className="list__meta">
                            {m.role !== undefined && <span>role: {String(m.role)}</span>}
                            {m.provider !== undefined && <span>· {String(m.provider)}</span>}
                            {selected && <Badge tone="accent">selected</Badge>}
                          </span>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
              <div className="panel__foot">
                <span className="t-faint">
                  The registry reports intent and verification status. It does not report token spend — Sythion has no
                  cost accounting endpoint, so no figures are shown here.
                </span>
              </div>
            </Panel>

            <Panel
              title="Credentials"
              icon="key"
              actions={dataMode === 'preview' ? <DemoTag /> : <DataBadge origin="live" />}
            >
              <KV
                items={[
                  {
                    k: 'Configured',
                    v:
                      (secrets.data?.configured ?? []).length > 0
                        ? `${secrets.data?.configured.length} secret(s) stored`
                        : 'none configured',
                  },
                  { k: 'Provider', v: health.data?.cloud_configured ? 'configured' : 'not configured' },
                  { k: 'Selected model', v: health.data?.selected_model || settings.data?.selected_model || '—' },
                  { k: 'Base URL', v: settings.data?.ollama_cloud_base_url ?? '—' },
                ]}
              />
              <Notice tone="accent" icon="lock">
                <span>
                  Secret values are write-only. The API returns only which names are configured, never their contents,
                  and this interface never displays or stores a credential.
                </span>
              </Notice>
            </Panel>
          </div>
        )}

        {tab === 'integrations' && (
          <>
            <Notice tone="warn" icon="alert">
              <span>
                <strong>The integration list below is a demonstration</strong> of the intended surface. The runtime,
                database and model rows reflect real health data; external services genuinely do not exist in this
                backend, which the capability registry independently confirms.
              </span>
            </Notice>

            <div className="grid grid--main">
              <Panel title="Integration status" icon="plug" flush actions={<DemoTag />}>
                <div className="list">
                  {demoIntegrations.map((i) => (
                    <div className="list__row" key={i.id}>
                      <Rail tone={INTEGRATION_TONE[i.state]} />
                      <div className="list__main">
                        <span className="t-title" style={{ fontSize: 12.5 }}>
                          {i.name}
                        </span>
                        <span className="list__meta">
                          <Badge tone="plain">{i.kind}</Badge>
                          <span>· {i.detail}</span>
                        </span>
                      </div>
                      <div className="list__trail">
                        <div style={{ textAlign: 'right' }}>
                          <Badge tone={INTEGRATION_TONE[i.state]}>{i.state}</Badge>
                          <div className="t-faint" style={{ marginTop: 3 }}>
                            checked {i.lastCheck}
                          </div>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </Panel>

              <Panel
                title="Capability consistency"
                icon="shield"
                actions={dataMode === 'preview' ? <DemoTag /> : <DataBadge origin="live" />}
              >
                {consistency.isLoading ? (
                  <LoadingBlock rows={3} />
                ) : (consistency.data ?? []).length === 0 ? (
                  <EmptyState
                    icon="check"
                    title="Registry is consistent"
                    description="Every declared capability resolves to a registered tool."
                  />
                ) : (
                  <div className="stack stack--tight">
                    {(consistency.data ?? []).map((issue, i) => (
                      <span key={i} className="row t-warn" style={{ gap: 7, alignItems: 'flex-start' }}>
                        <Icon name="alert" size={12} style={{ flex: '0 0 auto', marginTop: 2 }} />
                        <span>{issue}</span>
                      </span>
                    ))}
                  </div>
                )}
                <p className="t-faint" style={{ marginTop: 10 }}>
                  Consistency is reported, never auto-repaired. A capability whose tools are missing resolves to
                  UNAVAILABLE regardless of autonomy mode.
                </p>
              </Panel>
            </div>
          </>
        )}

        {tab === 'diagnostics' && (
          <div className="grid grid--2">
            <Panel
              title="Retention purge receipts"
              icon="trash"
              flush
              actions={dataMode === 'preview' ? <DemoTag /> : <DataBadge origin="live" />}
            >
              {receipts.isLoading ? (
                <div className="panel__body">
                  <LoadingBlock rows={3} />
                </div>
              ) : receiptList.length === 0 ? (
                <EmptyState
                  icon="trash"
                  title="Nothing purged"
                  description="Receipts appear here after an explicitly requested retention purge."
                />
              ) : (
                <div className="list">
                  {receiptList.map((r) => (
                    <div className="list__row" key={`${r.task_id}-${r.purged_at}`}>
                      <Rail tone="idle" />
                      <div className="list__main">
                        <span className="t-mono t-truncate" style={{ color: 'var(--text-0)' }}>
                          {r.task_id}
                        </span>
                        <span className="list__meta">
                          <span>{r.reason}</span>
                          <span>· {r.event_count} events</span>
                        </span>
                      </div>
                      <div className="list__trail t-faint">{dateTime(r.purged_at)}</div>
                    </div>
                  ))}
                </div>
              )}
              <div className="panel__foot">
                <span className="t-faint">Receipts carry identity and timing only — never message content.</span>
              </div>
            </Panel>

            <Panel
              title="Registered tools"
              icon="layers"
              flush
              actions={
                <>
                  {dataMode === 'preview' ? <DemoTag /> : <DataBadge origin="live" />}
                  <span className="t-faint">{tools.data?.length ?? 0}</span>
                </>
              }
            >
              {tools.isLoading ? (
                <div className="panel__body">
                  <LoadingBlock rows={5} />
                </div>
              ) : (tools.data ?? []).length === 0 ? (
                <EmptyState icon="layers" title="No tools registered" />
              ) : (
                <div className="list" style={{ maxHeight: 420, overflowY: 'auto' }}>
                  {(tools.data ?? []).map((t) => (
                    <div className="list__row" key={t.name}>
                      <div className="list__main">
                        <span className="t-mono t-title" style={{ fontSize: 11.5 }}>
                          {t.name}
                        </span>
                        <span className="t-muted t-clamp-2">{t.description}</span>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </Panel>
          </div>
        )}

        <Panel title="Updates & recovery" icon="download" tight>
          <div className="grid grid--3">
            <div className="stack stack--tight">
              <span className="field__label">Update state</span>
              <span className="t-muted">
                Sythion reports its own version through <span className="t-mono">GET /api/v1/version</span>. There is no
                update channel and no self-update mechanism, so no update is offered here.
              </span>
            </div>
            <div className="stack stack--tight">
              <span className="field__label">Recovery</span>
              <span className="t-muted">
                If the runtime fails a health check, pausing and resuming the Main Agent re-derives its state from the
                database. Nothing else is auto-repaired.
              </span>
            </div>
            <div className="stack stack--tight">
              <span className="field__label">Resource usage</span>
              <span className="t-muted">
                No CPU, memory or cost telemetry is exposed over the API, so none is displayed. Inventing figures here
                would be worse than showing nothing.
              </span>
            </div>
          </div>
        </Panel>
      </div>
    </div>
  );
}
