/**
 * @vitest-environment jsdom
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';

// Enable React's modern act environment for this jsdom test file.
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
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

// jsdom does not implement Element.scrollTo; the component's scroll
// restoration must not crash the test harness.
if (typeof Element !== 'undefined' && typeof Element.prototype.scrollTo !== 'function') {
  Element.prototype.scrollTo = function () {};
}
if (typeof HTMLTextAreaElement !== 'undefined' && typeof HTMLTextAreaElement.prototype.focus !== 'function') {
  // noop
}

let container: HTMLDivElement;
let root: Root;
let client: QueryClient;

const findTextarea = () => container.querySelector<HTMLTextAreaElement>('textarea[aria-label="Message Sythion"]')!;
const findSendButton = () => container.querySelector<HTMLButtonElement>('button[aria-label="Send message"]')!;

const draftText = async () => {
  const textarea = findTextarea();
  await act(async () => {
    const valueSetter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value')?.set;
    valueSetter?.call(textarea, 'Hello Synth');
    textarea.dispatchEvent(new Event('input', { bubbles: true }));
    await new Promise((r) => setTimeout(r, 0));
  });
};

const waitFor = async (fn: () => boolean, timeout = 1000) => {
  const start = Date.now();
  while (!fn()) {
    if (Date.now() - start > timeout) throw new Error('Timed out waiting');
    await new Promise((r) => setTimeout(r, 10));
  }
};

beforeEach(async () => {
  vi.clearAllMocks();
  localStorage.setItem('sythion.replyMode', 'text');
  useAppStore.setState({ isConnected: true, dataMode: 'live', isListening: false, isVoiceSpeaking: false, transcriptDraft: '' });
  mock.taskList = [];
  mock.conversations.mockResolvedValue({ conversations: [], next_cursor: null });
  mock.turn.mockResolvedValue({ exchange_id: 'turn-1', user_text: 'Hello Synth', assistant_text: 'Saved reply', status: 'completed', background_task_ids: [] });
  mock.speak.mockResolvedValue({ success: true });
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root.render(<QueryClientProvider client={client}><AssistantScreen /></QueryClientProvider>);
    await new Promise((r) => setTimeout(r, 0));
  });
});

afterEach(async () => {
  await act(async () => {
    root.unmount();
    await new Promise((r) => setTimeout(r, 0));
  });
  client.clear();
  container.remove();
  vi.unstubAllGlobals();
});

describe('Assistant composer DOM focus restoration', () => {
  it('restores textarea focus after a successful click send', async () => {
    await draftText();
    const textarea = findTextarea();
    const sendButton = findSendButton();
    let resolveTurn!: (value: unknown) => void;
    mock.turn.mockImplementationOnce(() => new Promise((r) => { resolveTurn = r; }));

    await act(async () => {
      sendButton.click();
      await new Promise((r) => setTimeout(r, 0));
    });

    // In-flight: submitting true disables the textarea; finish turn.
    await act(async () => {
      resolveTurn({ exchange_id: 'click-1', user_text: 'Hello Synth', assistant_text: 'Hi from click', status: 'completed', background_task_ids: [] });
      await new Promise((r) => setTimeout(r, 0));
    });

    await waitFor(() => document.activeElement === textarea);
    expect(document.activeElement).toBe(textarea);
  });

  it('restores textarea focus after a successful keyboard Enter send', async () => {
    await draftText();
    const textarea = findTextarea();
    let resolveTurn!: (value: unknown) => void;
    mock.turn.mockImplementationOnce(() => new Promise((r) => { resolveTurn = r; }));

    await act(async () => {
      textarea.focus();
      textarea.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', shiftKey: false, bubbles: true, cancelable: true }));
      await new Promise((r) => setTimeout(r, 0));
    });

    await act(async () => {
      resolveTurn({ exchange_id: 'kbd-1', user_text: 'Hello Synth', assistant_text: 'Hi from keyboard', status: 'completed', background_task_ids: [] });
      await new Promise((r) => setTimeout(r, 0));
    });

    await waitFor(() => document.activeElement === textarea);
    expect(document.activeElement).toBe(textarea);
  });

  it('restores textarea focus after a failed click send', async () => {
    await draftText();
    const textarea = findTextarea();
    const sendButton = findSendButton();
    let rejectTurn!: (reason: unknown) => void;
    mock.turn.mockImplementationOnce(() => new Promise((_r, rej) => { rejectTurn = rej; }));

    await act(async () => {
      sendButton.click();
      await new Promise((r) => setTimeout(r, 0));
    });

    await act(async () => {
      rejectTurn(new Error('Connection lost'));
      await new Promise((r) => setTimeout(r, 0));
    });

    await waitFor(() => document.activeElement === textarea);
    expect(document.activeElement).toBe(textarea);
  });

  it('preserves/restores textarea focus after a failed keyboard Enter send', async () => {
    await draftText();
    const textarea = findTextarea();
    let rejectTurn!: (reason: unknown) => void;
    mock.turn.mockImplementationOnce(() => new Promise((_r, rej) => { rejectTurn = rej; }));

    await act(async () => {
      textarea.focus();
      textarea.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', shiftKey: false, bubbles: true, cancelable: true }));
      await new Promise((r) => setTimeout(r, 0));
    });

    await act(async () => {
      rejectTurn(new Error('Connection lost'));
      await new Promise((r) => setTimeout(r, 0));
    });

    await waitFor(() => document.activeElement === textarea);
    expect(document.activeElement).toBe(textarea);
  });

  it('does not steal focus from an intentionally focused control during send', async () => {
    await draftText();
    const sendButton = findSendButton();
    const micButton = container.querySelector<HTMLButtonElement>('button[aria-label="Start voice input"], button[aria-label="Stop listening"]')!;
    let resolveTurn!: (value: unknown) => void;
    mock.turn.mockImplementationOnce(() => new Promise((r) => { resolveTurn = r; }));

    await act(async () => {
      sendButton.click();
      await new Promise((r) => setTimeout(r, 0));
    });

    // Intentionally move focus during in-flight.
    await act(async () => {
      micButton.focus();
      await new Promise((r) => setTimeout(r, 0));
    });

    await act(async () => {
      resolveTurn({ exchange_id: 'intentional-1', user_text: 'Hello Synth', assistant_text: 'Hi', status: 'completed', background_task_ids: [] });
      await new Promise((r) => setTimeout(r, 0));
    });

    await new Promise((r) => setTimeout(r, 50));
    expect(document.activeElement).toBe(micButton);
  });
});
