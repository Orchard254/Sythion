import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AssistantScreen } from './Assistant';
import { useAppStore } from '../store/appStore';
import type { Task } from '../types';

const mock = vi.hoisted(() => ({
  turn: vi.fn(),
  conversations: vi.fn().mockResolvedValue({ conversations: [], next_cursor: null }),
}));

vi.mock('../services/api', () => ({
  assistantService: { turn: mock.turn },
  voiceService: { speak: vi.fn() },
  conversationService: { list: mock.conversations },
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
const task = (fr: string | null): Task => ({
  id: 'task-failed', original_request: 'do work', normalized_goal: null, state: 'failed', version: 1, active_action_id: null,
  pending_question: null, final_result: fr, terminal_reason: 'FILE_NOT_FOUND',
  created_at: '2026-10-22T00:00:00Z', updated_at: '2026-10-22T00:00:00Z',
});

const settled = async (fn?: () => void) => {
  await act(async () => { if (fn) fn(); await new Promise((r) => setTimeout(r, 0)); });
};

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 0 } } });
  useAppStore.setState({ isConnected: true, transcriptDraft: '', notifications: [], unreadNotifications: 0 });
  vi.stubGlobal('document', { addEventListener: vi.fn(), removeEventListener: vi.fn(), visibilityState: 'visible' });
  vi.stubGlobal('localStorage', { getItem: () => null, setItem: vi.fn(), removeItem: vi.fn() });
});

afterEach(() => { view?.unmount(); vi.unstubAllGlobals(); });

describe('diagnostic async display', () => {
  it('shows asynchronously updated final_result without reload', async () => {
    view = render();
    await settled(() => { client.setQueryData(['tasks'], [task(null)]); });
    expect(text()).toContain('FILE_NOT_FOUND');
    await settled(() => { client.setQueryData(['tasks'], [task('FILE_NOT_FOUND\n\nDiagnostic: check path')]); });
    expect(text()).toContain('Diagnostic: check path');
  });
});