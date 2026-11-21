import { useState } from 'react';
import { Icon } from '../components/icons';
import {
  Badge,
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
  useCapabilities,
  useHealth,
  useModels,
  useRelayAcks,
  useRetentionReceipts,
  useSecrets,
  useSkills,
  useTools,
  useVersion,
  useVoiceAssets,
} from '../hooks/useBackend';
import { dateTime, humanize, relTime } from '../lib/format';
import { CLOUD_PROVIDER_LABELS } from '../services/api';
import { DeviceChannelPanel } from '../components/DeviceChannelPanel';
import { RelayAckPanel } from '../components/RelayAckPanel';

type Tab = 'runtime' | 'models' | 'capabilities' | 'diagnostics';

function summarizeEvidenceValue(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (Array.isArray(value)) {
    return value.length === 0 ? 'none' : `${value.length} ${value.length === 1 ? 'item' : 'items'}`;
  }
  if (typeof value === 'object') {
    const entries = Object.entries(value as Record<string, unknown>);
    if (entries.length === 0) return 'empty';
    return entries
      .map(([k, v]) => `${k}=${typeof v === 'object' && v !== null ? JSON.stringify(v) : String(v)}`)
      .join(', ');
  }
  return String(value);
}

function summarizeEvidence(evidence: Record<string, unknown>): string {
  return Object.entries(evidence)
    .map(([k, v]) => `${humanize(k)}: ${summarizeEvidenceValue(v)}`)
    .join(' · ');
}

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
  const capabilities = useCapabilities();
  const receipts = useRetentionReceipts();
  const assets = useVoiceAssets();
  const relayAcks = useRelayAcks();

  const checks = agentHealth.data?.checks ?? [];
  const receiptList = receipts.data ?? [];

  return (
    <div className="screen">
      <div className="screen__inner">
        <ScreenHead
          eyebrow="Control"
          title="System & Integrations"
          description="Runtime state, models, connections and evidence."
          actions={
            <>
              <Segmented
                value={tab}
                onChange={setTab}
                ariaLabel="System view"
                options={[
                  { value: 'runtime', label: 'Runtime' },
                  { value: 'models', label: 'Models' },
                  { value: 'capabilities', label: 'Capabilities' },
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
            value={health.data && !health.isError ? (health.data.status === 'degraded' ? 'Degraded' : 'Online') : 'Offline'}
            tone={health.data ? (health.data.status === 'degraded' ? 'warn' : undefined) : 'accent'}
            foot={health.data && !health.isError ? `v${health.data.version} · ${version.data?.runtime ?? '—'}${health.data.status === 'degraded' ? ' · startup incomplete' : ''}` : 'No response'}
          />
          <Stat
            label="Health"
            icon="activity"
            value={agentHealth.data ? humanize(agentHealth.data.overall) : '—'}
            foot={checks.length === 0
              ? 'No checks reported'
              : [
                  `${checks.length} checks`,
                  ...[
                    ['HEALTHY', 'healthy'],
                    ['DEGRADED', 'degraded'],
                    ['UNAVAILABLE', 'unavailable'],
                    ['UNKNOWN', 'unknown'],
                  ]
                    .map(([key, label]) => {
                      const n = checks.filter((c) => c.status === key).length;
                      return n > 0 ? `${n} ${label}` : null;
                    })
                    .filter(Boolean),
                ].join(' · ')}
          />
          <Stat label="Tools" icon="layers" value={tools.data?.length ?? '—'} foot="Registered in the executor" />
          <Stat label="Skills" icon="automations" value={skills.data?.length ?? '—'} foot="Validated procedures" />
        </div>

        {tab === 'runtime' && (
          <div className="grid grid--2">
            <DeviceChannelPanel />
            <RelayAckPanel summary={relayAcks.data ?? null} />
            <Panel
              title="Process"
              icon="server"
             
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
                    Per-task bounds only — they limit one task, not the system.
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
                        The speech model's checksum is unverified. Sythion will not download or verify it for you.
                      </span>
                    </Notice>
                  )}
                </>
              )}
            </Panel>

            <Panel
              title="Health checks"
              icon="activity"
             
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
                          <span className="t-faint t-clamp-2" title={JSON.stringify(c.evidence)}>
                            {summarizeEvidence(c.evidence as Record<string, unknown>)}
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
                    const selected = health.data?.active_model_id === id ||
                      health.data?.selected_model === id || settings.data?.selected_model === id;
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
                  Registry reports intent and verification only — no cost accounting endpoint exists.
                </span>
              </div>
            </Panel>

            <Panel
              title="Credentials"
              icon="key"
             
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
                  {
                    k: 'Active AI provider',
                    v: health.data?.active_provider_id
                      ? `${CLOUD_PROVIDER_LABELS[health.data.active_provider_id as keyof typeof CLOUD_PROVIDER_LABELS]
                          || health.data.active_provider_id}`
                      : 'unavailable',
                  },
                  { k: 'Active AI model', v: health.data?.active_model_id || '—' },
                  {
                    k: 'Local provider',
                    v: health.data?.cloud_configured ? 'healthy' : 'unavailable',
                  },
                  {
                    k: 'Local model',
                    v: health.data?.selected_model || settings.data?.selected_model || '—',
                  },
                  { k: 'Base URL', v: settings.data?.ollama_cloud_base_url ?? '—' },
                ]}
              />
              <Notice tone="accent" icon="lock">
                <span>
                  Secrets are write-only — names only, never values.
                </span>
              </Notice>
            </Panel>
          </div>
        )}

        {tab === 'capabilities' && (
            <div className="grid grid--main">
              <Panel title="Capabilities" icon="shield" flush>
                {capabilities.isLoading ? (
                  <div className="panel__body">
                    <LoadingBlock rows={5} />
                  </div>
                ) : (capabilities.data ?? []).length === 0 ? (
                  <EmptyState icon="plug" title="No capabilities registered" />
                ) : (
                  <div className="list">
                    {(capabilities.data ?? []).map((c) => (
                      <div className="list__row" key={c.id}>
                        <Rail tone={c.status === 'AVAILABLE' ? 'ok' : c.status === 'UNAVAILABLE' ? 'danger' : c.status === 'DEGRADED' ? 'warn' : c.status === 'REQUIRES_PERMISSION' ? 'warn' : 'idle'} />
                        <div className="list__main">
                          <span className="t-title" style={{ fontSize: 12.5 }}>
                            {c.name}
                          </span>
                          <span className="list__meta">
                            <Badge tone={c.status === 'AVAILABLE' ? 'ok' : c.status === 'UNAVAILABLE' ? 'danger' : c.status === 'DEGRADED' ? 'warn' : c.status === 'REQUIRES_PERMISSION' ? 'warn' : 'idle'}>{c.status}</Badge>
                            <Badge tone={c.risk_class === 'HIGH' ? 'danger' : c.risk_class === 'MEDIUM' ? 'warn' : 'ok'}>{c.risk_class} risk</Badge>
                            {c.requires_external_service && <span>· external service</span>}
                          </span>
                          <span className="t-muted t-clamp-2">{c.description}</span>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </Panel>

              <Panel
                title="Capability consistency"
                icon="shield"
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
                  Reported, never auto-repaired. Missing tools resolve to UNAVAILABLE regardless of mode.
                </p>
              </Panel>
            </div>
        )}

        {tab === 'diagnostics' && (
          <div className="grid grid--2">
            <Panel
              title="Retention purge receipts"
              icon="trash"
              flush
             
            >
              {receipts.isLoading ? (
                <div className="panel__body">
                  <LoadingBlock rows={3} />
                </div>
              ) : receiptList.length === 0 ? (
                <EmptyState
                  icon="trash"
                  title="Nothing purged"
                  description="Receipts appear after an explicit purge request."
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
                Sythion reports its version via <span className="t-mono">GET /api/v1/version</span>. No update channel
                exists, so no update is offered.
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
                No CPU, memory or cost telemetry is exposed, so none is displayed.
              </span>
            </div>
          </div>
        </Panel>
      </div>
    </div>
  );
}
