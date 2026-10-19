import { useMemo, useState } from 'react';
import { Icon } from '../components/icons';
import {
  Badge,
  Button,
  Chips,
  DataBadge,
  DemoTag,
  EmptyState,
  KV,
  Notice,
  Panel,
  Rail,
  ScreenHead,
  Stat,
} from '../components/ui';
import { useAppStore } from '../store/appStore';
import { demoCalls, demoNotifications, demoThreads } from '../data/demo';
import { clockTime, dateTime, relTime } from '../lib/format';

/* Communications.
 *
 * The backend has exactly one outbound communication tool: local text-to-speech
 * (POST /api/v1/voice/speak). There is no email, SMS, messaging or telephony
 * endpoint — those capabilities resolve to UNAVAILABLE in the live registry,
 * which this screen reports next to its demonstration records.
 */

type Thread = (typeof demoThreads)[number];

const CHANNEL_TONE: Record<string, 'info' | 'accent' | 'warn'> = {
  Email: 'info',
  Message: 'accent',
  Draft: 'warn',
};

const CALL_TONE: Record<string, 'ok' | 'danger' | 'warn'> = {
  completed: 'ok',
  failed: 'danger',
  rejected: 'warn',
  pending: 'warn',
};

export function CommunicationsScreen() {
  const dataMode = useAppStore((s) => s.dataMode);
  const [tab, setTab] = useState<'messages' | 'calls' | 'notifications'>('messages');
  const [filter, setFilter] = useState<'__all__' | string>('__all__');

  const threads = useMemo(
    () =>
      filter === '__all__'
        ? demoThreads
        : demoThreads.filter((t) => t.channel === filter),
    [filter]
  );

  const unread = demoThreads.reduce((n, t) => n + t.unread, 0);
  const drafts = demoThreads.filter((t) => t.channel === 'Draft').length;

  return (
    <div className="screen">
      <div className="screen__inner">
        <ScreenHead
          eyebrow="Personal"
          title="Communications"
          description="One place for messages, calls and alerts — with a hard line between what was sent and what is still a draft."
          actions={<DemoTag />}
        />

        <Notice tone="warn" icon="alert">
          <span>
            <strong>No messaging or telephony integration exists.</strong> Sythion cannot send email, SMS or place a
            call today. The records below are demonstration data, and nothing here was transmitted. Outbound speech is
            the only communication path the backend implements.
          </span>
        </Notice>

        <div className="grid grid--4">
          <Stat label="Unread" icon="inbox" value={unread} foot="Across all channels" />
          <Stat
            label="Awaiting send"
            icon="file"
            value={drafts}
            tone={drafts > 0 ? 'accent' : undefined}
            foot="Drafted but never transmitted"
          />
          <Stat
            label="Calls today"
            icon="phone"
            value={demoCalls.length}
            foot="Demonstration call history"
          />
          <Stat
            label="Alerts"
            icon="bell"
            value={demoNotifications.filter((n) => n.unread).length}
            foot="Unacknowledged"
          />
        </div>

        <div className="row row--wrap row--between">
          <Chips
            value={tab}
            onChange={setTab}
            allLabel=""
            options={[
              { value: 'messages', label: 'Messages' },
              { value: 'calls', label: 'Call history' },
              { value: 'notifications', label: 'Alerts' },
            ]}
          />
          {tab === 'messages' && (
            <Chips
              value={filter}
              onChange={setFilter}
              options={[
                { value: 'Email', label: 'Email' },
                { value: 'Message', label: 'Messages' },
                { value: 'Draft', label: 'Drafts' },
              ]}
            />
          )}
        </div>

        {tab === 'messages' && (
          <div className="grid grid--main">
            <Panel title="Conversations" icon="comms" flush actions={<DemoTag />}>
              {threads.length === 0 ? (
                <EmptyState icon="comms" title="Nothing on this channel" />
              ) : (
                <div className="list">
                  {threads.map((t) => (
                    <ThreadRow key={t.id} thread={t} />
                  ))}
                </div>
              )}
            </Panel>

            <div className="stack">
              <Panel title="Compose" icon="edit" tight>
                <div className="stack stack--tight">
                  <Notice tone="danger" icon="lock">
                    <span>
                      Sending is disabled. <span className="t-mono">POST /api/v1/email/send</span> does not exist in this
                      backend, and outbound communication is a high-risk capability that requires owner authorization
                      even once it does.
                    </span>
                  </Notice>
                  <div className="field">
                    <span className="field__label">To</span>
                    <input className="input" placeholder="No recipient can be resolved" disabled />
                  </div>
                  <div className="field">
                    <span className="field__label">Message</span>
                    <textarea className="textarea" placeholder="Compose disabled" disabled />
                  </div>
                  <Button icon="send" variant="primary" block disabled>
                    Send
                  </Button>
                </div>
              </Panel>

              <Panel title="Available today" icon="plug" actions={<DataBadge origin={dataMode === 'preview' ? 'demo' : 'live'} />}>
                <div className="stack stack--tight">
                  <span className="row t-muted" style={{ gap: 7, alignItems: 'flex-start' }}>
                    <Icon name="check" size={12} style={{ color: 'var(--ok)', flex: '0 0 auto', marginTop: 2 }} />
                    <span>
                      Local text-to-speech via <span className="t-mono">POST /api/v1/voice/speak</span>
                    </span>
                  </span>
                  <span className="row t-muted" style={{ gap: 7, alignItems: 'flex-start' }}>
                    <Icon name="close" size={12} style={{ color: 'var(--danger)', flex: '0 0 auto', marginTop: 2 }} />
                    <span>Email — no credentials, no endpoint</span>
                  </span>
                  <span className="row t-muted" style={{ gap: 7, alignItems: 'flex-start' }}>
                    <Icon name="close" size={12} style={{ color: 'var(--danger)', flex: '0 0 auto', marginTop: 2 }} />
                    <span>SMS / messaging bridge — not registered</span>
                  </span>
                  <span className="row t-muted" style={{ gap: 7, alignItems: 'flex-start' }}>
                    <Icon name="close" size={12} style={{ color: 'var(--danger)', flex: '0 0 auto', marginTop: 2 }} />
                    <span>Telephone — no telephony provider</span>
                  </span>
                </div>
              </Panel>
            </div>
          </div>
        )}

        {tab === 'calls' && (
          <Panel title="Call history" icon="phone" flush actions={<DemoTag />}>
            <div className="list">
              {demoCalls.map((c) => (
                <div className="list__row" key={c.id}>
                  <Rail tone={CALL_TONE[c.outcome]} />
                  <div className="list__main">
                    <span className="t-title" style={{ fontSize: 12.5 }}>
                      {c.party}
                    </span>
                    <span className="list__meta">
                      <Badge tone={c.direction === 'inbound' ? 'info' : 'idle'}>
                        {c.direction === 'inbound' ? 'Inbound' : 'Outbound'}
                      </Badge>
                      <span>{clockTime(c.started)}</span>
                      <span>· {dateTime(c.started)}</span>
                      <span>· {c.duration}</span>
                    </span>
                    <span className="t-muted">{c.summary}</span>
                  </div>
                  <div className="list__trail">
                    <Badge tone={CALL_TONE[c.outcome]}>{c.outcome}</Badge>
                  </div>
                </div>
              ))}
            </div>
          </Panel>
        )}

        {tab === 'notifications' && (
          <Panel title="Alerts" icon="bell" flush actions={<DemoTag />}>
            <div className="list">
              {demoNotifications.map((n) => (
                <div className="list__row" key={n.id}>
                  <Rail tone={n.tone} />
                  <div className="list__main">
                    <span className="t-title" style={{ fontSize: 12.5 }}>
                      {n.title}
                    </span>
                    <span className="t-muted">{n.body}</span>
                    <span className="list__meta">
                      <span>{relTime(n.when)}</span>
                    </span>
                  </div>
                  <div className="list__trail">
                    {n.unread ? <Badge tone="accent">unread</Badge> : <Badge tone="idle">read</Badge>}
                  </div>
                </div>
              ))}
            </div>
          </Panel>
        )}

        <Panel title="Delivery guarantees" icon="shield" tight>
          <KV
            items={[
              { k: 'Sent vs draft', v: 'Drafts are stored locally in the interface and never transmitted' },
              { k: 'Call outcomes', v: 'Completed, failed and rejected attempts are always distinguished' },
              { k: 'Authorization', v: 'Outbound communication is HIGH risk and gated by capability policy' },
              { k: 'Evidence', v: 'A message is only marked sent once the backend confirms delivery' },
            ]}
          />
        </Panel>
      </div>
    </div>
  );
}

function ThreadRow({ thread }: { thread: Thread }) {
  return (
    <div className="list__row">
      <Rail tone={thread.channel === 'Draft' ? 'warn' : CHANNEL_TONE[thread.channel] ?? 'idle'} />
      <div className="list__main">
        <div className="row row--between" style={{ gap: 10 }}>
          <span className="t-title t-truncate" style={{ fontSize: 12.5 }}>
            {thread.title}
          </span>
          <span className="t-faint">{thread.when}</span>
        </div>
        <span className="t-muted t-clamp-2">{thread.preview}</span>
        <span className="list__meta">
          <Badge tone={CHANNEL_TONE[thread.channel] ?? 'idle'}>{thread.channel}</Badge>
          {thread.channel === 'Draft' && <Badge tone="warn">not sent</Badge>}
          {thread.unread > 0 && <Badge tone="accent">{thread.unread} unread</Badge>}
        </span>
      </div>
    </div>
  );
}