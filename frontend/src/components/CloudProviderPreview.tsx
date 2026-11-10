import { useEffect, useState } from 'react';
import {
  cloudPreviewService,
  type CloudPreviewProviderId,
  type CloudPreviewSelection,
  type CloudPreviewStatus,
} from '../services/api';
import { Button, Notice, Panel } from '../components/ui';

const PROVIDERS: { id: CloudPreviewProviderId; label: string }[] = [
  { id: 'openrouter', label: 'OpenRouter' },
  { id: 'nvidia', label: 'NVIDIA NIM' },
  { id: 'groq', label: 'Groq' },
];

/** Do not reflect unknown provider text, upstream errors or credentials. */
function previewFailure(error: unknown): string {
  const status = error && typeof error === 'object' &&
    'response' in error && error.response && typeof error.response === 'object' &&
    'status' in error.response ? error.response.status : null;
  if (status === 429) return 'Cloud rate limit or capacity reached. Do not retry repeatedly; check your provider quota.';
  if (status === 401) return 'Sythion rejected this request. Recheck the local app connection.';
  if (status === 409) return 'The candidate or credential is not ready. Save the key, stage this exact model and try again.';
  if (status === 422) return 'The model did not demonstrate the required function call. It remains inactive.';
  if (status === 502) return 'The provider rejected the request. Check key access, model capabilities and provider status.';
  if (status === 503) return 'The network or provider outcome is uncertain. Check status before requesting another probe.';
  return 'That action could not be completed. Check the local connection and configuration.';
}

export function CloudProviderPreview() {
  const [provider, setProvider] = useState<CloudPreviewProviderId>('openrouter');
  const [model, setModel] = useState('');
  const [key, setKey] = useState('');
  const [status, setStatus] = useState<CloudPreviewStatus | null>(null);
  const [staged, setStaged] = useState<CloudPreviewSelection | null>(null);
  const [consent, setConsent] = useState(false);
  const [probePassed, setProbePassed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  useEffect(() => {
    let cancelled = false;
    void cloudPreviewService.status().then((value) => {
      if (cancelled) return;
      setStatus(value);
      const selected = value.staged;
      if (selected?.provider_id && PROVIDERS.some((p) => p.id === selected.provider_id) &&
          typeof selected.model_id === 'string') {
        setProvider(selected.provider_id);
        setModel(selected.model_id);
        setStaged({ provider_id: selected.provider_id, model_id: selected.model_id });
      }
    }).catch(() => {
      if (!cancelled) setError('Could not load cloud provider choices from Sythion.');
    });
    return () => { cancelled = true; };
  }, []);

  const setProviderChoice = (value: CloudPreviewProviderId) => {
    setProvider(value);
    setModel('');
    setKey('');
    setConsent(false);
    setProbePassed(false);
    setNotice('');
    setError('');
  };

  const setModelChoice = (value: string) => {
    setModel(value);
    setConsent(false);
    setProbePassed(false);
    setNotice('');
    setError('');
  };

  const perform = async (action: () => Promise<void>) => {
    setError('');
    setNotice('');
    setBusy(true);
    try {
      await action();
    } catch (caught) {
      setError(previewFailure(caught));
    } finally {
      setBusy(false);
    }
  };

  const credentialConfigured = status?.providers.some(
    (p) => p.provider_id === provider && p.credential_configured === true
  ) === true;
  const selection = { provider_id: provider, model_id: model.trim() };
  const stagedMatches = staged?.provider_id === selection.provider_id &&
    staged?.model_id === selection.model_id;
  const canProbe = credentialConfigured && stagedMatches && consent && !busy;

  const saveKey = () => perform(async () => {
    const value = key.trim();
    if (!value) return;
    await cloudPreviewService.saveCredential(provider, value);
    setKey('');
    setConsent(false);
    setProbePassed(false);
    setStatus((old) => old ? {
      ...old,
      providers: old.providers.map((p) => p.provider_id === provider
        ? { ...p, credential_configured: true } : p),
    } : null);
    setNotice('Key saved to this Sythion backend’s secret store. It has not yet been verified by the provider.');
  });

  const stage = () => perform(async () => {
    if (!selection.model_id) return;
    await cloudPreviewService.stage(selection);
    setStaged(selection);
    setConsent(false);
    setProbePassed(false);
    setNotice('Model candidate saved for testing. It is not active.');
  });

  const probe = () => perform(async () => {
    if (!canProbe) return;
    setConsent(false);
    setProbePassed(false);
    const response = await cloudPreviewService.probe(selection);
    if (response.tool_probe_verified !== true || response.active !== false ||
        response.ready_for_execution !== false || response.evidence_persisted !== false ||
        response.model_id !== selection.model_id || response.provider_id !== selection.provider_id) {
      throw new Error('Unexpected capability evidence');
    }
    setProbePassed(true);
    setNotice('One function-call test passed. This does not activate the model or enable task execution.');
  });

  return (
    <Panel title="Alternative cloud providers — preview" icon="info">
      <div className="stack" style={{ gap: 12 }}>
        <p className="t-muted">
          Prepare OpenRouter, NVIDIA or Groq while Ollama Cloud is unavailable.
          These providers are not yet connected to Synth's active conversation or task engine.
        </p>
        <label htmlFor="cloud-preview-provider">Cloud provider</label>
        <select id="cloud-preview-provider" className="input" disabled={busy}
          value={provider} onChange={(e) => setProviderChoice(e.target.value as CloudPreviewProviderId)}>
          {PROVIDERS.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
        </select>
        <label htmlFor="cloud-preview-key">Provider API key (write-only)</label>
        <input id="cloud-preview-key" className="input" type="password" autoComplete="off"
          value={key} disabled={busy} placeholder="Paste the key"
          onChange={(e) => setKey(e.target.value)} />
        <p className="t-muted">
          {credentialConfigured ? 'A key is stored for this provider.' : 'No key is stored for this provider.'}
          {' '}Saving a key does not prove authentication or provide free-model quota.
        </p>
        <Button disabled={busy || !key.trim()} onClick={() => void saveKey()}>Save provider key</Button>
        <label htmlFor="cloud-preview-model">Exact model ID</label>
        <input id="cloud-preview-model" className="input" type="text" autoComplete="off"
          value={model} disabled={busy} placeholder="Provider/model-name:free"
          onChange={(e) => setModelChoice(e.target.value)} />
        <Button disabled={busy || !selection.model_id} onClick={() => void stage()}>Stage model candidate</Button>
        <p className="t-muted">
          {stagedMatches ? 'This exact provider and model are staged.' : 'Stage this provider/model before testing.'}
        </p>
        <label className="row" style={{ gap: 8 }}>
          <input type="checkbox" checked={consent} disabled={busy || !credentialConfigured || !stagedMatches}
            onChange={(e) => { setConsent(e.target.checked); setProbePassed(false); }} />
          I authorize one cloud model function-call test; it may use my provider quota.
        </label>
        <Button disabled={!canProbe} onClick={() => void probe()}>Test cloud function calling</Button>
        {probePassed && <Notice tone="info" icon="check">
          This model responded correctly to one synthetic function-call probe, but is not authorized for tasks.
        </Notice>}
        {notice && <Notice tone="info" icon="info">{notice}</Notice>}
        {error && <Notice tone="danger" icon="alert">{error}</Notice>}
        <p className="t-muted">
          This is a development preview. Model checks are not saved as execution approval.
          Ollama's existing onboarding requirements still apply to Start conversation.
        </p>
      </div>
    </Panel>
  );
}
