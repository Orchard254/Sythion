import { useCallback, useEffect, useRef, useState } from 'react';
import {
  cloudPreviewService,
  type CloudPreviewSelection,
  type CloudConversationHistoryTurn,
  type CloudPreviewStatus,
} from '../services/api';
import { singleScreenContentStyle, singleScreenShellStyle } from '../lib/shellLayout';
import { SythionMark } from '../components/icons';
import { Button, Notice, Panel } from '../components/ui';

// A 409 means the proof/model binding changed. That state can settle shortly
// afterwards, so re-read the read-only status endpoint a bounded number of
// times before showing an error. Never auto-retry a send: it may bill quota.
const RECHECK_ATTEMPTS = 3;

const STALE_PROOF_ERROR =
  'I re-checked the cloud text proof, but it is no longer current. Nothing was sent.';
const KEY_REQUIRED_ERROR =
  'A cloud API key is required for this provider. Connect a valid key to continue.';
const KEY_REJECTED_ERROR =
  'The cloud key was rejected (HTTP 401). Connect a valid key to continue.';
const RECHECK_UNAVAILABLE_ERROR =
  'I could not re-check cloud verification. Nothing was sent.';

function responseStatus(error: unknown): number | null {
  const response = error && typeof error === 'object' && 'response' in error
    ? error.response : null;
  return response && typeof response === 'object' && 'status' in response &&
    typeof response.status === 'number' ? response.status : null;
}

function safeCloudError(error: unknown): string {
  const code = responseStatus(error);
  if (code === 429) return 'Cloud quota or capacity reached. Do not retry repeatedly.';
  if (code === 409) return STALE_PROOF_ERROR;
  if (code === 401) return KEY_REJECTED_ERROR;
  if (code === 502) {
    const response = error && typeof error === 'object' && 'response' in error
      ? error.response : null;
    const detail = response && typeof response === 'object' && 'data' in response &&
      response.data && typeof response.data === 'object' && 'detail' in response.data
      ? response.data.detail : null;
    if (detail === 'Cloud model requires an eligible agentic harness (upstream HTTP 403)') {
      return 'This free model is restricted to eligible agentic harnesses. Choose another model.';
    }
    return 'Cloud provider rejected the request. Check its credentials and availability.';
  }
  if (code === 503) return 'Request outcome is uncertain. It was not retried.';
  return 'Cloud conversation request failed. No reply or action was recorded.';
}

/** Honest assessment of the current, read-only status: never claims a model is verified
 * when status disagrees. A missing key is the one genuinely owner-actionable case. */
function assessStatus(status: CloudPreviewStatus): {
  selection: CloudPreviewSelection | null;
  credentialMissing: boolean;
} {
  const providerId = status.staged.provider_id;
  const modelId = status.staged.model_id;
  const providerConfigured = providerId
    ? status.providers.some(
        (p) => p.provider_id === providerId && p.credential_configured,
      )
    : false;
  if (status.text_probe_verified && providerId && modelId &&
      status.ready_for_execution === false && providerConfigured) {
    return {
      selection: { provider_id: providerId, model_id: modelId },
      credentialMissing: false,
    };
  }
  return {
    selection: null,
    credentialMissing: Boolean(providerId) && !providerConfigured,
  };
}

/** This screen is deliberately outside AssistantScreen and WorkEntry. */
export function CloudConversationOnlyScreen({ onExit }: { onExit: () => void }) {
  const [selection, setSelection] = useState<CloudPreviewSelection | null>(null);
  const [verified, setVerified] = useState(false);
  const [loading, setLoading] = useState(true);
  const [checking, setChecking] = useState(false);
  const [missingCredential, setMissingCredential] = useState(false);
  const [messages, setMessages] = useState<CloudConversationHistoryTurn[]>([]);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  // React state updates can batch two rapid submit events. This ref is a
  // synchronous one-request gate, preventing duplicate provider charges.
  const requestInFlight = useRef(false);
  const [error, setError] = useState('');

  /** Re-read status internally (bounded) and only ask the owner to act for a
   * genuine credential problem. No probe/send is retried automatically. */
  const recheck = useCallback(async (isCancelled: () => boolean = () => false) => {
    setChecking(true);
    let sawStatus = false;
    for (let attempt = 0; attempt < RECHECK_ATTEMPTS; attempt++) {
      if (isCancelled()) return;
      try {
        const status = await cloudPreviewService.status();
        if (isCancelled()) return;
        sawStatus = true;
        const next = assessStatus(status);
        if (next.selection) {
          setSelection(next.selection);
          setVerified(true);
          setMissingCredential(false);
          setError('');
          setChecking(false);
          return;
        }
        if (next.credentialMissing) {
          setSelection(null);
          setVerified(false);
          setMissingCredential(true);
          setError(KEY_REQUIRED_ERROR);
          setChecking(false);
          return;
        }
      } catch {
        // A transient status failure is not proof of a bad model. Keep trying.
      }
    }
    if (isCancelled()) return;
    setSelection(null);
    setVerified(false);
    setMissingCredential(false);
    setError(sawStatus ? STALE_PROOF_ERROR : RECHECK_UNAVAILABLE_ERROR);
    setChecking(false);
  }, []);

  useEffect(() => {
    let cancelled = false;
    void recheck(() => cancelled).finally(() => {
      if (!cancelled) setLoading(false);
    });
    return () => { cancelled = true; };
  }, [recheck]);

  const send = async () => {
    const text = draft.trim();
    if (!verified || !selection || busy || checking || requestInFlight.current ||
        !text || text.length > 1200) return;
    requestInFlight.current = true;
    setBusy(true);
    setError('');
    try {
      // Only three prior full pairs are sent; the user-facing transcript is
      // local to this mounted screen, and is never silently persisted.
      const history = messages.slice(-6).map((turn) => ({
        role: turn.role,
        content: turn.content.slice(0, 1200),
      }));
      const response = await cloudPreviewService.conversationTurn(selection, text, history);
      if (response.provider_id !== selection.provider_id ||
          response.model_id !== selection.model_id ||
          response.conversation_only !== true ||
          response.active !== false || response.ready_for_execution !== false ||
          response.persisted !== false || response.task_executed !== false ||
          typeof response.assistant_text !== 'string' || !response.assistant_text.trim()) {
        throw new Error('Unexpected cloud conversation contract');
      }
      setMessages((old) => [
        ...old,
        { role: 'user', content: text },
        { role: 'assistant', content: response.assistant_text },
      ]);
      setDraft('');
    } catch (e) {
      const code = responseStatus(e);
      if (code === 401) {
        // Missing/invalid credential is the one case that needs the owner.
        setVerified(false);
        setMissingCredential(true);
        setError(KEY_REJECTED_ERROR);
      } else if (code === 409) {
        // Stale proof may already have settled: re-read status internally
        // instead of telling the owner to return to setup.
        void recheck();
      } else {
        setMissingCredential(false);
        setError(safeCloudError(e));
      }
    } finally {
      requestInFlight.current = false;
      setBusy(false);
    }
  };

  return (
    <div className="shell" style={singleScreenShellStyle()}>
      <main className="shell__main" style={singleScreenContentStyle({ overflowY: 'auto', padding: '32px 20px' })}>
        <div className="stack" style={{ maxWidth: 760, margin: '0 auto', gap: 20 }}>
          <div className="row" style={{ gap: 12, alignItems: 'center' }}>
            <SythionMark size={38} />
            <div>
              <h1 className="screen-head__title">Sythion — cloud conversation only</h1>
              <p className="t-muted">
                {checking || loading ? 'Checking cloud verification…' :
                  verified && selection ? selection.provider_id + ' · ' + selection.model_id :
                  'No verified cloud model'}
              </p>
            </div>
          </div>
          <Notice tone="info" icon="info">
            Text-only mode. No tool execution, automation, file access, or memory writes.
            Messages remain in this screen only; they are not saved.
            Each Send makes one cloud request and may use your quota.
          </Notice>
          {missingCredential ? (
            <Button variant="subtle" disabled={busy || checking} onClick={onExit}>
              Connect a key in setup
            </Button>
          ) : (
            <Button variant="subtle" disabled={busy || checking}
              onClick={() => void recheck()}>
              {checking ? 'Checking again…' : 'Check again'}
            </Button>
          )}
          {loading && <p role="status">Checking verified text capability…</p>}
          {error && <Notice tone="danger" icon="alert">{error}</Notice>}
          <Panel title="Cloud conversation" icon="info">
            <div className="stack" style={{ gap: 12 }}>
              {messages.length === 0 && <p className="t-muted">
                {verified ? 'Ask a question. This mode answers only; it cannot act on requests.' :
                  'A successful text preview is required before sending messages.'}
              </p>}
              {messages.map((message, index) => (
                <div key={index} className="stack" style={{ gap: 3 }}>
                  <strong>{message.role === 'user' ? 'You' : 'Model reply (unverified text)'}</strong>
                  <p style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{message.content}</p>
                </div>
              ))}
              <label htmlFor="cloud-only-message">Your message</label>
              <textarea id="cloud-only-message" className="input" value={draft}
                maxLength={1200} rows={3} disabled={!verified || busy || checking || loading}
                onChange={(e) => setDraft(e.target.value)}
                placeholder="Ask a question — no Sythion actions can run here" />
              <Button variant="primary" disabled={!verified || busy || checking || !draft.trim()}
                onClick={() => void send()}>
                {busy ? 'Sending one cloud request…' : 'Send text-only message'}
              </Button>
            </div>
          </Panel>
        </div>
      </main>
    </div>
  );
}
