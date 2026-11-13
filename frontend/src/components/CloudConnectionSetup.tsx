import { useEffect, useRef, useState } from 'react';
import {
  cloudPreviewService,
  type CloudPreviewProviderId,
  type CloudPreviewStatus,
  type CloudCatalogModel,
} from '../services/api';
import { Button, Notice, Panel } from './ui';

/** Shared, owner-driven cloud model configuration for onboarding and Settings.
 * A read-only local status lookup is the only automatic request.
 * Browsing is explicit. Verifying is one explicitly consented model request.
 * The result authorizes answer-only conversation, never tasks or tools.
 */
const LABELS: Record<CloudPreviewProviderId, string> = {
  openrouter: 'OpenRouter',
  nvidia: 'NVIDIA NIM',
  groq: 'Groq',
};
const PROVIDERS: CloudPreviewProviderId[] = ['openrouter', 'nvidia', 'groq'];
const TEST_PROMPT = 'Say hello in one short plain-text sentence.';

function safeError(error: unknown): string {
  const response = error && typeof error === 'object' && 'response' in error
    ? error.response : null;
  const status = response && typeof response === 'object' && 'status' in response
    ? response.status : null;
  const data = response && typeof response === 'object' && 'data' in response
    ? response.data : null;
  const detail = data && typeof data === 'object' && 'detail' in data ? data.detail : null;
  if (status === 429) return 'Provider capacity or free quota reached. No automatic retry was made.';
  if (status === 409) return 'The selected model or key changed. Review the setup before continuing.';
  if (status === 502 && detail === 'Cloud model requires an eligible agentic harness (upstream HTTP 403)')
    return 'This free model is restricted to eligible agentic harnesses. Choose a different model.';
  if (status === 502) return 'The cloud provider rejected this model request. Choose another model or review account access.';
  if (status === 503) return 'Cloud response is uncertain. No automatic retry was made.';
  return 'Cloud model setup did not complete. Review the provider, key and model.';
}

export function CloudConnectionSetup({
  provider: initialProvider = 'openrouter', onReady, hideProviderPicker = false,
}: {
  provider?: CloudPreviewProviderId;
  onReady?: () => void;
  hideProviderPicker?: boolean;
}) {
  const [provider, setProvider] = useState<CloudPreviewProviderId>(initialProvider);
  const [status, setStatus] = useState<CloudPreviewStatus | null>(null);
  const [key, setKey] = useState('');
  const [model, setModel] = useState('');
  const [catalog, setCatalog] = useState<CloudCatalogModel[]>([]);
  const [search, setSearch] = useState('');
  const [freeOnly, setFreeOnly] = useState(false);
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  useEffect(() => {
    let active = true;
    void cloudPreviewService.status().then((value) => {
      if (!active) return;
      setStatus(value);
      const staged = value.staged;
      if (staged.provider_id === initialProvider && typeof staged.model_id === 'string') {
        setModel(staged.model_id);
      }
    }).catch(() => {
      if (active) setError('Could not read saved cloud model configuration.');
    });
    return () => { active = false; };
  }, [initialProvider]);

  const configured = status?.providers.some(
    (p) => p.provider_id === provider && p.credential_configured === true
  ) === true;
  const match = status?.staged.provider_id === provider &&
    status.staged.model_id === model.trim();
  const verified = configured && !key.trim() && match && status?.text_probe_verified === true &&
    status.ready_for_execution === false;
  const selection = { provider_id: provider, model_id: model.trim() };
  const filtered = catalog.filter((item) =>
    (!freeOnly || item.free_variant) && item.id.toLowerCase().includes(search.toLowerCase()));

  const chooseProvider = (next: CloudPreviewProviderId) => {
    setProvider(next);
    setModel(status?.staged.provider_id === next ? status.staged.model_id ?? '' : '');
    setKey('');
    setCatalog([]);
    setSearch('');
    setConsent(false);
    setNotice('');
    setError('');
  };

  const browse = async () => {
    if (inFlight.current || (!configured && !key.trim())) return;
    inFlight.current = true; setBusy(true); setError(''); setNotice('');
    try {
      if (key.trim()) {
        await cloudPreviewService.saveCredential(provider, key.trim());
        setKey('');
        setConsent(false);
        setStatus((previous) => previous ? {
          ...previous,
          text_probe_verified: false,
          tool_probe_verified: false,
          providers: previous.providers.map((p) => p.provider_id === provider
            ? { ...p, credential_configured: true } : p),
        } : null);
      }
      const result = await cloudPreviewService.modelCatalog(provider);
      if (result.provider_id !== provider || result.ready_for_execution !== false ||
          !Array.isArray(result.models) || result.models.length > 1000) {
        throw new Error('Invalid catalog response');
      }
      setCatalog(result.models);
      setNotice('Choose a model below. A listed model is not guaranteed to be free or available.');
    } catch (e) {
      setError(safeError(e));
    } finally { inFlight.current = false; setBusy(false); }
  };

  const continueWithModel = async () => {
    if (inFlight.current || !configured || !model.trim() || model.trim().length > 180 ||
        !consent || key.trim()) return;
    inFlight.current = true; setBusy(true); setError(''); setNotice('');
    setConsent(false);
    try {
      // Re-read the local backend's current identity; never trust stale UI proof.
      const current = await cloudPreviewService.status();
      const valid = current.staged.provider_id === provider &&
        current.staged.model_id === selection.model_id &&
        current.text_probe_verified === true &&
        current.ready_for_execution === false &&
        current.providers.some((p) => p.provider_id === provider && p.credential_configured);
      if (!valid) {
        await cloudPreviewService.stage(selection);
        const result = await cloudPreviewService.chat(selection, TEST_PROMPT);
        if (result.provider_id !== provider || result.model_id !== selection.model_id ||
            result.active !== false || result.ready_for_execution !== false ||
            result.persisted !== false || result.task_executed !== false ||
            typeof result.assistant_text !== 'string' || !result.assistant_text.trim()) {
          throw new Error('Unexpected model response');
        }
      }
      const latest = await cloudPreviewService.status();
      if (latest.staged.provider_id !== provider || latest.staged.model_id !== selection.model_id ||
          latest.text_probe_verified !== true || latest.ready_for_execution !== false ||
          !latest.providers.some((p) => p.provider_id === provider && p.credential_configured)) {
        throw new Error('Cloud verification changed');
      }
      setStatus(latest);
      if (onReady) onReady();
      else setNotice('Model verified for answer-only cloud conversation. It is not authorized for tasks or tools.');
    } catch (e) {
      setError(safeError(e));
    } finally { inFlight.current = false; setBusy(false); }
  };

  return (
    <Panel title={`Configure ${LABELS[provider]}`} icon="assistant">
      <div className="stack" style={{ gap: 12 }}>
        {!hideProviderPicker && (
          <>
            <label htmlFor="cloud-config-provider">Cloud provider</label>
            <select id="cloud-config-provider" className="input" value={provider} disabled={busy}
              onChange={(e) => chooseProvider(e.target.value as CloudPreviewProviderId)}>
              {PROVIDERS.map((id) => <option key={id} value={id}>{LABELS[id]}</option>)}
            </select>
          </>
        )}
        <label htmlFor="cloud-config-key">API key</label>
        <input id="cloud-config-key" className="input" type="password" autoComplete="off"
          value={key} disabled={busy} onChange={(e) => {
            setKey(e.target.value); setConsent(false);
          }}
          placeholder={configured ? 'Saved key available — leave empty to keep it' : 'Paste provider API key'} />
        <p className="t-muted">Credentials stay in Sythion's secure key store. Keys are never shown again.</p>
        <Button disabled={busy || (!configured && !key.trim())}
          onClick={() => void browse()}>
          {key.trim() ? 'Save key and browse models' : 'Browse available models'}
        </Button>
        {catalog.length > 0 && <>
          <label htmlFor="cloud-config-search">Find a model</label>
          <input id="cloud-config-search" className="input" type="search" value={search}
            disabled={busy} onChange={(e) => setSearch(e.target.value)} />
          <label className="row" style={{ gap: 8 }}>
            <input type="checkbox" checked={freeOnly} disabled={busy}
              onChange={(e) => setFreeOnly(e.target.checked)} />
            Show :free models only (availability and quota not guaranteed)
          </label>
          <select id="cloud-config-catalog" className="input" value="" disabled={busy}
            onChange={(e) => { setModel(e.target.value); setConsent(false); }}>
            <option value="">Select a model</option>
            {filtered.slice(0, 80).map((item) => <option key={item.id} value={item.id}>{item.id}</option>)}
          </select>
        </>}
        <label htmlFor="cloud-config-model">Selected model</label>
        <input id="cloud-config-model" className="input" value={model} disabled={busy}
          onChange={(e) => { setModel(e.target.value); setConsent(false); }}
          placeholder="Select above or enter exact model ID" />
        {verified && <Notice tone="info" icon="check">
          This exact model and key have current text-only verification.
        </Notice>}
        <label className="row" style={{ gap: 8 }}>
          <input type="checkbox" checked={consent} disabled={busy || !configured || !model.trim() || !!key.trim()}
            onChange={(e) => setConsent(e.target.checked)} />
          I authorize this model connection check. If unverified, it sends one cloud request that may use my quota.
        </label>
        <Button variant="primary" disabled={busy || !configured || !model.trim() ||
            model.trim().length > 180 || !consent || !!key.trim()}
          onClick={() => void continueWithModel()}>
          {onReady ? (verified ? 'Start cloud conversation' : 'Verify and start cloud conversation') :
            (verified ? 'Keep verified model' : 'Verify and save model')}
        </Button>
        <p className="t-muted">
          Cloud models currently support answer-only conversations. Task execution, file tools and
          memory access remain disabled until separate runtime permission work is completed.
        </p>
        {notice && <Notice tone="info" icon="info">{notice}</Notice>}
        {error && <Notice tone="danger" icon="alert">{error}</Notice>}
      </div>
    </Panel>
  );
}
