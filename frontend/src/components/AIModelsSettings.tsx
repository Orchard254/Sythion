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

/** A deferred ("pending") activation leaves the OLD model active until
 * in-flight work settles. Poll a bounded number of times so the "Active model"
 * line and the notice reflect the real backend state once the switch lands,
 * without ever asking the owner to refresh or reopen anything. The window is
 * long enough to outlast ordinary in-flight work; GET /inference/active now
 * reports `pending_activation`, so we can also stop early on a terminal state. */
const CONFIRM_POLL_INTERVAL_MS = 2500;
const CONFIRM_POLL_MAX_ATTEMPTS = 30; // ~75s total; keeps watching while a choice is pending

/** The known-good, tool-capable model with no reasoning-token burn. The quick
 * path verifies it through ActiveCloudSetup's existing proof sequence. */
const RECOMMENDED_PROVIDER: AIProvider = 'nvidia';
const RECOMMENDED_MODEL_ID = 'nvidia/nemotron-3.5-bolt-e4b';

/** First-time configuration belongs in onboarding; later changes belong here.
 * Only the existing Sythion runtime is used. Provider preview chat is never
 * mounted and selecting a provider does not authorize actions or send probes.
 */
export function AIModelsSettings() {
  const [active, setActive] = useState<ActiveModel | null>(null);
  const [provider, setProvider] = useState<AIProvider | null>(null);
  const [quick, setQuick] = useState(false);
  const [quickDone, setQuickDone] = useState(false);
  const [hasKey, setHasKey] = useState<Partial<Record<CloudPreviewProviderId, boolean>>>({});
  const [keyEditor, setKeyEditor] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const mounted = useRef(false);
  const inFlight = useRef(false);
  const pollGeneration = useRef(0);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  /** Invalidate any in-flight confirmation poll. Bumping the generation makes
   * an already-running poll's async result a no-op; clearing the timer stops a
   * not-yet-fired one. Used on unmount and on provider changes. */
  const stopPolling = () => {
    pollGeneration.current += 1;
    if (pollTimer.current !== null) {
      clearTimeout(pollTimer.current);
      pollTimer.current = null;
    }
  };

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      stopPolling();
    };
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
    stopPolling(); // a provider change must not let a stale poll overwrite the UI
    setProvider(next);
    setError(''); setMessage('');
    setKeyEditor(false);
    setQuick(false);
    setQuickDone(false);
  };

  /** Confirm a previously deferred choice. The FIRST read is deliberately
   * delayed (via setTimeout), so at pending time we do NOT re-check status —
   * the old model is still legitimately active. A bounded poll then watches
   * until the backend reports the chosen provider/model (pending cleared) and
   * we can honestly update the "Active model" line, until it reports a
   * terminal blocked/damaged state, or until the window is exhausted. */
  const confirmDeferredActivation = (forProvider: AIProvider, modelId: string) => {
    const generation = pollGeneration.current;
    let attempts = 0;
    const tick = () => {
      if (!mounted.current || generation !== pollGeneration.current) return;
      void (async () => {
        try {
          const current = await activeInferenceService.status();
          if (!mounted.current || generation !== pollGeneration.current) return;
          if (current.provider_id === forProvider && current.model_id === modelId) {
            setActive({
              provider_id: current.provider_id,
              model_id: current.model_id,
              permissions_unchanged: true,
            });
            setMessage('Active AI model updated. Sythion keeps its existing assistant and permissions.');
            return; // the pending field has cleared — nothing left to watch
          }
          const pending = current.pending_activation;
          if (pending === 'blocked_credential') {
            setMessage('The verified model is ready, but its provider key changed, so Sythion kept your previous model active. Reconnect the key or select the model again to finish the switch.');
            return; // terminal: stop polling early with the honest reason
          }
          if (pending === 'damaged') {
            setMessage('Sythion could not finish this model switch, so your previous model is still active. Select the model again to retry.');
            return; // terminal: stop polling early with the honest reason
          }
          // 'ready' / 'blocked_in_flight' (or a transient failure): the queued
          // choice has not landed yet, so keep waiting within the bound.
        } catch {
          // A transient status failure is not a confirmation; keep polling.
        }
        if (!mounted.current || generation !== pollGeneration.current) return;
        attempts += 1;
        if (attempts >= CONFIRM_POLL_MAX_ATTEMPTS) {
          // The window is over. Be honest: Sythion is still applying the choice
          // in the background, but we are no longer watching it in this session,
          // so do NOT promise an in-session update or tell the owner to refresh
          // or reopen. The line reflects reality the next time Settings opens.
          setMessage('Sythion is still applying the selected model in the background. The active model line will update the next time Settings opens. Existing permissions are unchanged.');
          return;
        }
        pollTimer.current = setTimeout(tick, CONFIRM_POLL_INTERVAL_MS);
      })();
    };
    pollTimer.current = setTimeout(tick, CONFIRM_POLL_INTERVAL_MS);
  };

  const applyVerified = (forProvider: AIProvider, modelId: string, pending?: boolean) => {
    setError('');
    if (pending) {
      // The backend queued this verified choice because in-flight work still
      // holds the old provider binding. It will apply automatically when work
      // settles. Do NOT re-check status synchronously — it legitimately still
      // reports the old model, and telling the owner to refresh would hand work
      // back to them. A bounded poll confirms the switch once it lands.
      setMessage('Model selected. It will activate automatically when current work finishes. Existing permissions are unchanged.');
      confirmDeferredActivation(forProvider, modelId);
      return;
    }
    // ActiveCloudSetup validates the activate() response against the chosen
    // provider/model/permissions BEFORE calling this callback. That result is
    // authoritative, so a redundant status() re-check here only produced
    // stale "refresh/reopen" hand-backs — it is removed. Trust the verified
    // result and update the displayed selection from what was confirmed.
    setActive({ provider_id: forProvider, model_id: modelId, permissions_unchanged: true });
    setMessage('Active AI model updated. Sythion keeps its existing assistant and permissions.');
  };

  const onCloudConnected = () => {
    if (!provider) return;
    setHasKey((previous) => ({ ...previous, [provider]: true }));
    setKeyEditor(false);
    setError(''); setMessage('');
  };

  const onCloudVerified = (modelId: string, pending?: boolean) => {
    if (!provider) return;
    applyVerified(provider, modelId, pending);
  };

  const startRecommended = () => {
    if (loading || quick) return; // a second click must not clear the result notice
    stopPolling(); // a provider change must not let a stale poll overwrite the UI
    // Switch to the provider the recommendation actually uses before any
    // proof runs: the dropdown and the active-model line stay honest, and we
    // never verify one provider while claiming another.
    setProvider(RECOMMENDED_PROVIDER);
    setQuick(true);
    setQuickDone(false);
    setError(''); setMessage('');
    setKeyEditor(false);
  };

  const onRecommendedConnected = () => {
    setHasKey((previous) => ({ ...previous, [RECOMMENDED_PROVIDER]: true }));
    setError(''); setMessage('');
  };

  const onRecommendedVerified = (modelId: string, pending?: boolean) => {
    // ActiveCloudSetup already validated the activate() response against this
    // exact provider/model by the time this fires — reuse the same deferred
    // activation handling as the manual path.
    setQuickDone(true);
    setHasKey((previous) => ({ ...previous, [RECOMMENDED_PROVIDER]: true }));
    applyVerified(RECOMMENDED_PROVIDER, modelId, pending);
  };

  const effectiveProvider: AIProvider | null = quick ? RECOMMENDED_PROVIDER : provider;

  return (
    <Panel title="AI Models" icon="assistant">
      <div className="stack" style={{ gap: 12 }}>
        <p className="t-muted">
          Configure which AI model powers Sythion. Conversation history, goals,
          permissions and the main Assistant are unchanged.
        </p>
        {loading ? <p role="status">Checking the active AI model…</p> : <>
          <div className="stack" style={{ gap: 8 }}>
            <p className="t-muted">
              <strong>Recommended for Sythion</strong> — one click fully verifies
              and activates {RECOMMENDED_MODEL_ID} on {PROVIDERS[RECOMMENDED_PROVIDER]},
              a tool-capable model with zero reasoning-token burn. No tools or
              permissions are granted; the usual text, function-call and
              Assistant-contract proofs still run.
            </p>
            <Button id="settings-recommended" variant="primary" disabled={quick && !quickDone}
              onClick={startRecommended}>
              {quickDone ? 'Recommended model selected'
                : `Use recommended model (${RECOMMENDED_MODEL_ID})`}
            </Button>
          </div>
          <p className="t-muted">Or choose a provider and model manually:</p>
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
          {effectiveProvider && <>
            {!quick && hasKey[effectiveProvider] && !keyEditor &&
              <Button variant="subtle" onClick={() => setKeyEditor(true)}>
                Change saved API key
              </Button>}
            <ActiveCloudSetup
              key={quick ? 'recommended' : `${effectiveProvider}:${hasKey[effectiveProvider] && !keyEditor ? 'model' : 'connect'}`}
              provider={effectiveProvider}
              phase={quick ? 'recommended' : (hasKey[effectiveProvider] && !keyEditor ? 'model' : 'connect')}
              quickModelId={quick ? RECOMMENDED_MODEL_ID : undefined}
              onConnected={quick ? onRecommendedConnected : onCloudConnected}
              onVerified={quick ? onRecommendedVerified : onCloudVerified}
            />
          </>}
        </>}
        {message && <Notice tone="info" icon="check">{message}</Notice>}
        {error && <Notice tone="danger" icon="alert">{error}</Notice>}
      </div>
    </Panel>
  );
}
