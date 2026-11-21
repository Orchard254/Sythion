import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { WorkEntry, type CreateWorkTask } from './WorkEntry';
import type { Task } from '../types';

const request = 'Check battery level using a read-only tool';
const task: Task = {
  id: 'task-1', original_request: request, normalized_goal: null,
  state: 'created', version: 1, active_action_id: null, pending_question: null,
  final_result: null, terminal_reason: null,
  created_at: '2026-11-10T01:00:00Z', updated_at: '2026-11-10T01:00:00Z',
};

let view: ReactTestRenderer;
// Vitest 1.x Mock takes the argument tuple and return type separately.
let createTask: Mock<Parameters<CreateWorkTask>, ReturnType<CreateWorkTask>>;
let created: ReturnType<typeof vi.fn>;
let store: Map<string, string>;

function button(label: string) {
  return view.root.findAllByType('button').find((node) => node.props['aria-label'] === label);
}
async function click(label: string) {
  const target = button(label);
  expect(target).toBeDefined();
  await act(async () => { target!.props.onClick(); });
}

beforeEach(() => {
  store = new Map();
  vi.stubGlobal('sessionStorage', {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => { store.set(key, value); },
    removeItem: (key: string) => { store.delete(key); },
  });
  createTask = vi.fn<Parameters<CreateWorkTask>, ReturnType<CreateWorkTask>>().mockResolvedValue(task);
  created = vi.fn();
  act(() => {
    view = create(<WorkEntry text={request} disabled={false} createTask={createTask} onCreated={created} />);
  });
});

afterEach(() => {
  act(() => view.unmount());
  vi.unstubAllGlobals();
});

describe('explicit executable work', () => {
  it('does not dispatch from ordinary chat or before confirmation', async () => {
    expect(button('Create task from message')).toBeDefined();
    expect(createTask).not.toHaveBeenCalled();
    await click('Create task from message');
    expect(createTask).not.toHaveBeenCalled();
    expect(JSON.stringify(view.toJSON())).toContain(request);
    await click('Confirm create task');
    expect(createTask).toHaveBeenCalledTimes(1);
    expect(createTask).toHaveBeenCalledWith(request, 'task', expect.any(String));
    expect(created).toHaveBeenCalledWith(task, request);
    expect(store.size).toBe(0);
  });

  it('does not automatically retry an unknown result; a manual retry reuses the id', async () => {
    createTask.mockRejectedValueOnce(new Error('PRIVATE BACKEND STACK DETAIL'));
    await click('Create task from message');
    await click('Confirm create task');
    expect(createTask).toHaveBeenCalledTimes(1);
    expect(JSON.stringify(view.toJSON())).toContain('Task outcome is not confirmed');
    expect(JSON.stringify(view.toJSON())).not.toContain('PRIVATE BACKEND STACK DETAIL');
    expect(created).not.toHaveBeenCalled();
    const id = createTask.mock.calls[0][2];
    await click('Confirm create task');
    expect(createTask).toHaveBeenCalledTimes(2);
    expect(createTask.mock.calls[1]).toEqual([request, 'task', id]);
    expect(created).toHaveBeenCalledTimes(1);
  });

  it('reports a conversation response as no executable task', async () => {
    createTask.mockResolvedValueOnce({
      ...task, id: 'conv-abc', interaction_type: 'CONVERSATION', state: 'completed',
    });
    await click('Create task from message');
    await click('Confirm create task');
    expect(created).not.toHaveBeenCalled();
    expect(JSON.stringify(view.toJSON())).toContain('No executable task was created');
    await click('Confirm create task');
    expect(createTask).toHaveBeenCalledTimes(1);
  });

  it('requires a fresh confirmation after the draft changes', async () => {
    await click('Create task from message');
    await act(async () => {
      view.update(<WorkEntry text="A different request" disabled={false} createTask={createTask} onCreated={created} />);
    });
    expect(button('Confirm create task')?.props.disabled).toBe(true);
    await click('Confirm create task');
    expect(createTask).not.toHaveBeenCalled();
  });

  it('fails closed if request identity cannot be retained', async () => {
    vi.stubGlobal('sessionStorage', {
      getItem: () => null, setItem: () => {}, removeItem: () => {},
    });
    await click('Create task from message');
    await click('Confirm create task');
    expect(createTask).not.toHaveBeenCalled();
    expect(JSON.stringify(view.toJSON())).toContain('Task safety storage is unavailable');
  });

  it('never offers execution while disconnected', async () => {
    await act(async () => {
      view.update(<WorkEntry text={request} disabled={true} createTask={createTask} onCreated={created} />);
    });
    expect(button('Create task from message')?.props.disabled).toBe(true);
    await click('Create task from message');
    expect(createTask).not.toHaveBeenCalled();
  });
});

describe('internal resolution of uncertain work', () => {
  it('resolves an uncertain create from the backend instead of asking the owner to check', async () => {
    const lookupTask = vi.fn().mockResolvedValue(task);
    act(() => {
      view.update(<WorkEntry text={request} disabled={false} createTask={createTask} onCreated={created} lookupTask={lookupTask} />);
    });
    createTask.mockRejectedValueOnce(new Error('PRIVATE BACKEND DETAIL'));

    await click('Create task from message');
    await click('Confirm create task');

    expect(createTask).toHaveBeenCalledTimes(1);
    expect(lookupTask).toHaveBeenCalledWith(request);
    expect(created).toHaveBeenCalledWith(task, request);
    const json = JSON.stringify(view.toJSON());
    expect(json).not.toContain('PRIVATE BACKEND DETAIL');
    expect(json).not.toContain('Check Tasks');
    expect(json).not.toContain('retrying');
    expect(store.size).toBe(0);
  });

  it('stays honest when the backend cannot be queried and does not claim resolution', async () => {
    const lookupTask = vi.fn().mockRejectedValue(new Error('backend down'));
    act(() => {
      view.update(<WorkEntry text={request} disabled={false} createTask={createTask} onCreated={created} lookupTask={lookupTask} />);
    });
    createTask.mockRejectedValueOnce(new Error('PRIVATE BACKEND DETAIL'));

    await click('Create task from message');
    await click('Confirm create task');

    expect(created).not.toHaveBeenCalled();
    const json = JSON.stringify(view.toJSON());
    expect(json).toContain('not confirmed');
    expect(json).not.toContain('PRIVATE BACKEND DETAIL');
    expect(json).not.toContain('Check Tasks');
    expect(json).not.toContain('backend down');

    // No auto-resend; an explicit retry reuses the very same identity.
    const id = createTask.mock.calls[0][2];
    await click('Confirm create task');
    expect(createTask).toHaveBeenCalledTimes(2);
    expect(createTask.mock.calls[1][2]).toBe(id);
  });

  it('does not ask the owner to review tasks when local identity clearing fails', async () => {
    vi.stubGlobal('sessionStorage', {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => { if (value === '') throw new Error('ignored'); store.set(key, value); },
      removeItem: () => { throw new Error('ignored'); },
    });

    await click('Create task from message');
    await click('Confirm create task');

    expect(created).toHaveBeenCalledWith(task, request);
    const json = JSON.stringify(view.toJSON());
    expect(json).toContain('Task was recorded');
    expect(json).not.toContain('Review Tasks');
    expect(json).not.toContain('review it before retrying');
  });

  it('resolves a conversation-classified uncertain create without a hand-back', async () => {
    const conversation = { ...task, id: 'conv-1', interaction_type: 'CONVERSATION', state: 'completed' };
    const lookupTask = vi.fn().mockResolvedValue(conversation);
    act(() => {
      view.update(<WorkEntry text={request} disabled={false} createTask={createTask} onCreated={created} lookupTask={lookupTask} />);
    });
    createTask.mockRejectedValueOnce(new Error('PRIVATE BACKEND DETAIL'));

    await click('Create task from message');
    await click('Confirm create task');

    expect(created).not.toHaveBeenCalled();
    const json = JSON.stringify(view.toJSON());
    expect(json).toContain('conversation');
    expect(json).not.toContain('PRIVATE BACKEND DETAIL');
    expect(json).not.toContain('retrying');
  });
});
