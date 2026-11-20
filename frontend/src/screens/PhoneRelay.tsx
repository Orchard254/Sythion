import { useCallback, useEffect, useRef, useState } from 'react';
import { relayService } from '../services/api';
import {
  openDeviceRelaySocket, sendRelayText,
  type RelaySocketFrame, type RelaySocketLike,
} from '../services/deviceRelaySocket';
import { Badge, Button, Notice, Panel } from '../components/ui';
import { RelayBriefCard } from '../components/RelayBriefCard';
import { useNavStore } from '../store/navStore';
import { parseRelayBrief } from '../lib/relayBrief';
import type { RelayMessage } from '../types';

const RECONNECT_MAX_ATTEMPTS = 3;
const RECONNECT_BASE_DELAY_MS = 1000;
const RECONNECT_MAX_DELAY_MS = 8000;

/**
 * Minimal phone client for the Core's first-party relay.
 *
 * Primary transport is the device-authenticated WSS gateway, which delivers
 * real-time sequenced replies. A dropped socket is reconnected with bounded
 * exponential backoff; after the retry budget is spent the client falls back to
 * HTTP polling on the same relay. HTTP remains the fallback when WSS is
 * unavailable. No third party is involved; nothing here can approve an action,
 * clear a blocker, or grant a permission.
 */
export function PhoneRelayScreen() {
  const setRoute = useNavStore((s) => s.setRoute);
  const [deviceId, setDeviceId] = useState('');
  const [token, setToken] = useState('');
  const [connected, setConnected] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);
  const [transport, setTransport] = useState<'ws' | 'http'>('ws');
  const [messages, setMessages] = useState<RelayMessage[]>([]);
  const [draft, setDraft] = useState('');
  const [error, setError] = useState('');
  const [tracked, setTracked] = useState<{ replySequence: number; actionState: string } | null>(null);
  const socketRef = useRef<RelaySocketLike | null>(null);
  const sessionRef = useRef<string | null>(null);
  const lastId = useRef(0);
  const pendingKey = useRef<string | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnectAttemptsRef = useRef(0);
  const manualDisconnectRef = useRef(false);
  // Latest reconnect handler, read by the socket's onClose at call time so it
  // always sees the current transport/credentials without stale closures.
  const reconnectRef = useRef<() => void>(() => {});

  const clearReconnectTimer = useCallback(() => {
    if (reconnectTimerRef.current !== null) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
  }, []);

  // Generate an idempotency key in the trusted UI, never from the model.
  const newIdempotencyKey = useCallback(() => {
    const random = typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    return `phone-${random}`;
  }, []);

  const appendMessage = useCallback((message: RelayMessage) => {
    // Deduplicate by durable sequence id. A frame can be re-delivered across a
    // transport switch or reconnect; showing it twice would be misleading.
    setMessages((old) => (
      old.some((m) => m.id === message.id) ? old : [...old, message]
    ));
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
    clearReconnectTimer();
    const opened = await relayService.open(deviceId, token);
    sessionRef.current = opened.session_id;
    setTransport('http');
    setConnected(true);
    setReconnecting(false);
    const next = await relayService.poll(opened.session_id, deviceId, token, lastId.current);
    if (next.length) {
      lastId.current = next[next.length - 1].id;
      next.forEach(appendMessage);
    }
  }, [deviceId, token, appendMessage, clearReconnectTimer]);

  // Open a WSS socket without resetting conversation state (used by reconnect,
  // so messages and the idempotency cursor survive a transient drop).
  const establishWss = useCallback(async () => {
    const socket = await openDeviceRelaySocket(deviceId, token, {
      onFrame: handleFrame,
      onOpen: () => {
        reconnectAttemptsRef.current = 0;
        setTransport('ws');
        setConnected(true);
        setReconnecting(false);
      },
      onClose: () => {
        if (socketRef.current === socket) socketRef.current = null;
        // A manual disconnect / unmount clears the intent flag first, so this
        // is a no-op in those cases (see scheduleReconnect).
        reconnectRef.current();
      },
      onError: (err) => setError(err),
    });
    socketRef.current = socket;
    return socket;
  }, [deviceId, token, handleFrame]);

  const scheduleReconnect = useCallback(() => {
    if (manualDisconnectRef.current) {
      setConnected(false);
      setReconnecting(false);
      return;
    }
    // HTTP has its own polling loop; only a dropped WSS socket is reconnected.
    if (transport !== 'ws') {
      setConnected(false);
      return;
    }
    setConnected(false);
    const attempts = reconnectAttemptsRef.current + 1;
    // Retry budget spent: switch to HTTP polling on the same relay.
    if (attempts > RECONNECT_MAX_ATTEMPTS) {
      reconnectAttemptsRef.current = 0;
      setReconnecting(false);
      void connectHttp().catch((err) => setError(String(err)));
      return;
    }
    reconnectAttemptsRef.current = attempts;
    setReconnecting(true);
    const delay = Math.min(
      RECONNECT_BASE_DELAY_MS * 2 ** (attempts - 1), RECONNECT_MAX_DELAY_MS,
    );
    clearReconnectTimer();
    reconnectTimerRef.current = setTimeout(() => {
      reconnectTimerRef.current = null;
      void establishWss().catch(() => {
        // A rejected open produces no socket/onClose, so drive the next attempt
        // (or the HTTP fallback once the budget is spent) explicitly.
        reconnectRef.current();
      });
    }, delay);
  }, [transport, connectHttp, establishWss, clearReconnectTimer]);

  // Keep the onClose handler pointed at the latest scheduleReconnect.
  reconnectRef.current = scheduleReconnect;

  const connect = async () => {
    setError('');
    lastId.current = 0;
    setMessages([]);
    setTracked(null);
    pendingKey.current = null;
    manualDisconnectRef.current = false;
    reconnectAttemptsRef.current = 0;
    clearReconnectTimer();
    setReconnecting(false);
    try {
      await establishWss();
    } catch (err) {
      // WebSocket unavailable: fall back to HTTP polling on the same relay.
      try {
        await connectHttp();
      } catch {
        setError(String(err));
      }
    }
  };

  const disconnect = () => {
    manualDisconnectRef.current = true;
    clearReconnectTimer();
    reconnectAttemptsRef.current = 0;
    const socket = socketRef.current;
    socketRef.current = null;
    socket?.close();
    setConnected(false);
    setReconnecting(false);
  };

  useEffect(() => {
    const timerRef = reconnectTimerRef;
    const socketSlot = socketRef;
    return () => {
      manualDisconnectRef.current = true;
      if (timerRef.current !== null) clearTimeout(timerRef.current);
      socketSlot.current?.close();
      socketSlot.current = null;
    };
  }, []);

  // HTTP fallback polling only while on the HTTP transport.
  useEffect(() => {
    if (!connected || transport !== 'http' || !sessionRef.current) return;
    const sid = sessionRef.current;
    const timer = setInterval(() => {
      void relayService.poll(sid, deviceId, token, lastId.current).then((next) => {
        if (next.length) {
          lastId.current = next[next.length - 1].id;
          next.forEach(appendMessage);
        }
      }).catch((err) => setError(String(err)));
    }, 3000);
    return () => clearInterval(timer);
  }, [connected, transport, deviceId, token, appendMessage]);

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
        const sent = await relayService.send(sessionRef.current, deviceId, token, text, key);
        // The HTTP backend returns the tracked action state and reply; surface
        // it exactly like the WSS 'accepted' frame so both transports agree.
        if (sent && typeof sent.action_state === 'string' && sent.reply) {
          setTracked({
            replySequence: Number(sent.reply.id) || 0,
            actionState: sent.action_state,
          });
        }
        const next = await relayService.poll(sessionRef.current, deviceId, token, lastId.current);
        if (next.length) {
          lastId.current = next[next.length - 1].id;
          next.forEach(appendMessage);
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
          device and the Core. Real-time replies arrive over the device WebSocket,
          with bounded reconnect and an HTTP polling fallback. This client cannot
          approve actions or clear blockers.
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
          {(connected || reconnecting) && (
            <Button onClick={disconnect}>Disconnect</Button>
          )}
          {reconnecting && <Badge tone="warn">Reconnecting…</Badge>}
          {connected && <Badge tone="ok">Connected via {transport.toUpperCase()}</Badge>}
        </div>
        {error && <Notice tone="danger">{error}</Notice>}
      </Panel>

      {(connected || reconnecting) && (
        <Panel title="Conversation">
          <div className="relay__messages" data-testid="relay-messages">
            {messages.length === 0 && <p className="t-faint">No messages yet.</p>}
            {messages.map((message) => {
              const brief = parseRelayBrief(message.kind, message.text);
              return (
                <div key={message.id} className={`relay__message relay__message--${message.role}`}>
                  <span className="relay__role">{message.role}</span>
                  {brief
                    ? <RelayBriefCard brief={brief} onAction={setRoute} />
                    : <span>{message.text}</span>}
                </div>
              );
            })}
          </div>
          {tracked && (
            <div className="row row--wrap" style={{ gap: 8, marginTop: 8 }}
              data-testid="relay-tracked-action">
              <Badge tone={tracked.actionState === 'completed' ? 'ok' : 'warn'}>
                {tracked.actionState}
              </Badge>
              <span className="t-mono t-faint">reply #{tracked.replySequence}</span>
              {tracked.actionState === 'uncertain' && (
                <Button size="sm" variant="ghost" icon="chevronRight"
                  onClick={() => setRoute('overview')}>
                  Reconcile
                </Button>
              )}
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
