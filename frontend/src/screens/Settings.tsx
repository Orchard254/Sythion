import { useEffect, useState } from 'react';
import { Icon } from '../components/icons';
import {
  Badge,
  Button,
  DataBadge,
  DemoTag,
  KV,
  LoadingBlock,
  Notice,
  Panel,
  ScreenHead,
  Switch,
} from '../components/ui';
import { useActions, useAppSettings, useHealth, useSecrets, useVoiceAssets } from '../hooks/useBackend';
import { useAppStore } from '../store/appStore';
import * as api from '../services/api';
import { useNavStore } from '../store/navStore';

type Density = 'comfortable' | 'compact';

export function SettingsScreen() {
  const settings = useAppSettings();
  const health = useHealth();
  const secrets = useSecrets();
  const assets = useVoiceAssets();
  const actions = useActions();
  const dataMode = useAppStore((s) => s.dataMode);
  const notify = useAppStore((s) => s.notify);
  const navigate = useNavStore((s) => s.setRoute);

  const [retention, setRetention] = useState<number | null>(null);
  const [roots, setRoots] = useState<string[] | null>(null);
  const [confirmFallback, setConfirmFallback] = useState<{ persisted_file_roots: string[]; fallback_file_roots: string[] } | null>(null);
  const [saving, setSaving] = useState(false);
  const [density, setDensity] = useState<Density>(() => {
    const stored = localStorage.getItem('sythion.density');
    return stored === 'compact' ? 'compact' : 'comfortable';
  });
  const [installingModel, setInstallingModel] = useState(false);
  const [installError, setInstallError] = useState<string | null>(null);
  const [sidecarVersion, setSidecarVersion] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    api.healthService
      .version()
      .then((v) => {
        if (active) setSidecarVersion(v.version);
      })
      .catch(() => {
        if (active) setSidecarVersion(null);
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    document.documentElement.style.setProperty(
      '--density-scale',
      density === 'compact' ? '0.92' : '1'
    );
    localStorage.setItem('sythion.density', density);
  }, [density]);

  useEffect(() => {
    if (settings.data) {
      setRetention(settings.data.retention_days);
      setRoots(settings.data.persisted_file_roots ? [...settings.data.persisted_file_roots] : null);
      setConfirmFallback(null);
    }
  }, [settings.data]);

  const installSpeechModel = async () => {
    if (installingModel || dataMode === 'preview') return;
    setInstallingModel(true);
    setInstallError(null);
    try {
      await api.voiceService.installAssets();
      await assets.refetch();
      notify({ tone: 'ok', title: 'Speech model installed', body: 'Pinned Vosk model verified and ready.' });
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Unknown error';
      setInstallError(message);
      notify({ tone: 'danger', title: 'Speech model install failed', body: message });
    } finally {
      setInstallingModel(false);
    }
  };

  const rootsDirty = roots !== null && JSON.stringify(roots) !== JSON.stringify(settings.data?.persisted_file_roots);
  const clearingToFallback = rootsDirty && roots?.length === 0 && (settings.data?.persisted_file_roots?.length ?? 0) > 0 && settings.data?.environment_file_roots?.length === 0;
  const fallbackKnown = settings.data?.fallback_file_roots !== undefined && settings.data.environment_file_roots !== undefined;
  const dirty =
    settings.data !== undefined &&
    ((retention !== null && retention !== settings.data.retention_days) ||
      rootsDirty);

  const save = async (confirmed = false) => {
    if (rootsDirty && roots?.length === 0 && !fallbackKnown) return;
    if (clearingToFallback && !confirmed) {
      setConfirmFallback({ persisted_file_roots: [...settings.data!.persisted_file_roots!], fallback_file_roots: [...settings.data!.fallback_file_roots!] });
      return;
    }
    if (confirmed && (!confirmFallback || !clearingToFallback ||
      JSON.stringify(confirmFallback.persisted_file_roots) !== JSON.stringify(settings.data?.persisted_file_roots) ||
      JSON.stringify(confirmFallback.fallback_file_roots) !== JSON.stringify(settings.data?.fallback_file_roots))) return;
    setSaving(true);
    try {
      const updates: Record<string, unknown> = {};
      if (retention !== null && retention !== settings.data?.retention_days) updates.retention_days = retention;
      if (rootsDirty && roots !== null) updates.file_roots = roots;
      if (confirmed) updates.file_roots_fallback_acknowledgement = confirmFallback;
      await actions.updateSettings(updates);
      setConfirmFallback(null);
      settings.refetch();
      notify({ tone: 'ok', title: 'Settings saved', body: 'Persisted through PUT /api/v1/settings.' });
    } catch (err) {
      if (confirmed) {
        setConfirmFallback(null);
        settings.refetch();
      }
      notify({
        tone: 'danger',
        title: 'Could not save settings',
        body: err instanceof Error ? err.message : 'Unknown error',
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="screen">
      <div className="screen__inner">
        <ScreenHead
          eyebrow="Control"
          title="Settings"
          description="Application settings that Sythion enforces, plus the preferences of this interface."
          actions={dataMode === 'preview' ? <DemoTag /> : <DataBadge origin="live" />}
        />

        <div className="grid grid--2">
          <Panel
            title="Runtime settings"
            icon="server"
            actions={dataMode === 'preview' ? <DemoTag /> : <DataBadge origin="live" />}
          >
            {settings.isLoading ? (
              <LoadingBlock rows={5} />
            ) : dataMode === 'preview' ? (
              <>
                <KV
                  items={[
                    { k: 'Retention', v: `${settings.data?.retention_days ?? '—'} days` },
                    { k: 'File roots', v: `${settings.data?.file_roots.length ?? 0} approved` },
                    { k: 'Max actions / task', v: String(settings.data?.max_actions_per_task ?? '—') },
                    { k: 'Voice', v: settings.data?.voice_enabled ? 'enabled' : 'disabled' },
                  ]}
                />
                <Notice tone="warn" icon="lock">
                  <span>Read-only in preview mode — there is no backend to persist to.</span>
                </Notice>
              </>
            ) : (
              <div className="stack">
                <div className="field">
                  <label className="field__label" htmlFor="retention">
                    Retention window (days)
                  </label>
                  <input
                    id="retention"
                    className="input"
                    type="number"
                    min={1}
                    max={3650}
                    value={retention ?? 90}
                    onChange={(e) => setRetention(Math.max(1, Number(e.target.value) || 90))}
                  />
                  <span className="field__hint">
                    This window is used when a retention purge is explicitly requested. Purging is not automatic.
                  </span>
                </div>

                <div className="field">
                  <span className="field__label">Persisted file grants</span>
                  {roots?.map((root, index) => (
                    <div className="row" key={index}>
                      <input className="input" aria-label={`File grant ${index + 1}`} value={root}
                        onChange={(e) => { const next = [...roots]; next[index] = e.target.value; setRoots(next); setConfirmFallback(null); }} />
                      <button type="button" onClick={() => { setRoots(roots.filter((_, i) => i !== index)); setConfirmFallback(null); }}>Remove grant {index + 1}</button>
                    </div>
                  ))}
                  <button type="button" disabled={roots === null} onClick={() => { setRoots([...(roots ?? []), '']); setConfirmFallback(null); }}>Add file grant</button>
                  <span className="field__hint">
                    Environment roots are separate and cannot be removed here. With no configured roots, file tools
                    use the home and working directories; otherwise they also allow the backend directory. Clearing
                    grants can therefore change, rather than eliminate, access.
                  </span>
                  {!settings.data?.persisted_file_roots && <span className="field__hint">Editing roots requires a newer backend that separates persisted grants.</span>}
                  {rootsDirty && roots?.length === 0 && !fallbackKnown && <span className="field__hint">Cannot clear grants: backend does not report the fallback roots.</span>}
                  <span className="field__hint">Environment roots: {settings.data?.environment_file_roots?.join(', ') || 'none reported'}</span>
                  <span className="field__hint">Effective file-tool roots: {settings.data?.effective_file_roots?.join(', ') || 'not reported by backend'}</span>
                </div>

                <div className="row">
                  <Button
                    variant="primary"
                    icon="check"
                    disabled={!dirty || saving || Boolean(roots?.some((root) => !root.trim())) || Boolean(rootsDirty && roots?.length === 0 && !fallbackKnown)}
                    onClick={() => void save()}
                  >
                    {saving ? 'Saving…' : 'Save settings'}
                  </Button>
                  {dirty && <span className="t-faint">Unsaved changes</span>}
                </div>
                {confirmFallback && clearingToFallback && (
                  <div role="alert" className="stack">
                    <strong>Clearing the last grant enables the following fallback file-tool roots:</strong>
                    <ul>{confirmFallback.fallback_file_roots.map((root, i) => <li key={i}>{root}</li>)}</ul>
                    <span>This may broaden access. Confirm only if you intend to allow these directories.</span>
                    <div className="row">
                      <button type="button" disabled={saving} onClick={() => void save(true)}>Confirm fallback access</button>
                      <button type="button" onClick={() => setConfirmFallback(null)}>Cancel</button>
                    </div>
                  </div>
                )}
              </div>
            )}
          </Panel>

          <Panel
            title="Interface preferences"
            icon="settings"
            hint="Stored in this window only. Nothing here reaches the backend."
          >
            <div className="stack">
              <div className="row row--between">
                <div>
                  <span className="t-title">Density</span>
                  <p className="t-muted" style={{ marginTop: 3 }}>
                    Compact reduces spacing to fit more rows in the same window.
                  </p>
                </div>
                <div className="segmented">
                  <button
                    type="button"
                    className={`segmented__item${density === 'comfortable' ? ' segmented__item--active' : ''}`}
                    onClick={() => setDensity('comfortable')}
                  >
                    Comfortable
                  </button>
                  <button
                    type="button"
                    className={`segmented__item${density === 'compact' ? ' segmented__item--active' : ''}`}
                    onClick={() => setDensity('compact')}
                  >
                    Compact
                  </button>
                </div>
              </div>

              <div className="row row--between">
                <div>
                  <span className="t-title">Data source</span>
                  <p className="t-muted" style={{ marginTop: 3 }}>
                    Switch to demonstration data without closing the app.
                  </p>
                </div>
                <Switch
                  checked={dataMode === 'preview'}
                  onChange={(v) => {
                    useAppStore.getState().setDataMode(v ? 'preview' : 'live');
                    notify({
                      tone: v ? 'warn' : 'ok',
                      title: v ? 'Preview mode enabled' : 'Live mode enabled',
                      body: v ? 'All panels now show labelled demonstration data.' : undefined,
                    });
                  }}
                />
              </div>

              {dataMode === 'preview' && (
                <Notice tone="warn" icon="alert">
                  <span>
                    Preview mode is on. Panels show invented data and every action that would change the backend is
                    disabled.
                  </span>
                </Notice>
              )}
            </div>
          </Panel>

          <Panel
            title="Voice"
            icon="microphone"
            actions={dataMode === 'preview' ? <DemoTag /> : <DataBadge origin="live" />}
          >
            {assets.isLoading ? (
              <LoadingBlock rows={3} />
            ) : (
              <>
                <KV
                  items={[
                    { k: 'Speech to text', v: assets.data?.stt_installed ? 'installed' : 'not installed' },
                    { k: 'Checksum', v: assets.data?.stt_sha256_verified ? 'verified' : 'not verified' },
                    { k: 'Voice output', v: assets.data?.tts_voice || 'none selected' },
                    { k: 'Microphone', v: 'never records without an explicit start' },
                  ]}
                />
                {!assets.data?.stt_installed && (
                  <Notice tone="info" icon="info">
                    <span>
                      Voice input needs the pinned local speech model. Sythion never downloads it on startup — it must
                      be installed deliberately.
                    </span>
                  </Notice>
                )}

                {installError && (
                  <Notice tone="danger" icon="alert">
                    <span>{installError}</span>
                  </Notice>
                )}

                <div className="row">
                  <Button
                    size="sm"
                    variant="primary"
                    icon="download"
                    disabled={
                      dataMode === 'preview' || installingModel || Boolean(assets.data?.stt_installed)
                    }
                    onClick={() => void installSpeechModel()}
                  >
                    {installingModel
                      ? 'Installing…'
                      : assets.data?.stt_installed
                      ? 'Speech model installed'
                      : 'Install speech model'}
                  </Button>
                  {Boolean(assets.data?.stt_sha256_verified) && (
                    <span className="t-faint">Checksum verified</span>
                  )}
                </div>
              </>
            )}
          </Panel>

          <Panel
            title="Privacy & credentials"
            icon="lock"
            actions={dataMode === 'preview' ? <DemoTag /> : <DataBadge origin="live" />}
          >
            <KV
              items={[
                {
                  k: 'Configured secrets',
                  v: (secrets.data?.configured ?? []).length > 0
                    ? `${secrets.data?.configured.length} (${secrets.data?.configured.join(', ')})`
                    : 'none',
                },
                { k: 'Backend', v: sidecarVersion ? `v${sidecarVersion}` : `v${health.data?.version ?? 'unreachable'}` },
                { k: 'Values shown', v: 'never — the API returns names only' },
              ]}
            />
            <Notice tone="accent" icon="shield">
              <span>
                Sythion stores secrets write-only through <span className="t-mono">PUT /api/v1/secrets/&#123;name&#125;</span>.
                This interface has no field that could display one.
              </span>
            </Notice>
            <Button size="sm" variant="subtle" icon="shield" onClick={() => navigate('security')}>
              Open Security &amp; Control
            </Button>
          </Panel>
        </div>

        <Panel title="Keyboard" icon="key" tight>
          <div className="grid grid--3">
            {[
              { keys: 'Enter', action: 'Send the composed command' },
              { keys: 'Shift + Enter', action: 'New line inside the composer' },
              { keys: 'Esc', action: 'Close an open panel or dialog' },
            ].map((s) => (
              <div className="row" key={s.keys} style={{ gap: 9 }}>
                <Badge tone="plain">
                  <span className="t-mono">{s.keys}</span>
                </Badge>
                <span className="t-muted">{s.action}</span>
              </div>
            ))}
          </div>
          <p className="t-faint" style={{ marginTop: 10 }}>
            <Icon name="info" size={11} style={{ verticalAlign: '-1px', marginRight: 5 }} />
            Screens are reachable by URL fragment — for example <span className="t-mono">#/security</span> opens the
            control surface directly.
          </p>
        </Panel>
      </div>
    </div>
  );
}
