import { useEffect, useRef, useState } from 'react';
import {
  activeInferenceService, cloudPreviewService,
  type CloudCatalogModel, type CloudPreviewProviderId,
} from '../services/api';
import { Button, Notice } from './ui';

/** Provider setup embedded in the *existing* Sythion onboarding wizard.
 * No new assistant, navigation lane, or task execution is introduced.
 */
export function ActiveCloudSetup({
  provider, phase, onConnected, onVerified,
}: {
  provider: CloudPreviewProviderId;
  phase: 'connect' | 'model';
  onConnected?: () => void;
  onVerified?: (modelId: string) => void;
}) {
  const [hasKey, setHasKey] = useState(false);
  const [key, setKey] = useState('');
  const [models, setModels] = useState<CloudCatalogModel[]>([]);
  const [model, setModel] = useState('');
  const [freeOnly, setFreeOnly] = useState(true);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [verified, setVerified] = useState(false);
  const [notice, setNotice] = useState('');
  const inFlight = useRef(false);
  const generation = useRef(0);
  const mounted = useRef(false);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  useEffect(() => {
    let active = true;
    const current = ++generation.current;
    setLoading(true);
    setHasKey(false);
    setModels([]);
    setModel('');
    setVerified(false);
    setError('');
    void cloudPreviewService.status().then(async (status) => {
      if (!active || current !== generation.current) return;
      const found = status.providers.some((entry) =>
        entry.provider_id === provider && entry.credential_configured === true);
      setHasKey(found);
      if (phase !== 'model' || !found) return;
      // Catalog is read-only. No model test happens on mount or filter change.
      const listing = await cloudPreviewService.modelCatalog(provider);
      if (!active || current !== generation.current) return;
      if (listing.provider_id !== provider || listing.ready_for_execution !== false ||
          !Array.isArray(listing.models)) throw new Error('Invalid provider catalog');
      setModels(listing.models);
    }).catch(() => {
      if (active && current === generation.current) {
        setError('Could not load the selected provider. Check its key and connection.');
      }
    }).finally(() => {
      if (active && current === generation.current) setLoading(false);
    });
    return () => { active = false; };
  }, [provider, phase]);

  const submitKey = async () => {
    if (inFlight.current || !key.trim() || loading) return;
    inFlight.current = true; setBusy(true); setError('');
    const current = generation.current;
    try {
      await cloudPreviewService.saveCredential(provider, key.trim());
      if (!mounted.current || generation.current !== current) return;
      setKey('');
      const status = await cloudPreviewService.status();
      if (!mounted.current || generation.current !== current) return;
      if (!status.providers.some((entry) =>
        entry.provider_id === provider && entry.credential_configured === true)) {
        throw new Error('Credential was not saved');
      }
      setHasKey(true);
      onConnected?.();
    } catch {
      if (mounted.current && generation.current === current) setError('Could not save the provider key. Check your account and retry.');
    } finally {
      inFlight.current = false;
      if (mounted.current && generation.current === current) setBusy(false);
    }
  };

  const verifyModel = async (id: string) => {
    if (inFlight.current || !id || !hasKey || !models.some((m) => m.id === id)) return;
    inFlight.current = true;
    const current = generation.current;
    setBusy(true); setError(''); setNotice(''); setVerified(false); setModel(id);
    try {
      const selection = { provider_id: provider, model_id: id };
      const status = await cloudPreviewService.status();
      if (!mounted.current || generation.current !== current) return;
      const proofCurrent = status.staged.provider_id === provider &&
        status.staged.model_id === id &&
        status.text_probe_verified === true &&
        status.tool_probe_verified === true;
      if (!proofCurrent) {
        await cloudPreviewService.stage(selection);
        if (!mounted.current || generation.current !== current) return;
        const chat = await cloudPreviewService.chat(
          selection, 'Say hello in one short plain-text sentence.');
        if (!mounted.current || generation.current !== current) return;
        if (chat.provider_id !== provider || chat.model_id !== id ||
            chat.active !== false || chat.persisted !== false ||
            chat.task_executed !== false || !chat.assistant_text?.trim()) {
          throw new Error('Invalid text qualification');
        }
        const probe = await cloudPreviewService.probe(selection);
        if (!mounted.current || generation.current !== current) return;
        if (probe.provider_id !== provider || probe.model_id !== id ||
            probe.tool_probe_verified !== true || probe.active !== false ||
            probe.evidence_persisted !== true) {
          throw new Error('Invalid function-call qualification');
        }
      }
      // The ordinary text + synthetic tool tests are not enough: normal
      // Assistant turns require an exact structured JSON contract.
      // Verify its real prompt/validator BEFORE changing the active model.
      const contract = await cloudPreviewService.assistantContract(selection);
      if (!mounted.current || generation.current !== current) return;
      if (contract.provider_id !== provider || contract.model_id !== id ||
          contract.assistant_contract_verified !== true ||
          contract.evidence_persisted !== true ||
          contract.task_executed !== false || contract.active !== false) {
        throw new Error('Invalid Assistant contract evidence');
      }
      // Activation is now backed by THREE exact credential-bound proofs.
      const activated = await activeInferenceService.activate(selection);
      if (!mounted.current || generation.current !== current) return;
      if (activated.provider_id !== provider || activated.model_id !== id ||
          activated.permissions_unchanged !== true) {
        throw new Error('Active provider did not match selection');
      }
      setVerified(true);
      setNotice('Model selected for Sythion. Existing permissions are unchanged.');
      onVerified?.(id);
    } catch (cause) {
      if (!mounted.current || generation.current !== current) return;
      const response = cause && typeof cause === 'object' && 'response' in cause
        ? cause.response : null;
      const status = response && typeof response === 'object' && 'status' in response
        ? response.status : null;
      const body = response && typeof response === 'object' && 'data' in response
        ? response.data : null;
      const detail = body && typeof body === 'object' && 'detail' in body ? body.detail : null;
      if (status === 429) {
        setError('The provider reached its quota or capacity. This does not prove model incompatibility.');
      } else if (status === 502 &&
                 detail === 'Cloud model requires an eligible agentic harness (upstream HTTP 403)') {
        setError('This model requires an eligible agentic application. Choose a different model.');
      } else if (status === 422) {
        setError('This model did not demonstrate the structured Assistant format or function calling Sythion requires. Choose another model.');
      } else if (status === 409) {
        setError('Model verification changed or Sythion has unfinished tasks. Review your setup.');
      } else {
        setError('Sythion could not verify this model for text and function calling. Choose another model or check provider access.');
      }
    } finally {
      inFlight.current = false;
      if (mounted.current && generation.current === current) setBusy(false);
    }
  };

  const visibleModels = models.filter((entry) => !freeOnly || entry.free_variant);

  if (phase === 'connect') return (
    <div className="stack" style={{ gap: 12 }}>
      {loading && <p role="status">Checking saved provider credentials…</p>}
      {hasKey && <Notice tone="info" icon="check">
        API key already saved. Use it or enter a replacement.
      </Notice>}
      <label htmlFor="setup-cloud-key">{provider} API key</label>
      <input id="setup-cloud-key" type="password" className="input" autoComplete="off"
        value={key} disabled={busy || loading} placeholder={hasKey ? 'Leave empty to keep saved key' : 'Paste API key'}
        onChange={(event) => setKey(event.target.value)} />
      <div className="row" style={{ gap: 8 }}>
        {key.trim() && <Button variant="primary" disabled={busy || loading}
          onClick={() => void submitKey()}>Connect and save key</Button>}
        {hasKey && !key.trim() && <Button variant="primary" disabled={busy || loading}
          onClick={onConnected}>Use configured key</Button>}
      </div>
      {error && <Notice tone="danger" icon="alert">{error}</Notice>}
    </div>
  );

  return (
    <div className="stack" style={{ gap: 12 }}>
      {loading && <p role="status">Loading available models…</p>}
      <label htmlFor="setup-cloud-filter">Available models</label>
      <select id="setup-cloud-filter" className="input" value={freeOnly ? 'free' : 'all'}
        disabled={busy || loading} onChange={(event) => {
          setFreeOnly(event.target.value === 'free');
          setModel(''); setVerified(false); setNotice(''); setError('');
        }}>
        <option value="free">Free models</option>
        <option value="all">All models</option>
      </select>
      <label htmlFor="setup-cloud-model">Choose a model</label>
      <select id="setup-cloud-model" className="input" disabled={busy || loading || !hasKey}
        value={model} onChange={(event) => void verifyModel(event.target.value)}>
        <option value="">Select a model</option>
        {visibleModels.map((entry) => <option key={entry.id} value={entry.id}>{entry.id}</option>)}
      </select>
      {!loading && visibleModels.length === 0 &&
        <p>No matching models listed. Try All models or change provider.</p>}
      <p className="t-muted">Selection verifies text, a harmless function call
        and Sythion's actual Assistant format (up to three provider requests, which may consume quota).
        It does not grant tools or permissions and never retries automatically.</p>
      {busy && <p role="status">Checking the selected model…</p>}
      {verified && <Notice tone="info" icon="check">{notice}</Notice>}
      {error && <Notice tone="danger" icon="alert">{error}
        <Button disabled={busy} onClick={() => void verifyModel(model)}>Retry selected model</Button>
      </Notice>}
    </div>
  );
}
