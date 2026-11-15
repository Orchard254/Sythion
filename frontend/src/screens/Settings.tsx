import { useEffect, useState } from 'react';
import {
  Badge,
  Button,
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
import { AIModelsSettings } from '../components/AIModelsSettings';
import { companionEnabled, setCompanionEnabled } from '../services/companion';

type Density = 'comfortable' | 'compact';

export function SettingsScreen() {
  const settings = useAppSettings();
  const health = useHealth();
  const secrets = useSecrets();
  const assets = useVoiceAssets();
  const actions = useActions();
  const notify = useAppStore((s) => s.notify);
  const navigate = useNavStore((s) => s.setRoute);

  const [retention, setRetention] = useState<number | null>(null);
  const [roots, setRoots] = useState<string[] | null>(null);
  const [confirmFallback, setConfirmFallback] = useState<{ persisted_file_roots: string[]; fallback_file_roots: string[] } | null>(null);
  const [saving, setSaving] = useState(false);
  const [spokenReplies, setSpokenReplies] = useState(() => localStorage.getItem('sythion.replyMode') !== 'text');
  const [companionOn, setCompanionOn] = useState(companionEnabled);
  const [companionBusy, setCompanionBusy] = useState(false);
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

  const toggleCompanion = async (enabled: boolean) => {
    if (companionBusy) return;
    setCompanionBusy(true);
    try {
      await setCompanionEnabled(enabled);
      setCompanionOn(enabled);
      notify({ tone: 'ok', title: enabled ? 'Companion enabled' : 'Companion hidden',
        body: enabled ? 'Companion overlay enabled from Settings.' : 'Companion stays hidden until enabled.' });
    } catch {
      notify({ tone: 'danger', title: 'Companion setting failed',
        body: 'Desktop could not change Companion visibility. The previous preference was kept.' });
    } finally {
      setCompanionBusy(false);
    }
  };

  const installSpeechModel = async () => {
    if (installingModel) return;
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
          description="Settings and interface preferences."
        />

        <AIModelsSettings />

        <div className="grid grid--2">
          <Panel
            title="Runtime settings"
            icon="server"
           
          >
            {settings.isLoading ? (
              <LoadingBlock rows={5} />
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
                    Used by explicit purges only. Purging is never automatic.
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
                    Environment roots can't be removed here. With no configured roots, file tools use home and working
                    directories (plus the backend directory when roots are set) — clearing grants can broaden access.
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


            </div>
          </Panel>

          <Panel title="Companion" icon="assistant">
            <div className="stack">
              <Switch label="Show Companion overlay" checked={companionOn}
                onChange={(enabled) => { void toggleCompanion(enabled); }} />
              <span className="field__hint">The floating Companion is optional.
                Enable or hide it here. Your AI model and permissions stay unchanged.</span>
              {companionBusy && <span role="status">Updating Companion…</span>}
            </div>
          </Panel>

          <Panel
            title="Voice"
            icon="microphone"
           
          >
            <Switch label="Read new replies aloud" checked={spokenReplies} onChange={(enabled) => {
              setSpokenReplies(enabled);
              localStorage.setItem('sythion.replyMode', enabled ? 'voice' : 'text');
            }} />
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
                {!assets.data?.tts_voice && (
                  <Notice tone="info" icon="info">
                    <span>Spoken replies need a local voice. If none is available, replies stay as text.</span>
                  </Notice>
                )}

                {!assets.data?.stt_installed && (
                  <Notice tone="info" icon="info">
                    <span>
                      Voice input needs the pinned local speech model — installed here deliberately, never on startup.
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
                      installingModel || Boolean(assets.data?.stt_installed)
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
              ]}
            />
            <Notice tone="accent" icon="shield">
              <span>
                Secrets are write-only — this interface has no field that could display one.
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

        </Panel>
      </div>
    </div>
  );
}
