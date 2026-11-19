import { useCallback, useEffect, useRef, useState } from 'react';
import { relayService } from '../services/api';
import { Badge, Button, Notice, Panel } from '../components/ui';
import type { RelayMessage, RelaySession } from '../types';

/**
 * Minimal remote client for the Core's first-party relay.
 *
 * A device presents its id and credential, opens a session, then polls durable
 * messages and posts text. No third party is involved. Nothing here can approve
 * an action, clear a blocker, or grant a permission.
 */
export function PhoneRelayScreen() {
  const [deviceId, setDeviceId] = useState('');
  const [token, setToken] = useState('');
  const [session, setSession] = useState<RelaySession | null>(null);
  const [messages, setMessages] = useState<RelayMessage[]>([]);
  const [draft, setDraft] = useState('');
  const [error, setError] = useState('');
  const lastId = useRef(0);

  const poll = useCallback(async (current: RelaySession) => {
    try {
      const next = await relayService.poll(
        current.session_id, deviceId, token, lastId.current);
      if (next.length) {
        lastId.current = next[next.length - 1].id;
        setMessages((old) => [...old, ...next]);
      }
    } catch (err) {
      setError(String(err));
    }
  }, [deviceId, token]);

  useEffect(() => {
    if (!session) return;
    const timer = setInterval(() => void poll(session), 3000);
    return () => clearInterval(timer);
  }, [session, poll]);

  const connect = async () => {
    setError('');
    try {
      lastId.current = 0;
      setMessages([]);
      const opened = await relayService.open(deviceId, token);
      setSession(opened);
      await poll(opened);
    } catch (err) {
      setError(String(err));
    }
  };

  const send = async () => {
    if (!session || !draft.trim()) return;
    const text = draft;
    setDraft('');
    try {
      await relayService.send(session.session_id, deviceId, token, text);
      await poll(session);
    } catch (err) {
      setError(String(err));
    }
  };

  return (
    <div className="stack">
      <Panel title="Remote client" hint="Talk to Synth through its first-party relay">
        <Notice tone="info">
          No third party is involved. Your device credential stays between this
          device and the Core. This client cannot approve actions or clear blockers.
        </Notice>
        <div className="form-grid" style={{ marginTop: 12 }}>
          <label>
            <span>Device ID</span>
            <input value={deviceId} onChange={(e) => setDeviceId(e.target.value)} />
          </label>
          <label>
            <span>Device credential</span>
            <input
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
            />
          </label>
        </div>
        <div className="row row--wrap" style={{ gap: 8, marginTop: 12 }}>
          <Button onClick={() => void connect()} disabled={!deviceId || !token}>
            Connect
          </Button>
          {session && <Badge tone="ok">Session {session.session_id}</Badge>}
        </div>
        {error && <Notice tone="danger">{error}</Notice>}
      </Panel>

      {session && (
        <Panel title="Conversation">
          <div className="relay__messages" data-testid="relay-messages">
            {messages.length === 0 && <p className="t-faint">No messages yet.</p>}
            {messages.map((message) => (
              <div key={message.id} className={`relay__message relay__message--${message.role}`}>
                <span className="relay__role">{message.role}</span>
                <span>{message.text}</span>
              </div>
            ))}
          </div>
          <div className="row row--wrap" style={{ gap: 8, marginTop: 12 }}>
            <input
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="Message Synth"
              aria-label="Message Synth"
            />
            <Button onClick={() => void send()} disabled={!draft.trim()}>
              Send
            </Button>
          </div>
        </Panel>
      )}
    </div>
  );
}
