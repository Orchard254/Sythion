import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { CloudConnectionSetup } from './CloudConnectionSetup';

const mock = vi.hoisted(() => ({
  status: vi.fn(), modelCatalog: vi.fn(), saveCredential: vi.fn(),
  stage: vi.fn(), chat: vi.fn(),
}));
vi.mock('../services/api', async (original) => ({
  ...await original<typeof import('../services/api')>(),
  cloudPreviewService: mock,
}));

let view: ReactTestRenderer;
const settle = async (fn: () => void) => {
  await act(async () => { fn(); for (let i = 0; i < 25; i++) await Promise.resolve(); });
};
const text = () => JSON.stringify(view.toJSON());
const button = (label: string) => {
  const found = view.root.findAllByType('button').find((b) => b.children.includes(label));
  if (!found) throw new Error('Missing button ' + label);
  return found;
};
const empty = {
  staged: {},
  providers: [
    { provider_id: 'openrouter', credential_configured: false },
    { provider_id: 'nvidia', credential_configured: false },
    { provider_id: 'groq', credential_configured: false },
  ],
  text_probe_verified: false,
  ready_for_execution: false,
  tool_probe_verified: false,
};
const configured = {
  ...empty,
  providers: [{ provider_id: 'openrouter', credential_configured: true }],
};
const verified = {
  ...configured,
  staged: { provider_id: 'openrouter', model_id: 'unit/cloud:free' },
  text_probe_verified: true,
};
const selection = { provider_id: 'openrouter', model_id: 'unit/cloud:free' };
const list = {
  provider_id: 'openrouter', models: [
    { id: 'unit/cloud:free', free_variant: true, tools_listed: false },
    { id: 'unit/paid', free_variant: false, tools_listed: false },
  ], truncated: false, ready_for_execution: false,
};
const response = {
  ...selection,
  assistant_text: 'Hello!', ready_for_execution: false,
  active: false, persisted: false, task_executed: false,
};
const remount = async (onReady?: () => void) => {
  await settle(() => {
    view.unmount();
    view = create(<CloudConnectionSetup onReady={onReady} />);
  });
};
const choose = async (modelId = selection.model_id) => {
  await settle(() => view.root.findByProps({ id: 'cloud-config-catalog' }).props.onChange({
    target: { value: modelId },
  }));
};

beforeEach(async () => {
  vi.clearAllMocks();
  mock.status.mockResolvedValue(empty);
  mock.modelCatalog.mockResolvedValue(list);
  mock.saveCredential.mockResolvedValue(undefined);
  mock.stage.mockResolvedValue(undefined);
  mock.chat.mockResolvedValue(response);
  await settle(() => { view = create(<CloudConnectionSetup />); });
});
afterEach(() => { act(() => view.unmount()); });

it('loads only local state when no credential exists and never probes automatically', () => {
  expect(mock.status).toHaveBeenCalledTimes(1);
  expect(mock.modelCatalog).not.toHaveBeenCalled();
  expect(mock.saveCredential).not.toHaveBeenCalled();
  expect(mock.stage).not.toHaveBeenCalled();
  expect(mock.chat).not.toHaveBeenCalled();
  expect(view.root.findAllByProps({ id: 'cloud-config-catalog' })).toHaveLength(0);
});

it('loads the model catalog automatically for a saved credential, without inference', async () => {
  mock.status.mockReset().mockResolvedValue(configured);
  await remount();
  expect(mock.modelCatalog).toHaveBeenCalledTimes(1);
  expect(mock.saveCredential).not.toHaveBeenCalled();
  expect(mock.stage).not.toHaveBeenCalled();
  expect(mock.chat).not.toHaveBeenCalled();
  expect(view.root.findAllByProps({ id: 'cloud-config-search' })).toHaveLength(0);
  expect(view.root.findAllByProps({ id: 'cloud-config-model' })).toHaveLength(0);
  expect(view.root.findAllByType('input').filter((x) => x.props.type === 'checkbox')).toHaveLength(0);
  expect(text()).toContain('unit/cloud:free');
  expect(text()).not.toContain('unit/paid');
});

it('switches between Free and All without running inference', async () => {
  mock.status.mockReset().mockResolvedValue(configured);
  await remount();
  await settle(() => view.root.findByProps({ id: 'cloud-model-filter' }).props.onChange({
    target: { value: 'all' },
  }));
  expect(text()).toContain('unit/paid');
  await settle(() => view.root.findByProps({ id: 'cloud-model-filter' }).props.onChange({
    target: { value: 'free' },
  }));
  expect(text()).not.toContain('unit/paid');
  expect(mock.stage).not.toHaveBeenCalled();
  expect(mock.chat).not.toHaveBeenCalled();
  expect(mock.modelCatalog).toHaveBeenCalledTimes(1);
});

it('saves a new key and loads models with one click, without needing Browse', async () => {
  mock.status.mockReset().mockResolvedValueOnce(empty).mockResolvedValue(configured);
  await remount();
  await settle(() => view.root.findByProps({ id: 'cloud-config-key' }).props.onChange({
    target: { value: 'synthetic-test-key' },
  }));
  await settle(() => button('Connect and load models').props.onClick());
  expect(mock.saveCredential).toHaveBeenCalledTimes(1);
  expect(mock.saveCredential).toHaveBeenCalledWith('openrouter', 'synthetic-test-key');
  expect(mock.modelCatalog).toHaveBeenCalledTimes(1);
  expect(view.root.findByProps({ id: 'cloud-config-key' }).props.value).toBe('');
  expect(text()).not.toContain('synthetic-test-key');
  expect(view.root.findAllByProps({ id: 'cloud-config-catalog' })).toHaveLength(1);
  expect(mock.chat).not.toHaveBeenCalled();
});

it('automatically checks the chosen model once and enters answer-only chat', async () => {
  const enter = vi.fn();
  mock.status.mockReset().mockResolvedValueOnce(configured)
    .mockResolvedValueOnce(configured).mockResolvedValueOnce(verified);
  await remount(enter);
  await choose();
  expect(mock.stage).toHaveBeenCalledTimes(1);
  expect(mock.stage).toHaveBeenCalledWith(selection);
  expect(mock.chat).toHaveBeenCalledTimes(1);
  expect(mock.chat).toHaveBeenCalledWith(selection, 'Say hello in one short plain-text sentence.');
  expect(enter).toHaveBeenCalledTimes(1);
  expect(text()).not.toContain('synthetic-test-key');
});

it('reuses verified proof on selection without spending another inference request', async () => {
  const enter = vi.fn();
  mock.status.mockReset().mockResolvedValue(verified);
  await remount(enter);
  expect(mock.chat).not.toHaveBeenCalled();
  expect(mock.stage).not.toHaveBeenCalled();
  await choose();
  expect(enter).toHaveBeenCalledTimes(1);
  expect(mock.chat).not.toHaveBeenCalled();
  expect(mock.stage).not.toHaveBeenCalled();
});

it('does not silently test the previously staged model when reopening setup', async () => {
  const enter = vi.fn();
  mock.status.mockReset().mockResolvedValue(verified);
  await remount(enter);
  expect(view.root.findByProps({ id: 'cloud-config-catalog' }).props.value).toBe('');
  expect(mock.chat).not.toHaveBeenCalled();
  expect(enter).not.toHaveBeenCalled();
});

it('reports provider restrictions without leaking provider response bodies or retrying', async () => {
  mock.status.mockReset().mockResolvedValue(configured);
  mock.chat.mockRejectedValueOnce({ response: { status: 502, data: {
    detail: 'Cloud model requires an eligible agentic harness (upstream HTTP 403)',
    raw: 'PRIVATE-UPSTREAM-BODY',
  } } });
  await remount();
  await choose();
  expect(mock.chat).toHaveBeenCalledTimes(1);
  expect(text()).toContain('restricted to eligible agentic harnesses');
  expect(text()).not.toContain('PRIVATE-UPSTREAM-BODY');
  expect(button('Retry selected model')).toBeTruthy();
  // Re-rendering and changing Free/All must not retry the failed call.
  await settle(() => view.root.findByProps({ id: 'cloud-model-filter' }).props.onChange({
    target: { value: 'all' },
  }));
  expect(mock.chat).toHaveBeenCalledTimes(1);
});

it('retries an unsuccessful model only after the user deliberately clicks Retry', async () => {
  mock.status.mockReset().mockResolvedValue(configured);
  mock.chat.mockRejectedValueOnce(new Error('upstream unavailable'));
  await remount();
  await choose();
  expect(mock.chat).toHaveBeenCalledTimes(1);
  expect(button('Retry selected model')).toBeTruthy();
  await settle(() => button('Retry selected model').props.onClick());
  expect(mock.chat).toHaveBeenCalledTimes(2);
});

it('blocks two rapid selections from sending duplicate inference requests', async () => {
  mock.status.mockReset().mockResolvedValueOnce(configured)
    .mockResolvedValueOnce(configured).mockResolvedValueOnce(verified);
  let release!: (value: unknown) => void;
  mock.chat.mockImplementationOnce(() => new Promise((resolve) => { release = resolve; }));
  await remount();
  await settle(() => {
    view.root.findByProps({ id: 'cloud-config-catalog' }).props.onChange({
      target: { value: 'unit/cloud:free' },
    });
    view.root.findByProps({ id: 'cloud-config-catalog' }).props.onChange({
      target: { value: 'unit/cloud:free' },
    });
  });
  expect(mock.chat).toHaveBeenCalledTimes(1);
  await settle(() => release(response));
  expect(mock.chat).toHaveBeenCalledTimes(1);
});
