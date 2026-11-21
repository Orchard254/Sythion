import { useEffect, useRef, useState } from 'react';
import { ActiveCloudSetup } from './ActiveCloudSetup';
import { Button, Notice, Panel } from './ui';
import {
  activeInferenceService, cloudPreviewService,
  type CloudPreviewProviderId,
} from '../services/api';

type AIProvider = CloudPreviewProviderId;
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
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const mounted = useRef(false);
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
      ]);
      if (cancelled || !mounted.current) return;
      const [selection, credentials] = results;
      if (selection.status === 'fulfilled') {
        const current = selection.value;
        setActive(current);
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
      setLoading(false);
    }
    void load();
    return () => { cancelled = true; };
  }, []);

  const chooseProvider = (next: AIProvider) => {
    if (inFlight.current || loading) return;
    setProvider(next);
    setError(''); setMessage('');
    setKeyEditor(false);
  };

  const onCloudConnected = () => {
    if (!provider) return;
    setHasKey((previous) => ({ ...previous, [provider]: true }));
    setKeyEditor(false);
    setError(''); setMessage('');
  };

  const onCloudVerified = (modelId: string, pending?: boolean) => {
    if (!provider) return;
    setError('');
    if (pending) {
      // The backend queued this verified choice because in-flight work still
      // holds the old provider binding. It will apply automatically when work
      // settles. Do NOT re-check status now — it legitimately still reports the
      // old model, and telling the owner to refresh would hand work back to
      // them. The "Active model" line stays honest until the switch lands.
      setMessage('Model selected. It will activate automatically when current work finishes. Existing permissions are unchanged.');
      return;
    }
    // ActiveCloudSetup validates the activate() response against the chosen
    // provider/model/permissions BEFORE calling this callback. That result is
    // authoritative, so a redundant status() re-check here only produced
    // stale "refresh/reopen" hand-backs — it is removed. Trust the verified
    // result and update the displayed selection from what was confirmed.
    setActive({ provider_id: provider, model_id: modelId, permissions_unchanged: true });
    setMessage('Active AI model updated. Sythion keeps its existing assistant and permissions.');
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
            onChange={(event) => chooseProvider(event.target.value as AIProvider)}>
            {!provider && <option value="" disabled>Choose a provider</option>}
            {Object.entries(PROVIDERS).map(([id, label]) =>
              <option key={id} value={id}>{label}</option>)}
          </select>
          {provider && <>
            {hasKey[provider] && !keyEditor &&
              <Button variant="subtle" onClick={() => setKeyEditor(true)}>
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
        {message && <Notice tone="info" icon="check">{message}</Notice>}
        {error && <Notice tone="danger" icon="alert">{error}</Notice>}
      </div>
    </Panel>
  );
}
