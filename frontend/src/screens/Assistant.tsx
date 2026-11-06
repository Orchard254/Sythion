import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query';
import { conversationService, taskService, voiceService } from '../services/api';
import { conversationTasks, nextSpokenReply } from '../lib/firstConversation';
import { Icon } from '../components/icons';
import { TaskDetailDrawer } from '../components/TaskDetailDrawer';
import { AssistantStatusIndicator } from '../components/AssistantStatus';
import { useVoiceCapture } from '../hooks/useVoiceCapture';
import {
  Badge,
  Button,
  DemoTag,
  EmptyState,
} from '../components/ui';
import { useActions, useAgent, useTasks, useVoiceAssets } from '../hooks/useBackend';
import { useAppStore } from '../store/appStore';
import { useNavStore } from '../store/navStore';
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

function createClientRequestId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `composer-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

export function AssistantScreen() {
  const [selected, setSelected] = useState<Task | null>(null);
  const [voiceError, setVoiceError] = useState<string | null>(null);
  const [speechHelpOpen, setSpeechHelpOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [unconfirmedSchedulingTaskIds, setUnconfirmedSchedulingTaskIds] = useState<string[] | null>(null);
  const [composerStorageCompromised, setComposerStorageCompromised] = useState(false);
  const [recoveredTasks, setRecoveredTasks] = useState<Task[]>([]);

  const queryClient = useQueryClient();
  const [replyMode] = useState<'text' | 'voice'>(() => localStorage.getItem('sythion.replyMode') === 'text' ? 'text' : 'voice');
  const [playbackTaskId, setPlaybackTaskId] = useState<string | null>(null);
  const playbackBusy = useRef(false);
  const submittedIds = useRef(new Set<string>());
  const narratedIds = useRef(new Set<string>());
  type PersistedComposerRequest = { text: string; id: string };
  const readPersistedComposerRequest = useCallback((): PersistedComposerRequest | null => {
    try {
      if (typeof sessionStorage === 'undefined') return null;
      const storedText = sessionStorage.getItem('sythion.composer.clientRequestText');
      const storedId = sessionStorage.getItem('sythion.composer.clientRequestId');
      if (typeof storedText !== 'string' || storedText.length === 0) return null;
      if (typeof storedId !== 'string' || storedId.length === 0) return null;
      return { text: storedText, id: storedId };
    } catch {
      // Storage may be unavailable; in-memory tracking still applies.
      return null;
    }
  }, []);
  const hydratedComposerRequest = readPersistedComposerRequest();
  const clientComposerRequestRef = useRef<PersistedComposerRequest | null>(hydratedComposerRequest);
useLayoutEffect(() => {
  try {
    const storedPair = readPersistedComposerRequest();
    if (storedPair) {
      if (!clientComposerRequestRef.current) {
        clientComposerRequestRef.current = storedPair;
      }
      if (useAppStore.getState().transcriptDraft === '') {
        useAppStore.getState().setTranscriptDraft(storedPair.text);
      }
    }
  } catch {
    // Storage may be unavailable; in-memory tracking still applies and no
    // continuity is implied.
  }
}, [readPersistedComposerRequest]);
  const [captureBusy, setCaptureBusy] = useState(false);
  const tasks = useTasks();
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
  const setRoute = useNavStore((s) => s.setRoute);

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

  const conversationAssociatedTaskIds = useMemo(() => {
    const ids = (conversationQuery.data?.pages.flatMap((page) => page.conversations) ?? [])
      .flatMap((conversation) => conversation.background_task_ids ?? [])
      .filter((id): id is string => typeof id === 'string' && id.length > 0);
    return Array.from(new Set(ids));
  }, [conversationQuery.data]);

  const recoverTasksById = useCallback(async (ids: string[]) => {
    const unique = Array.from(new Set(ids));
    const fetchOne = typeof taskService?.get === 'function'
      ? (id: string) => taskService.get(id)
      : null;
    if (!fetchOne || !unique.length) return;
    const fetched = await Promise.all(unique.map((id) => fetchOne(id).catch(() => null)));
    const valid = fetched.filter((item): item is Task => !!item);
    if (!valid.length) return;
    setRecoveredTasks((current) => {
      const merged = [...current];
      for (const item of valid) {
        const index = merged.findIndex((existing) => existing.id === item.id);
        if (index >= 0) merged[index] = item;
        else merged.push(item);
      }
      return merged;
    });
  }, []);

  useEffect(() => {
    if (dataMode !== 'live') return;
    if (!conversationAssociatedTaskIds.length) return;
    void recoverTasksById(conversationAssociatedTaskIds);
  }, [conversationAssociatedTaskIds, dataMode, recoverTasksById]);

  // Poll only recovered terminal tasks whose diagnostic repair is still pending.
  // Terminal completed/cancelled tasks and repaired failures do not need refresh.
  const pendingDiagnosticTaskIds = useMemo(
    () => recoveredTasks
      .filter((task) => task.state === 'failed' && !(task.final_result ?? '').includes('Diagnostic:'))
      .map((task) => task.id),
    [recoveredTasks]
  );
  // Keep the latest pending ids reachable from the polling interval without
  // changing the interval's identity. Serializing the ids gives a stable key so
  // that recovered-task updates which do not change the pending set do not reset
  // the timer. JSON.stringify preserves ids containing commas or other
  // characters without ambiguity.
  const pendingDiagnosticTaskIdsRef = useRef(pendingDiagnosticTaskIds);
  useEffect(() => {
    pendingDiagnosticTaskIdsRef.current = pendingDiagnosticTaskIds;
  }, [pendingDiagnosticTaskIds]);
  const pendingDiagnosticTaskIdsKey = useMemo(
    () => JSON.stringify(pendingDiagnosticTaskIds),
    [pendingDiagnosticTaskIds]
  );
  useEffect(() => {
    if (dataMode !== 'live' || pendingDiagnosticTaskIdsKey === '[]') return;
    const timer = setInterval(() => {
      const ids = pendingDiagnosticTaskIdsRef.current;
      if (ids.length) void recoverTasksById(ids);
    }, 5000);
    return () => clearInterval(timer);
  }, [dataMode, pendingDiagnosticTaskIdsKey, recoverTasksById]);
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
  const allTasksWithRecovered = useMemo(() => {
    const seen = new Set(allTasks.map((task) => task.id));
    return [...allTasks, ...recoveredTasks.filter((task) => !seen.has(task.id))];
  }, [allTasks, recoveredTasks]);
  const threadItems = useMemo(
    () => dataMode === 'preview' ? allTasksWithRecovered : [...allTasksWithRecovered, ...Array.from(
      new Map([...storedConversations, ...recentResponses].map((item) => [item.id, item])).values()
    )],
    [allTasksWithRecovered, storedConversations, recentResponses, dataMode]
  );

  const status = useMemo(
    () =>
      deriveAssistantStatus({
        dataMode,
        isConnected,
        isListening,
        isSpeaking,
        submitting,
        tasks: allTasksWithRecovered.filter((task) => !(task.state === 'waiting_for_user' && (task.waiting_kind === 'clarification' || task.waiting_kind === 'unknown'))),
        error: voiceError ?? (tasks.error ? tasks.error.message : null),
        // No analyser is attached, so no level is claimed.
        audioLevel: null,
      }),
    [dataMode, isConnected, isListening, isSpeaking, submitting, allTasksWithRecovered, voiceError, tasks.error]
  );

  // Tasks blocked on the owner. Pinned, and excluded from the thread so they
  // are never shown twice. The predicate lives in lib/assistantStatus so the
  // status model and this view cannot disagree about what "blocked" means.
  const awaitingTasks = useMemo(() => findAwaitingTasks(allTasksWithRecovered), [allTasksWithRecovered]);
  // Only authorization, permission, and unknown holds are pinned. Ordinary
  // clarification questions render in the thread as normal assistant replies.
  const visibleAwaiting = awaitingTasks.filter((task) =>
    task.state === 'interrupted' ||
    (task.state === 'waiting_for_user' && task.waiting_kind === 'permission')
  );
  const pinnedIds = useMemo(() => new Set(visibleAwaiting.map((t) => t.id)), [visibleAwaiting]);

  // The thread. Completed work and genuine failures read as conversation;
  // anything waiting on the owner is pinned above instead.
  const thread = useMemo(
    () =>
      conversationTasks(threadItems, pinnedIds),
    [threadItems, pinnedIds]
  );

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

  const sttReady = voiceSttAvailable ?? assets.data?.stt_installed ?? false;
  const voiceUnavailable = !sttReady;
  const voiceBlocked = dataMode === 'preview' || !actions.live;

  const onToggleVoice = useCallback(async () => {
    if (captureBusy) return;
    setVoiceError(null);
    // When speech-to-text is unavailable, do not attempt to open a microphone
    // session. Surface the contextual explanation (and its Settings link) so
    // the user knows why voice input cannot start and where to fix it.
    if (!isListening && voiceUnavailable) {
      setSpeechHelpOpen(true);
      return;
    }
    setSpeechHelpOpen(false);
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
  }, [actions.live, captureBusy, isListening, setVoiceEnabled, startCapture, stopCapture, voiceUnavailable]);

  const playReply = useCallback(async (task: Task) => {
    if (playbackBusy.current || !task.final_result || !actions.live) return;
    playbackBusy.current = true;
    setPlaybackTaskId(task.id);
    setVoiceError(null);
    try {
      // The local playback endpoint accepts at most 1,000 characters.
      const result = await voiceService.speak(task.final_result.slice(0, 1000));
      // Known unavailable TTS is expected on machines without a usable voice:
      // the reply is already visible, so this must never become a chat warning
      // or an alarming status. Voice availability belongs in setup / Voice
      // settings, not in the conversation.
      if (!result.success) return;
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Playback failed. Read the reply below.';
      // Preserve genuine, unrelated playback failures (device loss, permissions,
      // network). Known unavailable-TTS messages stay silent in chat.
      if (/playback is unavailable|TTS (is )?unavailable|no (usable )?voice/i.test(message)) return;
      setVoiceError(message);
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

  const authorization = deriveAuthorization(agent.data);
  // When the backend reports no authorization level we still behave as guarded,
  // but we must not present that as something it confirmed.
  const authorizationLabel = authorization.known ? authorization.label : `${authorization.label} (assumed)`;

  const allUnconfirmedSchedulingTasksCompleted =
    Array.isArray(unconfirmedSchedulingTaskIds) &&
    unconfirmedSchedulingTaskIds.length > 0 &&
    unconfirmedSchedulingTaskIds.every((id) =>
      allTasks.some((task) => task.id === id && task.state === 'completed')
    );
  const schedulingWarning: string | null =
    Array.isArray(unconfirmedSchedulingTaskIds) &&
    unconfirmedSchedulingTaskIds.length > 0 &&
    !allUnconfirmedSchedulingTasksCompleted
      ? 'Scheduling is unknown: Sythion could not confirm that this work was ' +
        'scheduled. It was not automatically retried, restarted, cancelled, ' +
        'or assumed to be running.'
      : null;

  return (
    <div className="screen screen--assistant">
      <div className="assistant">
        {/* One slim bar. No heading, no description paragraph: the top bar
            already names the screen. */}
        <div className="assistant__bar">
          <h1 className="sr-only">Talk to Sythion</h1>
          <AssistantStatusIndicator status={status} />
          <span className="spacer" />
          {dataMode === 'preview' ? <DemoTag /> : null}
        </div>

        {/* Essential safety information, pinned and always visible. */}
        {visibleAwaiting.length > 0 && (
          <div className="attention" role="region" aria-label="Needs your decision">
            {visibleAwaiting.map((t) => (
              <ApprovalCard key={t.id} task={t} onOpen={() => setSelected(t)} />
            ))}
          </div>
        )}


        {schedulingWarning && (
          <div className="ac__scheduling-warning" role="status" aria-live="polite">
            {schedulingWarning}
          </div>
        )}

        {composerStorageCompromised && (
          <div className="ac__composer-storage-warning" role="status" aria-live="polite">
            Request identity storage could not be verified as cleared after sending. It will not be reused automatically while this page stays open, but reloading may reopen the previous request; it was not automatically retried or assumed complete.
          </div>
        )}

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
                  thread.map((t) => <Exchange key={t.id} task={t} />)
                )}
              </div>

              <div className="composer-foot composer-foot--assistant">
                {speechHelpOpen && voiceUnavailable && (
                  <div className="ac__voice-help" role="status" aria-live="polite">
                    <p>Speech-to-text is not installed on this machine, so voice input cannot start. You can still type your message. Install the speech model in Settings.</p>
                    <Button size="sm" variant="primary" aria-label="Open Settings" onClick={() => { setSpeechHelpOpen(false); setRoute('settings'); }}>Settings</Button>
                    <Button size="sm" variant="ghost" onClick={() => setSpeechHelpOpen(false)}>Dismiss</Button>
                  </div>
                )}
                {playbackTaskId && <p role="status">Requesting spoken playback… The full reply stays in the conversation.</p>}
                {thread.length === 0 && dataMode === 'live' && <Button variant="subtle" disabled={submitting || captureBusy} onClick={() => setTranscriptDraft('Hello Synth, what can you help me with?')}>Try a first message</Button>}
                <AssistantComposer
                  value={transcriptDraft}
                  onChange={setTranscriptDraft}
                  submitting={submitting || captureBusy}
                  status={status}
                  blocked={status.state === 'offline'}
                  voiceActive={isListening}
                  voiceBlocked={captureBusy || (!isListening && voiceBlocked)}
                  voiceNotice={null}
                  voiceUnavailable={voiceUnavailable}
                  authorizationLabel={authorizationLabel}
                  authorizationTone={authorization.known ? authorization.tone : 'unknown'}
                  onToggleVoice={() => void onToggleVoice()}
                  onSubmit={async (text) => {
                    if (submitting || isListening || captureBusy) return;
                    const submittedDraft = useAppStore.getState().transcriptDraft;
                    const clearSubmittedDraft = () => {
                      if (useAppStore.getState().transcriptDraft === submittedDraft) setTranscriptDraft('');
                    };
                    setSubmitting(true);
                    setVoiceError(null);
                    const priorUnconfirmedSchedulingTaskIds = unconfirmedSchedulingTaskIds;
                    setUnconfirmedSchedulingTaskIds(null);
                    // Capture the submission time before the request completes so the
                    // initiating exchange stays ahead of any background task that
                    // finishes (or even starts) while the request is in flight.
                    const submittedAt = new Date().toISOString();
                    // A stable, caller-supplied request identity for this submission.
                    // It survives an in-flight retry within this tab and is
                    // persisted until the backend accepts the turn, so a reload
                    // does not mint a second request for the same composer text.
                    const readStoredComposerRequest = (): PersistedComposerRequest | null => {
                      // Fail closed while mounted if an earlier successful turn could
                      // not verify deletion of its persisted request identity.
                      if (composerStorageCompromised) return null;
                      try {
                        if (typeof sessionStorage === 'undefined') return null;
                        const storedText = sessionStorage.getItem('sythion.composer.clientRequestText');
                        const storedId = sessionStorage.getItem('sythion.composer.clientRequestId');
                        if (typeof storedText !== 'string' || storedText.length === 0) return null;
                        if (typeof storedId !== 'string' || storedId.length === 0) return null;
                        return { text: storedText, id: storedId };
                      } catch {
                        return null;
                      }
                    };
                    const storedComposerRequest = readStoredComposerRequest();
                    const refComposerRequest = clientComposerRequestRef.current;

                    let clientRequestId = '';
                    if (refComposerRequest && refComposerRequest.text === text) {
                      clientRequestId = refComposerRequest.id;
                    } else if (storedComposerRequest && storedComposerRequest.text === text) {
                      clientRequestId = storedComposerRequest.id;
                      clientComposerRequestRef.current = storedComposerRequest;
                    }
                    if (!clientRequestId) {
                      clientRequestId = createClientRequestId();
                      clientComposerRequestRef.current = { text, id: clientRequestId };
                    }
                    try {
                      if (typeof sessionStorage !== 'undefined' && clientComposerRequestRef.current) {
                        sessionStorage.setItem('sythion.composer.clientRequestText', clientComposerRequestRef.current.text);
                        sessionStorage.setItem('sythion.composer.clientRequestId', clientComposerRequestRef.current.id);
                      }
                    } catch {
                      // Storage may be unavailable; in-memory tracking still applies.
                    }
                    try {
                      const turnResult = await actions.assistantTurn(text, clientRequestId);
                      const exchangeId = turnResult.exchange_id;
                      const mappedTask: Task = {
                        id: exchangeId,
                        conversation_id: exchangeId,
                        interaction_type: 'CONVERSATION',
                        original_request: turnResult.user_text || text,
                        final_result: turnResult.assistant_text,
                        state: turnResult.status === 'failed' || turnResult.status === 'error'
                          ? 'failed'
                          : turnResult.status === 'queued'
                            ? 'queued'
                            : 'completed',
                        version: 1,
                        normalized_goal: null,
                        active_action_id: null,
                        pending_question: null,
                        terminal_reason: null,
                        created_at: submittedAt,
                        updated_at: submittedAt,
                      };
                      submittedIds.current.add(exchangeId);
                      await queryClient.cancelQueries({ queryKey: ['conversation-responses'] });
                      queryClient.setQueryData<Task[]>(['conversation-responses'], (old = []) => [...old.filter((item) => item.id !== mappedTask.id), mappedTask]);
                      void queryClient.invalidateQueries({ queryKey: ['conversation-history'] });
                      const backgroundTaskIds = Array.isArray(turnResult.background_task_ids)
                        ? turnResult.background_task_ids
                        : [];
                      if (backgroundTaskIds.length) {
                        void tasks.refetch();
                      }
                      if (
                        backgroundTaskIds.length &&
                        turnResult.scheduling_established !== true
                      ) {
                        setUnconfirmedSchedulingTaskIds(backgroundTaskIds);
                      } else {
                        setUnconfirmedSchedulingTaskIds(null);
                      }
                      clearSubmittedDraft();
                      const completedRequestId = clientRequestId;
                      const completedPair = clientComposerRequestRef.current;
                      if (completedPair && completedPair.text === text && completedPair.id === completedRequestId) {
                        clientComposerRequestRef.current = null;
                      }
                      try {
                        if (typeof sessionStorage !== 'undefined') {
                          const currentStoredText = sessionStorage.getItem('sythion.composer.clientRequestText');
                          const currentStoredId = sessionStorage.getItem('sythion.composer.clientRequestId');
                          if (currentStoredText === text && currentStoredId === completedRequestId) {
                            let removed = false;
                            try {
                              sessionStorage.removeItem('sythion.composer.clientRequestId');
                              sessionStorage.removeItem('sythion.composer.clientRequestText');
                              removed =
                                sessionStorage.getItem('sythion.composer.clientRequestText') === null &&
                                sessionStorage.getItem('sythion.composer.clientRequestId') === null;
                            } catch {
                              removed = false;
                            }
                            if (!removed) {
                              let guarded = false;
                              try {
                                sessionStorage.setItem('sythion.composer.clientRequestText', '');
                                sessionStorage.setItem('sythion.composer.clientRequestId', '');
                                guarded =
                                  sessionStorage.getItem('sythion.composer.clientRequestText') === '' &&
                                  sessionStorage.getItem('sythion.composer.clientRequestId') === '';
                              } catch {
                                guarded = false;
                              }
                              if (!guarded) {
                                // Deletion could not be verified. Fail closed while
                                // mounted so the stale pair is not reused. If both
                                // removeItem and setItem fail or no-op, the pair
                                // may survive a page reload; that cannot be repaired
                                // from inside the browser without a writable durable
                                // marker, so the limitation is documented here and
                                // in tests rather than over-claiming continuity.
                                setComposerStorageCompromised(true);
                              }
                            }
                          }
                        }
                      } catch {
                        // Storage may be unavailable; ignore.
                      }
                      void tasks.refetch();
                      notify({
                        tone: turnResult.status === 'queued' ? 'accent' : 'accent',
                        title: turnResult.status === 'queued' ? 'Queued' : 'Sent',
                        body: text.slice(0, 120),
                        route: 'assistant',
                      });
                    } catch (err) {
                      const message = err instanceof Error ? err.message : 'Failed to send the message';
                      // Only the model-output rejection is proven to have created
                      // no conversation and no task; drop the reserved request
                      // identity so an identical retry can run. All other
                      // backend- or transport-reported failures retain the
                      // reserved identity, because their message text does not
                      // prove the absence of task/exchange side effects.
                       const definiteBackendFailure =
                         message.startsWith('Synth could not understand the model\'s reply.');
                      if (definiteBackendFailure && clientRequestId) {
                        const completedPair = clientComposerRequestRef.current;
                        if (completedPair && completedPair.text === text && completedPair.id === clientRequestId) {
                          clientComposerRequestRef.current = null;
                        }
                        if (!composerStorageCompromised) {
                          try {
                            if (typeof sessionStorage !== 'undefined') {
                              const currentStoredText = sessionStorage.getItem('sythion.composer.clientRequestText');
                              const currentStoredId = sessionStorage.getItem('sythion.composer.clientRequestId');
                              if (currentStoredText === text && currentStoredId === clientRequestId) {
                                let removed = false;
                                try {
                                  sessionStorage.removeItem('sythion.composer.clientRequestId');
                                  sessionStorage.removeItem('sythion.composer.clientRequestText');
                                  removed =
                                    sessionStorage.getItem('sythion.composer.clientRequestText') === null &&
                                    sessionStorage.getItem('sythion.composer.clientRequestId') === null;
                                } catch {
                                  removed = false;
                                }
                                if (!removed) {
                                  let guarded = false;
                                  try {
                                    sessionStorage.setItem('sythion.composer.clientRequestText', '');
                                    sessionStorage.setItem('sythion.composer.clientRequestId', '');
                                    guarded =
                                      sessionStorage.getItem('sythion.composer.clientRequestText') === '' &&
                                      sessionStorage.getItem('sythion.composer.clientRequestId') === '';
                                  } catch {
                                    guarded = false;
                                  }
                                  if (!guarded) {
                                    setComposerStorageCompromised(true);
                                  }
                                }
                              }
                            }
                          } catch {
                            // Storage may be unavailable; ignore.
                          }
                        }
                      }
                      notify({ tone: 'danger', title: 'Could not send', body: message });
                      setVoiceError(message);
                      setUnconfirmedSchedulingTaskIds(priorUnconfirmedSchedulingTaskIds);
                    } finally {
                      setSubmitting(false);
                    }
                  }}
                />
              </div>
            </div>
      </div>

      {selected && (
        <TaskDetailDrawer
          task={allTasks.find((task) => task.id === selected.id) ?? selected}
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
  voiceUnavailable,
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
  voiceUnavailable?: boolean;
  authorizationLabel: string;
  /** 'unknown' when the backend reported no authorization level at all. */
  authorizationTone: 'ok' | 'warn' | 'unknown';
  onToggleVoice: () => void;
  onSubmit: (text: string) => Promise<void>;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const sendButtonRef = useRef<HTMLButtonElement>(null);
  // Tracks whether the most recent state transition into `submitting === false`
  // followed an actual submission, so focus restoration only runs after
  // send/completion and never on initial mount or unrelated re-renders.
  const didSubmitRef = useRef(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 168)}px`;
  }, [value]);

  // Keep/restore keyboard focus on the composer after a send completes, while
  // never stealing focus from another control the user intentionally moved to
  // during the in-flight request. The textarea is disabled while submitting, so
  // without this the field goes unblurred-to-nothing and the next message
  // requires an explicit click.
  useEffect(() => {
    if (submitting) return;
    if (!didSubmitRef.current) return;
    didSubmitRef.current = false;
    const target = ref.current;
    if (!target) return;
    // Hidden documents must not take focus; screen readers/mobile should not be
    // surprised by focus changes they cannot see.
    if (typeof document !== 'undefined' && document.visibilityState && document.visibilityState !== 'visible') return;
    const now = typeof document !== 'undefined' ? (document.activeElement as HTMLElement | null) : null;
    const sendButton = sendButtonRef.current;
    // Only restore when focus is on the body, the send button that initiated the
    // action, or the field itself. Any other control means the user moved
    // intentionally during submission and must keep focus.
    const intentionalMove =
      !!now && now !== document.body && now !== target && now !== sendButton;
    if (intentionalMove) return;
    try {
      target.focus();
    } catch {
      // Defensive: focus() can throw in some test or constrained environments.
    }
  }, [submitting]);

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
              if (canSend) {
                didSubmitRef.current = true;
                void onSubmit(value.trim());
              }
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
              voiceUnavailable
                ? 'Speech-to-text is not installed. Click for help installing it in Settings.'
                : voiceBlocked
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
            ref={sendButtonRef}
            disabled={!canSend}
            onClick={() => {
              if (!canSend) return;
              didSubmitRef.current = true;
              void onSubmit(value.trim());
            }}
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

  const resume = async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await actions.resumeTask(task.id);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Could not resume the task';
      setError(message);
      notify({ tone: 'danger', title: 'Could not resume', body: message });
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
            {held ? 'Interrupted' : 'Needs your answer'}
          </Badge>
          {/* Never truncated: knowing exactly what you are approving is the point. */}
          <span className="t-muted attention__request">{task.original_request}</span>
        </div>
        {stated ? (
          <p className="attention__text">{stated}</p>
        ) : (
          <p className="attention__text">
            {held ? 'This task was interrupted and will not continue until resumed.' : 'Sythion is paused on this and will not continue until you reply.'}
          </p>
        )}
        {blocked ? (
          <p className="t-faint attention__note">{held ? 'Demonstration record. Start a backend to resume this task.' : 'Demonstration record. Start a backend to answer this.'}</p>
        ) : held ? (
          <div className="attention__reply">
            <Button size="sm" variant="primary" disabled={busy} onClick={() => void resume()}>
              {busy ? 'Resuming…' : 'Resume task'}
            </Button>
            <Button size="sm" variant="ghost" icon="external" onClick={onOpen}>Details</Button>
          </div>
        ) : (
          <div className="attention__reply">
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

function Exchange({ task }: { task: Task }) {
  const actions = useActions();
  const notify = useAppStore((s) => s.notify);
  const dataMode = useAppStore((s) => s.dataMode);
  // Only a genuine failure reads as a failure. An interrupted task is held for
  // authorization and is pinned above instead of appearing here.
  const failed = task.state === 'failed';
  const askedQuestion =
    task.state === 'waiting_for_user' && (task.waiting_kind === 'clarification' || task.waiting_kind === 'unknown')
      ? awaitingReason(task)
      : null;

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
            <div className="msg__bubble">{task.final_result.split(/(\*\*[^\n*]+\*\*)/g).map((part, index) => part.startsWith('**') && part.endsWith('**') ? <strong key={index}>{part.slice(2, -2)}</strong> : part)}</div>
          ) : askedQuestion ? (
            <div className="msg__bubble">{askedQuestion.split(/(\*\*[^\n*]+\*\*)/g).map((part, index) => part.startsWith('**') && part.endsWith('**') ? <strong key={index}>{part.slice(2, -2)}</strong> : part)}</div>
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

          {!task.final_result && !askedQuestion && !failed && <div className="msg__bubble" role="status">{task.state === 'cancelled' ? 'This message was cancelled.' : task.state === 'created' ? 'This request is queued and has not started yet.' : 'Synth is working on your message…'}</div>}
          {(task.state === 'running' || task.state === 'created') && dataMode === 'live' && (
            <Button
              size="sm"
              variant="ghost"
              onClick={(e) => {
                e?.stopPropagation();
                void Promise.resolve(actions.cancelTask(task.id)).catch((err: Error) =>
                  notify({ tone: 'danger', title: 'Interrupt failed', body: err.message })
                );
              }}
            >
              Stop
            </Button>
          )}

        </div>
      </div>
    </>
  );
}
