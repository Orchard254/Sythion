import { useCallback, useEffect, useRef, useState } from 'react';
import { relayService } from '../services/api';
import {
  openDeviceRelaySocket, sendRelayText,
  type RelaySocketFrame, type RelaySocketLike,
} from '../services/deviceRelaySocket';
import { Badge, Button, Notice, Panel } from '../components/ui';
import type { RelayMessage } from '../types';

/**
 * Minimal phone client for the Core's first-party relay.
 *
 * Primary transport is the device-authenticated WSS gateway, which delivers
 * real-time sequenced replies. HTTP polling remains as a fallback when a
 * WebSocket is unavailable. No third party is involved; nothing here can
 * approve an action, clear a blocker, or grant a permission.
 */
export function PhoneRelayScreen() {
  const [deviceId, setDeviceId] = useState('');
  const [token, setToken] = useState('');
  const [connected, setConnected] = useState(false);
  const [transport, setTransport] = useState<'ws' | 'http'>('ws');
  const [messages, setMessages] = useState<RelayMessage[]>([]);
  const [draft, setDraft] = useState('');
  const [error, setError] = useState('');
  const [tracked, setTracked] = useState<{ replySequence: number; actionState: string } | null>(null);
  const socketRef = useRef<RelaySocketLike | null>(null);
  const sessionRef = useRef<string | null>(null);
  const lastId = useRef(0);
  const pendingKey = useRef<string | null>(null);

  // Generate an idempotency key in the trusted UI, never from the model.
  const newIdempotencyKey = useCallback(() => {
    const random = typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    return `phone-${random}`;
  }, []);

  const appendMessage = useCallback((message: RelayMessage) => {
    setMessages((old) => [...old, message]);
  }, []);

  const handleFrame = useCallback((frame: RelaySocketFrame) => {
    if (frame.type === 'session') {
      sessionRef.current = frame.session_id;
      return;
    }
    if (frame.type === 'message') {
      lastId.current = Math.max(lastId.current, frame.sequence);
      appendMessage({
        id: frame.sequence, role: frame.role, kind: frame.kind,
        text: frame.text, created_at: '',
      });
      return;
    }
    if (frame.type === 'accepted') {
      // The durable reply is tracked: bind its sequence and lifecycle state.
      setTracked({ replySequence: frame.reply_sequence, actionState: frame.action_state });
      return;
    }
    if (frame.type === 'error') {
      setError(frame.reason);
    }
  }, [appendMessage]);

  const connectHttp = useCallback(async () => {
    const opened = await relayService.open(deviceId, token);
    sessionRef.current = opened.session_id;
    setTransport('http');
    setConnected(true);
    const next = await relayService.poll(opened.session_id, deviceId, token, lastId.current);
    if (next.length) {
      lastId.current = next[next.length - 1].id;
      setMessages((old) => [...old, ...next]);
    }
  }, [deviceId, token]);

  const connect = async () => {
    setError('');
    lastId.current = 0;
    setMessages([]);
    setTracked(null);
    pendingKey.current = null;
    try {
      const socket = await openDeviceRelaySocket(deviceId, token, {
        onFrame: handleFrame,
        onOpen: () => { setTransport('ws'); setConnected(true); },
        onClose: () => setConnected(false),
        onError: (err) => setError(err),
      });
      socketRef.current = socket;
    } catch (err) {
      // WebSocket unavailable: fall back to HTTP polling on the same relay.
      try {
        await connectHttp();
      } catch {
        setError(String(err));
      }
    }
  };

  // HTTP fallback polling only while on the HTTP transport.
  useEffect(() => {
    if (!connected || transport !== 'http' || !sessionRef.current) return;
    const sid = sessionRef.current;
    const timer = setInterval(() => {
      void relayService.poll(sid, deviceId, token, lastId.current).then((next) => {
        if (next.length) {
          lastId.current = next[next.length - 1].id;
          setMessages((old) => [...old, ...next]);
        }
      }).catch((err) => setError(String(err)));
    }, 3000);
    return () => clearInterval(timer);
  }, [connected, transport, deviceId, token]);

  useEffect(() => () => {
    socketRef.current?.close();
    socketRef.current = null;
  }, []);

  const send = async () => {
    if (!connected || !draft.trim()) return;
    const text = draft;
    const key = pendingKey.current ?? newIdempotencyKey();
    setDraft('');
    setTracked(null);
    try {
      if (transport === 'ws' && socketRef.current) {
        sendRelayText(socketRef.current, text, key);
      } else if (sessionRef.current) {
        await relayService.send(sessionRef.current, deviceId, token, text);
        const next = await relayService.poll(sessionRef.current, deviceId, token, lastId.current);
        if (next.length) {
          lastId.current = next[next.length - 1].id;
          setMessages((old) => [...old, ...next]);
        }
      }
    } catch (err) {
      setError(String(err));
    } finally {
      pendingKey.current = null;
    }
  };

  const onDraftChange = (value: string) => {
    if (!value) pendingKey.current = null;
    else if (!draft && !pendingKey.current) pendingKey.current = newIdempotencyKey();
    setDraft(value);
  };

  return (
    <div className="stack">
      <Panel title="Remote client" hint="Talk to Synth through its first-party relay">
        <Notice tone="info">
          No third party is involved. Your device credential stays between this
          device and the Core. Real-time replies arrive over the device WebSocket
          (HTTP polling fallback). This client cannot approve actions or clear blockers.
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
          {connected && <Badge tone="ok">Connected via {transport.toUpperCase()}</Badge>}
        </div>
        {error && <Notice tone="danger">{error}</Notice>}
      </Panel>

      {connected && (
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
          {tracked && (
            <div className="row row--wrap" style={{ gap: 8, marginTop: 8 }}
              data-testid="relay-tracked-action">
              <Badge tone={tracked.actionState === 'completed' ? 'ok' : 'warn'}>
                {tracked.actionState}
              </Badge>
              <span className="t-mono t-faint">reply #{tracked.replySequence}</span>
            </div>
          )}
          <div className="row row--wrap" style={{ gap: 8, marginTop: 12 }}>
            <input
              value={draft}
              onChange={(e) => onDraftChange(e.target.value)}
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
