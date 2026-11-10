import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CloudProviderPreview } from './CloudProviderPreview';

const mock = vi.hoisted(() => ({
  status: vi.fn(), modelCatalog: vi.fn(), verifyStoredKey: vi.fn(), saveCredential: vi.fn(), stage: vi.fn(), probe: vi.fn(), chat: vi.fn(),
}));
vi.mock('../services/api', async (importOriginal) => ({
  ...await importOriginal<typeof import('../services/api')>(),
  cloudPreviewService: mock,
}));

let view: ReactTestRenderer;
const text = (node: { children: unknown[] }): string =>
  node.children.map((v): string => typeof v === 'string' ? v :
    v && typeof v === 'object' && 'children' in v ? text(v as { children: unknown[] }) : '').join('');
const button = (label: string) => view.root.findAllByType('button').find((n) => text(n) === label)!;
const settle = async (fn: () => void) => {
  await act(async () => { fn(); for (let i = 0; i < 16; i++) await Promise.resolve(); });
};
const candidate = { provider_id: 'openrouter' as const, model_id: 'google/gemma-4-26b-a4b-it:free' };

beforeEach(async () => {
  vi.clearAllMocks();
  mock.status.mockResolvedValue({
    active_provider: 'ollama-cloud',
    staged: {},
    providers: [
      { provider_id: 'openrouter', credential_configured: false },
      { provider_id: 'nvidia', credential_configured: false },
      { provider_id: 'groq', credential_configured: false },
    ],
    ready_for_execution: false,
  });
  mock.modelCatalog.mockResolvedValue({
    provider_id: 'openrouter', truncated: false, ready_for_execution: false,
    models: [
      { id: 'a/paid-model', free_variant: false, tools_listed: true },
      { id: 'b/free-model:free', free_variant: true, tools_listed: false },
    ],
  });
  mock.verifyStoredKey.mockResolvedValue({
    provider_id: 'openrouter', credential_verified: true,
    active: false, ready_for_execution: false,
  });
  mock.saveCredential.mockResolvedValue(undefined);
  mock.stage.mockResolvedValue(undefined);
  mock.probe.mockResolvedValue({
    ...candidate, tool_probe_verified: true, active: false,
    ready_for_execution: false, evidence_persisted: true,
  });
  mock.chat.mockResolvedValue({
    ...candidate, assistant_text: 'Hello from a text-only model!',
    active: false, ready_for_execution: false, persisted: false, task_executed: false,
  });
  await settle(() => { view = create(<CloudProviderPreview />); });
});
afterEach(() => { act(() => view.unmount()); });

const enterKey = async () => {
  await settle(() => view.root.findByProps({ id: 'cloud-preview-key' }).props.onChange({
    target: { value: 'inert-key-for-unit-test' },
  }));
  await settle(() => button('Save provider key').props.onClick());
};

const enterModel = async () => {
  await settle(() => view.root.findByProps({ id: 'cloud-preview-model' }).props.onChange({
    target: { value: candidate.model_id },
  }));
  await settle(() => button('Stage model candidate').props.onClick());
};

describe('alternative provider first-run preview', () => {
  it('does not request inference automatically or equate listing with readiness', () => {
    expect(mock.status).toHaveBeenCalledTimes(1);
    expect(mock.probe).not.toHaveBeenCalled();
    expect(button('Test cloud function calling').props.disabled).toBe(true);
    expect(text(view.root)).toContain('not yet connected');
    expect(text(view.root)).toContain('Ollama');
  });

  it('stores a key via the bounded service, clears the password and never prints it', async () => {
    await enterKey();
    expect(mock.saveCredential).toHaveBeenCalledWith('openrouter', 'inert-key-for-unit-test');
    expect(view.root.findByProps({ id: 'cloud-preview-key' }).props.value).toBe('');
    expect(text(view.root)).not.toContain('inert-key-for-unit-test');
    expect(text(view.root)).toContain('has not yet been verified');
    expect(mock.probe).not.toHaveBeenCalled();
  });

  it('requires key, exact staging and explicit consent before one probe', async () => {
    await enterModel();
    expect(mock.stage).toHaveBeenCalledWith(candidate);
    expect(button('Test cloud function calling').props.disabled).toBe(true);
    await enterKey();
    expect(button('Test cloud function calling').props.disabled).toBe(true);
    await settle(() => view.root.findAllByType('input').find((n) => n.props.type === 'checkbox')!.props.onChange({
      target: { checked: true },
    }));
    expect(button('Test cloud function calling').props.disabled).toBe(false);
    await settle(() => button('Test cloud function calling').props.onClick());
    expect(mock.probe).toHaveBeenCalledTimes(1);
    expect(mock.probe).toHaveBeenCalledWith(candidate);
    expect(text(view.root)).toContain('not authorized for tasks');
    expect(button('Test cloud function calling').props.disabled).toBe(true);
  });

  it('changing the model revokes consent and any previous passed signal', async () => {
    await enterKey();
    await enterModel();
    await settle(() => view.root.findAllByType('input').find((n) => n.props.type === 'checkbox')!.props.onChange({
      target: { checked: true },
    }));
    await settle(() => button('Test cloud function calling').props.onClick());
    expect(text(view.root)).toContain('one synthetic function-call probe');
    await settle(() => view.root.findByProps({ id: 'cloud-preview-model' }).props.onChange({
      target: { value: 'other/model:free' },
    }));
    expect(text(view.root)).not.toContain('one synthetic function-call probe');
    expect(button('Test cloud function calling').props.disabled).toBe(true);
    expect(mock.probe).toHaveBeenCalledTimes(1);
  });

  it('sanitizes rate-limit errors and does not automatically retry', async () => {
    mock.probe.mockRejectedValueOnce({
      response: { status: 429, data: { detail: 'private-token-in-upstream-body' } },
    });
    await enterKey();
    await enterModel();
    await settle(() => view.root.findAllByType('input').find((n) => n.props.type === 'checkbox')!.props.onChange({
      target: { checked: true },
    }));
    await settle(() => button('Test cloud function calling').props.onClick());
    expect(text(view.root)).toContain('Cloud rate limit or capacity reached');
    expect(text(view.root)).not.toContain('private-token-in-upstream-body');
    expect(mock.probe).toHaveBeenCalledTimes(1);
    expect(button('Test cloud function calling').props.disabled).toBe(true);
  });

  it('rejects a response that asserts active execution or mismatched identity', async () => {
    mock.probe.mockResolvedValueOnce({
      ...candidate, tool_probe_verified: true, active: true,
      ready_for_execution: true, evidence_persisted: true,
    });
    await enterKey();
    await enterModel();
    await settle(() => view.root.findAllByType('input').find((n) => n.props.type === 'checkbox')!.props.onChange({
      target: { checked: true },
    }));
    await settle(() => button('Test cloud function calling').props.onClick());
    expect(text(view.root)).not.toContain('one synthetic function-call probe');
    expect(text(view.root)).toContain('That action could not be completed');
  });
});



it('shows previously verified capability evidence as non-executable on reopening', async () => {
  // We intentionally read status again instead of assuming an in-memory
  // success constitutes durable provider readiness.
  await settle(() => view.unmount());
  mock.status.mockResolvedValueOnce({
    active_provider: 'ollama-cloud',
    staged: candidate,
    providers: [{ provider_id: 'openrouter', credential_configured: true }],
    tool_probe_verified: true,
    ready_for_execution: false,
  });
  await settle(() => { view = create(<CloudProviderPreview />); });
  expect(text(view.root)).toContain('recent staged tool-call test is recorded');
  expect(text(view.root)).toContain('does not enable tasks');
  expect(mock.probe).not.toHaveBeenCalled();
});



it('allows one text-only preview without the tool-call probe after staging and consent', async () => {
  await enterKey();
  await enterModel();
  expect(mock.probe).not.toHaveBeenCalled();
  expect(button('Send one test message').props.disabled).toBe(true);
  await settle(() => view.root.findByProps({ id: 'cloud-preview-chat' }).props.onChange({
    target: { value: 'Hello' },
  }));
  expect(button('Send one test message').props.disabled).toBe(true);
  const checks = view.root.findAllByType('input').filter((n) => n.props.type === 'checkbox');
  expect(checks).toHaveLength(2);
  await settle(() => checks[1].props.onChange({ target: { checked: true } }));
  expect(button('Send one test message').props.disabled).toBe(false);
  await settle(() => button('Send one test message').props.onClick());
  expect(mock.chat).toHaveBeenCalledTimes(1);
  expect(mock.chat).toHaveBeenCalledWith(candidate, 'Hello');
  expect(mock.probe).not.toHaveBeenCalled();
  expect(text(view.root)).toContain('Hello from a text-only model!');
  expect(text(view.root)).toContain('Sythion did not execute or save any actions');
  expect(button('Send one test message').props.disabled).toBe(true);
});


it('rejects model chat response claiming task execution and consumes consent', async () => {
  mock.chat.mockResolvedValueOnce({
    ...candidate, assistant_text: 'I ran your task.',
    active: true, ready_for_execution: true, persisted: true, task_executed: true,
  });
  await enterKey();
  await enterModel();
  await settle(() => view.root.findByProps({ id: 'cloud-preview-chat' }).props.onChange({
    target: { value: 'Hi' },
  }));
  const checks = view.root.findAllByType('input').filter((n) => n.props.type === 'checkbox');
  await settle(() => checks[1].props.onChange({ target: { checked: true } }));
  await settle(() => button('Send one test message').props.onClick());
  expect(mock.chat).toHaveBeenCalledTimes(1);
  expect(text(view.root)).not.toContain('I ran your task.');
  expect(text(view.root)).toContain('That action could not be completed');
  expect(button('Send one test message').props.disabled).toBe(true);
});


it('does not auto-retry a rate-limited text reply, and clears consent', async () => {
  mock.chat.mockRejectedValueOnce({
    response: { status: 429, data: { detail: 'provider-secret-error' } },
  });
  await enterKey();
  await enterModel();
  await settle(() => view.root.findByProps({ id: 'cloud-preview-chat' }).props.onChange({
    target: { value: 'How are you?' },
  }));
  const checks = view.root.findAllByType('input').filter((n) => n.props.type === 'checkbox');
  await settle(() => checks[1].props.onChange({ target: { checked: true } }));
  await settle(() => button('Send one test message').props.onClick());
  expect(text(view.root)).toContain('Cloud rate limit or capacity reached');
  expect(text(view.root)).not.toContain('provider-secret-error');
  expect(button('Send one test message').props.disabled).toBe(true);
  expect(mock.chat).toHaveBeenCalledTimes(1);
});



it('enters answer-only mode only after a fresh matching server verification', async () => {
  await settle(() => view.unmount());
  // The shared beforeEach mounted a previous instance. Only count requests
  // made by the actual candidate check on this new component instance.
  mock.status.mockClear();
  const entered = vi.fn();
  mock.status.mockResolvedValue({
    active_provider: 'ollama-cloud', staged: candidate,
    tool_probe_verified: false, text_probe_verified: true,
    providers: [{ provider_id: 'openrouter', credential_configured: true }],
    ready_for_execution: false,
  });
  await settle(() => { view = create(<CloudProviderPreview onEnterConversationOnly={entered} />); });
  await settle(() => button('Enter conversation-only cloud mode').props.onClick());
  expect(entered).toHaveBeenCalledTimes(1);
  expect(mock.status).toHaveBeenCalledTimes(2); // mount, fresh proof before entry
  expect(mock.probe).not.toHaveBeenCalled();
  expect(mock.chat).not.toHaveBeenCalled();
});


it('rejects cloud-mode entry when the server reports expired evidence', async () => {
  await settle(() => view.unmount());
  const entered = vi.fn();
  mock.status.mockResolvedValueOnce({
    active_provider: 'ollama-cloud', staged: candidate,
    tool_probe_verified: false, text_probe_verified: true,
    providers: [{ provider_id: 'openrouter', credential_configured: true }],
    ready_for_execution: false,
  }).mockResolvedValueOnce({
    active_provider: 'ollama-cloud', staged: candidate,
    tool_probe_verified: false, text_probe_verified: false,
    providers: [{ provider_id: 'openrouter', credential_configured: true }],
    ready_for_execution: false,
  });
  await settle(() => { view = create(<CloudProviderPreview onEnterConversationOnly={entered} />); });
  await settle(() => button('Enter conversation-only cloud mode').props.onClick());
  expect(entered).not.toHaveBeenCalled();
  expect(text(view.root)).toContain('That action could not be completed');
});

it('shows a safe 403 explanation for a rejected credential write without echoing secret data', async () => {
  mock.saveCredential.mockRejectedValueOnce({
    response: { status: 403, data: { detail: 'private-provider-key-here' } },
  });
  await enterKey();
  expect(text(view.root)).toContain('blocked the local request (403)');
  expect(text(view.root)).not.toContain('private-provider-key-here');
  expect(text(view.root)).toContain('No key is stored');
});

it('points to local logs on a failed credential-store operation without disclosing provider data', async () => {
  mock.saveCredential.mockRejectedValueOnce({
    response: { status: 500, data: { detail: 'private-provider-key-here' } },
  });
  await enterKey();
  expect(text(view.root)).toContain('local operation (500)');
  expect(text(view.root)).toContain('sidecar.err.log');
  expect(text(view.root)).not.toContain('private-provider-key-here');
  expect(text(view.root)).toContain('No key is stored');
});


it('does not fetch model IDs automatically or before a key is stored', async () => {
  expect(mock.modelCatalog).not.toHaveBeenCalled();
  expect(button('Fetch model IDs').props.disabled).toBe(true);
  await enterKey();
  expect(button('Fetch model IDs').props.disabled).toBe(false);
  expect(mock.modelCatalog).not.toHaveBeenCalled();
});

it('retrieves, filters and selects a model ID without triggering inference', async () => {
  await enterKey();
  await settle(() => button('Fetch model IDs').props.onClick());
  expect(mock.modelCatalog).toHaveBeenCalledWith('openrouter');
  expect(text(view.root)).toContain('a/paid-model');
  expect(text(view.root)).toContain('b/free-model:free');
  await settle(() => view.root.findAllByType('input')
    .filter((n) => n.props.type === 'checkbox')[0].props.onChange({ target: { checked: true } }));
  expect(view.root.findAllByType('option').some((n) => n.props.value === 'a/paid-model')).toBe(false);
  await settle(() => view.root.findByProps({ id: 'cloud-preview-catalog-model' }).props.onChange({
    target: { value: 'b/free-model:free' },
  }));
  expect(view.root.findByProps({ id: 'cloud-preview-model' }).props.value).toBe('b/free-model:free');
  expect(mock.stage).not.toHaveBeenCalled();
  await settle(() => button('Stage model candidate').props.onClick());
  expect(mock.stage).toHaveBeenCalledWith({ provider_id: 'openrouter', model_id: 'b/free-model:free' });
  expect(mock.probe).not.toHaveBeenCalled();
  expect(mock.chat).not.toHaveBeenCalled();
});


it('verifies the exact key in desktop storage only after a deliberate click', async () => {
  expect(mock.verifyStoredKey).not.toHaveBeenCalled();
  expect(button('Verify stored OpenRouter key (one network request)').props.disabled).toBe(true);
  await enterKey();
  expect(button('Verify stored OpenRouter key (one network request)').props.disabled).toBe(false);
  await settle(() => button('Verify stored OpenRouter key (one network request)').props.onClick());
  expect(mock.verifyStoredKey).toHaveBeenCalledTimes(1);
  expect(text(view.root)).toContain('authenticated the key saved in this desktop');
  expect(mock.chat).not.toHaveBeenCalled();
  expect(mock.probe).not.toHaveBeenCalled();
});

it('shows safe upstream 402 diagnostics without displaying provider body', async () => {
  mock.chat.mockRejectedValueOnce({
    response: { status: 502, data: {
      detail: 'Cloud provider requires payment or credits (upstream HTTP 402)',
      confidential: 'private-provider-response',
    }},
  });
  await enterKey();
  await enterModel();
  await settle(() => view.root.findByProps({ id: 'cloud-preview-chat' }).props.onChange({
    target: { value: 'Hello' },
  }));
  const checks = view.root.findAllByType('input').filter((n) => n.props.type === 'checkbox');
  await settle(() => checks[1].props.onChange({ target: { checked: true } }));
  await settle(() => button('Send one test message').props.onClick());
  expect(text(view.root)).toContain('account credit or payment (402)');
  expect(text(view.root)).not.toContain('private-provider-response');
  expect(mock.chat).toHaveBeenCalledTimes(1);
});


it('identifies the provider-reported agentic harness restriction without exposing raw upstream details', async () => {
  mock.chat.mockRejectedValueOnce({ response: { status: 502, data: {
    detail: 'Cloud model requires an eligible agentic harness (upstream HTTP 403)',
    other: 'confidential-upstream-detail',
  } } });
  await enterKey();
  await enterModel();
  await settle(() => view.root.findByProps({ id: 'cloud-preview-chat' }).props.onChange({
    target: { value: 'hi' },
  }));
  const checks = view.root.findAllByType('input').filter((n) => n.props.type === 'checkbox');
  await settle(() => checks[1].props.onChange({ target: { checked: true } }));
  await settle(() => button('Send one test message').props.onClick());
  expect(text(view.root)).toContain('restricts this free model to eligible agentic harnesses');
  expect(text(view.root)).not.toContain('confidential-upstream-detail');
  expect(mock.chat).toHaveBeenCalledTimes(1);
});
