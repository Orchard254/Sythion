/**
 * @vitest-environment jsdom
 */
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AssistantScreen } from './Assistant';
import { useAppStore } from '../store/appStore';
import type { Task } from '../types';

const mock = vi.hoisted(() => ({
  turn: vi.fn(),
  conversations: vi.fn(),
  getTask: vi.fn(),
}));

vi.mock('../services/api', () => ({
  assistantService: { turn: mock.turn },
  voiceService: { speak: vi.fn() },
  conversationService: { list: mock.conversations },
  taskService: { get: mock.getTask },
}));

vi.mock('../hooks/useVoiceCapture', () => ({
  useVoiceCapture: () => ({ startCapture: vi.fn(), stopCapture: vi.fn() }),
}));

vi.mock('../hooks/useBackend', async () => {
  const { useQuery } = await import('@tanstack/react-query');
  return {
    useTasks: () => useQuery({ queryKey: ['tasks'], queryFn: async () => [], initialData: [], enabled: false }),
    useTools: () => ({ data: [] }),
    useAgent: () => ({ data: null }),
    useVoiceAssets: () => ({ data: { stt_installed: false } }),
    useActions: () => ({ live: true, assistantTurn: mock.turn, replyToTask: vi.fn(), resumeTask: vi.fn(), cancelTask: vi.fn() }),
    useTaskEvents: () => ({ data: [] }),
  };
});

const CONVERSATION = {
  id: 'conv-poll',
  session_id: 's',
  user_text: 'Saved message',
  assistant_text: 'Saved complete reply',
  created_at: '2026-10-22T00:00:00Z',
  background_task_ids: ['task-pending'],
};

const TASK: Task = {
  id: 'task-pending',
  original_request: 'do work',
  normalized_goal: null,
  state: 'failed',
  version: 1,
  active_action_id: null,
  pending_question: null,
  final_result: 'FILE_NOT_FOUND',
  terminal_reason: 'FILE_NOT_FOUND',
  created_at: '2026-10-22T00:00:00Z',
  updated_at: '2026-10-22T00:00:00Z',
};

let client: QueryClient;
let view: ReactTestRenderer;

const render = () => create(<QueryClientProvider client={client}><AssistantScreen /></QueryClientProvider>);

beforeEach(() => {
  vi.useFakeTimers();
  client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 0 } } });
  useAppStore.setState({ isConnected: true, transcriptDraft: '', notifications: [], unreadNotifications: 0, isVoiceSpeaking: false });
  vi.stubGlobal('document', { addEventListener: vi.fn(), removeEventListener: vi.fn(), visibilityState: 'visible' });
  vi.stubGlobal('localStorage', { getItem: () => null, setItem: vi.fn(), removeItem: vi.fn() });
  mock.turn.mockReset();
  mock.conversations.mockResolvedValue({ conversations: [CONVERSATION], next_cursor: null });
  mock.getTask.mockResolvedValue(TASK);
  mock.getTask.mockClear();
});

afterEach(() => {
  view?.unmount();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('diagnostic polling does not reset on unchanged pending ids', () => {
  it('polls by the original 5s tick after a recovered-task update', async () => {
    view = render();
    for (let i = 0; i < 4; i++) {
      await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    }
    expect(mock.getTask).toHaveBeenCalledWith('task-pending');

    // Trigger a recovered-task update at ~1s without changing pending ids.
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    await act(async () => {
      client.setQueryData(['conversation-history'], {
        pages: [{ conversations: [{ ...CONVERSATION, assistant_text: 'Updated reply' }], next_cursor: null }],
        pageParams: [undefined],
      });
      await vi.advanceTimersByTimeAsync(0);
    });
    for (let i = 0; i < 4; i++) {
      await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    }
    const updatedCalls = mock.getTask.mock.calls.length;

    // Under the bug, the timer resets at ~1s and polls at ~6s. Under the fix,
    // it still polls by the original ~5s tick.
    await act(async () => { await vi.advanceTimersByTimeAsync(4000); });
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });

    expect(mock.getTask.mock.calls.length).toBeGreaterThan(updatedCalls);
  });
});
