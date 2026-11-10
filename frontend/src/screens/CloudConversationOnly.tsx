import { useEffect, useRef, useState } from 'react';
import {
  cloudPreviewService,
  type CloudPreviewSelection,
  type CloudConversationHistoryTurn,
} from '../services/api';
import { singleScreenContentStyle, singleScreenShellStyle } from '../lib/shellLayout';
import { SythionMark } from '../components/icons';
import { Button, Notice, Panel } from '../components/ui';

function safeCloudError(error: unknown): string {
  const response = error && typeof error === 'object' && 'response' in error
    ? error.response : null;
  const code = response && typeof response === 'object' && 'status' in response
    ? response.status : null;
  if (code === 429) return 'Cloud quota or capacity reached. Do not retry repeatedly.';
  if (code === 409) return 'Cloud model or verification changed. Return to setup and verify again.';
  if (code === 401) return 'Sythion connection is not authorized. Return to setup.';
  if (code === 502) {
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

/** This screen is deliberately outside AssistantScreen and WorkEntry. */
export function CloudConversationOnlyScreen({ onExit }: { onExit: () => void }) {
  const [selection, setSelection] = useState<CloudPreviewSelection | null>(null);
  const [verified, setVerified] = useState(false);
  const [loading, setLoading] = useState(true);
  const [messages, setMessages] = useState<CloudConversationHistoryTurn[]>([]);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  // React state updates can batch two rapid submit events. This ref is a
  // synchronous one-request gate, preventing duplicate provider charges.
  const requestInFlight = useRef(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    void cloudPreviewService.status().then((status) => {
      if (cancelled) return;
      const candidate = status.staged;
      const providerExists = status.providers.some(
        (p) => p.provider_id === candidate.provider_id && p.credential_configured,
      );
      if (!status.text_probe_verified || !providerExists ||
          !candidate.provider_id || !candidate.model_id ||
          status.ready_for_execution !== false) {
        setVerified(false);
        setError('No current cloud text verification. Return to setup and test one reply.');
        return;
      }
      setSelection({ provider_id: candidate.provider_id, model_id: candidate.model_id });
      setVerified(true);
    }).catch(() => {
      if (!cancelled) setError('Could not verify cloud conversation setup.');
    }).finally(() => {
      if (!cancelled) setLoading(false);
    });
    return () => { cancelled = true; };
  }, []);

  const send = async () => {
    const text = draft.trim();
    if (!verified || !selection || busy || requestInFlight.current ||
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
      const response = e && typeof e === 'object' && 'response' in e
        ? e.response : null;
      if (response && typeof response === 'object' &&
          'status' in response && response.status === 409) setVerified(false);
      setError(safeCloudError(e));
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
                {selection ? selection.provider_id + ' · ' + selection.model_id : 'Checking cloud verification…'}
              </p>
            </div>
          </div>
          <Notice tone="info" icon="info">
            Text-only mode. No tool execution, automation, file access, or memory writes.
            Messages remain in this screen only; they are not saved.
            Each Send makes one cloud request and may use your quota.
          </Notice>
          <Button variant="subtle" disabled={busy} onClick={onExit}>Return to setup</Button>
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
                maxLength={1200} rows={3} disabled={!verified || busy || loading}
                onChange={(e) => setDraft(e.target.value)}
                placeholder="Ask a question — no Sythion actions can run here" />
              <Button variant="primary" disabled={!verified || busy || !draft.trim()}
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
