import { useEffect, useRef, useState } from 'react';
import {
  cloudPreviewService,
  type CloudPreviewProviderId,
  type CloudPreviewStatus,
  type CloudCatalogModel,
} from '../services/api';
import { Button, Notice, Panel } from './ui';

/**
 * One provider/model picker reused by onboarding and Settings.
 * Discover model names automatically when an existing key is configured.
 * A user's explicit model selection is the sole trigger for inference:
 * one text check and one harmless function-call check at most when new.
 * No hidden model scans or retries.
 *
 * Neither proof activates task/tool/memory execution.
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
  if (status === 429) return 'Provider quota or capacity reached. This is not proof the model is incompatible. No automatic retry was made.';
  if (status === 409) return 'The selected model or key changed. Select the model again after checking setup.';
  if (status === 422 && detail === 'Model did not demonstrate required tool calling')
    return 'This model can chat, but failed Sythion\'s function-call requirement. Choose another model for full assistant compatibility.';
  if (status === 422)
    return 'This model did not meet Sythion\'s compatibility checks. Choose another model.';
  if (status === 502 && detail === 'Cloud model requires an eligible agentic harness (upstream HTTP 403)')
    return 'This free model is restricted to eligible agentic harnesses. Choose a different model.';
  if (status === 502 && detail === 'Cloud provider rejected the capability request')
    return 'The provider rejected the function-call check. This model is not verified for Sythion tools; try another model or check access.';
  if (status === 502) return 'The provider rejected this model request. Try another model or check account access.';
  if (status === 503) return 'Cloud response is uncertain. No automatic retry was made.';
  return 'The model could not be verified. Review provider access or choose another model.';
}

function validCatalog(result: unknown, provider: CloudPreviewProviderId): result is {
  provider_id: CloudPreviewProviderId;
  models: CloudCatalogModel[];
  truncated: boolean;
  ready_for_execution: false;
} {
  if (!result || typeof result !== 'object') return false;
  const data = result as {
    provider_id?: unknown; models?: unknown; ready_for_execution?: unknown;
  };
  return data.provider_id === provider && data.ready_for_execution === false &&
    Array.isArray(data.models) && data.models.length <= 1000 &&
    data.models.every((m: unknown) => m && typeof m === 'object' &&
      typeof (m as CloudCatalogModel).id === 'string' &&
      (m as CloudCatalogModel).id.length > 0 &&
      typeof (m as CloudCatalogModel).free_variant === 'boolean');
}

function isTextVerified(
  status: CloudPreviewStatus,
  provider: CloudPreviewProviderId,
  modelId: string,
): boolean {
  return status.staged.provider_id === provider &&
    status.staged.model_id === modelId &&
    status.text_probe_verified === true &&
    status.ready_for_execution === false &&
    status.providers.some((p) => p.provider_id === provider && p.credential_configured === true);
}

function isQualified(
  status: CloudPreviewStatus,
  provider: CloudPreviewProviderId,
  modelId: string,
): boolean {
  return isTextVerified(status, provider, modelId) &&
    status.tool_probe_verified === true;
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
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [catalogTruncated, setCatalogTruncated] = useState(false);
  const [freeOnly, setFreeOnly] = useState(true);
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const mounted = useRef(true);
  const catalogGeneration = useRef(0);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  useEffect(() => {
    mounted.current = true;
    // Each catalog effect cancels itself on cleanup; mounted also guards
    // asynchronous selection requests. No generation ref mutation is needed here.
    return () => { mounted.current = false; };
  }, []);

  useEffect(() => {
    let active = true;
    const generation = ++catalogGeneration.current;
    setCatalog([]);
    setModel('');
    setStatus(null);
    setCatalogLoading(true);
    setError('');
    void cloudPreviewService.status().then(async (value) => {
      if (!active || !mounted.current || generation !== catalogGeneration.current) return;
      setStatus(value);
      const configured = value.providers.some(
        (p) => p.provider_id === provider && p.credential_configured === true);
      if (!configured) return;
      const listing = await cloudPreviewService.modelCatalog(provider);
      if (!active || !mounted.current || generation !== catalogGeneration.current) return;
      if (!validCatalog(listing, provider)) throw new Error('Unexpected model listing');
      setCatalog(listing.models);
      setCatalogTruncated(listing.truncated === true);
    }).catch((err: unknown) => {
      if (active && mounted.current && generation === catalogGeneration.current) {
        setError(safeError(err));
      }
    }).finally(() => {
      if (active && mounted.current && generation === catalogGeneration.current) {
        setCatalogLoading(false);
      }
    });
    return () => { active = false; };
  }, [provider]);

  const configured = status?.providers.some(
    (p) => p.provider_id === provider && p.credential_configured === true) === true;
  const verified = !!status && model.length > 0 && !key.trim() && isQualified(status, provider, model);
  const visibleModels = catalog.filter((item) => !freeOnly || item.free_variant);
  const canChoose = configured && !key.trim() && !busy && !catalogLoading;
  const showRetry = !!error && !!model && canChoose &&
    catalog.some((entry) => entry.id === model);

  const chooseProvider = (next: CloudPreviewProviderId) => {
    if (inFlight.current || next === provider) return;
    catalogGeneration.current++;
    setProvider(next);
    setKey('');
    setModel('');
    setCatalog([]);
    setCatalogTruncated(false);
    setError('');
    setNotice('');
  };

  // Save a user-entered key, then discover models without a second Browse click.
  const connect = async () => {
    const value = key.trim();
    if (inFlight.current || !value || catalogLoading) return;
    inFlight.current = true;
    ++catalogGeneration.current;
    setBusy(true); setError(''); setNotice('');
    setCatalog([]); setModel(''); setCatalogTruncated(false);
    try {
      await cloudPreviewService.saveCredential(provider, value);
      if (!mounted.current) return;
      setKey('');
      const current = await cloudPreviewService.status();
      if (!mounted.current) return;
      setStatus(current);
      if (!current.providers.some((p) => p.provider_id === provider && p.credential_configured)) {
        throw new Error('Key was not saved');
      }
      const listing = await cloudPreviewService.modelCatalog(provider);
      if (!mounted.current) return;
      if (!validCatalog(listing, provider)) throw new Error('Unexpected model listing');
      setCatalog(listing.models);
      setCatalogTruncated(listing.truncated === true);
    } catch (err) {
      if (mounted.current) setError(safeError(err));
    } finally {
      inFlight.current = false;
      if (mounted.current) setBusy(false);
    }
  };

  // Called ONLY on explicit model selection, or the visible manual Retry.
  // Free/All changes, mounting and catalog refresh never trigger inference.
  // At most two isolated requests: one text check, one harmless tool-call
  // probe. Tool proof does NOT grant runtime execution permission.
  const checkSelected = async (modelId: string) => {
    if (inFlight.current || !configured || !modelId || key.trim() ||
        !catalog.some((entry) => entry.id === modelId)) return;
    inFlight.current = true;
    setBusy(true); setError(''); setNotice('');
    try {
      const current = await cloudPreviewService.status();
      if (!mounted.current) return;
      if (!current.providers.some((p) => p.provider_id === provider && p.credential_configured)) {
        setStatus(current);
        throw new Error('Credential no longer available');
      }

      const textAlreadyVerified = isTextVerified(current, provider, modelId);
      const selection = { provider_id: provider, model_id: modelId };
      if (!textAlreadyVerified) {
        await cloudPreviewService.stage(selection);
        if (!mounted.current) return;
        const result = await cloudPreviewService.chat(selection, TEST_PROMPT);
        if (!mounted.current) return;
        if (result.provider_id !== provider || result.model_id !== modelId ||
            result.active !== false || result.ready_for_execution !== false ||
            result.persisted !== false || result.task_executed !== false ||
            typeof result.assistant_text !== 'string' || !result.assistant_text.trim()) {
          throw new Error('Unexpected cloud text result');
        }
      }

      // Stage invalidates both previous proofs. If text is still current,
      // a current real tool-call proof can be reused without any new charge.
      if (!textAlreadyVerified || current.tool_probe_verified !== true) {
        const proof = await cloudPreviewService.probe(selection);
        if (!mounted.current) return;
        if (proof.provider_id !== provider || proof.model_id !== modelId ||
            proof.tool_probe_verified !== true || proof.evidence_persisted !== true ||
            proof.active !== false || proof.ready_for_execution !== false) {
          throw new Error('Unexpected function-call proof');
        }
      }

      const latest = await cloudPreviewService.status();
      if (!mounted.current) return;
      if (!isQualified(latest, provider, modelId)) {
        setStatus(latest);
        throw new Error('Model qualification changed');
      }
      setStatus(latest);
      if (onReady) onReady();
      else setNotice('Text and basic function-call checks passed. The cloud assistant remains answer-only until runtime integration is verified.');
    } catch (err) {
      if (mounted.current) setError(safeError(err));
    } finally {
      inFlight.current = false;
      if (mounted.current) setBusy(false);
    }
  };

  const selectModel = (value: string) => {
    if (!canChoose || !visibleModels.some((entry) => entry.id === value)) return;
    setModel(value);
    void checkSelected(value);
  };

  return (
    <Panel title={`Configure ${LABELS[provider]}`} icon="assistant">
      <div className="stack" style={{ gap: 12 }}>
        {!hideProviderPicker && <>
          <label htmlFor="cloud-config-provider">Cloud provider</label>
          <select id="cloud-config-provider" className="input" value={provider} disabled={busy}
            onChange={(e) => chooseProvider(e.target.value as CloudPreviewProviderId)}>
            {PROVIDERS.map((id) => <option key={id} value={id}>{LABELS[id]}</option>)}
          </select>
        </>}

        <label htmlFor="cloud-config-key">API key</label>
        <input id="cloud-config-key" className="input" type="password" autoComplete="off"
          value={key} disabled={busy} onChange={(e) => setKey(e.target.value)}
          placeholder={configured ? 'Saved key available — leave empty to keep it' : 'Paste provider API key'} />
        {!configured && <p className="t-muted">Enter a key to connect. Sythion stores it securely and does not show it again.</p>}
        {key.trim() && <Button disabled={busy || catalogLoading} onClick={() => void connect()}>
          Connect and load models
        </Button>}

        {configured && !key.trim() && <>
          <label htmlFor="cloud-model-filter">Available models</label>
          <select id="cloud-model-filter" className="input" disabled={busy || catalogLoading}
            value={freeOnly ? 'free' : 'all'}
            onChange={(e) => {
              setFreeOnly(e.target.value === 'free');
              setModel('');
              setNotice('');
              setError('');
            }}>
            <option value="free">Free models</option>
            <option value="all">All models</option>
          </select>

          <label htmlFor="cloud-config-catalog">Choose an AI model</label>
          <select id="cloud-config-catalog" className="input" value={model} disabled={!canChoose}
            onChange={(e) => selectModel(e.target.value)}>
            <option value="">Select a model</option>
            {visibleModels.map((entry) =>
              <option key={entry.id} value={entry.id}>{entry.id}</option>)}
          </select>
          {catalogLoading && <p role="status" className="t-muted">Loading available models…</p>}
          {!catalogLoading && visibleModels.length === 0 && <p className="t-muted">
            {catalog.length > 0 ? 'No free models listed. Choose All models to see other options.' :
              'No models found. Review your key or provider availability.'}
          </p>}
          {catalogTruncated && <p className="t-muted">
            The provider returned more models than Sythion can display. This list may be incomplete.
          </p>}
          <p className="t-muted">
            Selecting a model checks text conversation and basic function calling automatically
            (up to two provider requests if not yet verified). This may use your quota;
            no real tools run and failed checks are never retried automatically.
          </p>
          {busy && <p role="status">Checking conversation and function-call compatibility…</p>}
          {verified && !busy && <Notice tone="info" icon="check">
            Basic text and function-call checks passed. Task execution is not enabled.
          </Notice>}
          {showRetry && <Button disabled={busy} onClick={() => void checkSelected(model)}>
            Retry selected model
          </Button>}
        </>}

        <p className="t-muted">
          Sythion requires both text and function-call compatibility for an assistant model.
          These checks do not enable tasks, file access or memory writes.
        </p>
        {notice && <Notice tone="info" icon="info">{notice}</Notice>}
        {error && <Notice tone="danger" icon="alert">{error}</Notice>}
      </div>
    </Panel>
  );
}
