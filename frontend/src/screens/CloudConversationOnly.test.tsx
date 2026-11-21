import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CloudConversationOnlyScreen } from './CloudConversationOnly';

const mock = vi.hoisted(() => ({ status: vi.fn(), conversationTurn: vi.fn() }));
vi.mock('../services/api', async (original) => ({
  ...await original<typeof import('../services/api')>(),
  cloudPreviewService: mock,
}));

const candidate = { provider_id: 'openrouter' as const, model_id: 'unit/model:free' };
const RECHECK_ATTEMPTS = 3;
let view: ReactTestRenderer;
let exit: ReturnType<typeof vi.fn>;
const settle = async (fn: () => void) => {
  await act(async () => { fn(); for (let i = 0; i < 24; i++) await Promise.resolve(); });
};
const text = (node: { children: unknown[] }): string =>
  node.children.map((v): string => typeof v === 'string' ? v :
    v && typeof v === 'object' && 'children' in v
      ? text(v as { children: unknown[] }) : '').join('');
const button = (name: string) => view.root.findAllByType('button').find((b) => text(b) === name)!;
const typeText = async (value: string) => settle(
  () => view.root.findByProps({ id: 'cloud-only-message' }).props.onChange({
    target: { value },
  }),
);
const status = (verified = true) => ({
  active_provider: 'ollama-cloud',
  staged: candidate,
  text_probe_verified: verified,
  tool_probe_verified: false,
  providers: [{ provider_id: 'openrouter', credential_configured: true }],
  ready_for_execution: false,
});
const useStatus = (value: ReturnType<typeof status>) => {
  // Override the default only; keep the call count for bounded-retry assertions.
  mock.status.mockResolvedValue(value);
};

beforeEach(async () => {
  vi.clearAllMocks();
  exit = vi.fn();
  mock.status.mockResolvedValue(status());
  mock.conversationTurn.mockResolvedValue({
    ...candidate, assistant_text: 'A safe text response.',
    conversation_only: true, active: false, ready_for_execution: false,
    persisted: false, task_executed: false,
  });
  await settle(() => { view = create(<CloudConversationOnlyScreen onExit={exit} />); });
});
afterEach(() => { act(() => view.unmount()); });

describe('isolated cloud conversation-only screen', () => {
  it('verifies backend status before any requests and never uses assistant turn or tasks', () => {
    expect(mock.status).toHaveBeenCalledTimes(1);
    expect(mock.conversationTurn).not.toHaveBeenCalled();
    expect(text(view.root)).toContain('No tool execution');
    expect(text(view.root)).toContain('they are not saved');
    expect(button('Send text-only message').props.disabled).toBe(true);
    expect(button('Check again')).toBeTruthy();
    expect(text(view.root)).not.toContain('Return to setup');
  });

  it('sends two conversational turns using only previous in-session context', async () => {
    await typeText('First question');
    await settle(() => button('Send text-only message').props.onClick());
    expect(mock.conversationTurn).toHaveBeenCalledWith(candidate, 'First question', []);
    await typeText('Follow up');
    await settle(() => button('Send text-only message').props.onClick());
    expect(mock.conversationTurn).toHaveBeenNthCalledWith(2, candidate, 'Follow up', [
      { role: 'user', content: 'First question' },
      { role: 'assistant', content: 'A safe text response.' },
    ]);
    expect(text(view.root)).toContain('Follow up');
    expect(mock.conversationTurn).toHaveBeenCalledTimes(2);
    expect(view.root.findByProps({ id: 'cloud-only-message' }).props.value).toBe('');
  });

  it('never sends more than three complete prior pairs, truncating long replies', async () => {
    mock.conversationTurn.mockImplementation(async () => ({
      ...candidate, assistant_text: 'L'.repeat(1800),
      conversation_only: true, active: false, ready_for_execution: false,
      persisted: false, task_executed: false,
    }));
    for (let i = 0; i < 5; i++) {
      await typeText('Question ' + String(i));
      await settle(() => button('Send text-only message').props.onClick());
    }
    const fifth = mock.conversationTurn.mock.calls[4];
    expect(fifth[2]).toHaveLength(6);
    expect(fifth[2][0]).toEqual({ role: 'user', content: 'Question 1' });
    expect(fifth[2][1].content).toHaveLength(1200);
  });

  it('re-checks stale entry evidence internally, then reports it honestly without sending the owner to setup', async () => {
    await settle(() => view.unmount());
    mock.status.mockClear();
    useStatus(status(false));
    await settle(() => { view = create(<CloudConversationOnlyScreen onExit={exit} />); });
    expect(mock.status).toHaveBeenCalledTimes(RECHECK_ATTEMPTS);
    expect(text(view.root)).toContain('no longer current');
    expect(text(view.root)).not.toContain('Return to setup');
    expect(button('Send text-only message').props.disabled).toBe(true);
    expect(mock.conversationTurn).not.toHaveBeenCalled();
  });

  it('self-heals stale entry evidence on Check again without requiring setup', async () => {
    await settle(() => view.unmount());
    useStatus(status(false));
    await settle(() => { view = create(<CloudConversationOnlyScreen onExit={exit} />); });
    expect(button('Send text-only message').props.disabled).toBe(true);
    useStatus(status(true));
    await settle(() => button('Check again').props.onClick());
    await typeText('Now verified');
    expect(text(view.root)).not.toContain('no longer current');
    expect(text(view.root)).not.toContain('No verified cloud model');
    expect(button('Send text-only message').props.disabled).toBe(false);
    expect(exit).not.toHaveBeenCalled();
    expect(mock.conversationTurn).not.toHaveBeenCalled();
  });

  it('never surfaces a response that asserts task execution', async () => {
    mock.conversationTurn.mockResolvedValueOnce({
      ...candidate, assistant_text: 'pretend I executed',
      conversation_only: true, active: true, ready_for_execution: true,
      persisted: true, task_executed: true,
    });
    await typeText('Do something');
    await settle(() => button('Send text-only message').props.onClick());
    expect(text(view.root)).not.toContain('pretend I executed');
    expect(text(view.root)).toContain('Cloud conversation request failed');
    expect(view.root.findByProps({ id: 'cloud-only-message' }).props.value).toBe('Do something');
  });

  it('a changed cloud proof stops sends without resending, and stays honest if status is still stale', async () => {
    mock.conversationTurn.mockRejectedValueOnce({
      response: { status: 409, data: { detail: 'secret-key-in-provider-response' } },
    });
    await typeText('What next?');
    useStatus(status(false));
    await settle(() => button('Send text-only message').props.onClick());
    expect(text(view.root)).toContain('no longer current');
    expect(text(view.root)).not.toContain('secret-key-in-provider-response');
    expect(text(view.root)).not.toContain('Return to setup');
    expect(button('Send text-only message').props.disabled).toBe(true);
    expect(mock.conversationTurn).toHaveBeenCalledTimes(1);
    expect(mock.status).toHaveBeenCalledTimes(1 + RECHECK_ATTEMPTS);
  });

  it('a 409 self-heals by re-checking status and re-enabling Send without a setup trip', async () => {
    mock.conversationTurn.mockRejectedValueOnce({ response: { status: 409 } });
    await typeText('What next?');
    await settle(() => button('Send text-only message').props.onClick());
    expect(text(view.root)).not.toContain('Return to setup');
    expect(text(view.root)).not.toContain('no longer current');
    expect(button('Send text-only message').props.disabled).toBe(false);
    expect(mock.status).toHaveBeenCalledTimes(2);
    expect(mock.conversationTurn).toHaveBeenCalledTimes(1);
    expect(view.root.findByProps({ id: 'cloud-only-message' }).props.value).toBe('What next?');
  });

  it('asks for a key only when status shows no configured credential', async () => {
    await settle(() => view.unmount());
    useStatus({ ...status(true), providers: [
      { provider_id: 'openrouter', credential_configured: false },
    ] });
    await settle(() => { view = create(<CloudConversationOnlyScreen onExit={exit} />); });
    expect(text(view.root)).toContain('A cloud API key is required');
    expect(button('Connect a key in setup')).toBeTruthy();
    expect(button('Send text-only message').props.disabled).toBe(true);
    expect(exit).not.toHaveBeenCalled();
  });

  it('requires owner action only for a rejected key, with a precise message', async () => {
    mock.conversationTurn.mockRejectedValueOnce({ response: { status: 401 } });
    await typeText('Hello');
    await settle(() => button('Send text-only message').props.onClick());
    expect(text(view.root)).toContain('rejected (HTTP 401)');
    expect(text(view.root)).toContain('Connect a valid key');
    expect(text(view.root)).not.toContain('Return to setup');
    expect(button('Send text-only message').props.disabled).toBe(true);
    expect(exit).not.toHaveBeenCalled();
    await settle(() => button('Connect a key in setup').props.onClick());
    expect(exit).toHaveBeenCalledOnce();
  });

  it('rate-limit errors do not trigger repeated model calls', async () => {
    mock.conversationTurn.mockRejectedValueOnce({ response: { status: 429 } });
    await typeText('Hello');
    await settle(() => button('Send text-only message').props.onClick());
    expect(text(view.root)).toContain('Cloud quota or capacity reached');
    expect(mock.conversationTurn).toHaveBeenCalledTimes(1);
  });

  it('never submits twice when the owner double-clicks before React rerenders', async () => {
    let resolveTurn: (value: unknown) => void = () => {};
    mock.conversationTurn.mockImplementationOnce(() => new Promise((resolve) => {
      resolveTurn = resolve;
    }));
    await typeText('One billable request');
    await settle(() => {
      const click = button('Send text-only message').props.onClick;
      click();
      click();
    });
    expect(mock.conversationTurn).toHaveBeenCalledTimes(1);
    // During an in-flight request, the button changes its visible label.
    expect(button('Sending one cloud request…').props.disabled).toBe(true);
    await settle(() => resolveTurn({
      ...candidate, assistant_text: 'Once only.',
      conversation_only: true, active: false, ready_for_execution: false,
      persisted: false, task_executed: false,
    }));
    expect(text(view.root)).toContain('Once only.');
    expect(mock.conversationTurn).toHaveBeenCalledTimes(1);
  });

  it('Check again re-checks internally and does not exit', async () => {
    await settle(() => button('Check again').props.onClick());
    expect(exit).not.toHaveBeenCalled();
    expect(mock.status).toHaveBeenCalledTimes(2);
    expect(mock.conversationTurn).not.toHaveBeenCalled();
  });
});

it('reports the specific eligible-agentic-harness restriction without exposing provider data', async () => {
  mock.conversationTurn.mockRejectedValueOnce({ response: { status: 502, data: {
    detail: 'Cloud model requires an eligible agentic harness (upstream HTTP 403)',
    private: 'DO-NOT-PRINT-THIS',
  } } });
  await typeText('Hello');
  await settle(() => button('Send text-only message').props.onClick());
  expect(text(view.root)).toContain('restricted to eligible agentic harnesses');
  expect(text(view.root)).not.toContain('DO-NOT-PRINT-THIS');
  expect(mock.conversationTurn).toHaveBeenCalledTimes(1);
});
