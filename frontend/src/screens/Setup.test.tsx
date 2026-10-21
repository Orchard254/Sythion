import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SetupScreen } from './Setup';

const mock = vi.hoisted(() => {
  const readiness = vi.fn();
  return { readiness, testCredentials: vi.fn(), selectModel: vi.fn(), inference: vi.fn(), applySafeSettings: vi.fn(), update: vi.fn(), speak: vi.fn() };
});
vi.mock('../services/api', () => ({
  setupService: { readiness: mock.readiness, testCredentials: mock.testCredentials, selectModel: mock.selectModel, inference: mock.inference, applySafeSettings: mock.applySafeSettings },
  modelService: { available: vi.fn().mockResolvedValue(['cloud-model']) },
  voiceService: { speak: mock.speak },
  settingsService: { update: mock.update },
}));
vi.mock('../hooks/useVoiceCapture', () => ({ useVoiceCapture: () => ({ startCapture: vi.fn(), stopCapture: vi.fn() }) }));
let view: ReactTestRenderer;
let checks: Record<string, boolean>;
let selected: string;
const complete = vi.fn();
const labels = (node: { children: unknown[] }): string => node.children.map((c): string => typeof c === 'string' ? c : c && typeof c === 'object' && 'children' in c ? labels(c as {children:unknown[]}) : '').join('');
const button = (name: string) => view.root.findAllByType('button').find((b) => labels(b) === name)!;
const settle = async (action: () => void) => { await act(async () => { action(); for (let i=0;i<15;i++) await Promise.resolve(); }); };

beforeEach(async () => {
  vi.clearAllMocks(); selected = '';
  checks = { resources_ok:true,database_ok:true,sidecar_ok:true,ollama_api_key_ok:false,ollama_health_ok:false,selected_model_ok:false,inference_verified_ok:false,safe_settings_ok:false,tts_voice_ok:true,stt_model_installed:false };
  vi.stubGlobal('localStorage', { setItem: vi.fn() });
  mock.readiness.mockImplementation(async () => ({ checks: {...checks}, selected_model:selected }));
  mock.testCredentials.mockImplementation(async () => { checks.ollama_api_key_ok=true;checks.ollama_health_ok=true;return {valid:true,models:['cloud-model']}; });
  mock.selectModel.mockImplementation(async (model: string) => {selected=model;checks.selected_model_ok=true;});
  mock.inference.mockImplementation(async () => { checks.inference_verified_ok=true;return {success:true}; });
  mock.applySafeSettings.mockImplementation(async () => { checks.safe_settings_ok=true; });
  mock.update.mockResolvedValue({});mock.speak.mockResolvedValue({success:true});
  await settle(() => {view=create(<SetupScreen onComplete={complete} />);});
});
afterEach(() => { act(() => view.unmount()); vi.unstubAllGlobals(); });

async function connect() {
  await settle(() => view.root.findByProps({id:'setup-key'}).props.onChange({target:{value:'test-key'}}));
  await settle(() => button('Connect and save key').props.onClick());
  expect(view.root.findByProps({id:'setup-key'}).props.value).toBe('');
  await settle(() => view.root.findByProps({id:'setup-model'}).props.onChange({target:{value:'cloud-model'}}));
  await settle(() => button('Test selected model').props.onClick());
}

describe('setup to first conversation', () => {
  it('requires a real model check, applies guarded settings, and allows text without microphone', async () => {
    expect(button('Continue').props.disabled).toBe(true);
    await connect();
    expect(button('Continue').props.disabled).toBe(false);
    await settle(() => button('Continue').props.onClick());
    expect(mock.update).toHaveBeenCalledWith({autonomy_level:'guarded',retention_days:90});
    await settle(() => button('Continue').props.onClick());
    await settle(() => button('Start conversation').props.onClick());
    expect(complete).toHaveBeenCalledWith(true);
    expect(localStorage.setItem).toHaveBeenCalledWith('sythion.replyMode','text');
    expect(mock.speak).not.toHaveBeenCalled();
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
