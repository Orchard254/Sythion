import { useEffect, useState } from 'react';
import {
  cloudPreviewService,
  type CloudPreviewProviderId,
  type CloudPreviewSelection,
  type CloudPreviewStatus,
  type CloudCatalogModel,
} from '../services/api';
import { Button, Notice, Panel } from '../components/ui';

const PROVIDERS: { id: CloudPreviewProviderId; label: string }[] = [
  { id: 'openrouter', label: 'OpenRouter' },
  { id: 'nvidia', label: 'NVIDIA NIM' },
  { id: 'groq', label: 'Groq' },
];

function responseStatus(error: unknown): number | null {
  const response = error && typeof error === 'object' && 'response' in error
    ? error.response : null;
  return response && typeof response === 'object' && 'status' in response &&
    typeof response.status === 'number' ? response.status : null;
}

/** Do not reflect unknown provider text, upstream errors or credentials. */
function previewFailure(error: unknown): string {
  const status = responseStatus(error);
  if (status === 400) return 'Sythion rejected the provider or model selection. Verify the exact provider and model ID.';
  if (status === 403) return 'Sythion blocked the local request (403). Check the desktop connection and trusted app origin.';
  if (status === 404) return 'This cloud setup endpoint is not available in the connected Sythion backend.';
  if (status === 500) return 'Sythion could not complete the local operation (500). Check sidecar.err.log for the backend error; do not share API keys.';
  if (status === 429) return 'Cloud rate limit or capacity reached. Do not retry repeatedly; check your provider quota.';
  if (status === 401) return 'Sythion rejected this request. Recheck the local app connection.';
  // A 409 is reconciled internally by the caller (re-read status / re-stage).
  // This is only the fallback when that read-only reconciliation also failed.
  if (status === 409) return 'The staged cloud candidate or its credential changed while it was being checked, and the current setup could not be re-read.';
  if (status === 422) return 'The model did not demonstrate the required function call. It remains inactive.';
  const safeDetail = error && typeof error === 'object' &&
    'response' in error && error.response && typeof error.response === 'object' &&
    'data' in error.response && error.response.data &&
    typeof error.response.data === 'object' &&
    'detail' in error.response.data && typeof error.response.data.detail === 'string'
      ? error.response.data.detail : '';
  if (status === 502) {
    const mapped: Record<string, string> = {
      'Stored OpenRouter key rejected (upstream HTTP 401)': 'The key saved in Sythion was rejected by OpenRouter (401). Replace it with a valid key.',
      'Stored OpenRouter key lacks access (upstream HTTP 403)': 'OpenRouter denied access to the key saved in Sythion (403). Check key permissions.',
      'Cloud provider rejected stored credential (upstream HTTP 401)': 'OpenRouter rejected the stored credential (401); verify the exact key in Sythion.',
      'Cloud provider denied access (upstream HTTP 403)': 'The cloud provider denied model access (403). Verify account and model access.',
      'Cloud model requires an eligible agentic harness (upstream HTTP 403)': 'OpenRouter restricts this free model to eligible agentic harnesses. Sythion cannot access it here; select another model.',
      'Cloud provider rejected text request parameters (upstream HTTP 400)': 'The cloud provider rejected text request parameters (400). The request format may need adjustment.',
      'Cloud provider requires payment or credits (upstream HTTP 402)': 'The provider requires account credit or payment (402), even if the model is listed as free.',
      'Cloud model or endpoint unavailable (upstream HTTP 404)': 'The provider cannot access this model or endpoint (404). Check the model ID.',
      'Cloud provider rejected text request format (upstream HTTP 422)': 'The provider rejected the text request format (422).',
    };
    return mapped[safeDetail] ?? 'The provider rejected the request. Check key access, model capabilities and provider status.';
  }
  if (status === 503) return 'The network or provider outcome is uncertain. Check status before requesting another probe.';
  return 'That action could not be completed. Check the local connection and configuration.';
}

export function CloudProviderPreview({
  onEnterConversationOnly,
}: {
  onEnterConversationOnly?: () => void;
} = {}) {
  const [provider, setProvider] = useState<CloudPreviewProviderId>('openrouter');
  const [model, setModel] = useState('');
  const [key, setKey] = useState('');
  const [catalogProvider, setCatalogProvider] = useState<CloudPreviewProviderId | null>(null);
  const [catalogModels, setCatalogModels] = useState<CloudCatalogModel[]>([]);
  const [catalogTruncated, setCatalogTruncated] = useState(false);
  const [catalogSearch, setCatalogSearch] = useState('');
  const [catalogFreeOnly, setCatalogFreeOnly] = useState(false);
  const [status, setStatus] = useState<CloudPreviewStatus | null>(null);
  const [staged, setStaged] = useState<CloudPreviewSelection | null>(null);
  const [consent, setConsent] = useState(false);
  const [probePassed, setProbePassed] = useState(false);
  const [chatText, setChatText] = useState('');
  const [chatConsent, setChatConsent] = useState(false);
  const [chatAnswer, setChatAnswer] = useState<string | null>(null);
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
    setCatalogProvider(null);
    setCatalogModels([]);
    setCatalogTruncated(false);
    setCatalogSearch('');
    setCatalogFreeOnly(false);
    setConsent(false);
    setProbePassed(false);
    setStatus((old) => old ? { ...old, text_probe_verified: false } : null);
    setChatText('');
    setChatConsent(false);
    setChatAnswer(null);
    setNotice('');
    setError('');
  };

  const setModelChoice = (value: string) => {
    setModel(value);
    setConsent(false);
    setProbePassed(false);
    setChatConsent(false);
    setChatAnswer(null);
    setStatus((old) => old ? { ...old, text_probe_verified: false } : null);
    setNotice('');
    setError('');
  };

  /** A 409 means the staged candidate or its proof changed underneath us.
   * Re-read the read-only status and restore the exact candidate internally;
   * never re-run a quota-using probe or chat. Returns false if even the
   * read-only reconciliation was unavailable. */
  const reconcileAfterConflict = async (): Promise<boolean> => {
    try {
      const latest = await cloudPreviewService.status();
      setStatus(latest);
      const candidate = latest.staged;
      if (candidate.provider_id && PROVIDERS.some((p) => p.id === candidate.provider_id) &&
          typeof candidate.model_id === 'string' && candidate.model_id) {
        setProvider(candidate.provider_id);
        setModel(candidate.model_id);
        setStaged({ provider_id: candidate.provider_id, model_id: candidate.model_id });
      }
      const intended = { provider_id: provider, model_id: model.trim() };
      if (intended.model_id && !(latest.staged.provider_id === intended.provider_id &&
          latest.staged.model_id === intended.model_id)) {
        await cloudPreviewService.stage(intended);
        setStaged(intended);
      }
      // One-use authorizations cannot survive a changed candidate.
      setConsent(false);
      setProbePassed(false);
      setChatConsent(false);
      setChatAnswer(null);
      return true;
    } catch {
      return false;
    }
  };

  const perform = async (action: () => Promise<void>) => {
    setError('');
    setNotice('');
    setBusy(true);
    try {
      await action();
    } catch (caught) {
      if (responseStatus(caught) === 409) {
        const reconciled = await reconcileAfterConflict();
        setError(reconciled
          ? 'The staged cloud candidate changed while it was being checked. I re-read the current status and restored the candidate; nothing was sent to the provider.'
          : previewFailure(caught));
      } else {
        setError(previewFailure(caught));
      }
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
  const canChat = credentialConfigured && stagedMatches &&
    chatConsent && chatText.trim().length > 0 &&
    chatText.trim().length <= 1200 && !busy;

  const loadModelCatalog = () => perform(async () => {
    const listing = await cloudPreviewService.modelCatalog(provider);
    if (listing.provider_id !== provider || listing.ready_for_execution !== false ||
        !Array.isArray(listing.models) || listing.models.length > 1000) {
      throw new Error('Unexpected catalog response');
    }
    setCatalogProvider(provider);
    setCatalogModels(listing.models);
    setCatalogTruncated(listing.truncated === true);
    setNotice('Model IDs retrieved. Listing does not prove provider access, free quota, or tool capability.');
  });

  const catalogCurrent = catalogProvider === provider;
  const filteredCatalog = catalogCurrent ? catalogModels.filter((entry) =>
    (!catalogFreeOnly || entry.free_variant) &&
    entry.id.toLowerCase().includes(catalogSearch.trim().toLowerCase())
  ) : [];

  const verifyStoredKey = () => perform(async () => {
    const result = await cloudPreviewService.verifyStoredKey();
    if (result.provider_id !== 'openrouter' || result.credential_verified !== true ||
        result.ready_for_execution !== false || result.active !== false) {
      throw new Error('Unexpected key verification response');
    }
    setNotice('OpenRouter authenticated the key saved in this desktop. Model access and inference are not yet verified.');
  });

  const saveKey = () => perform(async () => {
    const value = key.trim();
    if (!value) return;
    await cloudPreviewService.saveCredential(provider, value);
    setKey('');
    setConsent(false);
    setProbePassed(false);
    setChatConsent(false);
    setChatAnswer(null);
    setStatus((old) => old ? {
      ...old,
      tool_probe_verified: false,
      text_probe_verified: false,
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
    setChatConsent(false);
    setChatAnswer(null);
    setStatus((old) => old ? { ...old, tool_probe_verified: false, text_probe_verified: false } : null);
    setNotice('Model candidate saved for testing. It is not active.');
  });

  const enterConversationOnly = () => perform(async () => {
    // Owner requests the independent answer-only lane explicitly. Re-fetch
    // current proof instead of trusting one old in-memory UI boolean.
    const latest = await cloudPreviewService.status();
    const selectionCurrent = latest.staged.provider_id === selection.provider_id &&
      latest.staged.model_id === selection.model_id;
    const storedKey = latest.providers.some((p) =>
      p.provider_id === selection.provider_id && p.credential_configured === true);
    if (latest.text_probe_verified !== true || !selectionCurrent || !storedKey ||
        latest.ready_for_execution !== false || !onEnterConversationOnly) {
      throw new Error('Cloud conversation verification is not current');
    }
    onEnterConversationOnly();
  });

  const probe = () => perform(async () => {
    if (!canProbe) return;
    setConsent(false);
    setProbePassed(false);
    const response = await cloudPreviewService.probe(selection);
    if (response.tool_probe_verified !== true || response.active !== false ||
        response.ready_for_execution !== false || response.evidence_persisted !== true ||
        response.model_id !== selection.model_id || response.provider_id !== selection.provider_id) {
      throw new Error('Unexpected capability evidence');
    }
    setProbePassed(true);
    setStatus((old) => old ? { ...old, tool_probe_verified: true } : null);
    setNotice('One function-call test passed. The evidence is recorded for up to one hour, but the provider remains inactive.');
  });

  const sendPreviewMessage = () => perform(async () => {
    if (!canChat) return;
    const text = chatText.trim();
    setChatConsent(false); // Explicit authorization is one-use, even on error.
    setChatAnswer(null);
    const response = await cloudPreviewService.chat(selection, text);
    if (response.active !== false || response.ready_for_execution !== false ||
        response.persisted !== false || response.task_executed !== false ||
        response.provider_id !== selection.provider_id || response.model_id !== selection.model_id ||
        typeof response.assistant_text !== 'string' || !response.assistant_text.trim()) {
      throw new Error('Unexpected text preview contract');
    }
    setChatAnswer(response.assistant_text);
    setStatus((old) => old ? { ...old, text_probe_verified: true } : null);
    setNotice('Cloud text response received; conversation capability is recorded for up to one hour. No Sythion action or conversation was saved.');
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
        {provider === 'openrouter' && (
          <Button disabled={busy || !credentialConfigured}
            onClick={() => void verifyStoredKey()}>
            Verify stored OpenRouter key (one network request)
          </Button>
        )}
        <Button disabled={busy || !credentialConfigured}
          onClick={() => void loadModelCatalog()}>Fetch model IDs</Button>
        {catalogCurrent && (
          <div className="stack" style={{ gap: 8 }}>
            <label htmlFor="cloud-preview-model-search">Search model IDs</label>
            <input id="cloud-preview-model-search" className="input" type="search"
              value={catalogSearch} disabled={busy} placeholder="Search provider models"
              onChange={(e) => setCatalogSearch(e.target.value)} />
            <label className="row" style={{ gap: 8 }}>
              <input type="checkbox" checked={catalogFreeOnly} disabled={busy}
                onChange={(e) => setCatalogFreeOnly(e.target.checked)} />
              Show :free variants only (quota and access not guaranteed)
            </label>
            <label htmlFor="cloud-preview-catalog-model">
              Model IDs ({filteredCatalog.length} matching; showing first 80)
            </label>
            <select id="cloud-preview-catalog-model" className="input" disabled={busy}
              value="" onChange={(e) => { if (e.target.value) setModelChoice(e.target.value); }}>
              <option value="">Choose a model ID to fill the field below</option>
              {filteredCatalog.slice(0, 80).map((entry) => (
                <option key={entry.id} value={entry.id}>
                  {entry.id}{entry.tools_listed ? ' (tools listed)' : ''}
                </option>
              ))}
            </select>
            {catalogTruncated && <p className="t-muted">Catalog capped at 1000 IDs; manual entry remains available.</p>}
            <p className="t-muted">Model discovery is read-only; capability and billing require separate verification.</p>
          </div>
        )}
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
        {!probePassed && stagedMatches && status?.tool_probe_verified === true && (
          <Notice tone="info" icon="check">
            A recent staged tool-call test is recorded for this exact model and key. It does not enable tasks.
          </Notice>
        )}
        {notice && <Notice tone="info" icon="info">{notice}</Notice>}
        {error && <Notice tone="danger" icon="alert">{error}</Notice>}
        <div className="stack" style={{ gap: 8 }}>
          <label htmlFor="cloud-preview-chat">Try a text-only cloud reply</label>
          {stagedMatches && status?.text_probe_verified === true && (
            <Notice tone="info" icon="check">
              A recent plain-text response is verified for this exact staged model and key.
              This does not enable the normal assistant or autonomous execution.
            </Notice>
          )}
          {onEnterConversationOnly && credentialConfigured && stagedMatches &&
              status?.text_probe_verified === true && (
            <Button variant="primary" disabled={busy}
              onClick={() => void enterConversationOnly()}>
              Enter conversation-only cloud mode
            </Button>
          )}
          <p className="t-muted">
            Send one short message directly to the staged provider. This does not need
            function-calling verification. No files, memories, tools, tasks, or conversation
            history are sent or saved.
          </p>
          <textarea id="cloud-preview-chat" className="input" rows={3} maxLength={1200}
            value={chatText} disabled={busy} placeholder="Hello! What can you help me understand?"
            onChange={(e) => { setChatText(e.target.value); setChatConsent(false); setChatAnswer(null); }} />
          <label className="row" style={{ gap: 8 }}>
            <input type="checkbox" checked={chatConsent}
              disabled={busy || !credentialConfigured || !stagedMatches || !chatText.trim()}
              onChange={(e) => setChatConsent(e.target.checked)} />
            I authorize one text-only cloud request; it may use my provider quota.
          </label>
          <Button disabled={!canChat} onClick={() => void sendPreviewMessage()}>
            Send one test message
          </Button>
          {chatAnswer !== null && <div className="stack" style={{ gap: 4 }}>
            <strong>Unverified model reply (preview only)</strong>
            <p aria-label="Cloud preview reply" style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
              {chatAnswer}
            </p>
            <p className="t-muted">
              This text was generated by a model. Sythion did not execute or save any actions.
            </p>
          </div>}
        </div>
        <p className="t-muted">
          This is a development preview. Recent model capability evidence expires automatically,
          and it is never execution approval. Ollama's existing onboarding requirements still apply to Start conversation.
        </p>
      </div>
    </Panel>
  );
}
