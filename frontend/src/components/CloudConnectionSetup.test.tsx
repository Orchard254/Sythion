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
  await act(async () => { fn(); for (let i = 0; i < 20; i++) await Promise.resolve(); });
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

beforeEach(async () => {
  vi.clearAllMocks();
  mock.status.mockResolvedValue(empty);
  mock.modelCatalog.mockResolvedValue({
    provider_id: 'openrouter', models: [
      { id: 'unit/cloud:free', free_variant: true, tools_listed: false },
      { id: 'unit/paid', free_variant: false, tools_listed: false },
    ], truncated: false, ready_for_execution: false,
  });
  mock.saveCredential.mockResolvedValue(undefined);
  mock.stage.mockResolvedValue(undefined);
  mock.chat.mockResolvedValue({
    provider_id: 'openrouter', model_id: 'unit/cloud:free',
    assistant_text: 'Hello!', ready_for_execution: false,
    active: false, persisted: false, task_executed: false,
  });
  await settle(() => { view = create(<CloudConnectionSetup />); });
});
afterEach(() => { act(() => view.unmount()); });

it('does not automatically browse, save, stage, send or execute inference', () => {
  expect(mock.status).toHaveBeenCalledTimes(1);
  expect(mock.modelCatalog).not.toHaveBeenCalled();
  expect(mock.saveCredential).not.toHaveBeenCalled();
  expect(mock.stage).not.toHaveBeenCalled();
  expect(mock.chat).not.toHaveBeenCalled();
  expect(button('Browse available models').props.disabled).toBe(true);
});

it('saves a key once and lists models only after the user clicks', async () => {
  await settle(() => view.root.findByProps({ id: 'cloud-config-key' }).props.onChange({
    target: { value: 'synthetic-test-key' },
  }));
  await settle(() => button('Save key and browse models').props.onClick());
  expect(mock.saveCredential).toHaveBeenCalledTimes(1);
  expect(mock.saveCredential).toHaveBeenCalledWith('openrouter', 'synthetic-test-key');
  expect(mock.modelCatalog).toHaveBeenCalledTimes(1);
  expect(view.root.findByProps({ id: 'cloud-config-key' }).props.value).toBe('');
  expect(text()).not.toContain('synthetic-test-key');
  expect(mock.chat).not.toHaveBeenCalled();
});

it('verifies exactly one chosen model after one explicit opt-in and stays answer-only', async () => {
  const ready = vi.fn();
  mock.status.mockReset().mockResolvedValueOnce(configured)
    .mockResolvedValueOnce(configured).mockResolvedValueOnce(verified);
  await settle(() => { view.unmount(); view = create(<CloudConnectionSetup onReady={ready} />); });
  await settle(() => view.root.findByProps({ id: 'cloud-config-model' }).props.onChange({
    target: { value: 'unit/cloud:free' },
  }));
  expect(button('Verify and start cloud conversation').props.disabled).toBe(true);
  await settle(() => view.root.findAllByType('input').find((node) => node.props.type === 'checkbox')!
    .props.onChange({ target: { checked: true } }));
  await settle(() => button('Verify and start cloud conversation').props.onClick());
  expect(mock.stage).toHaveBeenCalledTimes(1);
  expect(mock.stage).toHaveBeenCalledWith({ provider_id: 'openrouter', model_id: 'unit/cloud:free' });
  expect(mock.chat).toHaveBeenCalledTimes(1);
  expect(mock.chat.mock.calls[0][1]).toBe('Say hello in one short plain-text sentence.');
  expect(ready).toHaveBeenCalledTimes(1);
  expect(text()).not.toContain('synthetic-test-key');
});

it('reuses current proof without staging or spending inference quota', async () => {
  const ready = vi.fn();
  mock.status.mockReset().mockResolvedValue(verified);
  await settle(() => { view.unmount(); view = create(<CloudConnectionSetup onReady={ready} />); });
  // Current proof skips a redundant consent prompt and sends no inference.
  expect(view.root.findAllByType('input').filter((node) => node.props.type === 'checkbox')).toHaveLength(0);
  await settle(() => button('Start cloud conversation').props.onClick());
  expect(ready).toHaveBeenCalledTimes(1);
  expect(mock.stage).not.toHaveBeenCalled();
  expect(mock.chat).not.toHaveBeenCalled();
});

it('fails closed on a rejected model without exposing upstream private text', async () => {
  mock.status.mockReset().mockResolvedValue(configured);
  mock.chat.mockRejectedValueOnce({ response: { status: 502, data: {
    detail: 'Cloud model requires an eligible agentic harness (upstream HTTP 403)',
    raw: 'PRIVATE-UPSTREAM-BODY',
  } } });
  await settle(() => { view.unmount(); view = create(<CloudConnectionSetup />); });
  await settle(() => view.root.findByProps({ id: 'cloud-config-model' }).props.onChange({
    target: { value: 'unit/cloud:free' },
  }));
  await settle(() => view.root.findAllByType('input').find((node) => node.props.type === 'checkbox')!
    .props.onChange({ target: { checked: true } }));
  await settle(() => button('Verify and save model').props.onClick());
  expect(mock.chat).toHaveBeenCalledTimes(1);
  expect(text()).toContain('restricted to eligible agentic harnesses');
  expect(text()).not.toContain('PRIVATE-UPSTREAM-BODY');
});
