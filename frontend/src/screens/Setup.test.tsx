import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SetupScreen } from './Setup';
import apiClient from '../services/api';

const mock = vi.hoisted(() => {
  const readiness = vi.fn();
  return { readiness, testCredentials: vi.fn(), selectModel: vi.fn(), inference: vi.fn(), applySafeSettings: vi.fn(), update: vi.fn(), speak: vi.fn() };
});
vi.mock('../services/api', async (importOriginal) => ({
  ...await importOriginal<typeof import('../services/api')>(),
  setupService: { readiness: mock.readiness, testCredentials: mock.testCredentials, selectModel: mock.selectModel, inference: mock.inference, applySafeSettings: mock.applySafeSettings },
  voiceService: { speak: mock.speak },
  settingsService: { update: mock.update },
}));
vi.mock('../hooks/useVoiceCapture', () => ({ useVoiceCapture: () => ({ startCapture: vi.fn(), stopCapture: vi.fn() }) }));
let view: ReactTestRenderer;
const originalAdapter = apiClient.defaults.adapter;
let checks: Record<string, boolean>;
let selected: string;
const complete = vi.fn();
const labels = (node: { children: unknown[] }): string => node.children.map((c): string => typeof c === 'string' ? c : c && typeof c === 'object' && 'children' in c ? labels(c as {children:unknown[]}) : '').join('');
const button = (name: string) => view.root.findAllByType('button').find((b) => labels(b) === name)!;
const settle = async (action: () => void) => { await act(async () => { action(); for (let i=0;i<15;i++) await Promise.resolve(); }); };

beforeEach(async () => {
  vi.clearAllMocks(); selected = '';
  checks = { resources_ok:true,database_ok:true,sidecar_ok:true,ollama_api_key_ok:false,ollama_health_ok:false,selected_model_ok:false,inference_verified_ok:false,safe_settings_ok:false,tts_voice_ok:true,stt_model_installed:false };
  vi.stubGlobal('localStorage', { getItem: vi.fn(() => 'text'), setItem: vi.fn() });
  vi.stubGlobal('sessionStorage', { getItem: () => 'inert-discovery-token' });
  apiClient.defaults.adapter = async () => { throw new Error('Unexpected HTTP request in setup test'); };
  mock.readiness.mockImplementation(async () => ({ checks: {...checks}, selected_model:selected }));
  mock.testCredentials.mockImplementation(async () => { checks.ollama_api_key_ok=true;checks.ollama_health_ok=true;return {valid:true,models:['cloud-model']}; });
  mock.selectModel.mockImplementation(async (model: string) => {selected=model;checks.selected_model_ok=true;});
  mock.inference.mockImplementation(async () => { checks.inference_verified_ok=true;return {success:true}; });
  mock.applySafeSettings.mockImplementation(async () => { checks.safe_settings_ok=true; return { ok: true }; });
  mock.update.mockResolvedValue({});mock.speak.mockResolvedValue({success:true});
  await settle(() => {view=create(<SetupScreen onComplete={complete} />);});
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
    expect(labels(view.root)).toContain('The test sends a short request');
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
