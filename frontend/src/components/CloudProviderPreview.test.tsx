import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CloudProviderPreview } from './CloudProviderPreview';
import { cloudPreviewService } from '../services/api';

const mock = vi.hoisted(() => ({
  status: vi.fn(), saveCredential: vi.fn(), stage: vi.fn(), probe: vi.fn(),
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
  mock.saveCredential.mockResolvedValue(undefined);
  mock.stage.mockResolvedValue(undefined);
  mock.probe.mockResolvedValue({
    ...candidate, tool_probe_verified: true, active: false,
    ready_for_execution: false, evidence_persisted: false,
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
      ready_for_execution: true, evidence_persisted: false,
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
