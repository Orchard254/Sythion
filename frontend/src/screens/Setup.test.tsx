import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SetupScreen } from './Setup';
import apiClient from '../services/api';

const mock = vi.hoisted(() => {
  const readiness = vi.fn();
  return { readiness, applySafeSettings: vi.fn(), speak: vi.fn(),
    cloudStatus: vi.fn(), cloudSaveCredential: vi.fn(), cloudStage: vi.fn(),
    cloudProbe: vi.fn(), cloudChat: vi.fn(), cloudCatalog: vi.fn(),
    cloudContract: vi.fn(), cloudActivate: vi.fn(), cloudRestore: vi.fn() };
});
vi.mock('../services/api', async (importOriginal) => ({
  ...await importOriginal<typeof import('../services/api')>(),
  setupService: { readiness: mock.readiness, applySafeSettings: mock.applySafeSettings },
  voiceService: { speak: mock.speak },
  cloudPreviewService: {
    status: mock.cloudStatus, saveCredential: mock.cloudSaveCredential,
    stage: mock.cloudStage, probe: mock.cloudProbe, chat: mock.cloudChat,
    assistantContract: mock.cloudContract, modelCatalog: mock.cloudCatalog,
  },
  activeInferenceService: {
    activate: mock.cloudActivate, restoreOllama: mock.cloudRestore,
  },
}));
vi.mock('../hooks/useVoiceCapture', () => ({ useVoiceCapture: () => ({ startCapture: vi.fn(), stopCapture: vi.fn() }) }));
let view: ReactTestRenderer;
const originalAdapter = apiClient.defaults.adapter;
let checks: Record<string, boolean>;
let selected: string;
let activeProvider: string;
const complete = vi.fn();
const cloudMode = vi.fn();
const labels = (node: { children: unknown[] }): string => node.children.map((c): string => typeof c === 'string' ? c : c && typeof c === 'object' && 'children' in c ? labels(c as {children:unknown[]}) : '').join('');
const button = (name: string) => view.root.findAllByType('button').find((b) => labels(b) === name)!;
const settle = async (action: () => void) => { await act(async () => { action(); for (let i=0;i<15;i++) await Promise.resolve(); }); };

beforeEach(async () => {
  vi.clearAllMocks();
  selected = ''; activeProvider = 'ollama-cloud';
  checks = {
    resources_ok: true, database_ok: true, sidecar_ok: true,
    selected_model_ok: false, inference_verified_ok: false,
    safe_settings_ok: false, tts_voice_ok: true, stt_model_installed: false,
    active_provider_ready_ok: false,
  };
  vi.stubGlobal('localStorage', { getItem: vi.fn(() => 'text'), setItem: vi.fn() });
  vi.stubGlobal('sessionStorage', { getItem: () => 'inert-discovery-token' });
  apiClient.defaults.adapter = async () => { throw new Error('Unexpected HTTP request in setup test'); };
  mock.readiness.mockImplementation(async () => ({
    checks: { ...checks },
    selected_model: selected,
    active_provider_id: activeProvider,
    // The backend returns this at the TOP LEVEL, not under checks.
    active_provider_ready_ok: checks.active_provider_ready_ok === true,
  }));
  mock.applySafeSettings.mockImplementation(async () => { checks.safe_settings_ok = true; return { ok: true }; });
  mock.speak.mockResolvedValue({ success: true });
  mock.cloudStatus.mockResolvedValue({
    active_provider: 'ollama-cloud', staged: {}, tool_probe_verified: false,
    text_probe_verified: false, ready_for_execution: false,
    providers: [
      { provider_id: 'ollama-cloud', credential_configured: true },
      { provider_id: 'openrouter', credential_configured: true },
      { provider_id: 'nvidia', credential_configured: true },
      { provider_id: 'groq', credential_configured: true },
    ],
  });
  mock.cloudCatalog.mockImplementation(async (provider: string) => ({
    provider_id: provider, ready_for_execution: false, truncated: false,
    models: [{ id: 'unit/model:free', free_variant: true, tools_listed: false }],
  }));
  mock.cloudChat.mockImplementation(async (selection: { provider_id: string; model_id: string }) => ({
    ...selection, assistant_text: 'Hello', active: false, persisted: false,
    task_executed: false, ready_for_execution: false,
  }));
  mock.cloudProbe.mockImplementation(async (selection: { provider_id: string; model_id: string }) => ({
    ...selection, tool_probe_verified: true, active: false,
    evidence_persisted: true, ready_for_execution: false,
  }));
  mock.cloudContract.mockImplementation(async (selection: { provider_id: string; model_id: string }) => ({
    ...selection, assistant_contract_verified: true, active: false,
    task_executed: false, evidence_persisted: true, ready_for_execution: false,
  }));
  mock.cloudActivate.mockImplementation(async (request: { provider_id: string; model_id: string }) => {
    activeProvider = request.provider_id;
    selected = request.model_id;
    checks.selected_model_ok = true;
    checks.inference_verified_ok = true;
    checks.active_provider_ready_ok = true;
    return { ...request, permissions_unchanged: true };
  });
  await settle(() => { view = create(<SetupScreen onComplete={complete} onCloudConversationOnly={cloudMode} />); });
});
afterEach(() => { act(() => view.unmount()); apiClient.defaults.adapter = originalAdapter; vi.unstubAllGlobals(); });

async function useConfiguredKey(provider = 'ollama-cloud') {
  if (provider !== 'ollama-cloud') {
    await settle(() => view.root.findByProps({ id: 'setup-provider' }).props.onChange({
      target: { value: provider },
    }));
  }
  await settle(() => button('Use configured key').props.onClick());
  await settle(() => view.root.findByProps({ id: 'setup-cloud-model' }).props.onChange({
    target: { value: 'unit/model:free' },
  }));
}

describe('setup to first conversation', () => {
  it('uses the same generic provider flow for Ollama Cloud and other providers', async () => {
    expect(view.root.findByProps({ id: 'setup-provider' }).props.value).toBe('ollama-cloud');
    expect(labels(view.root)).toContain('OpenRouter');
    expect(labels(view.root)).toContain('NVIDIA NIM');
    expect(labels(view.root)).toContain('Groq');
    // The bespoke Ollama-only key/model UI is gone.
    expect(view.root.findAllByProps({ id: 'setup-key' })).toHaveLength(0);
    expect(view.root.findAllByProps({ id: 'setup-model' })).toHaveLength(0);
    expect(mock.cloudStatus).toHaveBeenCalled();
    expect(mock.cloudChat).not.toHaveBeenCalled();

    await settle(() => button('Use configured key').props.onClick());
    expect(view.root.findByProps({ id: 'setup-cloud-model' })).toBeTruthy();
    await settle(() => view.root.findByProps({ id: 'setup-cloud-model' }).props.onChange({
      target: { value: 'unit/model:free' },
    }));
    expect(mock.cloudStage).toHaveBeenCalledWith({
      provider_id: 'ollama-cloud', model_id: 'unit/model:free',
    });
    expect(mock.cloudChat).toHaveBeenCalledTimes(1);
    expect(mock.cloudProbe).toHaveBeenCalledTimes(1);
    expect(mock.cloudContract).toHaveBeenCalledTimes(1);
    expect(mock.cloudActivate).toHaveBeenCalledWith({
      provider_id: 'ollama-cloud', model_id: 'unit/model:free',
    });
  });

  it('carries a selected cloud provider through the original wizard and Assistant handoff', async () => {
    await useConfiguredKey('openrouter');
    expect(mock.cloudActivate).toHaveBeenCalledWith({
      provider_id: 'openrouter', model_id: 'unit/model:free',
    });
    expect(button('Continue').props.disabled).toBe(false);
    await settle(() => button('Continue').props.onClick());
    await settle(() => button('Continue').props.onClick());
    await settle(() => button('Start conversation').props.onClick());
    expect(complete).toHaveBeenCalledWith(true);
    expect(cloudMode).not.toHaveBeenCalled();
  });

  it('keeps onboarding on model selection and explains a failed tool requirement', async () => {
    mock.cloudProbe.mockRejectedValueOnce({ response: {
      status: 422, data: { detail: 'Model did not demonstrate required tool calling' },
    } });
    await useConfiguredKey('openrouter');
    expect(mock.cloudActivate).not.toHaveBeenCalled();
    expect(labels(view.root)).toContain('structured Assistant format or function calling');
    expect(button('Continue').props.disabled).toBe(true);
  });

  it('explains a provider 402 without bypassing model readiness', async () => {
    mock.cloudContract.mockRejectedValueOnce({
      message: 'Inference failed: HTTP error 402 (private-provider-trace)',
      response: { status: 502, data: { detail: 'HTTP error 402 (private-provider-trace)' } },
    });
    await useConfiguredKey('ollama-cloud');
    expect(labels(view.root)).toContain('payment required');
    expect(labels(view.root)).toContain('credit, billing and usage limits');
    expect(labels(view.root)).not.toContain('private-provider-trace');
    expect(button('Continue').props.disabled).toBe(true);
    expect(complete).not.toHaveBeenCalled();
  });

  it('requires a real model check, applies safe settings, and allows text without microphone', async () => {
    await useConfiguredKey();
    expect(button('Continue').props.disabled).toBe(false);
    await settle(() => button('Continue').props.onClick());
    expect(mock.applySafeSettings).toHaveBeenCalled();
    await settle(() => button('Continue').props.onClick());
    await settle(() => button('Start conversation').props.onClick());
    expect(complete).toHaveBeenCalledWith(true);
    expect(localStorage.setItem).toHaveBeenCalledWith('sythion.replyMode', 'text');
    expect(mock.speak).not.toHaveBeenCalled();
  });

  it('keeps the active model when verification of a different candidate fails', async () => {
    mock.cloudCatalog.mockImplementation(async (provider: string) => ({
      provider_id: provider, ready_for_execution: false, truncated: false,
      models: [
        { id: 'unit/model:free', free_variant: true, tools_listed: false },
        { id: 'other/model:free', free_variant: true, tools_listed: false },
      ],
    }));
    await useConfiguredKey();
    mock.cloudContract.mockRejectedValueOnce(new Error('Invalid provider model'));
    await settle(() => view.root.findByProps({ id: 'setup-cloud-model' }).props.onChange({
      target: { value: 'other/model:free' },
    }));
    expect(mock.cloudActivate).not.toHaveBeenCalledWith({
      provider_id: 'ollama-cloud', model_id: 'other/model:free',
    });
    expect(labels(view.root)).toContain('could not verify this model');
    // The previously verified model stays active; no readiness is invented.
    expect(button('Continue').props.disabled).toBe(false);
  });

  it('stays on step 1 when safe settings return ok=false', async () => {
    await useConfiguredKey();
    mock.applySafeSettings.mockResolvedValueOnce({ ok: false });
    await settle(() => button('Continue').props.onClick());
    expect(mock.applySafeSettings).toHaveBeenCalled();
    expect(view.root.findByProps({ id: 'setup-cloud-model' })).toBeTruthy();
    expect(complete).not.toHaveBeenCalled();
  });

  it('requires successful speaker playback for voice replies and recovers with text', async () => {
    await useConfiguredKey();
    await settle(() => button('Continue').props.onClick());
    await settle(() => view.root.findAllByType('input').find((n) => n.props.type === 'radio' && !n.props.checked)!.props.onChange());
    expect(button('Continue').props.disabled).toBe(true);
    mock.speak.mockResolvedValueOnce({ success: false });
    await settle(() => button('Test speaker').props.onClick());
    expect(button('Continue').props.disabled).toBe(true);
    await settle(() => button('Test speaker').props.onClick());
    await settle(() => button('Continue').props.onClick());
    await settle(() => button('Start conversation').props.onClick());
    expect(complete).toHaveBeenCalledWith(false);
    expect(localStorage.setItem).toHaveBeenCalledWith('sythion.replyMode', 'voice');
  });

  it('rechecks readiness before entry and preserves setup on inference loss', async () => {
    await useConfiguredKey();
    await settle(() => button('Continue').props.onClick());
    await settle(() => button('Continue').props.onClick());
    checks.inference_verified_ok = false;
    await settle(() => button('Start conversation').props.onClick());
    expect(complete).not.toHaveBeenCalled();
    expect(button('Start conversation').props.disabled).toBe(true);
  });
});
