import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AssistantScreen } from './Assistant';
import { useAppStore } from '../store/appStore';
import type { Task } from '../types';

const mock = vi.hoisted(() => ({
  turn: vi.fn(),
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
    useActions: () => ({ live: true, assistantTurn: mock.turn, replyToTask: mock.replyToTask, resumeTask: mock.resumeTask, cancelTask: mock.cancelTask }),
    useTaskEvents: () => ({ data: [] }),
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

  it('does not show live conversation in preview mode', async () => {
    mock.turn.mockResolvedValueOnce({ exchange_id: 'conv-1', user_text: 'Hello Synth', assistant_text: 'Private response', status: 'completed', background_task_ids: [] });
    await draft(); await send();
    expect(text()).toContain('Private response');
    await settle(() => useAppStore.setState({ dataMode: 'preview' }));
    expect(text()).not.toContain('Private response');
    await settle(() => useAppStore.setState({ dataMode: 'live' }));
    expect(text()).toContain('Private response');
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
    expect(text()).not.toContain('Execution');
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
  useAppStore.setState({ isConnected: true, dataMode: 'live', isListening: false, isVoiceSpeaking: false, transcriptDraft: '' });
  mock.taskList = [];
  mock.conversations.mockResolvedValue({ conversations: [], next_cursor: null });
  mock.turn.mockResolvedValue({ exchange_id: 'turn-1', user_text: 'Hello Synth', assistant_text: 'Saved reply', status: 'completed', background_task_ids: [] });
  mock.speak.mockResolvedValue({ success: true });
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await settle(() => { view = create(<QueryClientProvider client={client}><AssistantScreen /></QueryClientProvider>); });
});

afterEach(() => { act(() => view.unmount()); client.clear(); vi.unstubAllGlobals(); });
