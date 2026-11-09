import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AssistantScreen } from './Assistant';
import { useAppStore } from '../store/appStore';
import type { Task } from '../types';

const mock = vi.hoisted(() => ({
  turn: vi.fn(),
  conversations: vi.fn().mockResolvedValue({ conversations: [{ id: 'conv-saved', session_id: 's', user_text: 'Saved message', assistant_text: 'Saved complete reply', created_at: '2026-10-22T00:00:00Z', background_task_ids: ['task-hidden'] }], next_cursor: null }),
  getTask: vi.fn().mockResolvedValue({ id: 'task-hidden', original_request: 'do work', normalized_goal: null, state: 'failed', version: 1, active_action_id: null, pending_question: null, final_result: 'FILE_NOT_FOUND\n\nDiagnostic: check path', terminal_reason: 'FILE_NOT_FOUND', created_at: '2026-10-22T00:00:00Z', updated_at: '2026-10-22T00:00:00Z' } as Task),
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

let client: QueryClient;
let view: ReactTestRenderer;
const render = () => create(<QueryClientProvider client={client}><AssistantScreen /></QueryClientProvider>);
function text() { return JSON.stringify(view?.toJSON()); }
const settled = async (fn?: () => void) => {
  await act(async () => { if (fn) fn(); await new Promise((r) => setTimeout(r, 0)); });
};

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 0 } } });
  useAppStore.setState({ isConnected: true, transcriptDraft: '', notifications: [], unreadNotifications: 0, isVoiceSpeaking: false });
  vi.stubGlobal('document', { addEventListener: vi.fn(), removeEventListener: vi.fn(), visibilityState: 'visible' });
  vi.stubGlobal('localStorage', { getItem: () => null, setItem: vi.fn(), removeItem: vi.fn() });
  const storage: Record<string, string> = { 'sythion.taskAssociations': JSON.stringify({ 'conv-saved': ['task-hidden'] }) };
  vi.stubGlobal('sessionStorage', {
    getItem: (key: string) => (key in storage ? storage[key] : null),
    setItem: (key: string, value: string) => { storage[key] = value; },
    removeItem: (key: string) => { delete storage[key]; },
  });
});

afterEach(() => { view?.unmount(); vi.unstubAllGlobals(); });

describe('task association recovery', () => {
  it('shows diagnostic for an associated task omitted from the 50-task list after reload', async () => {
    view = render();
    // Let conversation history hydrate and task recovery run.
    await settled();
    await settled();
    await settled();
    expect(mock.getTask).toHaveBeenCalledWith('task-hidden');
    expect(text()).toContain('Diagnostic: check path');
  });
});