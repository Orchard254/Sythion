import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SetupScreen } from './Setup';
import apiClient from '../services/api';

const mock = vi.hoisted(() => {
  const readiness = vi.fn();
  return { readiness, testCredentials: vi.fn(), selectModel: vi.fn(), inference: vi.fn(), applySafeSettings: vi.fn(), update: vi.fn(), speak: vi.fn(),
    cloudStatus: vi.fn(), cloudSaveCredential: vi.fn(), cloudStage: vi.fn(),
    cloudProbe: vi.fn(), cloudChat: vi.fn(), cloudCatalog: vi.fn(),
    cloudActivate: vi.fn(), cloudRestore: vi.fn() };
});
vi.mock('../services/api', async (importOriginal) => ({
  ...await importOriginal<typeof import('../services/api')>(),
  setupService: { readiness: mock.readiness, testCredentials: mock.testCredentials, selectModel: mock.selectModel, inference: mock.inference, applySafeSettings: mock.applySafeSettings },
  voiceService: { speak: mock.speak },
  settingsService: { update: mock.update },
  cloudPreviewService: {
    status: mock.cloudStatus, saveCredential: mock.cloudSaveCredential,
    stage: mock.cloudStage, probe: mock.cloudProbe, chat: mock.cloudChat,
    modelCatalog: mock.cloudCatalog,
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
  vi.clearAllMocks(); selected = ''; activeProvider = 'ollama-cloud';
  checks = { resources_ok:true,database_ok:true,sidecar_ok:true,ollama_api_key_ok:false,ollama_health_ok:false,selected_model_ok:false,inference_verified_ok:false,safe_settings_ok:false,tts_voice_ok:true,stt_model_installed:false };
  vi.stubGlobal('localStorage', { getItem: vi.fn(() => 'text'), setItem: vi.fn() });
  vi.stubGlobal('sessionStorage', { getItem: () => 'inert-discovery-token' });
  apiClient.defaults.adapter = async () => { throw new Error('Unexpected HTTP request in setup test'); };
  mock.readiness.mockImplementation(async () => ({
    checks: {...checks}, selected_model: selected, active_provider_id: activeProvider,
  }));
  mock.testCredentials.mockImplementation(async () => { checks.ollama_api_key_ok=true;checks.ollama_health_ok=true;return {valid:true,models:['cloud-model']}; });
  mock.selectModel.mockImplementation(async (model: string) => {selected=model;checks.selected_model_ok=true;});
  mock.inference.mockImplementation(async () => { checks.inference_verified_ok=true;return {success:true}; });
  mock.applySafeSettings.mockImplementation(async () => { checks.safe_settings_ok=true; return { ok: true }; });
  mock.update.mockResolvedValue({});mock.speak.mockResolvedValue({success:true});
  mock.cloudStatus.mockResolvedValue({
    active_provider: 'ollama-cloud', staged: {}, ready_for_execution: false,
    providers: [
      { provider_id: 'openrouter', credential_configured: false },
      { provider_id: 'nvidia', credential_configured: false },
      { provider_id: 'groq', credential_configured: false },
    ],
  });
  mock.cloudStage.mockResolvedValue(undefined);
  mock.cloudSaveCredential.mockResolvedValue(undefined);
  mock.cloudCatalog.mockResolvedValue({
    provider_id: 'openrouter',
    ready_for_execution: false, truncated: false,
    models: [{ id: 'unit/model:free', free_variant: true, tools_listed: false }],
  });
  mock.cloudChat.mockResolvedValue({
    provider_id: 'openrouter', model_id: 'unit/model:free',
    assistant_text: 'Hello', active: false, persisted: false,
    task_executed: false, ready_for_execution: false,
  });
  mock.cloudProbe.mockResolvedValue({
    provider_id: 'openrouter', model_id: 'unit/model:free',
    tool_probe_verified: true, active: false, evidence_persisted: true,
  });
  mock.cloudActivate.mockImplementation(async (request: { provider_id: string; model_id: string }) => {
    activeProvider = request.provider_id;
    selected = request.model_id;
    checks.selected_model_ok = true;
    checks.inference_verified_ok = true;
    checks.active_provider_ready_ok = true;
    return { ...request, permissions_unchanged: true };
  });
  await settle(() => {view=create(<SetupScreen onComplete={complete} onCloudConversationOnly={cloudMode} />);});
});
afterEach(() => { act(() => view.unmount()); apiClient.defaults.adapter = originalAdapter; vi.unstubAllGlobals(); });

async function connect() {
  await settle(() => view.root.findByProps({id:'setup-key'}).props.onChange({target:{value:'test-key'}}));
  await settle(() => button('Connect and save key').props.onClick());
  expect(view.root.findAllByProps({id:'setup-key'})).toHaveLength(0);
  await settle(() => view.root.findByProps({id:'setup-model'}).props.onChange({target:{value:'cloud-model'}}));
  await settle(() => button('Test selected model').props.onClick());
}

describe('setup to first conversation', () => {
  it('offers provider choice directly, without a separate cloud preview or hidden requests', async () => {
    expect(view.root.findByProps({ id: 'setup-provider' }).props.value).toBe('ollama-cloud');
    expect(labels(view.root)).toContain('OpenRouter');
    expect(labels(view.root)).toContain('NVIDIA NIM');
    expect(labels(view.root)).toContain('Groq');
    expect(labels(view.root)).not.toContain('Explore alternative cloud providers');
    expect(mock.cloudStatus).not.toHaveBeenCalled();
    expect(mock.cloudChat).not.toHaveBeenCalled();
    expect(mock.cloudProbe).not.toHaveBeenCalled();
    expect(mock.cloudActivate).not.toHaveBeenCalled();
    await settle(() => view.root.findByProps({ id: 'setup-provider' }).props.onChange({
      target: { value: 'openrouter' },
    }));
    expect(view.root.findByProps({ id: 'setup-cloud-key' })).toBeTruthy();
    expect(mock.cloudStatus).toHaveBeenCalledTimes(1);
    expect(mock.cloudChat).not.toHaveBeenCalled();
  });

  it('uses a real selected cloud provider through the original wizard and main Assistant handoff', async () => {
    mock.cloudStatus.mockResolvedValue({
      active_provider: 'ollama-cloud', staged: {}, tool_probe_verified: false,
      text_probe_verified: false, ready_for_execution: false,
      providers: [{ provider_id: 'openrouter', credential_configured: true }],
    });
    await settle(() => view.root.findByProps({ id: 'setup-provider' }).props.onChange({
      target: { value: 'openrouter' },
    }));
    await settle(() => button('Use configured key').props.onClick());
    expect(view.root.findByProps({ id: 'setup-cloud-model' })).toBeTruthy();
    await settle(() => view.root.findByProps({ id: 'setup-cloud-model' }).props.onChange({
      target: { value: 'unit/model:free' },
    }));
    expect(mock.cloudStage).toHaveBeenCalledTimes(1);
    expect(mock.cloudChat).toHaveBeenCalledTimes(1);
    expect(mock.cloudProbe).toHaveBeenCalledTimes(1);
    expect(mock.cloudActivate).toHaveBeenCalledTimes(1);
    expect(mock.cloudActivate).toHaveBeenCalledWith({
      provider_id: 'openrouter', model_id: 'unit/model:free',
    });
    expect(button('Continue').props.disabled).toBe(false);
    await settle(() => button('Continue').props.onClick());
    await settle(() => button('Continue').props.onClick());
    await settle(() => button('Start conversation').props.onClick());
    expect(complete).toHaveBeenCalledWith(true);
    expect(mock.inference).not.toHaveBeenCalled();
    expect(cloudMode).not.toHaveBeenCalled();
    expect(checks.ollama_api_key_ok).toBe(false);
  });

  it('keeps onboarding on model selection and explains a failed tool requirement', async () => {
    mock.cloudStatus.mockResolvedValue({
      staged: {}, tool_probe_verified: false, text_probe_verified: false,
      ready_for_execution: false,
      providers: [{ provider_id: 'openrouter', credential_configured: true }],
    });
    mock.cloudProbe.mockRejectedValueOnce({ response: {
      status: 422, data: { detail: 'Model did not demonstrate required tool calling' },
    } });
    await settle(() => view.root.findByProps({ id: 'setup-provider' }).props.onChange({
      target: { value: 'openrouter' },
    }));
    await settle(() => button('Use configured key').props.onClick());
    await settle(() => view.root.findByProps({ id: 'setup-cloud-model' }).props.onChange({
      target: { value: 'unit/model:free' },
    }));
    expect(mock.cloudActivate).not.toHaveBeenCalled();
    expect(labels(view.root)).toContain('did not demonstrate the function calling');
    expect(button('Continue').props.disabled).toBe(true);
  });

  it('guides a new user to connect a key before discovering models', async () => {
    expect(view.root.findAllByProps({id:'setup-model'})).toHaveLength(0);
    expect(view.root.findAllByType('button').some((b) => labels(b) === 'Use configured key')).toBe(false);
    await settle(() => view.root.findByProps({id:'setup-key'}).props.onChange({target:{value:'test-key'}}));
    await settle(() => button('Connect and save key').props.onClick());
    expect(view.root.findAllByProps({id:'setup-key'})).toHaveLength(0);
    expect(view.root.findByProps({id:'setup-model'})).toBeTruthy();
    expect(button('Continue').props.disabled).toBe(true);
    await settle(() => button('Back').props.onClick());
    expect(view.root.findByProps({id:'setup-key'}).props.value).toBe('');
    expect(button('Use configured key')).toBeTruthy();
  });
  it('discovers exact selectable beam through the real object-to-ID service without saving a key', async () => {
    checks.ollama_api_key_ok = true;
    await settle(() => button('Recheck connection').props.onClick());
    apiClient.defaults.adapter = async (config) => {
      expect(config.url).toBe('/models');
      return { data: JSON.stringify({ models: [
        { name: 'beam', model: 'beam' }, { model: 'other:exact-tag' }, { name: 'beam' },
      ] }), status: 200, statusText: 'OK', headers: {}, config };
    };
    await settle(() => button('Use configured key').props.onClick());
    expect(view.root.findAllByType('option').map((option) => [option.props.value, labels(option)]))
      .toStrictEqual([['', 'Select a model'], ['beam', 'beam'], ['other:exact-tag', 'other:exact-tag']]);
    await settle(() => view.root.findByProps({id:'setup-model'}).props.onChange({target:{value:'beam'}}));
    expect(view.root.findByProps({id:'setup-model'}).props.value).toBe('beam');
    expect(button('Test selected model').props.disabled).toBe(false);
    await settle(() => button('Test selected model').props.onClick());
    expect(mock.selectModel).toHaveBeenCalledWith('beam');
    expect(mock.inference).toHaveBeenCalledWith('beam');
    expect(mock.testCredentials).not.toHaveBeenCalled();
    expect(view.root.findAllByProps({id:'setup-key'})).toHaveLength(0);
  });
  it('explains a verified provider 402 without bypassing model readiness', async () => {
    await settle(() => view.root.findByProps({id:'setup-key'}).props.onChange({target:{value:'test-key'}}));
    await settle(() => button('Connect and save key').props.onClick());
    await settle(() => view.root.findByProps({id:'setup-model'}).props.onChange({target:{value:'cloud-model'}}));
    mock.inference.mockRejectedValueOnce(new Error('Inference failed: HTTP error 402 (hidden-provider-detail)'));
    await settle(() => button('Test selected model').props.onClick());
    expect(labels(view.root)).toContain('Ollama Cloud returned HTTP 402');
    expect(labels(view.root)).toContain('Check your account credit');
    expect(labels(view.root)).not.toContain('hidden-provider-detail');
    expect(button('Continue').props.disabled).toBe(true);
    expect(complete).not.toHaveBeenCalled();
  });

  it('requires a real model check, applies safe settings, and allows text without microphone', async () => {
    expect(button('Connect and save key').props.disabled).toBe(true);
    await connect();
    expect(button('Continue').props.disabled).toBe(false);
    await settle(() => button('Continue').props.onClick());
    expect(mock.applySafeSettings).toHaveBeenCalled();
    expect(mock.update).not.toHaveBeenCalled();
    await settle(() => button('Continue').props.onClick());
    await settle(() => button('Start conversation').props.onClick());
    expect(complete).toHaveBeenCalledWith(true);
    expect(localStorage.setItem).toHaveBeenCalledWith('sythion.replyMode','text');
    expect(mock.speak).not.toHaveBeenCalled();
  });

  it('does not say the connection is ready for a different untested model', async () => {
    await connect();
    await settle(() => view.root.findByProps({id:'setup-model'}).props.onChange({target:{value:'another-model'}}));
    expect(button('Continue').props.disabled).toBe(true);
    expect(labels(view.root)).not.toContain('Your connection is ready.');
    expect(labels(view.root)).toContain('Select and verify a model to continue.');
  });

  it('explains key setup and offers a useful recovery when connection fails', async () => {
    expect(labels(view.root)).toContain('ollama.com/settings/keys');
    expect(labels(view.root)).toContain('Keep it private');
    mock.testCredentials.mockRejectedValueOnce(new Error('technical failure'));
    await settle(() => view.root.findByProps({id:'setup-key'}).props.onChange({target:{value:'test-key'}}));
    await settle(() => button('Connect and save key').props.onClick());
    expect(labels(view.root)).toContain('Check your key and internet connection');
    expect(view.root.findByProps({id:'setup-key'}).props.value).toBe('test-key');
    expect(view.root.findAllByProps({id:'setup-model'})).toHaveLength(0);
  });

  it('stays on step 1 when safe settings return ok=false', async () => {
    await connect();
    mock.applySafeSettings.mockResolvedValueOnce({ ok: false });
    await settle(() => button('Continue').props.onClick());
    expect(mock.applySafeSettings).toHaveBeenCalled();
    expect(view.root.findByProps({ id: 'setup-model' })).toBeTruthy();
    expect(complete).not.toHaveBeenCalled();
  });

  it('stays on step 1 when safe settings request is rejected', async () => {
    await connect();
    mock.applySafeSettings.mockRejectedValueOnce(new Error('boom'));
    await settle(() => button('Continue').props.onClick());
    expect(complete).not.toHaveBeenCalled();
  });

  it('stays on step 1 when refresh says safe settings are not ready', async () => {
    await connect();
    mock.applySafeSettings.mockImplementationOnce(async () => ({ ok: true }));
    await settle(() => button('Continue').props.onClick());
    expect(complete).not.toHaveBeenCalled();
  });
  it('requires successful speaker playback for voice replies and recovers with text', async () => {
    await connect();await settle(() => button('Continue').props.onClick());
    await settle(() => view.root.findAllByType('input').find((n)=>n.props.type==='radio' && !n.props.checked)!.props.onChange());
    expect(button('Continue').props.disabled).toBe(true);
    mock.speak.mockResolvedValueOnce({success:false});
    await settle(() => button('Test speaker').props.onClick());
    expect(button('Continue').props.disabled).toBe(true);
    await settle(() => button('Test speaker').props.onClick());
    await settle(() => button('Continue').props.onClick());
    await settle(() => button('Start conversation').props.onClick());
    expect(complete).toHaveBeenCalledWith(false);
    expect(localStorage.setItem).toHaveBeenCalledWith('sythion.replyMode','voice');
  });
  it('rechecks readiness before entry and preserves setup on inference loss', async () => {
    await connect();await settle(() => button('Continue').props.onClick());
    await settle(() => button('Continue').props.onClick());
    checks.inference_verified_ok=false;
    await settle(() => button('Start conversation').props.onClick());
    expect(complete).not.toHaveBeenCalled();
    expect(button('Start conversation').props.disabled).toBe(true);
  });
});
