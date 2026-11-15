import { useEffect, useRef, useState } from 'react';
import { ActiveCloudSetup } from './ActiveCloudSetup';
import { Button, Notice, Panel } from './ui';
import {
  activeInferenceService, cloudPreviewService, modelService, setupService,
  type CloudPreviewProviderId,
} from '../services/api';

type AIProvider = 'ollama-cloud' | CloudPreviewProviderId;
type ActiveModel = { provider_id: string; model_id: string; permissions_unchanged: true };

const PROVIDERS: Record<AIProvider, string> = {
  'ollama-cloud': 'Ollama Cloud',
  openrouter: 'OpenRouter',
  nvidia: 'NVIDIA NIM',
  groq: 'Groq',
};
const isSupported = (value: string): value is AIProvider => value in PROVIDERS;

/** First-time configuration belongs in onboarding; later changes belong here.
 * Only the existing Sythion runtime is used. Provider preview chat is never
 * mounted and selecting a provider does not authorize actions or send probes.
 */
export function AIModelsSettings() {
  const [active, setActive] = useState<ActiveModel | null>(null);
  const [provider, setProvider] = useState<AIProvider | null>(null);
  const [hasKey, setHasKey] = useState<Partial<Record<CloudPreviewProviderId, boolean>>>({});
  const [keyEditor, setKeyEditor] = useState(false);
  const [ollamaKey, setOllamaKey] = useState('');
  const [ollamaKeyReady, setOllamaKeyReady] = useState(false);
  const [ollamaModels, setOllamaModels] = useState<string[]>([]);
  const [selectedOllama, setSelectedOllama] = useState('');
  const [loading, setLoading] = useState(true);
  const [loadingModels, setLoadingModels] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const mounted = useRef(false);
  const generation = useRef(0);
  const inFlight = useRef(false);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      // Read actual active selection, not staged/cloud-preview state.
      // An invalid binding must NEVER be displayed as active Ollama.
      const results = await Promise.allSettled([
        activeInferenceService.status(),
        cloudPreviewService.status(),
        setupService.readiness(),
      ]);
      if (cancelled || !mounted.current) return;
      const [selection, credentials, readiness] = results;
      if (selection.status === 'fulfilled') {
        const current = selection.value;
        setActive(current);
        if (current.provider_id === 'ollama-cloud') setSelectedOllama(current.model_id);
        if (isSupported(current.provider_id)) setProvider(current.provider_id);
        else setError('The active AI provider is unsupported. Review configuration.');
      } else {
        setError('The active model could not be verified. Choose a provider to repair its configuration.');
      }
      if (credentials.status === 'fulfilled') {
        const entries = credentials.value.providers;
        setHasKey(Object.fromEntries(
          entries.map((entry) => [entry.provider_id, entry.credential_configured === true]),
        ) as Partial<Record<CloudPreviewProviderId, boolean>>);
      }
      if (readiness.status === 'fulfilled') {
        setOllamaKeyReady(readiness.value.checks.ollama_api_key_ok === true);
      }
      setLoading(false);
    }
    void load();
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (loading || provider !== 'ollama-cloud' || !ollamaKeyReady) return;
    let cancelled = false;
    const current = ++generation.current;
    setLoadingModels(true);
    void modelService.available().then((models) => {
      if (cancelled || !mounted.current || current !== generation.current) return;
      setOllamaModels([...new Set(models)]);
    }).catch(() => {
      if (!cancelled && mounted.current && current === generation.current) {
        setError('Could not load Ollama models. Check your Ollama connection or API key.');
      }
    }).finally(() => {
      if (!cancelled && mounted.current && current === generation.current) setLoadingModels(false);
    });
    return () => { cancelled = true; };
    // Fetch on provider change/key readiness, not on every active model update.
  }, [provider, loading, ollamaKeyReady]);

  const chooseProvider = (next: AIProvider) => {
    if (inFlight.current || busy || loading) return;
    generation.current++;
    setProvider(next);
    setError(''); setMessage('');
    setKeyEditor(false);
    setSelectedOllama(next === 'ollama-cloud' && active?.provider_id === 'ollama-cloud'
      ? active.model_id : '');
  };

  const saveOllamaKey = async () => {
    if (!ollamaKey.trim() || inFlight.current) return;
    inFlight.current = true; setBusy(true); setError(''); setMessage('');
    const current = generation.current;
    try {
      const result = await setupService.testCredentials(ollamaKey.trim());
      if (!mounted.current || current !== generation.current) return;
      if (!result.valid) throw new Error('Credential was not accepted');
      setOllamaKey(''); setOllamaKeyReady(true);
      setOllamaModels(result.models.filter(Boolean));
      setMessage('Ollama key saved. Select a model to verify it.');
    } catch {
      if (mounted.current && current === generation.current) {
        setError('Could not verify the Ollama Cloud API key. Review your account access.');
      }
    } finally {
      inFlight.current = false;
      if (mounted.current && current === generation.current) setBusy(false);
    }
  };

  const activateOllama = async (modelId: string) => {
    if (!modelId || !ollamaKeyReady || !ollamaModels.includes(modelId) ||
        inFlight.current || busy) return;
    inFlight.current = true; setBusy(true); setError(''); setMessage('');
    const current = generation.current;
    setSelectedOllama(modelId);
    try {
      // Model selection is guarded by the backend against unfinished tasks.
      // Do not change active provider until Ollama responds successfully.
      await setupService.selectModel(modelId);
      if (!mounted.current || current !== generation.current) return;
      const verified = await setupService.inference(modelId);
      if (!mounted.current || current !== generation.current) return;
      if (!verified.success || verified.model !== modelId) throw new Error('Unverified model');
      const before = await activeInferenceService.status();
      if (!mounted.current || current !== generation.current) return;
      if (before.provider_id !== 'ollama-cloud') {
        await activeInferenceService.restoreOllama();
      }
      if (!mounted.current || current !== generation.current) return;
      const next = await activeInferenceService.status();
      if (!mounted.current || current !== generation.current) return;
      if (next.provider_id !== 'ollama-cloud' || next.model_id !== modelId ||
          next.permissions_unchanged !== true) throw new Error('Unexpected active model');
      setActive(next);
      setMessage('Ollama model verified and selected for Sythion.');
    } catch {
      if (mounted.current && current === generation.current) {
        setError('Sythion could not activate this Ollama model. Check access and finish any active tasks before switching.');
      }
    } finally {
      inFlight.current = false;
      if (mounted.current && current === generation.current) setBusy(false);
    }
  };

  const onCloudConnected = () => {
    if (!provider || provider === 'ollama-cloud') return;
    setHasKey((previous) => ({ ...previous, [provider]: true }));
    setKeyEditor(false);
    setError(''); setMessage('');
  };

  const onCloudVerified = (modelId: string) => {
    if (!provider || provider === 'ollama-cloud') return;
    const expected = provider;
    const currentGeneration = generation.current;
    void activeInferenceService.status().then((current) => {
      if (!mounted.current || currentGeneration !== generation.current) return;
      if (current.provider_id !== expected || current.model_id !== modelId ||
          current.permissions_unchanged !== true) {
        setError('The active model changed. Refresh Settings to verify its identity.');
        return;
      }
      setActive(current);
      setMessage('Active AI model updated. Sythion keeps its existing assistant and permissions.');
    }).catch(() => {
      if (mounted.current && currentGeneration === generation.current) {
        setError('Could not confirm the active model. Reopen AI Models to review.');
      }
    });
  };

  return (
    <Panel title="AI Models" icon="assistant">
      <div className="stack" style={{ gap: 12 }}>
        <p className="t-muted">
          Configure which AI model powers Sythion. Conversation history, goals,
          permissions and the main Assistant are unchanged.
        </p>
        {loading ? <p role="status">Checking the active AI model…</p> : <>
          <p className="t-muted" role="status">
            Active model: {active ? `${PROVIDERS[active.provider_id as AIProvider] || active.provider_id} · ${active.model_id || 'not selected'}`
              : 'Not verified'}
          </p>
          <label htmlFor="settings-ai-provider">AI provider</label>
          <select id="settings-ai-provider" className="input" value={provider ?? ''}
            disabled={busy} onChange={(event) => chooseProvider(event.target.value as AIProvider)}>
            {!provider && <option value="" disabled>Choose a provider</option>}
            {Object.entries(PROVIDERS).map(([id, label]) =>
              <option key={id} value={id}>{label}</option>)}
          </select>
          {provider === 'ollama-cloud' && <>
            <label htmlFor="settings-ollama-key">Ollama Cloud API key</label>
            <input id="settings-ollama-key" className="input" type="password"
              autoComplete="off" disabled={busy} value={ollamaKey}
              placeholder={ollamaKeyReady ? 'Saved key available — leave empty to keep it' : 'Enter Ollama Cloud API key'}
              onChange={(event) => setOllamaKey(event.target.value)} />
            {ollamaKey.trim() && <Button disabled={busy} onClick={() => void saveOllamaKey()}>
              Connect Ollama Cloud
            </Button>}
            <label htmlFor="settings-ollama-model">Ollama model</label>
            <select id="settings-ollama-model" className="input" value={selectedOllama}
              disabled={busy || loadingModels || !ollamaKeyReady}
              onChange={(event) => void activateOllama(event.target.value)}>
              <option value="">Select a model</option>
              {[...new Set([...(selectedOllama ? [selectedOllama] : []), ...ollamaModels])]
                .map((id) => <option value={id} key={id}>{id}</option>)}
            </select>
            {loadingModels && <p role="status">Loading Ollama models…</p>}
            <p className="t-muted">Selecting a different model verifies it before use.
              Switching may be blocked until unfinished tasks are completed.</p>
          </>}
          {provider && provider !== 'ollama-cloud' && <>
            {hasKey[provider] && !keyEditor &&
              <Button variant="subtle" disabled={busy} onClick={() => setKeyEditor(true)}>
                Change saved API key
              </Button>}
            <ActiveCloudSetup
              key={`${provider}:${hasKey[provider] && !keyEditor ? 'model' : 'connect'}`}
              provider={provider}
              phase={hasKey[provider] && !keyEditor ? 'model' : 'connect'}
              onConnected={onCloudConnected}
              onVerified={onCloudVerified}
            />
          </>}
        </>}
        {busy && <p role="status">Checking selected AI model…</p>}
        {message && <Notice tone="info" icon="check">{message}</Notice>}
        {error && <Notice tone="danger" icon="alert">{error}</Notice>}
      </div>
    </Panel>
  );
}
