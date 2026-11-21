import { useEffect, useRef, useState } from 'react';
import {
  activeInferenceService, cloudPreviewService, CLOUD_PROVIDER_LABELS,
  type CloudCatalogModel, type CloudPreviewProviderId,
} from '../services/api';
import { GENERIC_MODEL_TEST_FAILURE, modelTestFailureMessage } from '../lib/modelTestFailure';
import { Button, Notice } from './ui';

/** Saving a key is a local vault write, not a billable provider call, so a
 * brief transport failure is safe to retry a bounded number of times. A 4xx is
 * a genuine refusal and is reported immediately. */
const SAVE_ATTEMPTS = 3;

/** 502/503 are transient, non-billable backend failures. Re-check internally
 * a bounded number of times instead of handing the owner a button. */
const TRANSIENT_RECHECK_ATTEMPTS = 2; // 3 total attempts

function responseStatus(error: unknown): number | null {
  const response = error && typeof error === 'object' && 'response' in error
    ? error.response : null;
  return response && typeof response === 'object' && 'status' in response &&
    typeof response.status === 'number' ? response.status : null;
}

/** Only a genuine model/credential/quota decision keeps a manual recheck.
 * 502/503 are auto-retried; a 4xx (401/422/429/409…) or an unknown transport
 * failure needs an explicit owner decision and must not be retried silently —
 * re-running those can bill quota or needs a different key/model. */
function needsOwnerRecheck(status: number | null): boolean {
  return status === null || (status >= 400 && status < 500);
}

/** Provider setup embedded in the *existing* Sythion onboarding wizard.
 * No new assistant, navigation lane, or task execution is introduced.
 */
export function ActiveCloudSetup({
  provider, phase, onConnected, onVerified, quickModelId, autoAdvance = true,
}: {
  provider: CloudPreviewProviderId;
  phase: 'connect' | 'model' | 'recommended';
  onConnected?: () => void;
  onVerified?: (modelId: string, pending?: boolean) => void;
  /** When present, `recommended` phase verifies+activates exactly this model
   * through the SAME verifyModel() proof sequence as the manual picker. */
  quickModelId?: string;
  /** First arrival reuses a saved key automatically. When the owner navigates
   * back to Connect to change provider, the saved-key action is shown again so
   * that genuine decision remains reachable. */
  autoAdvance?: boolean;
}) {
  const [hasKey, setHasKey] = useState(false);
  const [key, setKey] = useState('');
  const [models, setModels] = useState<CloudCatalogModel[]>([]);
  const [model, setModel] = useState('');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [verified, setVerified] = useState(false);
  const [notice, setNotice] = useState('');
  // Honest, stage-accurate progress for the one-click recommended path.
  const [progress, setProgress] = useState('');
  // A recheck is offered only when the outcome depends on a real model or
  // credential decision. Transient 502/503 failures are retried internally.
  const [canRecheck, setCanRecheck] = useState(false);
  const inFlight = useRef(false);
  const generation = useRef(0);
  const mounted = useRef(false);
  const autoStarted = useRef(false);
  const autoConnected = useRef(false);

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
    setCanRecheck(false);
    setProgress('');
    setNotice('');
    autoStarted.current = false;
    autoConnected.current = false;
    void cloudPreviewService.status().then(async (status) => {
      if (!active || current !== generation.current) return;
      const found = status.providers.some((entry) =>
        entry.provider_id === provider && entry.credential_configured === true);
      setHasKey(found);
      if (phase === 'recommended') {
        // The recommended model is known-good by construction, so no catalog
        // read is needed. Seed the model list so verifyModel()'s membership
        // guard is satisfied by the exact same path the manual picker uses.
        if (found && quickModelId) {
          setModels([{ id: quickModelId, free_variant: true, tools_listed: true }]);
        }
        return;
      }
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
  }, [provider, phase, quickModelId]);

  // A saved, verified key needs no second confirmation click. Advance as soon
  // as the backend confirms credential_configured === true for this provider.
  useEffect(() => {
    if (!autoAdvance || phase !== 'connect' || loading || !hasKey || autoConnected.current) return;
    autoConnected.current = true;
    onConnected?.();
  }, [autoAdvance, phase, loading, hasKey, onConnected]);

  const submitKey = async () => {
    if (inFlight.current || !key.trim() || loading) return;
    inFlight.current = true; setBusy(true); setError('');
    const current = generation.current;
    try {
      let saved = false;
      let rejected = false;
      for (let attempt = 0; attempt < SAVE_ATTEMPTS; attempt++) {
        try {
          await cloudPreviewService.saveCredential(provider, key.trim());
          saved = true;
          break;
        } catch (err) {
          const status = responseStatus(err);
          if (status !== null && status >= 400 && status < 500) {
            rejected = true; // A genuine refusal; retrying cannot fix it.
            break;
          }
        }
      }
      if (!mounted.current || generation.current !== current) return;
      if (!saved) {
        setError(rejected
          ? 'Sythion rejected the key. Enter a valid key for this provider.'
          : 'The key could not be saved because the backend is unavailable. No key was stored.');
        return;
      }
      setKey('');
      const status = await cloudPreviewService.status();
      if (!mounted.current || generation.current !== current) return;
      if (!status.providers.some((entry) =>
        entry.provider_id === provider && entry.credential_configured === true)) {
        setError('Sythion did not confirm the key was stored. No key is active.');
        return;
      }
      setHasKey(true);
      // Prevent the auto-advance effect from firing a second onConnected().
      autoConnected.current = true;
      onConnected?.();
    } catch {
      if (mounted.current && generation.current === current) {
        setError('The key could not be saved. No key was stored.');
      }
    } finally {
      inFlight.current = false;
      if (mounted.current && generation.current === current) setBusy(false);
    }
  };

  const verifyModel = async (id: string) => {
    const allowed = id === quickModelId || models.some((m) => m.id === id);
    if (inFlight.current || !id || !hasKey || !allowed) return;
    inFlight.current = true;
    const current = generation.current;
    setBusy(true); setError(''); setNotice(''); setVerified(false); setModel(id); setCanRecheck(false);
    setProgress(phase === 'recommended' ? 'Verifying…' : '');
    try {
      // Transient 502/503 responses are retried internally, starting with the
      // same read-only status check. Genuine 4xx decisions are never retried.
      for (let attempt = 0; attempt <= TRANSIENT_RECHECK_ATTEMPTS; attempt += 1) {
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
          setProgress(phase === 'recommended' ? 'Activating…' : '');
          const activated = await activeInferenceService.activate(selection);
          if (!mounted.current || generation.current !== current) return;
          if (activated.provider_id !== provider || activated.model_id !== id ||
              activated.permissions_unchanged !== true) {
            throw new Error('Active provider did not match selection');
          }
          setVerified(true);
          // Lazy-user contract: if current work held the old binding, the backend
          // queues this verified choice and applies it automatically when that work
          // finishes. Tell the owner plainly that they don't need to come back.
          setNotice(activated.pending
            ? (activated.message || 'Model selected. It will activate automatically when current work finishes. Existing permissions are unchanged.')
            : 'Model selected for Sythion. Existing permissions are unchanged.');
          onVerified?.(id, activated.pending === true);
          return;
        } catch (cause) {
          if (!mounted.current || generation.current !== current) return;
          const response = cause && typeof cause === 'object' && 'response' in cause
            ? cause.response : null;
          // Narrow the status to number | null (matches submitKey's helper).
          const errorStatus = responseStatus(cause);
          if ((errorStatus === 502 || errorStatus === 503) &&
              attempt < TRANSIENT_RECHECK_ATTEMPTS) {
            // Bounded internal re-check of a transient, non-billable failure.
            setError('');
            setProgress(phase === 'recommended' ? 'Retrying…' : '');
            continue;
          }
          // A 429/401/other 4xx (or an unknown transport failure) is a genuine
          // decision: keep the manual recheck instead of retrying silently.
          setCanRecheck(needsOwnerRecheck(errorStatus));
          const body = response && typeof response === 'object' && 'data' in response
            ? response.data : null;
          const detail = body && typeof body === 'object' && 'detail' in body ? body.detail : null;
          const failureText = modelTestFailureMessage(cause);
          if (failureText !== GENERIC_MODEL_TEST_FAILURE) {
            setError(failureText);
          } else if (errorStatus === 429) {
            setError('The provider reached its quota or capacity. This does not prove model incompatibility.');
          } else if (errorStatus === 502 &&
                     detail === 'Cloud model requires an eligible agentic harness (upstream HTTP 403)') {
            setError('This model requires an eligible agentic application. Choose a different model.');
          } else if (errorStatus === 422) {
            setError('This model did not demonstrate the structured Assistant format or function calling Sythion requires. It was not activated; your saved settings are unchanged.');
          } else if (errorStatus === 409 && detail && typeof detail === 'string') {
            // Surface the backend's specific, actionable detail (which tasks block
            // and what to do) instead of a vague "review your setup" message.
            setError(detail);
          } else if (errorStatus === 409) {
            // In-flight work no longer causes a 409 — it is deferred and applied
            // automatically. A 409 here means the exact verification/credential
            // evidence changed, which genuinely needs a fresh verification.
            setError('Sythion could not confirm this model\'s verification, so it was not activated. Your saved settings are unchanged.');
          } else {
            setError('Sythion could not verify this model for text and function calling, so it was not activated. Your saved settings are unchanged.');
          }
          return;
        }
      }
    } finally {
      inFlight.current = false;
      setProgress('');
      if (mounted.current && generation.current === current) setBusy(false);
    }
  };

  // One-click recommended path: once the status check confirms the credential,
  // run the full verification without requiring a second click. If the key was
  // missing, submitKey() sets hasKey after saving and this same effect picks up
  // automatically. A ref keeps it to a single run per mount.
  useEffect(() => {
    if (phase !== 'recommended' || loading || !hasKey || !quickModelId) return;
    if (autoStarted.current || inFlight.current) return;
    autoStarted.current = true;
    void verifyModel(quickModelId);
    // verifyModel is re-created each render; the guards above make this safe.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase, loading, hasKey, quickModelId]);

  // Free-first is a pure display default: show every catalogued model but put
  // free variants first, with no filter decision or click required.
  const visibleModels = [...models].sort((a, b) => Number(b.free_variant) - Number(a.free_variant));

  if (phase === 'connect' || (phase === 'recommended' && !hasKey)) return (
    <div className="stack" style={{ gap: 12 }}>
      {loading && <p role="status">Checking saved provider credentials…</p>}
      {phase === 'recommended' && !loading && <Notice tone="info" icon="info">
        The recommended model needs your {CLOUD_PROVIDER_LABELS[provider]} API key.
        Save it below and verification continues automatically. The key stays in
        Sythion's local vault and is never displayed here.
      </Notice>}
      {hasKey && <Notice tone="info" icon="check">
        API key already saved. Reusing it to continue.
      </Notice>}
      <label htmlFor="setup-cloud-key">{CLOUD_PROVIDER_LABELS[provider]} API key</label>
      <input id="setup-cloud-key" type="password" className="input" autoComplete="off"
        value={key} disabled={busy || loading} placeholder={hasKey ? 'Enter a replacement key' : 'Paste API key'}
        onChange={(event) => setKey(event.target.value)} />
      <div className="row" style={{ gap: 8 }}>
        {key.trim() && <Button variant="primary" disabled={busy || loading}
          onClick={() => void submitKey()}>
          {phase === 'recommended' ? 'Connect and continue' : 'Connect and save key'}
        </Button>}
        {phase === 'connect' && hasKey && !key.trim() && !autoAdvance && <Button variant="primary" disabled={busy || loading}
          onClick={onConnected}>Use configured key</Button>}
      </div>
      {busy && phase === 'recommended' && <p role="status">Saving key…</p>}
      {error && <Notice tone="danger" icon="alert">{error}</Notice>}
    </div>
  );

  if (phase === 'recommended') return (
    <div className="stack" style={{ gap: 12 }}>
      {loading && <p role="status">Checking the recommended model setup…</p>}
      <p className="t-muted">
        Verifying <strong>{quickModelId}</strong> on {CLOUD_PROVIDER_LABELS[provider]}:
        plain text, a harmless function call and Sythion's exact Assistant
        contract, before activation. No tools or permissions are granted.
      </p>
      {busy && <p role="status">{progress || 'Verifying…'}</p>}
      {verified && <Notice tone="info" icon="check">{notice}</Notice>}
      {error && <Notice tone="danger" icon="alert">{error}
        {canRecheck && <Button disabled={busy} onClick={() => void verifyModel(quickModelId ?? '')}>
          Check again
        </Button>}
      </Notice>}
    </div>
  );

  return (
    <div className="stack" style={{ gap: 12 }}>
      {loading && <p role="status">Loading available models…</p>}
      <label htmlFor="setup-cloud-model">Choose a model</label>
      <select id="setup-cloud-model" className="input" disabled={busy || loading || !hasKey}
        value={model} onChange={(event) => void verifyModel(event.target.value)}>
        <option value="">Select a model</option>
        {visibleModels.map((entry) => <option key={entry.id} value={entry.id}>{entry.id}</option>)}
      </select>
      {!loading && visibleModels.length === 0 &&
        <p>No models listed for this provider.</p>}
      <p className="t-muted">Free models are shown first. Selection verifies text, a harmless function call
        and Sythion's actual Assistant format (up to three provider requests, which may consume quota).
        It does not grant tools or permissions and is never retried automatically.</p>
      {busy && <p role="status">Checking the selected model…</p>}
      {verified && <Notice tone="info" icon="check">{notice}</Notice>}
      {error && <Notice tone="danger" icon="alert">{error}
        {canRecheck && <Button disabled={busy} onClick={() => void verifyModel(model)}>
          Check again
        </Button>}
      </Notice>}
    </div>
  );
}
