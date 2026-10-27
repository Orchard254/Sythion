import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query';
import { conversationService, voiceService } from '../services/api';
import { conversationTasks, nextSpokenReply } from '../lib/firstConversation';
import { Icon } from '../components/icons';
import { TaskDetailDrawer } from '../components/TaskDetailDrawer';
import { AssistantStatusIndicator } from '../components/AssistantStatus';
import { useVoiceCapture } from '../hooks/useVoiceCapture';
import {
  Badge,
  Button,
  Chips,
  DataBadge,
  DemoTag,
  EmptyState,
  IconButton,
  Panel,
  Rail,
  Segmented,
} from '../components/ui';
import { useActions, useAgent, useTaskEvents, useTasks, useTools, useVoiceAssets } from '../hooks/useBackend';
import { useAppStore } from '../store/appStore';
import { duration, humanize, relTime, taskLabel, taskTone } from '../lib/format';
import { deriveAuthorization } from '../lib/autonomy';
import { awaitingReason, deriveAssistantStatus, findAwaitingTasks } from '../lib/assistantStatus';
import type { Task } from '../types';

/* AI Assistant.
 *
 * Conversation-first. The thread is the interface; headings, timestamps,
 * tool traces and logs are progressively disclosed or omitted entirely.
 *
 * Two rules govern what this screen is allowed to claim:
 *
 *  1. In preview mode nothing runs. There is no state in which demo data is
 *     described as executing, and the composer says so plainly rather than
 *     implying that enabling it would start real work.
 *  2. An approval request is never behind a tab or below the fold. Tasks
 *     waiting on the owner are pinned above the thread with a reply control.
 */

type Pane = 'conversation' | 'executions';

export function AssistantScreen() {
  const [pane, setPane] = useState<Pane>('conversation');
  const [filter, setFilter] = useState<'__all__' | 'running' | 'waiting_for_user' | 'failed' | 'completed'>('__all__');
  const [selected, setSelected] = useState<Task | null>(null);
  const [showTools, setShowTools] = useState(false);
  const [voiceError, setVoiceError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const queryClient = useQueryClient();
  const [replyMode, setReplyMode] = useState<'text' | 'voice'>(() => localStorage.getItem('sythion.replyMode') === 'voice' ? 'voice' : 'text');
  const [playbackTaskId, setPlaybackTaskId] = useState<string | null>(null);
  const playbackBusy = useRef(false);
  const submittedIds = useRef(new Set<string>());
  const narratedIds = useRef(new Set<string>());
  const [captureBusy, setCaptureBusy] = useState(false);
  const tasks = useTasks();
  const tools = useTools();
  const agent = useAgent();
  const assets = useVoiceAssets();
  const actions = useActions();

  const transcriptDraft = useAppStore((s) => s.transcriptDraft);
  const setTranscriptDraft = useAppStore((s) => s.setTranscriptDraft);
  const setVoiceEnabled = useAppStore((s) => s.setVoiceEnabled);
  const isListening = useAppStore((s) => s.isListening);
  const isSpeaking = useAppStore((s) => s.isVoiceSpeaking);
  const voiceSttAvailable = useAppStore((s) => s.voiceSttAvailable);
  const isConnected = useAppStore((s) => s.isConnected);
  const notify = useAppStore((s) => s.notify);
  const dataMode = useAppStore((s) => s.dataMode);

  // Pure conversations are persisted separately from executable tasks.
  const conversationQuery = useInfiniteQuery({
    queryKey: ['conversation-history'],
    queryFn: ({ pageParam }) => conversationService.list(pageParam),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    enabled: dataMode === 'live' && isConnected,
    staleTime: 10_000,
    retry: 1,
  });
  const { data: recentResponses = [] } = useQuery<Task[]>({
    queryKey: ['conversation-responses'],
    queryFn: async () => [],
    initialData: [],
    enabled: false,
    staleTime: Infinity,
    gcTime: Infinity,
  });
  const storedConversations = useMemo<Task[]>(() =>
    (conversationQuery.data?.pages.flatMap((page) => page.conversations) ?? []).map((item) => ({
      id: item.id, conversation_id: item.id, interaction_type: 'CONVERSATION',
      original_request: item.user_text, final_result: item.assistant_text,
      state: 'completed', version: 1, normalized_goal: null, active_action_id: null,
      pending_question: null, terminal_reason: null,
      created_at: item.created_at, updated_at: item.created_at,
    })), [conversationQuery.data]);
  useEffect(() => {
    if (!storedConversations.length || !recentResponses.length) return;
    const persisted = new Set(storedConversations.map((item) => item.id));
    if (recentResponses.some((item) => persisted.has(item.id))) {
      queryClient.setQueryData<Task[]>(['conversation-responses'], (old = []) =>
        old.filter((item) => !persisted.has(item.id))
      );
    }
  }, [storedConversations, recentResponses, queryClient]);

  const { startCapture, stopCapture } = useVoiceCapture();

  const allTasks = useMemo(() => tasks.data ?? [], [tasks.data]);
  const threadItems = useMemo(
    () => dataMode === 'preview' ? allTasks : [...allTasks, ...Array.from(
      new Map([...storedConversations, ...recentResponses].map((item) => [item.id, item])).values()
    )],
    [allTasks, storedConversations, recentResponses, dataMode]
  );

  const status = useMemo(
    () =>
      deriveAssistantStatus({
        dataMode,
        isConnected,
        isListening,
        isSpeaking,
        submitting,
        tasks: allTasks,
        error: voiceError ?? (tasks.error ? tasks.error.message : null),
        // No analyser is attached, so no level is claimed.
        audioLevel: null,
      }),
    [dataMode, isConnected, isListening, isSpeaking, submitting, allTasks, voiceError, tasks.error]
  );

  // Tasks blocked on the owner. Pinned, and excluded from the thread so they
  // are never shown twice. The predicate lives in lib/assistantStatus so the
  // status model and this view cannot disagree about what "blocked" means.
  const awaitingTasks = useMemo(() => findAwaitingTasks(allTasks), [allTasks]);
  const pinnedIds = useMemo(() => new Set(awaitingTasks.map((t) => t.id)), [awaitingTasks]);

  // The thread. Completed work and genuine failures read as conversation;
  // anything waiting on the owner is pinned above instead.
  const thread = useMemo(
    () =>
      conversationTasks(threadItems, pinnedIds),
    [threadItems, pinnedIds]
  );

  const filtered = useMemo(() => {
    const live = allTasks.filter((t) => t.state !== 'cancelled');
    return filter === '__all__' ? live : live.filter((t) => t.state === filter);
  }, [allTasks, filter]);

  const scrollRef = useRef<HTMLDivElement>(null);
  const olderScroll = useRef<{ height: number; top: number } | null>(null);
  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    if (olderScroll.current) {
      el.scrollTo({ top: olderScroll.current.top + el.scrollHeight - olderScroll.current.height });
      olderScroll.current = null;
    } else {
      el.scrollTo({ top: el.scrollHeight });
    }
  }, [thread.length, awaitingTasks.length, transcriptDraft]);

  const onToggleVoice = useCallback(async () => {
    if (captureBusy) return;
    setVoiceError(null);
    setCaptureBusy(true);
    try {
      if (isListening) {
        await stopCapture();
        setVoiceEnabled(false);
      } else {
        if (!actions.live) throw new Error('Connect Sythion before using the microphone.');
        setVoiceEnabled(true);
        await startCapture();
      }
    } catch (err) {
      setVoiceEnabled(false);
      setVoiceError(err instanceof Error ? err.message : 'Microphone unavailable. You can still type.');
    } finally {
      setCaptureBusy(false);
    }
  }, [actions.live, captureBusy, isListening, setVoiceEnabled, startCapture, stopCapture]);

  const playReply = useCallback(async (task: Task) => {
    if (playbackBusy.current || !task.final_result || !actions.live) return;
    playbackBusy.current = true;
    setPlaybackTaskId(task.id);
    setVoiceError(null);
    try {
      // The local playback endpoint accepts at most 1,000 characters.
      const result = await voiceService.speak(task.final_result.slice(0, 1000));
      if (!result.success) throw new Error('Spoken playback is unavailable. Your reply is still shown below.');
    } catch (err) {
      setVoiceError(err instanceof Error ? err.message : 'Playback failed. Read the reply below or try Listen again.');
    } finally {
      playbackBusy.current = false;
      setPlaybackTaskId(null);
    }
  }, [actions.live]);

  useEffect(() => {
    if (replyMode !== 'voice' || playbackTaskId || isListening || captureBusy || document.visibilityState !== 'visible') return;
    const task = nextSpokenReply(threadItems, submittedIds.current, narratedIds.current);
    if (!task) return;
    // Mark before playback so polling and failures never repeat audio.
    narratedIds.current.add(task.id);
    void playReply(task);
  }, [threadItems, replyMode, playbackTaskId, isListening, captureBusy, playReply]);

  const sttReady = voiceSttAvailable ?? assets.data?.stt_installed ?? false;
  const authorization = deriveAuthorization(agent.data);
  // When the backend reports no authorization level we still behave as guarded,
  // but we must not present that as something it confirmed.
  const authorizationLabel = authorization.known ? authorization.label : `${authorization.label} (assumed)`;

  const voiceUnavailable = !sttReady;
  const voiceBlocked = dataMode === 'preview' || !actions.live;

  return (
    <div className="screen screen--assistant">
      <div className="assistant">
        {/* One slim bar. No heading, no description paragraph: the top bar
            already names the screen. */}
        <div className="assistant__bar">
          <h1 className="sr-only">Talk to Sythion</h1>
          <AssistantStatusIndicator status={status} />
          <span className="spacer" />
          {dataMode === 'preview' ? <DemoTag /> : <DataBadge origin="live" />}
          <label className="row" style={{ gap: 6 }}>
            <span className="t-muted">Replies</span>
            <select aria-label="Synth reply mode" className="input" style={{ width: 'auto' }} value={replyMode} onChange={(e) => {
              const mode = e.target.value === 'voice' ? 'voice' : 'text';
              setReplyMode(mode); localStorage.setItem('sythion.replyMode', mode);
            }}>
              <option value="text">Text</option><option value="voice">Voice + text</option>
            </select>
          </label>
          <Segmented
            value={pane}
            onChange={setPane}
            ariaLabel="Assistant view"
            options={[
              { value: 'conversation', label: 'Conversation' },
              { value: 'executions', label: 'Executions' },
            ]}
          />
          <IconButton
            icon="layers"
            label={showTools ? 'Hide the tool list' : 'Show the tool list'}
            tone={showTools ? 'default' : 'ghost'}
            onClick={() => setShowTools((v) => !v)}
          />
        </div>

        {/* Essential safety information, pinned and always visible. */}
        {awaitingTasks.length > 0 && (
          <div className="attention" role="region" aria-label="Needs your decision">
            {awaitingTasks.map((t) => (
              <ApprovalCard key={t.id} task={t} onOpen={() => setSelected(t)} />
            ))}
          </div>
        )}

        {showTools && (
          <Panel
            title="Tools this agent can invoke"
            icon="layers"
            tight
            actions={
              <>
                {dataMode === 'preview' ? <DemoTag /> : <DataBadge origin="live" />}
                <IconButton icon="close" label="Hide the tool list" tone="ghost" onClick={() => setShowTools(false)} />
              </>
            }
          >
            {tools.isLoading ? (
              <div className="skeleton" style={{ height: 60 }} />
            ) : (
              <div className="list list--dense">
                {(tools.data ?? []).map((t) => (
                  <div className="list__row" key={t.name}>
                    <div className="list__main">
                      <span className="t-mono t-title" style={{ fontSize: 12 }}>
                        {t.name}
                      </span>
                      <span className="t-muted t-clamp-2">{t.description}</span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Panel>
        )}

        {pane === 'conversation' ? (
          <>
            <div className="chat chat--assistant">
              <div className="chat__scroll" ref={scrollRef}>
                {dataMode === 'live' && conversationQuery.isError && (
                  <div role="alert">
                    <p>Conversation history could not be loaded. Check the connection and try again.</p>
                    <Button size="sm" variant="subtle" onClick={() => void conversationQuery.refetch()}>Retry history</Button>
                  </div>
                )}
                {dataMode === 'live' && conversationQuery.isLoading && <p role="status">Loading conversation history…</p>}
                {dataMode === 'live' && conversationQuery.hasNextPage && (
                  <Button size="sm" variant="subtle" disabled={conversationQuery.isFetchingNextPage} onClick={() => {
                    const el = scrollRef.current;
                    if (el) olderScroll.current = { height: el.scrollHeight, top: el.scrollTop };
                    void conversationQuery.fetchNextPage().then((result) => {
                      const pages = result.data?.pages;
                      if (result.isFetchNextPageError || (pages && pages[pages.length - 1]?.conversations.length === 0)) olderScroll.current = null;
                    });
                  }}>
                    {conversationQuery.isFetchingNextPage ? 'Loading…' : 'Load older messages'}
                  </Button>
                )}
                {thread.length === 0 ? (
                  // Shown even when an approval is pinned above: the pinned
                  // card is not conversation, and leaving the panel blank
                  // reads as a broken or loading screen.
                  <EmptyState
                    icon="assistant"
                    title={dataMode === 'preview' ? 'Nothing to show' : 'Ask Sythion something'}
                    description={
                      dataMode === 'preview'
                        ? 'This is a demonstration thread. Start a backend to hold a real conversation.'
                        : 'Hello, I’m Synth. Type a message below, or use the microphone, review your words, then send.'
                    }
                  />
                ) : (
                  thread.map((t) => <Exchange key={t.id} task={t} onOpen={() => setSelected(t)} onSpeak={() => void playReply(t)} playbackBusy={playbackTaskId !== null} />)
                )}
              </div>

              <div className="composer-foot composer-foot--assistant">
                {voiceError && <p role="alert" className="t-danger">{voiceError}</p>}
                {playbackTaskId && <p role="status">Requesting spoken playback… The full reply stays in the conversation.</p>}
                {thread.length === 0 && dataMode === 'live' && <Button variant="subtle" onClick={() => setTranscriptDraft('Hello Synth, what can you help me with?')}>Try a first message</Button>}
                <AssistantComposer
                  value={transcriptDraft}
                  onChange={setTranscriptDraft}
                  submitting={submitting || captureBusy}
                  status={status}
                  blocked={status.state === 'offline'}
                  voiceActive={status.state === 'listening' || status.state === 'speaking'}
                  voiceBlocked={voiceBlocked || voiceUnavailable || captureBusy}
                  voiceNotice={voiceUnavailable ? 'Speech-to-text is not installed on this machine' : null}
                  autonomyLabel={humanize(agent.data?.state ?? 'unknown')}
                  authorizationLabel={authorizationLabel}
                  authorizationTone={authorization.known ? authorization.tone : 'unknown'}
                  onToggleVoice={() => void onToggleVoice()}
                  onSubmit={async (text) => {
                    if (submitting || isListening || captureBusy) return;
                    setSubmitting(true);
                    setVoiceError(null);
                    try {
                      const task = await actions.createTask(text);
                      submittedIds.current.add(task.id);
                      if (task.interaction_type === 'CONVERSATION') {
                        await queryClient.cancelQueries({ queryKey: ['conversation-responses'] });
                        queryClient.setQueryData<Task[]>(['conversation-responses'], (old = []) => [...old.filter((t) => t.id !== task.id), task]);
                        void queryClient.invalidateQueries({ queryKey: ['conversation-history'] });
                      } else {
                        await queryClient.cancelQueries({ queryKey: ['tasks'] });
                        queryClient.setQueryData<Task[]>(['tasks'], (old = []) => [task, ...old.filter((t) => t.id !== task.id)]);
                      }
                      setTranscriptDraft('');
                      tasks.refetch();
                      notify({
                        tone: 'accent',
                        title: 'Sent',
                        body: text.slice(0, 120),
                        route: 'assistant',
                      });
                    } catch (err) {
                      const message = err instanceof Error ? err.message : 'Failed to send the message';
                      notify({ tone: 'danger', title: 'Could not send', body: message });
                      setVoiceError(message);
                    } finally {
                      setSubmitting(false);
                    }
                  }}
                />
              </div>
            </div>
          </>
        ) : (
          <div className="stack">
            <Chips
              value={filter}
              onChange={setFilter}
              options={[
                { value: 'running', label: 'Running' },
                { value: 'waiting_for_user', label: 'Needs input' },
                { value: 'failed', label: 'Failed' },
                { value: 'completed', label: 'Completed' },
              ]}
            />

            <Panel title="Task executions" icon="activity" flush>
              {tasks.isLoading ? (
                <div className="panel__body">
                  <div className="skeleton" style={{ height: 80 }} />
                </div>
              ) : filtered.length === 0 ? (
                <EmptyState
                  icon="inbox"
                  title="Nothing matches this filter"
                  description="Switch filters, or send a message to start work."
                />
              ) : (
                <div className="list">
                  {filtered.map((t) => (
                    <button
                      type="button"
                      key={t.id}
                      className="list__row list__row--clickable"
                      onClick={() => setSelected(t)}
                    >
                      <Rail tone={taskTone(t.state)} />
                      <div className="list__main">
                        <span className="t-title t-truncate" style={{ fontSize: 12.5 }}>
                          {t.normalized_goal || t.original_request}
                        </span>
                        <span className="list__meta">
                          <Badge tone={taskTone(t.state)}>{taskLabel(t.state)}</Badge>
                          <span>{relTime(t.updated_at)}</span>
                          {t.active_action_id && <span className="t-mono">{t.active_action_id}</span>}
                        </span>
                        {t.pending_question && (
                          <span className="t-warn t-clamp-2" style={{ fontSize: 11.5 }}>
                            {t.pending_question}
                          </span>
                        )}
                      </div>
                      <div className="list__trail">
                        {dataMode === 'live' && (t.state === 'running' || t.state === 'waiting_for_user') && (
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={(e) => {
                              e.stopPropagation();
                              void actions.cancelTask(t.id).catch((err: Error) =>
                                notify({ tone: 'danger', title: 'Interrupt failed', body: err.message })
                              );
                            }}
                          >
                            Stop
                          </Button>
                        )}
                        <Icon name="chevronRight" size={13} style={{ color: 'var(--text-3)' }} />
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </Panel>
          </div>
        )}
      </div>

      {selected && (
        <TaskDetailDrawer
          task={selected}
          origin={tasks.origin}
          onClose={() => setSelected(null)}
          onChanged={tasks.refetch}
        />
      )}
    </div>
  );
}

/* --- Composer -------------------------------------------------------------
 * Prominent by design: this is the primary control on the screen. The
 * microphone is a large circular control beside the field, and the permission
 * boundary stays visible as a compact shield chip.
 */

function AssistantComposer({
  value,
  onChange,
  submitting,
  status,
  blocked,
  voiceActive,
  voiceBlocked,
  voiceNotice,
  autonomyLabel,
  authorizationLabel,
  authorizationTone,
  onToggleVoice,
  onSubmit,
}: {
  value: string;
  onChange: (v: string) => void;
  submitting: boolean;
  status: ReturnType<typeof deriveAssistantStatus>;
  blocked: boolean;
  voiceActive: boolean;
  voiceBlocked: boolean;
  voiceNotice: string | null;
  autonomyLabel: string;
  authorizationLabel: string;
  /** 'unknown' when the backend reported no authorization level at all. */
  authorizationTone: 'ok' | 'warn' | 'unknown';
  onToggleVoice: () => void;
  onSubmit: (text: string) => Promise<void>;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 168)}px`;
  }, [value]);

  const canSend = value.trim().length > 0 && !blocked && !submitting && !voiceActive;

  const placeholder = blocked
    ? 'Preview mode — nothing here runs'
    : voiceActive
      ? 'Listening… stop the microphone to review and send'
      : 'Message Sythion';

  return (
    <div className="ac">
      <div className="ac__field">
        <textarea
          ref={ref}
          className="composer__input ac__input"
          rows={1}
          value={value}
          placeholder={placeholder}
          aria-label="Message Sythion"
          disabled={blocked || submitting}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              if (canSend) void onSubmit(value.trim());
            }
          }}
        />
        <div className="ac__actions">
          <button
            type="button"
            className={`mic-btn${voiceActive ? ' mic-btn--on' : ''}`}
            aria-pressed={voiceActive}
            aria-label={voiceActive ? 'Stop listening' : 'Start voice input'}
            title={
              voiceBlocked
                ? 'Voice input is routed to the local speech service and needs a running backend'
                : voiceNotice
                  ? voiceNotice
                  : voiceActive
                    ? 'Stop listening'
                    : 'Start voice input'
            }
            disabled={voiceBlocked}
            onClick={onToggleVoice}
          >
            <Icon name="microphone" size={17} />
            {voiceActive && <span className="mic-btn__ripple" />}
          </button>
          <button
            type="button"
            className="mic-btn mic-btn--send"
            aria-label="Send message"
            title={blocked ? 'Preview mode — messages are not sent' : 'Send message'}
            disabled={!canSend}
            onClick={() => void onSubmit(value.trim())}
          >
            <Icon name="send" size={16} />
          </button>
        </div>
      </div>

      <div className="ac__foot">
        <span
          className={`ac__boundary${authorizationTone === 'warn' ? ' ac__boundary--warn' : ''}${
            authorizationTone === 'unknown' ? ' ac__boundary--unknown' : ''
          }`}
          title="Authorization is enforced by the backend for every tool call."
        >
          <Icon name="shield" size={12} />
          {authorizationLabel}
        </span>
        <span className="ac__meta">{autonomyLabel}</span>
        <span className="spacer" />
        {blocked ? (
          <span className="ac__preview">
            Preview mode — messages are not sent and no task will be created.
          </span>
        ) : voiceNotice ? (
          <span className="t-faint">{voiceNotice}</span>
        ) : (
          <span className="ac__meta">{submitting ? 'Sending…' : 'Enter to send'}</span>
        )}
      </div>

      {/* Screen-reader confirmation of the current state. The visible indicator
          carries the same information, so this is sr-only to avoid a double
          announcement. */}
      <span className="sr-only" role="status" aria-live="polite">
        {status.label}
        {status.detail ? `. ${status.detail}` : ''}
      </span>
    </div>
  );
}

/* --- Pinned approval card ------------------------------------------------ */

function ApprovalCard({ task, onOpen }: { task: Task; onOpen: () => void }) {
  const [reply, setReply] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const actions = useActions();
  const notify = useAppStore((s) => s.notify);
  const dataMode = useAppStore((s) => s.dataMode);
  const blocked = dataMode === 'preview';

  // `waiting_for_user` asks a question; `interrupted` records an
  // authorization hold. A live backend may report either with no text at all,
  // so the state alone decides what is pinned; the explanation is a fallback.
  const held = task.state === 'interrupted';
  const stated = task.pending_question ?? task.terminal_reason ?? null;
  const reason = awaitingReason(task);

  const send = async () => {
    const answer = reply.trim();
    if (!answer || busy) return;
    setBusy(true);
    setError(null);
    try {
      await actions.replyToTask(task.id, answer);
      setReply('');
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Could not send your answer';
      setError(message);
      notify({ tone: 'danger', title: 'Could not send', body: message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="attention__item">
      <span className="attention__icon" aria-hidden="true">
        <Icon name={held ? 'lock' : 'alert'} size={14} />
      </span>
      <div className="attention__body">
        <div className="attention__head">
          <Badge tone={held ? 'danger' : 'warn'}>
            {held ? 'Held for your approval' : 'Needs your answer'}
          </Badge>
          {/* Never truncated: knowing exactly what you are approving is the point. */}
          <span className="t-muted attention__request">{task.original_request}</span>
        </div>
        {stated ? (
          <p className="attention__text">{stated}</p>
        ) : (
          <p className="attention__text">
            Sythion is paused on this and will not continue until you reply.
          </p>
        )}
        {blocked ? (
          <p className="t-faint attention__note">Demonstration record. Start a backend to answer this.</p>
        ) : (
          <div className="attention__reply">
            <input
              className="input"
              value={reply}
              placeholder={held ? 'Approve or decline…' : 'Your answer…'}
              aria-label={`${held ? 'Approve or decline' : 'Answer to Sythion'}: ${reason}`}
              disabled={busy}
              onChange={(e) => setReply(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault();
                  void send();
                }
              }}
            />
            <Button size="sm" variant="primary" disabled={!reply.trim() || busy} onClick={() => void send()}>
              {busy ? 'Sending…' : 'Send'}
            </Button>
            <Button size="sm" variant="ghost" icon="external" onClick={onOpen}>
              Details
            </Button>
          </div>
        )}
        {error && (
          <p className="t-danger" style={{ margin: '6px 0 0', fontSize: 11.5 }}>
            {error}
          </p>
        )}
      </div>
    </div>
  );
}

/* --- Exchange ------------------------------------------------------------ */

const PHASE_LABEL: Record<string, string> = {
  created: 'Queued',
  running: 'Working',
  waiting_for_user: 'Needs you',
  completed: 'Done',
  failed: 'Failed',
  interrupted: 'Held',
  cancelled: 'Cancelled',
};

function Exchange({ task, onOpen, onSpeak, playbackBusy }: { task: Task; onOpen: () => void; onSpeak: () => void; playbackBusy: boolean }) {
  const [open, setOpen] = useState(false);

  // Events are only fetched when the user asks for them. The previous screen
  // polled every task in the thread every 3 seconds.
  const events = useTaskEvents(open ? task.id : null);
  const actionEvents = (events.data ?? []).filter((e) => e.event_type.startsWith('action.'));
  // Only a genuine failure reads as a failure. An interrupted task is held for
  // authorization and is pinned above instead of appearing here.
  const failed = task.state === 'failed';

  return (
    <>
      <div className="msg msg--user">
        <div className="msg__avatar">
          <Icon name="eye" size={13} />
        </div>
        <div className="msg__body">
          <div className="msg__bubble">{task.original_request}</div>
        </div>
      </div>

      <div className="msg msg--agent">
        <div className="msg__avatar">
          <Icon name="assistant" size={13} />
        </div>
        <div className="msg__body">
          {task.final_result ? (
            <div className="msg__bubble">{task.final_result}</div>
          ) : (
            failed && (
              <div className="msg__bubble msg__bubble--fail">
                <span className="msg__bubble-label">
                  <Icon name="alert" size={12} /> This did not finish
                </span>
                {task.terminal_reason}
              </div>
            )
          )}

          {!task.final_result && !failed && <div className="msg__bubble" role="status">{task.state === 'cancelled' ? 'This message was cancelled.' : 'Synth is working on your message…'}</div>}
          {task.final_result && <Button size="sm" variant="subtle" disabled={playbackBusy} onClick={onSpeak}>Listen{task.final_result.length > 1000 ? ' to the first part' : ''}</Button>}

          {/* Compact execution summary. Timestamps and traces live behind the
              disclosure or in the drawer. */}
          {task.interaction_type !== 'CONVERSATION' && <div className="exec-line">
            <button
              type="button"
              className="exec-line__toggle"
              aria-expanded={open}
              onClick={() => setOpen((v) => !v)}
            >
              <Icon name={open ? 'chevronDown' : 'chevronRight'} size={12} />
              <Badge tone={taskTone(task.state)}>{PHASE_LABEL[task.state] ?? taskLabel(task.state)}</Badge>
              <span className="exec-line__time">{duration(task.created_at, task.updated_at)}</span>
              <span className="exec-line__hint">{open ? 'Hide execution' : 'Execution'}</span>
            </button>
            <button type="button" className="exec-line__open" onClick={onOpen}>
              Open
            </button>
          </div>}

          {open && (
            <div className="tool-card">
              {events.isLoading ? (
                <div className="tool-card__body">
                  <div className="skeleton" style={{ height: 44 }} />
                </div>
              ) : actionEvents.length === 0 ? (
                <div className="tool-card__body">No tool actions were recorded for this task.</div>
              ) : (
                <div className="tool-card__body">
                  <div className="t-faint" style={{ marginBottom: 6 }}>
                    {actionEvents.length} action{actionEvents.length === 1 ? '' : 's'}
                  </div>
                  {actionEvents.map((e) => {
                    const failedEvent = e.event_type.includes('fail');
                    return (
                      <div key={e.sequence_num} className="exec-trace">
                        <span className={failedEvent ? 't-danger' : 't-ok'} aria-hidden="true">
                          {failedEvent ? '✗' : '✓'}
                        </span>
                        <span className="exec-trace__text">
                          {String((e.event_data ?? {}).tool ?? e.event_type)} →{' '}
                          {String((e.event_data ?? {}).target ?? 'ok')}
                        </span>
                        <span className="exec-trace__time t-faint t-mono">
                          {new Date(e.created_at).toLocaleTimeString([], {
                            hour: '2-digit',
                            minute: '2-digit',
                            second: '2-digit',
                          })}
                        </span>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </>
  );
}
