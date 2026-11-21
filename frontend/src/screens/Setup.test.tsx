import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SetupScreen } from './Setup';
import apiClient from '../services/api';

const mock = vi.hoisted(() => {
  const readiness = vi.fn();
  return { readiness, applySafeSettings: vi.fn(), speak: vi.fn(), installAssets: vi.fn(),
    cloudStatus: vi.fn(), cloudSaveCredential: vi.fn(), cloudStage: vi.fn(),
    cloudProbe: vi.fn(), cloudChat: vi.fn(), cloudCatalog: vi.fn(),
    cloudContract: vi.fn(), cloudActivate: vi.fn(), cloudRestore: vi.fn() };
});
vi.mock('../services/api', async (importOriginal) => ({
  ...await importOriginal<typeof import('../services/api')>(),
  setupService: { readiness: mock.readiness, applySafeSettings: mock.applySafeSettings },
  voiceService: { speak: mock.speak, installAssets: mock.installAssets },
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
const settle = async (action: () => void) => { await act(async () => { action(); for (let i=0;i<40;i++) await Promise.resolve(); }); };

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
  mock.installAssets.mockResolvedValue({ stt_model_installed: true });
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

async function goToConnect() {
  if (view.root.findAllByProps({ id: 'setup-provider' }).length === 0) {
    await settle(() => button('Back').props.onClick());
  }
}

async function useConfiguredKey(provider = 'ollama-cloud') {
  if (provider !== 'ollama-cloud') {
    await goToConnect();
    await settle(() => view.root.findByProps({ id: 'setup-provider' }).props.onChange({
      target: { value: provider },
    }));
    await settle(() => {});
    // After deliberately returning to Connect, the saved-key action is shown
    // again so the provider decision remains a genuine owner action.
    await settle(() => button('Use configured key').props.onClick());
  }
  await settle(() => view.root.findByProps({ id: 'setup-cloud-model' }).props.onChange({
    target: { value: 'unit/model:free' },
  }));
}

/** Advance from the model step to chat: apply safe settings, then the final
 * step auto-transitions once readiness passes (no "Start conversation" click). */
async function finishOnboarding() {
  await settle(() => button('Continue').props.onClick());
  await settle(() => button('Continue').props.onClick());
  await settle(() => {});
}

describe('setup to first conversation', () => {
  it('uses the same generic provider flow for Ollama Cloud and other providers', async () => {
    // A saved key auto-advances past Connect; the provider decision is still
    // reachable via Back and remains a genuine owner action.
    await goToConnect();
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
    await finishOnboarding();
    expect(complete).toHaveBeenCalledWith(true);
    expect(cloudMode).not.toHaveBeenCalled();
    // The final transition is automatic: there is no manual start button.
    expect(view.root.findAllByType('button').some((b) => labels(b) === 'Start conversation')).toBe(false);
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
    // Every bounded 502/503 recheck still fails, so the honest 402 error shows
    // instead of a transient failure self-healing away.
    mock.cloudContract.mockRejectedValue({
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
    await settle(() => {});
    expect(complete).toHaveBeenCalledWith(true);
    expect(localStorage.setItem).toHaveBeenCalledWith('sythion.replyMode', 'text');
    // Text mode never runs the local speaker check or installs STT assets.
    expect(mock.speak).not.toHaveBeenCalled();
    expect(mock.installAssets).not.toHaveBeenCalled();
  });

  it('keeps the active model when verification of a different candidate fails', async () => {
    mock.cloudCatalog.mockImplementation(async (provider: string) => ({
      provider_id: provider, ready_for_execution: false, truncated: false,
      models: [
        { id: 'unit/model:free', free_variant: true, tools_listed: false },
        { id: 'other/model:free', free_variant: true, tools_listed: false },
      ],
    }));
    // Auto-advance loaded the model step in beforeEach; re-enter it so the
    // two-model catalog above is what the picker actually holds.
    await goToConnect();
    await settle(() => button('Use configured key').props.onClick());
    await settle(() => view.root.findByProps({ id: 'setup-cloud-model' }).props.onChange({
      target: { value: 'unit/model:free' },
    }));
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

  it('auto-runs the speaker check and STT install when voice is chosen, with no manual buttons', async () => {
    await useConfiguredKey();
    await settle(() => button('Continue').props.onClick());
    await settle(() => view.root.findAllByType('input').find((n) => n.props.type === 'radio' && !n.props.checked)!.props.onChange());
    expect(mock.speak).toHaveBeenCalledTimes(1);
    expect(mock.installAssets).toHaveBeenCalledTimes(1);
    // Neither check is presented as an owner step any more.
    expect(view.root.findAllByType('button').some((b) => labels(b) === 'Test speaker')).toBe(false);
    expect(view.root.findAllByType('button').some((b) => labels(b) === 'Install speech recognition')).toBe(false);
    // The microphone test remains a genuine owner action.
    expect(view.root.findAllByType('button').some((b) => labels(b) === 'Test microphone')).toBe(true);
    await settle(() => button('Continue').props.onClick());
    await settle(() => {});
    expect(complete).toHaveBeenCalledWith(false);
    expect(localStorage.setItem).toHaveBeenCalledWith('sythion.replyMode', 'voice');
  });

  it('stays honest when the automatic speaker check fails, then recovers with text', async () => {
    await useConfiguredKey();
    await settle(() => button('Continue').props.onClick());
    mock.speak.mockResolvedValueOnce({ success: false });
    await settle(() => view.root.findAllByType('input').find((n) => n.props.type === 'radio' && !n.props.checked)!.props.onChange());
    // No readiness is fabricated and the speaker failure is stated plainly.
    expect(labels(view.root)).toContain('Check your audio output');
    expect(complete).not.toHaveBeenCalled();
    expect(mock.installAssets).not.toHaveBeenCalled();
    expect(view.root.findAllByType('button').some((b) => labels(b) === 'Test speaker')).toBe(false);
    // Switching to text still lets the owner finish.
    await settle(() => view.root.findAllByType('input').find((n) => n.props.type === 'radio' && !n.props.checked)!.props.onChange());
    await settle(() => button('Continue').props.onClick());
    await settle(() => {});
    expect(complete).toHaveBeenCalledWith(true);
  });

  it('reports voice input as unavailable when the STT install fails, without faking readiness', async () => {
    mock.installAssets.mockRejectedValueOnce(new Error('download failed'));
    await useConfiguredKey();
    await settle(() => button('Continue').props.onClick());
    await settle(() => view.root.findAllByType('input').find((n) => n.props.type === 'radio' && !n.props.checked)!.props.onChange());
    expect(labels(view.root)).toContain('speech recognition is unavailable');
    expect(labels(view.root)).not.toContain('speech recognition are ready');
    expect(mock.readiness).toHaveBeenCalled();
    expect(complete).not.toHaveBeenCalled();
  });

  it('auto-transitions only after readiness passes and preserves setup on inference loss', async () => {
    await useConfiguredKey();
    await settle(() => button('Continue').props.onClick());
    checks.inference_verified_ok = false;
    await settle(() => button('Continue').props.onClick());
    await settle(() => {});
    expect(complete).not.toHaveBeenCalled();
    expect(view.root.findAllByType('button').some((b) => labels(b) === 'Start conversation')).toBe(false);
    // A transitive readiness failure must not send the owner back a step.
    expect(labels(view.root)).not.toContain('Go back and test your model again');
    expect(labels(view.root)).not.toContain('test your model again');
  });

  it('offers a bounded Try again fallback after the auto-transition fails', async () => {
    await useConfiguredKey();
    await settle(() => button('Continue').props.onClick());
    checks.inference_verified_ok = false;
    vi.useFakeTimers();
    try {
      await settle(() => button('Continue').props.onClick());
      await act(async () => { await vi.advanceTimersByTimeAsync(6_000); });
      for (let i = 0; i < 20; i += 1) {
        await act(async () => { await Promise.resolve(); });
      }
      expect(complete).not.toHaveBeenCalled();
      expect(labels(view.root)).toContain('could not confirm the connection is ready');
      expect(labels(view.root)).toContain('Nothing was started');
      expect(button('Try again')).toBeTruthy();
      // No permanent manual start button on the normal path.
      expect(view.root.findAllByType('button').some((b) => labels(b) === 'Start conversation')).toBe(false);

      checks.inference_verified_ok = true;
      await settle(() => button('Try again').props.onClick());
      await settle(() => {});
      expect(complete).toHaveBeenCalledWith(true);
      expect(view.root.findAllByType('button').some((b) => labels(b) === 'Try again')).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });

  it('auto-advances on a saved configured key without a confirmation click', async () => {
    await useConfiguredKey();
    expect(view.root.findByProps({ id: 'setup-cloud-model' })).toBeTruthy();
    expect(view.root.findAllByType('button').some((b) => labels(b) === 'Use configured key')).toBe(false);
  });

  it('self-heals a transient readiness failure during initial load', async () => {
    act(() => view.unmount());
    mock.readiness.mockClear();
    const defaultReadiness = mock.readiness.getMockImplementation()!;
    mock.readiness.mockRejectedValueOnce(new Error('temporary readiness failure'));
    vi.useFakeTimers();
    try {
      await act(async () => { view = create(<SetupScreen onComplete={complete} />); });
      expect(labels(view.root)).toContain('Checking your setup');
      expect(labels(view.root)).not.toContain('Retry when Sythion has connected');

      await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
      for (let i = 0; i < 10; i += 1) {
        await act(async () => { await Promise.resolve(); });
      }
      expect(mock.readiness).toHaveBeenCalledTimes(2);
      // The wizard renders (a saved key may auto-advance past the provider).
      expect(view.root.findAllByProps({ id: 'setup-provider' }).length +
        view.root.findAllByProps({ id: 'setup-cloud-model' }).length).toBeGreaterThan(0);
      expect(labels(view.root)).not.toContain('Checking your setup');
    } finally {
      mock.readiness.mockImplementation(defaultReadiness);
      vi.useRealTimers();
    }
  });

  it('does not retry applying safe settings and drops the try-again wording', async () => {
    await useConfiguredKey();
    mock.applySafeSettings.mockResolvedValueOnce({ ok: false });
    await settle(() => button('Continue').props.onClick());
    // applySafeSettings mutates consequential state: exactly one attempt.
    expect(mock.applySafeSettings).toHaveBeenCalledTimes(1);
    expect(labels(view.root)).toContain('could not apply the guarded permissions');
    expect(labels(view.root)).not.toContain('Please try again');
    expect(view.root.findAllByProps({ id: 'setup-cloud-model' })).toHaveLength(1);
  });

  it('never offers a manual recheck or a try-again handoff', () => {
    const text = labels(view.root);
    for (const phrase of [
      'Could not check setup',
      'Please try again',
      'Go back and test your model again',
      'Recheck connection',
    ]) {
      expect(text).not.toContain(phrase);
    }
  });
});
