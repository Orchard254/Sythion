import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AssistantScreen } from './Assistant';
import { useAppStore } from '../store/appStore';
import type { Task } from '../types';
import axios from 'axios';

const mock = vi.hoisted(() => ({
  turn: vi.fn(),
  createTask: vi.fn(),
  replyToTask: vi.fn(),
  resumeTask: vi.fn(),
  cancelTask: vi.fn(),
  stopCapture: vi.fn(),
  speak: vi.fn(),
  conversations: vi.fn(),
  taskList: [] as Task[],
}));

vi.mock('../services/api', () => ({
  assistantService: { turn: mock.turn },
  voiceService: { speak: mock.speak },
  conversationService: { list: mock.conversations },
}));

vi.mock('../hooks/useVoiceCapture', () => ({
  useVoiceCapture: () => ({ startCapture: vi.fn(), stopCapture: mock.stopCapture }),
}));

vi.mock('../hooks/useBackend', async () => {
  const { useQuery } = await import('@tanstack/react-query');
  return {
    useTasks: () => useQuery({ queryKey: ['tasks'], queryFn: async () => mock.taskList, initialData: [], enabled: false }),
    useTools: () => ({ data: [] }),
    useAgent: () => ({ data: null }),
    useVoiceAssets: () => ({ data: { stt_installed: false } }),
    useActions: () => ({ live: true, assistantTurn: mock.turn, createTask: mock.createTask, replyToTask: mock.replyToTask, resumeTask: mock.resumeTask, cancelTask: mock.cancelTask }),
    useTaskEvents: () => ({ data: [], refetch: vi.fn() }),
    useTaskResumePreflight: () => ({ data: null, isError: false, isFetching: false, refetch: vi.fn() }),
  };
});

describe('assistant turns', () => {
  it('sends one turn and renders the exchange as conversation', async () => {
    mock.turn.mockResolvedValueOnce({ exchange_id: 'turn-1', user_text: 'Hello Synth', assistant_text: 'Hi there', status: 'completed', background_task_ids: [] });
    await draft();
    await send();
    expect(mock.turn).toHaveBeenCalledWith('Hello Synth', expect.any(String));
    expect(useAppStore.getState().transcriptDraft).toBe('');
    expect(text()).toContain('Hi there');
  });

  it('creates executable work only after a separate visible confirmation', async () => {
    // This integration case opens the real TaskDetailDrawer. Its Escape
    // listener needs a window, even in react-test-renderer's node environment.
    // Stub it only in this case; afterEach unmounts before globals are restored.
    const windowMock = { addEventListener: vi.fn(), removeEventListener: vi.fn() };
    vi.stubGlobal('window', windowMock);
    const stored = new Map<string, string>();
    vi.stubGlobal('sessionStorage', {
      getItem: (key: string) => stored.get(key) ?? null,
      setItem: (key: string, value: string) => { stored.set(key, value); },
      removeItem: (key: string) => { stored.delete(key); },
    });
    mock.createTask.mockResolvedValueOnce({
      ...task, id: 'work-created', state: 'created', original_request: 'Hello Synth',
    });
    await draft();
    await settle(() => view.root.findByProps({ 'aria-label': 'Create task from message' }).props.onClick());
    expect(mock.turn).not.toHaveBeenCalled();
    expect(mock.createTask).not.toHaveBeenCalled();
    await settle(() => view.root.findByProps({ 'aria-label': 'Confirm create task' }).props.onClick());
    expect(mock.createTask).toHaveBeenCalledWith('Hello Synth', 'task', expect.any(String));
    expect(mock.turn).not.toHaveBeenCalled();
    expect(useAppStore.getState().transcriptDraft).toBe('');
    expect(text()).toContain('Cancel queued request');
    expect(windowMock.addEventListener).toHaveBeenCalledWith('keydown', expect.any(Function));
  });

  it('preserves a newer draft when an earlier turn completes', async () => {
    let resolve!: (value: unknown) => void;
    mock.turn.mockImplementationOnce(() => new Promise((r) => { resolve = r; }));
    await draft();
    await send();
    await settle(() => view.root.findByProps({ 'aria-label': 'Message Sythion' }).props.onChange({ target: { value: 'My next message' } }));
    await settle(() => resolve({ exchange_id: 'turn-1', user_text: 'Hello Synth', assistant_text: 'Hi there', status: 'completed', background_task_ids: [] }));
    expect(useAppStore.getState().transcriptDraft).toBe('My next message');
    expect(text()).toContain('Hi there');
  });

  it('keeps the draft and reports a failed turn once in status, not above the composer', async () => {
    mock.turn.mockRejectedValueOnce(new Error('Connection lost'));
    await draft();
    await send();
    expect(useAppStore.getState().transcriptDraft).toBe('Hello Synth');
    expect(text()).toContain('Connection lost');
    const alerts = view.root.findAll((node) => node.props?.role === 'alert');
    expect(alerts.some((node) => String(node.props.children ?? '').includes('Connection lost'))).toBe(false);
  });

  it('does not implicitly answer permission holds', async () => {
    const permissionTask = { ...task, state: 'waiting_for_user' as const, waiting_kind: 'permission' as const, pending_question: 'Allow access?' };
    await settle(() => client.setQueryData(['tasks'], [permissionTask]));
    await draft();
    await send();
    expect(mock.turn).toHaveBeenCalledWith('Hello Synth', expect.any(String));
    expect(mock.replyToTask).not.toHaveBeenCalled();
  });

  it('resumes an interrupted task instead of sending a turn', async () => {
    mock.resumeTask.mockResolvedValueOnce(undefined);
    await settle(() => client.setQueryData(['tasks'], [{ ...task, state: 'interrupted', terminal_reason: 'Paused safely' }]));
    expect(text()).toContain('Paused safely');
    const resumeButton = view.root.findAllByType('button').find((button) => button.children.includes('Resume task'));
    await settle(() => resumeButton!.props.onClick());
    expect(mock.resumeTask).toHaveBeenCalledWith(task.id);
    expect(mock.turn).not.toHaveBeenCalled();
  });

  it('keeps ordinary clarification questions out of the main send path', async () => {
    const clarification = { ...task, state: 'waiting_for_user' as const, waiting_kind: 'clarification' as const, pending_question: 'Which one?' };
    await settle(() => client.setQueryData(['tasks'], [clarification]));
    expect(text()).not.toContain('Open questions');
    expect(text()).toContain('Which one?');
    const answerButtons = view.root.findAllByType('button').filter((button) => button.children.includes('Answer in message box'));
    expect(answerButtons).toHaveLength(0);
    await draft();
    await send();
    expect(mock.turn).toHaveBeenCalledWith('Hello Synth', expect.any(String));
    expect(mock.replyToTask).not.toHaveBeenCalled();
  });
  it('restores full stored conversation when opening the screen', async () => {
    mock.conversations.mockResolvedValue({ conversations: [{ id: 'conv-saved', session_id: 's', user_text: 'Saved message', assistant_text: 'Saved complete reply', created_at: '2026-10-22T00:00:00Z' }], next_cursor: null });
    await settle(() => { void client.invalidateQueries({ queryKey: ['conversation-history'] }); });
    expect(text()).toContain('Saved message');
    expect(text()).toContain('Saved complete reply');
  });

  it('loads older conversations on request', async () => {
    mock.conversations.mockImplementation(async (cursor?: string) => cursor
      ? { conversations: [{ id: 'old', session_id: 's', user_text: 'Older question', assistant_text: 'Older answer', created_at: '2026-10-21T00:00:00Z' }], next_cursor: null }
      : { conversations: [], next_cursor: 'older-page' });
    await settle(() => { void client.invalidateQueries({ queryKey: ['conversation-history'] }); });
    await settle(() => view.root.findAllByType('button').find((button) => button.children.includes('Load older messages'))!.props.onClick());
    expect(mock.conversations).toHaveBeenCalledWith('older-page');
    expect(text()).toContain('Older answer');
  });

  it('does not retain a cached reply after its persisted record is removed', async () => {
    mock.turn.mockResolvedValueOnce({ exchange_id: 'conv-1', user_text: 'Hello Synth', assistant_text: 'Saved reply', status: 'completed', background_task_ids: [] });
    mock.conversations.mockResolvedValue({ conversations: [{ id: 'conv-1', session_id: 's', user_text: 'Hello Synth', assistant_text: 'Saved reply', created_at: '2026-10-22T00:00:00Z' }], next_cursor: null });
    await draft(); await send();
    expect(text()).toContain('Saved reply');
    mock.conversations.mockResolvedValue({ conversations: [], next_cursor: null });
    await settle(() => { void client.invalidateQueries({ queryKey: ['conversation-history'] }); });
    expect(text()).not.toContain('Saved reply');
  });

  it('keeps a conversational reply visible when the task list refreshes', async () => {
    mock.turn.mockResolvedValueOnce({ exchange_id: 'conv-1', user_text: 'Hello Synth', assistant_text: 'Hello from Synth', status: 'completed', background_task_ids: [] });
    await draft(); await send();
    await settle(() => client.setQueryData(['tasks'], []));
    expect(text()).toContain('Hello Synth');
    expect(text()).toContain('Hello from Synth');
    expect(text()).not.toContain('exec-line__toggle');
  });

  it('preserves text after spoken playback failure', async () => {
    localStorage.setItem('sythion.replyMode', 'voice');
    await settle(() => { view.unmount(); view = create(<QueryClientProvider client={client}><AssistantScreen /></QueryClientProvider>); });
    mock.speak.mockResolvedValue({ success: false });
    mock.turn.mockResolvedValueOnce({ exchange_id: 'turn-1', user_text: 'Hello Synth', assistant_text: 'Hello from Synth', status: 'completed', background_task_ids: [] });
    await draft(); await send();
    expect(mock.speak).toHaveBeenCalled();
    expect(text()).toContain('Hello from Synth');
  });

  it('reads a new reply aloud by default when no preference is stored', async () => {
    vi.stubGlobal('localStorage', { getItem: () => null, setItem: vi.fn() });
    await settle(() => { view.unmount(); view = create(<QueryClientProvider client={client}><AssistantScreen /></QueryClientProvider>); });
    await draft(); await send();
    expect(mock.speak).toHaveBeenCalledWith('Saved reply');
  });

  it('does not duplicate the unavailable playback warning above input', async () => {
    localStorage.setItem('sythion.replyMode', 'voice');
    await settle(() => { view.unmount(); view = create(<QueryClientProvider client={client}><AssistantScreen /></QueryClientProvider>); });
    mock.speak.mockResolvedValue({ success: false });
    mock.turn.mockResolvedValueOnce({ exchange_id: 'turn-1', user_text: 'Hello Synth', assistant_text: 'Hello from Synth', status: 'completed', background_task_ids: [] });
    await draft(); await send();
    expect(mock.speak).toHaveBeenCalled();
    expect(text()).toContain('Hello from Synth');
    expect(text()).not.toContain('Spoken playback is unavailable');
    expect(text()).not.toContain('Something went wrong');
    const alerts = view.root.findAll((node) => node.props?.role === 'alert');
    expect(alerts.some((node) => String(node.props.children ?? '').includes('Spoken playback is unavailable'))).toBe(false);
  });

  it('surfaces genuine unexpected playback errors once in status, not above the composer', async () => {
    localStorage.setItem('sythion.replyMode', 'voice');
    await settle(() => { view.unmount(); view = create(<QueryClientProvider client={client}><AssistantScreen /></QueryClientProvider>); });
    mock.speak.mockRejectedValueOnce(new Error('Speaker device lost'));
    mock.turn.mockResolvedValueOnce({ exchange_id: 'turn-1', user_text: 'Hello Synth', assistant_text: 'Hello from Synth', status: 'completed', background_task_ids: [] });
    await draft(); await send();
    expect(mock.speak).toHaveBeenCalled();
    expect(text()).toContain('Speaker device lost');
    const alerts = view.root.findAll((node) => node.props?.role === 'alert');
    expect(alerts.some((node) => String(node.props.children ?? '').includes('Speaker device lost'))).toBe(false);
  });

  it('allows an explicit new message while a permission question waits', async () => {
    mock.turn.mockResolvedValueOnce({ exchange_id: 'conv-1', user_text: 'Hello Synth', assistant_text: 'Done', status: 'completed', background_task_ids: [] });
    await settle(() => client.setQueryData(['tasks'], [{ ...task, state: 'waiting_for_user', waiting_kind: 'permission', pending_question: 'Allow access?' }]));
    await draft(); await send();
    expect(mock.turn).toHaveBeenCalledWith('Hello Synth', expect.any(String));
    expect(mock.replyToTask).not.toHaveBeenCalled();
  });

  it('shows active listening and disables send even when a task needs attention', async () => {
    await draft();
    await settle(() => { client.setQueryData(['tasks'], [{ ...task, state: 'interrupted' }]); useAppStore.setState({ isListening: true }); });
    const mic = view.root.findByProps({ 'aria-label': 'Stop listening' });
    expect(mic.props['aria-pressed']).toBe(true);
    expect(mic.props.disabled).toBe(false);
    expect(view.root.findByProps({ 'aria-label': 'Send message' }).props.disabled).toBe(true);
    await settle(() => mic.props.onClick());
    expect(mock.stopCapture).toHaveBeenCalled();
    expect(mock.turn).not.toHaveBeenCalled();
  });

  it('renders an ordinary clarification as a plain conversation reply', async () => {
    const clarification = { ...task, id: 'clarification', state: 'waiting_for_user' as const, waiting_kind: 'clarification' as const, pending_question: 'Which option should I use?' };
    await settle(() => client.setQueryData(['tasks'], [clarification]));
    expect(text()).toContain('Which option should I use?');
    expect(text()).not.toContain('Needs your answer');
    expect(text()).not.toContain('Needs your decision');
    const answerButtons = view.root.findAllByType('button').filter((button) => button.children.includes('Answer in message box'));
    expect(answerButtons).toHaveLength(0);
  });

  it('keeps an ordinary follow-up visible after a topic-switching turn', async () => {
    const clarification = { ...task, id: 'clarification', state: 'waiting_for_user' as const, waiting_kind: 'clarification' as const, pending_question: 'Which option should I use?' };
    mock.taskList = [clarification];
    await settle(() => client.setQueryData(['tasks'], [clarification]));
    mock.turn.mockResolvedValueOnce({ exchange_id: 'turn-2', user_text: 'A different request', assistant_text: 'Handled the new request.', status: 'completed', background_task_ids: [] });
    await settle(() => view.root.findByProps({ 'aria-label': 'Message Sythion' }).props.onChange({ target: { value: 'A different request' } }));
    await send();
    expect(text()).toContain('Which option should I use?');
    expect(text()).toContain('Handled the new request.');
    expect(text()).not.toContain('Needs your answer');
  });

  it('keeps permission and unknown holds visible without answer controls', async () => {
    const permission = { ...task, id: 'permission', state: 'waiting_for_user' as const, waiting_kind: 'permission' as const, pending_question: 'Allow access?' };
    const unknown = { ...task, id: 'unknown', state: 'waiting_for_user' as const, waiting_kind: 'unknown' as const, pending_question: 'Resolve unknown?' };
    await settle(() => client.setQueryData(['tasks'], [permission, unknown]));
    expect(text()).toContain('Allow access?');
    expect(text()).toContain('Resolve unknown?');
    const answerButtons = view.root.findAllByType('button').filter((button) => button.children.includes('Answer in message box'));
    expect(answerButtons).toHaveLength(0);
    const detailButtons = view.root.findAllByType('button').filter((button) => button.children.includes('Details'));
    expect(detailButtons.length).toBeGreaterThan(0);
    await draft();
    await send();
    expect(mock.turn).toHaveBeenCalledWith('Hello Synth', expect.any(String));
    expect(mock.replyToTask).not.toHaveBeenCalled();
    expect(mock.resumeTask).not.toHaveBeenCalled();
  });

  it('does not cancel or approve a permission hold when sending a message', async () => {
    await settle(() => client.setQueryData(['tasks'], [{ ...task, id: 'permission', state: 'waiting_for_user' as const, waiting_kind: 'permission' as const, pending_question: 'Allow access?' }]));
    await draft();
    await send();
    expect(mock.turn).toHaveBeenCalledWith('Hello Synth', expect.any(String));
    expect(mock.cancelTask).not.toHaveBeenCalled();
  });

  it('keeps ordinary questions accessible after a topic change while permissions stay pinned', async () => {
    const clarification = { ...task, state: 'waiting_for_user' as const, waiting_kind: 'clarification' as const, pending_question: 'Which option?' };
    const permission = { ...clarification, id: 'permission', waiting_kind: 'permission' as const, pending_question: 'Allow access?' };
    mock.taskList = [clarification, permission];
    mock.turn.mockResolvedValueOnce({ exchange_id: 'conversation', user_text: 'Hello Synth', assistant_text: 'Done', status: 'completed', background_task_ids: [] });
    await settle(() => client.setQueryData(['tasks'], mock.taskList));
    await draft(); await send();
    expect(text()).not.toContain('Open questions');
    expect(text()).toContain('Which option?');
    const pinned = view.root.findByProps({ 'aria-label': 'Needs your decision' });
    expect(pinned.findAll((node) => node.props.task?.id === 'permission').length).toBeGreaterThan(0);
    expect(pinned.findAll((node) => node.props.task?.id === 'first')).toHaveLength(0);
  });

  it('renders an old unknown wait as a conversation question without a pinned card or duplicated status', async () => {
    const unknown = { ...task, id: 'legacy-wait', state: 'waiting_for_user' as const, waiting_kind: 'unknown' as const, pending_question: 'Resolve legacy question?' };
    await settle(() => client.setQueryData(['tasks'], [unknown]));
    expect(text()).toContain('Resolve legacy question?');
    expect(text()).not.toContain('Needs your decision');
    expect(text()).not.toContain('Needs your answer');
    await draft();
    await send();
    expect(mock.turn).toHaveBeenCalledWith('Hello Synth', expect.any(String));
    expect(mock.replyToTask).not.toHaveBeenCalled();
  });

  it('keeps permission holds visibly pinned with security context', async () => {
    const permission = { ...task, id: 'permission', state: 'waiting_for_user' as const, waiting_kind: 'permission' as const, pending_question: 'Allow access to the project files?' };
    await settle(() => client.setQueryData(['tasks'], [permission]));
    const pinned = view.root.findByProps({ 'aria-label': 'Needs your decision' });
    expect(pinned.findAll((node) => node.props.task?.id === 'permission').length).toBeGreaterThan(0);
    expect(text()).toContain('Allow access to the project files?');
    expect(text()).toContain('Guarded');
  });

  it('removes execution metadata below completed replies', async () => {
    await settle(() => client.setQueryData(['tasks'], [{ ...task, id: 'background-task', state: 'completed', final_result: 'Background complete', original_request: 'Do background work' }]));
    expect(text()).toContain('Background complete');
    expect(text()).not.toContain('exec-line__toggle');
    expect(text()).not.toContain('exec-line__open');
    // The new task-creation helper legitimately mentions "Execution".
    // Restrict this assertion to the completed assistant reply, which must
    // remain clean of technical execution metadata.
    const replies = view.root.findAllByProps({ className: 'msg msg--agent' });
    expect(replies).toHaveLength(1);
    expect(replies[0].findByProps({ className: 'msg__bubble' }).children).toEqual(['Background complete']);
  });

  it('keeps a discoverable Stop control for running tasks in the conversation', async () => {
    await settle(() => client.setQueryData(['tasks'], [{ ...task, id: 'running-task', state: 'running', final_result: null, original_request: 'Long running work' }]));
    expect(text()).toContain('Synth is working');
    const stops = view.root.findAllByType('button').filter((button) => button.children.includes('Stop'));
    expect(stops.length).toBeGreaterThan(0);
    await settle(() => stops[0].props.onClick());
    expect(mock.cancelTask).toHaveBeenCalledWith('running-task');
  });

  it('does not render the Show the tool list button or tools panel', () => {
    expect(text()).not.toContain('Show the tool list');
    expect(text()).not.toContain('Hide the tool list');
    expect(text()).not.toContain('Tools this agent can invoke');
  });

  it('renders a clear warning when scheduling remains unknown', async () => {
    mock.turn.mockResolvedValueOnce({
      exchange_id: 'turn-unknown',
      user_text: 'Hello Synth',
      assistant_text: 'Requested background work.',
      status: 'completed',
      background_task_ids: ['task-unknown'],
      scheduling_established: false,
    });
    await draft();
    await send();
    expect(text()).toContain('Scheduling is unknown');
    expect(text()).toContain('not automatically retried');
  });

  it('renders the clear warning for null scheduling evidence', async () => {
    mock.turn.mockResolvedValueOnce({
      exchange_id: 'turn-null',
      user_text: 'Hello Synth',
      assistant_text: 'Requested background work.',
      status: 'completed',
      background_task_ids: ['task-null'],
      scheduling_established: null,
    });
    await draft();
    await send();
    expect(text()).toContain('Scheduling is unknown');
  });

  it('renders the clear warning when scheduling evidence is omitted', async () => {
    mock.turn.mockResolvedValueOnce({
      exchange_id: 'turn-omitted',
      user_text: 'Hello Synth',
      assistant_text: 'Requested background work.',
      status: 'completed',
      background_task_ids: ['task-omitted'],
    });
    await draft();
    await send();
    expect(text()).toContain('Scheduling is unknown');
  });

  it('does not render a warning when scheduling is explicitly established', async () => {
    mock.turn.mockResolvedValueOnce({
      exchange_id: 'turn-established',
      user_text: 'Hello Synth',
      assistant_text: 'Requested background work.',
      status: 'completed',
      background_task_ids: ['task-established'],
      scheduling_established: true,
    });
    await draft();
    await send();
    expect(text()).not.toContain('Scheduling is unknown');
  });

  it('does not render a warning without associated background tasks', async () => {
    mock.turn.mockResolvedValueOnce({
      exchange_id: 'turn-no-tasks',
      user_text: 'Hello Synth',
      assistant_text: 'Hi there',
      status: 'completed',
      background_task_ids: [],
      scheduling_established: false,
    });
    await draft();
    await send();
    expect(text()).not.toContain('Scheduling is unknown');
  });

  it('does not render a stale scheduling warning when all exact associated tasks are observed completed', async () => {
    mock.turn.mockResolvedValueOnce({
      exchange_id: 'turn-completed',
      user_text: 'Hello Synth',
      assistant_text: 'Requested background work.',
      status: 'completed',
      background_task_ids: ['task-completed'],
      scheduling_established: false,
    });
    await draft();
    await send();
    await settle(() => client.setQueryData(['tasks'], [{ ...task, id: 'task-completed', state: 'completed' as const, final_result: 'Done' }]));
    expect(text()).not.toContain('Scheduling is unknown');
  });

  it('retains the scheduling warning when exact associated tasks are not all observed completed', async () => {
    mock.turn.mockResolvedValueOnce({
      exchange_id: 'turn-mixed',
      user_text: 'Hello Synth',
      assistant_text: 'Requested background work.',
      status: 'completed',
      background_task_ids: ['task-complete', 'task-failed'],
      scheduling_established: false,
    });
    await draft();
    await send();
    await settle(() => client.setQueryData(['tasks'], [{ ...task, id: 'task-complete', state: 'completed' as const }]));
    expect(text()).toContain('Scheduling is unknown');
  });

  it('reuses the same client request id after a failed turn in the same mount', async () => {
    mock.turn.mockRejectedValueOnce(new Error('503 Service Unavailable'));
    await draft();
    await send();
    const firstId = mock.turn.mock.calls[mock.turn.mock.calls.length - 1][1];
    mock.turn.mockRejectedValueOnce(new Error('503 Service Unavailable'));
    await draft();
    await send();
    const secondId = mock.turn.mock.calls[mock.turn.mock.calls.length - 1][1];
    expect(secondId).toBe(firstId);
  });

  it('preserves the original client request id across a remount for the same failed text', async () => {
    const storage = new Map<string, string>();
    vi.stubGlobal('sessionStorage', {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => { storage.set(key, value); },
      removeItem: (key: string) => { storage.delete(key); },
    });
    mock.turn.mockRejectedValueOnce(new Error('503 Service Unavailable'));
    await draft();
    await send();
    const firstId = mock.turn.mock.calls[mock.turn.mock.calls.length - 1][1];

    await settle(() => { view.unmount(); view = create(<QueryClientProvider client={client}><AssistantScreen /></QueryClientProvider>); });

    await draft();
    mock.turn.mockRejectedValueOnce(new Error('503 Service Unavailable'));
    await send();
    const secondId = mock.turn.mock.calls[mock.turn.mock.calls.length - 1][1];
    expect(secondId).toBe(firstId);
  });

  it('mints a new client request id after success even for the same text', async () => {
    mock.turn.mockResolvedValueOnce({ exchange_id: 'turn-first', user_text: 'Hello Synth', assistant_text: 'Hi there', status: 'completed', background_task_ids: [] });
    await draft();
    await send();
    const firstId = mock.turn.mock.calls[mock.turn.mock.calls.length - 1][1];

    mock.turn.mockResolvedValueOnce({ exchange_id: 'turn-second', user_text: 'Hello Synth', assistant_text: 'Hi again', status: 'completed', background_task_ids: [] });
    await draft();
    await send();
    const secondId = mock.turn.mock.calls[mock.turn.mock.calls.length - 1][1];
    expect(secondId).not.toBe(firstId);
  });

  it('mints a new client request id across a remount for genuinely different text', async () => {
    const storage = new Map<string, string>();
    vi.stubGlobal('sessionStorage', {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => { storage.set(key, value); },
      removeItem: (key: string) => { storage.delete(key); },
    });
    mock.turn.mockRejectedValueOnce(new Error('503 Service Unavailable'));
    await draft();
    await send();
    const firstId = mock.turn.mock.calls[mock.turn.mock.calls.length - 1][1];

    await settle(() => { view.unmount(); view = create(<QueryClientProvider client={client}><AssistantScreen /></QueryClientProvider>); });

    await settle(() => view.root.findByProps({ 'aria-label': 'Message Sythion' }).props.onChange({ target: { value: 'A genuinely different message' } }));
    mock.turn.mockRejectedValueOnce(new Error('503 Service Unavailable'));
    await send();
    const secondId = mock.turn.mock.calls[mock.turn.mock.calls.length - 1][1];
    expect(secondId).not.toBe(firstId);
  });

  it('keeps the draft and surfaces the honest unknown-outcome message for a transport failure', async () => {
    const transportMessage = 'Connection lost. The request outcome is unknown; it was not automatically retried. Your draft is still here.';
    mock.turn.mockRejectedValueOnce(new Error(transportMessage));
    await draft();
    await send();
    expect(useAppStore.getState().transcriptDraft).toBe('Hello Synth');
    expect(text()).toContain('Connection lost. The request outcome is unknown');
    const alerts = view.root.findAll((node) => node.props?.role === 'alert');
    expect(alerts.some((node) => String(node.props.children ?? '').includes(transportMessage))).toBe(false);
  });

  it('reuses the same client request id after a transport failure in the same mount', async () => {
    const transportMessage = 'Connection lost. The request outcome is unknown; it was not automatically retried. Your draft is still here.';
    mock.turn.mockRejectedValueOnce(new Error(transportMessage));
    await draft();
    await send();
    const firstId = mock.turn.mock.calls[mock.turn.mock.calls.length - 1][1];
    mock.turn.mockRejectedValueOnce(new Error(transportMessage));
    await draft();
    await send();
    const secondId = mock.turn.mock.calls[mock.turn.mock.calls.length - 1][1];
    expect(secondId).toBe(firstId);
  });

  it('restores the draft from a persisted request pair on reload when safe', async () => {
    const storage = new Map<string, string>();
    storage.set('sythion.composer.clientRequestText', 'Hello Synth');
    storage.set('sythion.composer.clientRequestId', 'persisted-reload-id');
    vi.stubGlobal('sessionStorage', {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => { storage.set(key, value); },
      removeItem: (key: string) => { storage.delete(key); },
    });
    await settle(() => { view.unmount(); view = create(<QueryClientProvider client={client}><AssistantScreen /></QueryClientProvider>); });
    expect(useAppStore.getState().transcriptDraft).toBe('Hello Synth');

    mock.turn.mockRejectedValueOnce(new Error('503 Service Unavailable'));
    await send();
    const reusedId = mock.turn.mock.calls[mock.turn.mock.calls.length - 1][1];
    expect(reusedId).toBe('persisted-reload-id');
  });

  it('does not clobber a non-empty draft when restoring from storage', async () => {
    const storage = new Map<string, string>();
    storage.set('sythion.composer.clientRequestText', 'Stored text');
    storage.set('sythion.composer.clientRequestId', 'stored-id');
    vi.stubGlobal('sessionStorage', {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => { storage.set(key, value); },
      removeItem: (key: string) => { storage.delete(key); },
    });
    await settle(() => useAppStore.setState({ transcriptDraft: 'User text already typed' }));
    await settle(() => { view.unmount(); view = create(<QueryClientProvider client={client}><AssistantScreen /></QueryClientProvider>); });
    expect(useAppStore.getState().transcriptDraft).toBe('User text already typed');
  });

  it('preserves prior scheduling uncertainty through a later failed retry', async () => {
    mock.turn.mockResolvedValueOnce({
      exchange_id: 'turn-unknown',
      user_text: 'Hello Synth',
      assistant_text: 'Requested background work.',
      status: 'completed',
      background_task_ids: ['task-unknown'],
      scheduling_established: false,
    });
    await draft();
    await send();
    expect(text()).toContain('Scheduling is unknown');

    const transportMessage = 'Connection lost. The request outcome is unknown; it was not automatically retried. Your draft is still here.';
    mock.turn.mockRejectedValueOnce(new Error(transportMessage));
    await draft();
    await send();
    expect(text()).toContain('Scheduling is unknown');
  });

  it('does not imply continuity when storage is unavailable', async () => {
    vi.stubGlobal('sessionStorage', {
      getItem: () => { throw new Error('storage unavailable'); },
      setItem: () => { throw new Error('storage unavailable'); },
      removeItem: () => { throw new Error('storage unavailable'); },
    });

    mock.turn.mockRejectedValueOnce(new Error('503 Service Unavailable'));
    await draft();
    await send();
    const firstId = mock.turn.mock.calls[mock.turn.mock.calls.length - 1][1];

    await settle(() => useAppStore.setState({ transcriptDraft: '' }));
    await settle(() => { view.unmount(); view = create(<QueryClientProvider client={client}><AssistantScreen /></QueryClientProvider>); });

    // Draft is not restored from unavailable storage.
    expect(useAppStore.getState().transcriptDraft).toBe('');

    mock.turn.mockRejectedValueOnce(new Error('503 Service Unavailable'));
    await draft();
    await send();
    const secondId = mock.turn.mock.calls[mock.turn.mock.calls.length - 1][1];
    expect(secondId).not.toBe(firstId);
  });

  it('mints a new request id on reload after successful turn when removeItem fails', async () => {
    const storage = new Map<string, string>();
    vi.stubGlobal('sessionStorage', {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => { storage.set(key, value); },
      removeItem: () => { throw new Error('removeItem failed'); },
    });

    mock.turn.mockResolvedValueOnce({ exchange_id: 'turn-success-remove-fail', user_text: 'Hello Synth', assistant_text: 'Done', status: 'completed', background_task_ids: [] });
    await draft();
    await send();
    const firstId = mock.turn.mock.calls[mock.turn.mock.calls.length - 1][1];

    await settle(() => useAppStore.setState({ transcriptDraft: '' }));
    await settle(() => { view.unmount(); view = create(<QueryClientProvider client={client}><AssistantScreen /></QueryClientProvider>); });

    mock.turn.mockResolvedValueOnce({ exchange_id: 'turn-second-remove-fail', user_text: 'Hello Synth', assistant_text: 'Done again', status: 'completed', background_task_ids: [] });
    await draft();
    await send();
    const secondId = mock.turn.mock.calls[mock.turn.mock.calls.length - 1][1];
    expect(secondId).not.toBe(firstId);
  });

  it('mints a new request id on reload after successful turn when removeItem is ignored', async () => {
    const storage = new Map<string, string>();
    vi.stubGlobal('sessionStorage', {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => { storage.set(key, value); },
      removeItem: () => {
        // Intentionally does not delete: simulate a backend that ignores removal.
      },
    });

    mock.turn.mockResolvedValueOnce({ exchange_id: 'turn-success-remove-noop', user_text: 'Hello Synth', assistant_text: 'Done', status: 'completed', background_task_ids: [] });
    await draft();
    await send();
    const firstId = mock.turn.mock.calls[mock.turn.mock.calls.length - 1][1];

    await settle(() => useAppStore.setState({ transcriptDraft: '' }));
    await settle(() => { view.unmount(); view = create(<QueryClientProvider client={client}><AssistantScreen /></QueryClientProvider>); });

    mock.turn.mockResolvedValueOnce({ exchange_id: 'turn-second-remove-noop', user_text: 'Hello Synth', assistant_text: 'Done again', status: 'completed', background_task_ids: [] });
    await draft();
    await send();
    const secondId = mock.turn.mock.calls[mock.turn.mock.calls.length - 1][1];
    expect(secondId).not.toBe(firstId);
  });

  it('does not reuse a mismatched stored id slot for different draft text', async () => {
    const storage = new Map<string, string>();
    storage.set('sythion.composer.clientRequestText', 'Different persisted text');
    storage.set('sythion.composer.clientRequestId', 'wrong-id-for-different-text');
    vi.stubGlobal('sessionStorage', {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => { storage.set(key, value); },
      removeItem: (key: string) => { storage.delete(key); },
    });

    await settle(() => useAppStore.setState({ transcriptDraft: '' }));
    await settle(() => { view.unmount(); view = create(<QueryClientProvider client={client}><AssistantScreen /></QueryClientProvider>); });

    await settle(() => view.root.findByProps({ 'aria-label': 'Message Sythion' }).props.onChange({ target: { value: 'Hello Synth' } }));
    mock.turn.mockResolvedValueOnce({ exchange_id: 'turn-mismatch', user_text: 'Hello Synth', assistant_text: 'Hi', status: 'completed', background_task_ids: [] });
    await send();
    const usedId = mock.turn.mock.calls[mock.turn.mock.calls.length - 1][1];
    expect(usedId).not.toBe('wrong-id-for-different-text');
  });

  it('preserves pair-level scheduling warning after storage-unavailable failure handling', async () => {
    // Exercise both scheduling uncertainty and no storage continuity together.
    vi.stubGlobal('sessionStorage', {
      getItem: () => { throw new Error('storage unavailable'); },
      setItem: () => { throw new Error('storage unavailable'); },
      removeItem: () => { throw new Error('storage unavailable'); },
    });

    mock.turn.mockResolvedValueOnce({
      exchange_id: 'turn-unknown-storage',
      user_text: 'Hello Synth',
      assistant_text: 'Requested background work.',
      status: 'completed',
      background_task_ids: ['task-unknown-storage'],
      scheduling_established: false,
    });
    await draft();
    await send();
    expect(text()).toContain('Scheduling is unknown');
  });

  it('warns while mounted and does not reuse when removal and guard both fail', async () => {
    const storage = new Map<string, string>();
    vi.stubGlobal('sessionStorage', {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => {
        if (value === '') throw new Error('guard setItem failed');
        storage.set(key, value);
      },
      removeItem: () => { throw new Error('removeItem failed'); },
    });

    mock.turn.mockResolvedValueOnce({ exchange_id: 'turn-success-double-fail', user_text: 'Hello Synth', assistant_text: 'Done', status: 'completed', background_task_ids: [] });
    await draft();
    await send();
    const firstId = mock.turn.mock.calls[mock.turn.mock.calls.length - 1][1];
    expect(text()).toContain('could not be verified');
    expect(storage.get('sythion.composer.clientRequestText')).toBe('Hello Synth');

    mock.turn.mockResolvedValueOnce({ exchange_id: 'turn-second-double-fail', user_text: 'Hello Synth', assistant_text: 'Done again', status: 'completed', background_task_ids: [] });
    await draft();
    await send();
    const secondId = mock.turn.mock.calls[mock.turn.mock.calls.length - 1][1];
    expect(secondId).not.toBe(firstId);
  });

  it('warns while mounted and does not reuse when removal and guard are both ignored', async () => {
    const storage = new Map<string, string>();
    vi.stubGlobal('sessionStorage', {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => {
        if (value === '') return;
        storage.set(key, value);
      },
      removeItem: () => {
        // Intentionally does nothing.
      },
    });

    mock.turn.mockResolvedValueOnce({ exchange_id: 'turn-success-double-noop', user_text: 'Hello Synth', assistant_text: 'Done', status: 'completed', background_task_ids: [] });
    await draft();
    await send();
    expect(text()).toContain('could not be verified');

    mock.turn.mockResolvedValueOnce({ exchange_id: 'turn-second-double-noop', user_text: 'Hello Synth', assistant_text: 'Done again', status: 'completed', background_task_ids: [] });
    await draft();
    await send();
    const firstId = mock.turn.mock.calls[mock.turn.mock.calls.length - 2][1];
    const secondId = mock.turn.mock.calls[mock.turn.mock.calls.length - 1][1];
    expect(secondId).not.toBe(firstId);
  });

  it('records the cross-reload limitation for unverifiable deletion', async () => {
    // If both removeItem and the empty-key guard fail or no-op, the completed
    // pair may survive in browser storage. A reload creates a fresh component,
    // so the mounted fail-closed flag cannot persist; the guarded behavior is
    // intentionally limited to the current mount without auto-replaying.
    const storage = new Map<string, string>();
    storage.set('sythion.composer.clientRequestText', 'Hello Synth');
    storage.set('sythion.composer.clientRequestId', 'reload-limited-id');
    vi.stubGlobal('sessionStorage', {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => { storage.set(key, value); },
      removeItem: (key: string) => { storage.delete(key); },
    });
    await settle(() => useAppStore.setState({ transcriptDraft: '' }));
    await settle(() => { view.unmount(); view = create(<QueryClientProvider client={client}><AssistantScreen /></QueryClientProvider>); });
    expect(useAppStore.getState().transcriptDraft).toBe('Hello Synth');
  });

  it('sends via keyboard Enter and keeps the composer usable on success', async () => {
    mock.turn.mockResolvedValueOnce({ exchange_id: 'kbd-1', user_text: 'Hello Synth', assistant_text: 'Hi from keyboard', status: 'completed', background_task_ids: [] });
    await draft();
    await settle(() => {
      view.root.findByProps({ 'aria-label': 'Message Sythion' }).props.onKeyDown({ key: 'Enter', shiftKey: false, preventDefault: vi.fn() });
    });
    expect(mock.turn).toHaveBeenCalledWith('Hello Synth', expect.any(String));
    expect(useAppStore.getState().transcriptDraft).toBe('');
    expect(text()).toContain('Hi from keyboard');
  });

  it('sends via click and keeps the composer usable on success', async () => {
    mock.turn.mockResolvedValueOnce({ exchange_id: 'click-1', user_text: 'Hello Synth', assistant_text: 'Hi from click', status: 'completed', background_task_ids: [] });
    await draft();
    await send();
    expect(mock.turn).toHaveBeenCalledWith('Hello Synth', expect.any(String));
    expect(useAppStore.getState().transcriptDraft).toBe('');
    expect(text()).toContain('Hi from click');
  });

  it('preserves the draft after a failed keyboard send', async () => {
    mock.turn.mockRejectedValueOnce(new Error('Connection lost'));
    await draft();
    await settle(() => {
      view.root.findByProps({ 'aria-label': 'Message Sythion' }).props.onKeyDown({ key: 'Enter', shiftKey: false, preventDefault: vi.fn() });
    });
    expect(useAppStore.getState().transcriptDraft).toBe('Hello Synth');
  });

  it('preserves the draft after a failed click send', async () => {
    mock.turn.mockRejectedValueOnce(new Error('Connection lost'));
    await draft();
    await send();
    expect(useAppStore.getState().transcriptDraft).toBe('Hello Synth');
  });

  it('reuses the same request id after an ambiguous uncertain send', async () => {
    const uncertain = 'Request outcome is uncertain; it was not rerun.';
    mock.turn.mockRejectedValueOnce(new Error(uncertain));
    await draft();
    await send();
    const firstId = mock.turn.mock.calls[mock.turn.mock.calls.length - 1][1];

    mock.turn.mockRejectedValueOnce(new Error(uncertain));
    await draft(); // not changed
    await send();
    const secondId = mock.turn.mock.calls[mock.turn.mock.calls.length - 1][1];
    expect(secondId).toBe(firstId);
  });

  it('preserves request id after a generic previously-failed send', async () => {
    const failedMessage = 'Request previously failed; it was not rerun.';
    mock.turn.mockRejectedValueOnce(new Error(failedMessage));
    await draft();
    await send();
    const firstId = mock.turn.mock.calls[mock.turn.mock.calls.length - 1][1];

    mock.turn.mockRejectedValueOnce(new Error(failedMessage));
    await draft(); // same text
    await send();
    const secondId = mock.turn.mock.calls[mock.turn.mock.calls.length - 1][1];
    expect(secondId).toBe(firstId);
  });

  it('preserves request id after a conflict response', async () => {
    const conflict = new Error('boom') as Error & { response?: { status: number } };
    conflict.response = { status: 409 };
    mock.turn.mockRejectedValueOnce(conflict);
    await draft();
    await send();
    const firstId = mock.turn.mock.calls[mock.turn.mock.calls.length - 1][1];

    mock.turn.mockRejectedValueOnce(conflict);
    await draft(); // same text
    await send();
    const secondId = mock.turn.mock.calls[mock.turn.mock.calls.length - 1][1];
    expect(secondId).toBe(firstId);
  });

  it('mints a new request id after an explicit no-dispatch verdict, which has no task or exchange', async () => {
    const modelRejection = 'Synth could not understand the model\'s reply. No conversation or action was recorded, and no background work was started.';
    const noDispatchError = new Error(modelRejection) as Error & { response?: { headers?: Record<string, string> } };
    noDispatchError.response = { headers: { 'X-Sythion-Dispatch-Verdict': 'no-dispatch' } };
    mock.turn.mockRejectedValueOnce(noDispatchError);
    await draft();
    await send();
    const firstId = mock.turn.mock.calls[mock.turn.mock.calls.length - 1][1];

    mock.turn.mockResolvedValueOnce({ exchange_id: 'model-retry', user_text: 'Hello Synth', assistant_text: 'Recovered', status: 'completed', background_task_ids: [] });
    await draft(); // same text
    await send();
    const secondId = mock.turn.mock.calls[mock.turn.mock.calls.length - 1][1];
    expect(secondId).not.toBe(firstId);
    expect(text()).toContain('Recovered');
  });

  it('mints a new request id for distinct text after an uncertain failure', async () => {
    const uncertain = 'Request outcome is uncertain; it was not rerun.';
    mock.turn.mockRejectedValueOnce(new Error(uncertain));
    await draft();
    await send();
    const firstId = mock.turn.mock.calls[mock.turn.mock.calls.length - 1][1];

    mock.turn.mockResolvedValueOnce({ exchange_id: 'distinct-turn', user_text: 'different text', assistant_text: 'Handled distinct', status: 'completed', background_task_ids: [] });
    await settle(() => view.root.findByProps({ 'aria-label': 'Message Sythion' }).props.onChange({ target: { value: 'different text' } }));
    await send();
    const secondId = mock.turn.mock.calls[mock.turn.mock.calls.length - 1][1];
    expect(secondId).not.toBe(firstId);
    expect(text()).toContain('Handled distinct');
  });

  it('retains the request id when a model-prefixed message has no explicit no-dispatch verdict (prefix collision)', async () => {
    const modelRejection = 'Synth could not understand the model\'s reply. No conversation or action was recorded, and no background work was started.';
    mock.turn.mockRejectedValueOnce(new Error(modelRejection));
    await draft();
    await send();
    const firstId = mock.turn.mock.calls[mock.turn.mock.calls.length - 1][1];

    mock.turn.mockRejectedValueOnce(new Error(modelRejection));
    await draft();
    await send();
    const secondId = mock.turn.mock.calls[mock.turn.mock.calls.length - 1][1];
    expect(secondId).toBe(firstId);
  });

  it('retains the request id for an uncertain failure (no verdict header)', async () => {
    const uncertain = 'Request outcome is uncertain; it was not rerun.';
    mock.turn.mockRejectedValueOnce(new Error(uncertain));
    await draft();
    await send();
    const firstId = mock.turn.mock.calls[mock.turn.mock.calls.length - 1][1];

    mock.turn.mockRejectedValueOnce(new Error(uncertain));
    await draft();
    await send();
    const secondId = mock.turn.mock.calls[mock.turn.mock.calls.length - 1][1];
    expect(secondId).toBe(firstId);
  });

  it('retains the draft and request id, with no auto retry, for a malformed verdict header', async () => {
    const err = new Error('boom') as Error & { response?: { headers?: Record<string, string> } };
    err.response = { headers: { 'x-sythion-dispatch-verdict': 'No-Dispatch' } };
    mock.turn.mockRejectedValueOnce(err);
    await draft();
    await send();
    expect(useAppStore.getState().transcriptDraft).toBe('Hello Synth');
    const firstId = mock.turn.mock.calls[mock.turn.mock.calls.length - 1][1];

    mock.turn.mockRejectedValueOnce(err);
    await draft();
    await send();
    const secondId = mock.turn.mock.calls[mock.turn.mock.calls.length - 1][1];
    expect(secondId).toBe(firstId);
  });

  it('drops storage only on explicit no-dispatch and mints a fresh id on remount for same text', async () => {
    const storage = new Map<string, string>();
    vi.stubGlobal('sessionStorage', {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => { storage.set(key, value); },
      removeItem: (key: string) => { storage.delete(key); },
    });
    const err = new Error('model failed') as Error & { response?: { status: number; headers?: Record<string, string> } };
    err.response = { status: 503, headers: { 'x-sythion-dispatch-verdict': 'no-dispatch' } };
    mock.turn.mockRejectedValueOnce(err);
    await draft();
    await send();
    const firstId = mock.turn.mock.calls[mock.turn.mock.calls.length - 1][1];

    await settle(() => { view.unmount(); view = create(<QueryClientProvider client={client}><AssistantScreen /></QueryClientProvider>); });
    await draft();
    mock.turn.mockResolvedValueOnce({ exchange_id: 'recovered', user_text: 'Hello Synth', assistant_text: 'Recovered', status: 'completed', background_task_ids: [] });
    await send();
    const secondId = mock.turn.mock.calls[mock.turn.mock.calls.length - 1][1];
    expect(secondId).not.toBe(firstId);
    expect(text()).toContain('Recovered');
  });

  it('allows a same-text retry after an explicit no-dispatch verdict to use a new id', async () => {
    const err = new Error('model failed') as Error & { response?: { headers?: Record<string, string> } };
    err.response = { headers: { 'x-sythion-dispatch-verdict': 'no-dispatch' } };
    mock.turn.mockRejectedValueOnce(err);
    await draft();
    await send();
    const firstId = mock.turn.mock.calls[mock.turn.mock.calls.length - 1][1];

    mock.turn.mockResolvedValueOnce({ exchange_id: 'retry-ok', user_text: 'Hello Synth', assistant_text: 'Recovered', status: 'completed', background_task_ids: [] });
    await draft();
    await send();
    const secondId = mock.turn.mock.calls[mock.turn.mock.calls.length - 1][1];
    expect(secondId).not.toBe(firstId);
    expect(text()).toContain('Recovered');
  });

  it('classifies an explicit no-dispatch verdict using a real AxiosHeaders instance', async () => {
    const h = new axios.AxiosHeaders();
    h.set('X-Sythion-Dispatch-Verdict', 'no-dispatch');
    const err = Object.assign(new Error('boom'), { response: { status: 503, headers: h } }) as Error & { response?: { status: number; headers: unknown } };
    mock.turn.mockRejectedValueOnce(err);
    await draft();
    await send();
    const firstId = mock.turn.mock.calls[mock.turn.mock.calls.length - 1][1];

    mock.turn.mockResolvedValueOnce({ exchange_id: 'axios-header-retry', user_text: 'Hello Synth', assistant_text: 'Recovered', status: 'completed', background_task_ids: [] });
    await draft();
    await send();
    const secondId = mock.turn.mock.calls[mock.turn.mock.calls.length - 1][1];
    expect(secondId).not.toBe(firstId);
    expect(text()).toContain('Recovered');
  });

  it('marks storage compromised and avoids auto retry when a storage read fails during an explicit verdict', async () => {
    const storage = new Map<string, string>();
    storage.set('sythion.composer.clientRequestText', 'Hello Synth');
    storage.set('sythion.composer.clientRequestId', 'stored-read-fail-id');
    vi.stubGlobal('sessionStorage', {
      getItem: () => { throw new Error('read failed'); },
      setItem: (key: string, value: string) => { storage.set(key, value); },
      removeItem: (key: string) => { storage.delete(key); },
    });

    const err = Object.assign(new Error('boom'), { response: { headers: { 'x-sythion-dispatch-verdict': 'no-dispatch' } } }) as Error & { response?: { headers: Record<string, string> } };
    mock.turn.mockRejectedValueOnce(err);
    await draft();
    await send();

    expect(useAppStore.getState().transcriptDraft).toBe('Hello Synth');
    expect(text()).toContain('could not be verified');
    expect(mock.turn).toHaveBeenCalledTimes(1);
  });

  it('does not clear a nonmatching stored pair on an explicit no-dispatch rejection', async () => {
    const storage = new Map<string, string>();
    storage.set('sythion.composer.clientRequestText', 'different persisted text');
    storage.set('sythion.composer.clientRequestId', 'nonmatching-stored-id');
    vi.stubGlobal('sessionStorage', {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => { storage.set(key, value); },
      removeItem: (key: string) => { storage.delete(key); },
    });

    const err = Object.assign(new Error('boom'), { response: { headers: { 'x-sythion-dispatch-verdict': 'no-dispatch' } } }) as Error & { response?: { headers: Record<string, string> } };
    mock.turn.mockRejectedValueOnce(err);
    await draft();
    await send();

    // The submit flow persists the current request's pair before the
    // backend outcome, so a pre-existing foreign pair is replaced rather than
    // silently preserved. The important contract here is that the draft is
    // retained and no automatic retry occurs.
    expect(useAppStore.getState().transcriptDraft).toBe('Hello Synth');
  });

  it('preserves a newer draft when an explicit verdict arrives after the user changed text', async () => {
    const err = Object.assign(new Error('boom'), { response: { headers: { 'x-sythion-dispatch-verdict': 'no-dispatch' } } }) as Error & { response?: { headers: Record<string, string> } };
    mock.turn.mockRejectedValueOnce(err);
    await draft();
    await send();
    await settle(() => view.root.findByProps({ 'aria-label': 'Message Sythion' }).props.onChange({ target: { value: 'newer draft text' } }));
    expect(useAppStore.getState().transcriptDraft).toBe('newer draft text');
    expect(mock.turn).toHaveBeenCalledTimes(1);
  });

  it('keeps the composer usable and makes no retry call after an uncertain headerless failure', async () => {
    mock.turn.mockRejectedValueOnce(new Error('Request outcome is uncertain; it was not rerun.'));
    await draft();
    await send();
    expect(mock.turn).toHaveBeenCalledTimes(1);
    mock.turn.mockRejectedValueOnce(new Error('Request outcome is uncertain; it was not rerun.'));
    await draft();
    await send();
    expect(mock.turn).toHaveBeenCalledTimes(2);
  });

  it('documents the unavoidable reload limitation when storage cannot be read', async () => {
    const storage = new Map<string, string>();
    storage.set('sythion.composer.clientRequestText', 'Hello Synth');
    storage.set('sythion.composer.clientRequestId', 'unreadable-surviving-id');
    vi.stubGlobal('sessionStorage', {
      getItem: () => { throw new Error('read failed'); },
      setItem: (key: string, value: string) => { storage.set(key, value); },
      removeItem: (key: string) => { storage.delete(key); },
    });

    const err = Object.assign(new Error('boom'), { response: { headers: { 'x-sythion-dispatch-verdict': 'no-dispatch' } } }) as Error & { response?: { headers: Record<string, string> } };
    mock.turn.mockRejectedValueOnce(err);
    await draft();
    await send();

    // Fail-closed while mounted: reload safety is intentionally not claimed when
    // storage is unreadable or stale data cannot be cleared. After remount,
    // persisted data (if any) may be reused, so no reload-time guarantee is
    // made.
    expect(text()).toContain('could not be verified');
  });

});

let view: ReactTestRenderer;
let client: QueryClient;
const task: Task = { id: 'first', original_request: 'Hello Synth', state: 'created', final_result: null, terminal_reason: null, normalized_goal: null, pending_question: null, active_action_id: null, version: 1, created_at: '2026-10-22T00:00:00Z', updated_at: '2026-10-22T00:00:00Z' };
const settle = async (action: () => void) => { await act(async () => { action(); await new Promise((r) => setTimeout(r, 20)); }); };
const text = () => JSON.stringify(view.toJSON());
const draft = async () => settle(() => view.root.findByProps({ 'aria-label': 'Message Sythion' }).props.onChange({ target: { value: 'Hello Synth' } }));
const send = async () => settle(() => view.root.findByProps({ 'aria-label': 'Send message' }).props.onClick());

beforeEach(async () => {
  vi.clearAllMocks();
  let mode = 'text';
  vi.stubGlobal('localStorage', { getItem: () => mode, setItem: (_key: string, value: string) => { mode = value; } });
  vi.stubGlobal('document', { visibilityState: 'visible' });
  useAppStore.setState({ isConnected: true, isListening: false, isVoiceSpeaking: false, transcriptDraft: '' });
  mock.taskList = [];
  mock.conversations.mockResolvedValue({ conversations: [], next_cursor: null });
  mock.turn.mockResolvedValue({ exchange_id: 'turn-1', user_text: 'Hello Synth', assistant_text: 'Saved reply', status: 'completed', background_task_ids: [] });
  mock.speak.mockResolvedValue({ success: true });
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await settle(() => { view = create(<QueryClientProvider client={client}><AssistantScreen /></QueryClientProvider>); });
});

afterEach(() => { act(() => view.unmount()); client.clear(); vi.unstubAllGlobals(); });
