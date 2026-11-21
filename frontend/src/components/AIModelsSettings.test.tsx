import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { AIModelsSettings } from './AIModelsSettings';
import { ActiveCloudSetup } from './ActiveCloudSetup';

const mock = vi.hoisted(() => ({
  active: vi.fn(), restore: vi.fn(), activateOllama: vi.fn(),
  cloud: vi.fn(), readiness: vi.fn(),
  models: vi.fn(), credentials: vi.fn(), select: vi.fn(), inference: vi.fn(),
}));
vi.mock('../services/api', () => ({
  activeInferenceService: {
    status: mock.active, restoreOllama: mock.restore, activateOllama: mock.activateOllama,
  },
  cloudPreviewService: { status: mock.cloud },
  setupService: {
    readiness: mock.readiness, testCredentials: mock.credentials,
    selectModel: mock.select, inference: mock.inference,
  },
  modelService: { available: mock.models },
}));
vi.mock('./ActiveCloudSetup', () => ({
  ActiveCloudSetup: (props: Record<string, unknown>) =>
    <div id="mock-cloud-picker" data-provider={String(props.provider)}
      data-phase={String(props.phase)} data-quick={String(props.quickModelId ?? '')} />,
}));

let view: ReactTestRenderer;
const settle = async (fn: () => void) => {
  await act(async () => { fn(); for (let i = 0; i < 25; i++) await Promise.resolve(); });
};
const text = () => JSON.stringify(view.toJSON());
const active = (provider_id = 'openrouter', model_id = 'unit/model:free') => ({
  provider_id, model_id, permissions_unchanged: true,
});
const changeProvider = async (value: string) => {
  await settle(() => view.root.findByProps({ id: 'settings-ai-provider' }).props.onChange({
    target: { value },
  }));
};
/** Run timer-driven poll ticks inside act(), flushing the async status() reads. */
const advancePolls = async (ms: number) => {
  await act(async () => {
    vi.advanceTimersByTime(ms);
    for (let i = 0; i < 25; i++) await Promise.resolve();
  });
};
/** The poll reads status every 2500ms (see AIModelsSettings constants). */
const POLL_MS = 2500;

beforeEach(async () => {
  vi.clearAllMocks();
  mock.active.mockResolvedValue(active());
  mock.cloud.mockResolvedValue({
    providers: [
      { provider_id: 'ollama-cloud', credential_configured: true },
      { provider_id: 'openrouter', credential_configured: true },
      { provider_id: 'nvidia', credential_configured: false },
      { provider_id: 'groq', credential_configured: true },
    ],
  });
  mock.readiness.mockResolvedValue({ checks: { ollama_api_key_ok: true } });
  mock.models.mockResolvedValue(['ollama/test', 'ollama/second']);
  mock.select.mockResolvedValue({ persisted: true });
  mock.inference.mockImplementation(async (model: string) => ({
    success: true, model, response_preview: 'OK',
  }));
  mock.restore.mockResolvedValue(active('ollama-cloud', 'ollama/test'));
  mock.activateOllama.mockImplementation(async (modelId: string) => ({
    provider_id: 'ollama-cloud', model_id: modelId,
    verified: true, permissions_unchanged: true,
  }));
  mock.credentials.mockResolvedValue({ valid: true, models: ['ollama/test'] });
  await settle(() => { view = create(<AIModelsSettings />); });
});
afterEach(() => { act(() => view.unmount()); });

it('shows the actual active cloud model in Settings, not preview state', () => {
  expect(text()).toContain('AI Models');
  expect(text()).toContain('OpenRouter');
  expect(text()).toContain('unit/model:free');
  expect(view.root.findByProps({ id: 'settings-ai-provider' }).props.value).toBe('openrouter');
  expect(view.root.findByProps({ id: 'mock-cloud-picker' }).props['data-phase']).toBe('model');
  expect(mock.select).not.toHaveBeenCalled();
  expect(mock.inference).not.toHaveBeenCalled();
  expect(mock.restore).not.toHaveBeenCalled();
});

it('changes provider within Settings without inference, and uses existing key', async () => {
  await changeProvider('groq');
  const picker = view.root.findByProps({ id: 'mock-cloud-picker' });
  expect(picker.props['data-provider']).toBe('groq');
  expect(picker.props['data-phase']).toBe('model');
  expect(mock.select).not.toHaveBeenCalled();
  expect(mock.inference).not.toHaveBeenCalled();
});

it('requires the credentials flow when the provider has no saved key', async () => {
  await changeProvider('nvidia');
  let picker = view.root.findByProps({ id: 'mock-cloud-picker' });
  expect(picker.props['data-phase']).toBe('connect');
  await settle(() => view.root.findByType(ActiveCloudSetup).props.onConnected());
  picker = view.root.findByProps({ id: 'mock-cloud-picker' });
  expect(picker.props['data-phase']).toBe('model');
});

it('confirms a changed cloud model from the actual active backend status', async () => {
  await changeProvider('groq');
  mock.active.mockResolvedValueOnce(active('groq', 'groq/new'));
  await settle(() => view.root.findByType(ActiveCloudSetup).props.onVerified('groq/new'));
  expect(text()).toContain('groq/new');
  expect(text()).toContain('Active AI model updated');
  expect(mock.restore).not.toHaveBeenCalled();
});

it('uses the same guarded provider picker for Ollama Cloud as for other clouds', async () => {
  await changeProvider('ollama-cloud');
  const picker = view.root.findByProps({ id: 'mock-cloud-picker' });
  expect(picker.props['data-provider']).toBe('ollama-cloud');
  expect(picker.props['data-phase']).toBe('model');
  expect(view.root.findAllByProps({ id: 'settings-ollama-model' })).toHaveLength(0);
  mock.active.mockResolvedValueOnce(active('ollama-cloud', 'gemma4:31b'));
  await settle(() => view.root.findByType(ActiveCloudSetup).props.onVerified('gemma4:31b'));
  expect(text()).toContain('gemma4:31b');
  expect(text()).toContain('Active AI model updated');
  expect(mock.restore).not.toHaveBeenCalled();
});

it('trusts the validated activation result without a redundant status re-check', async () => {
  // The mismatch guard now lives in ActiveCloudSetup (it validates the
  // activate() response before firing onVerified). AIModelsSettings therefore
  // must not re-check status or hand work back to the owner.
  await changeProvider('ollama-cloud');
  mock.active.mockClear();
  await settle(() => view.root.findByType(ActiveCloudSetup).props.onVerified('gemma4:31b'));
  expect(mock.restore).not.toHaveBeenCalled();
  expect(mock.active).not.toHaveBeenCalled();
  expect(text()).toContain('gemma4:31b');
  expect(text()).toContain('Active AI model updated');
});

it('defers a pending cloud model without re-checking status or handing work back', async () => {
  await changeProvider('groq');
  // The initial mount already read status once; clear so the assertion below
  // isolates the pending callback's behaviour.
  mock.active.mockClear();
  await settle(() => view.root.findByType(ActiveCloudSetup)
    .props.onVerified('groq/new', true));
  expect(text()).toContain('will activate automatically when current work finishes');
  expect(text()).not.toContain('Refresh');
  expect(text()).not.toContain('Reopen');
  expect(mock.active).not.toHaveBeenCalled();
});

it('confirms a deferred choice once status reports the newly active model', async () => {
  vi.useFakeTimers();
  try {
    await changeProvider('groq');
    mock.active.mockClear();
    // First poll still sees the OLD binding; the second sees the promoted one.
    mock.active
      .mockResolvedValueOnce(active())
      .mockResolvedValueOnce(active('groq', 'groq/new'));
    await settle(() => view.root.findByType(ActiveCloudSetup)
      .props.onVerified('groq/new', true));
    // The immediate pending path must not call status or hand work back.
    expect(mock.active).not.toHaveBeenCalled();
    expect(text()).toContain('will activate automatically when current work finishes');

    await advancePolls(POLL_MS); // old model -> keep waiting
    expect(mock.active).toHaveBeenCalledTimes(1);
    expect(text()).not.toContain('Active AI model updated');

    await advancePolls(POLL_MS); // new model -> converge
    expect(mock.active).toHaveBeenCalledTimes(2);
    expect(text()).toContain('Groq · groq/new');
    expect(text()).toContain('Active AI model updated');
    expect(text()).not.toContain('Refresh');
    expect(text()).not.toContain('Reopen');
    expect(mock.restore).not.toHaveBeenCalled();
  } finally {
    vi.useRealTimers();
  }
});

it('leaves an honest, non-hand-back status when a deferred choice never settles', async () => {
  vi.useFakeTimers();
  try {
    await changeProvider('groq');
    mock.active.mockClear();
    // Always report the old binding: the backend never promotes the choice.
    mock.active.mockResolvedValue(active());
    await settle(() => view.root.findByType(ActiveCloudSetup)
      .props.onVerified('groq/new', true));
    expect(mock.active).not.toHaveBeenCalled();

    for (let i = 0; i < 6; i++) await advancePolls(POLL_MS); // each tick schedules the next after its async read
    expect(mock.active).toHaveBeenCalledTimes(6);
    expect(text()).toContain('the active model line will update when it settles');
    expect(text()).toContain('OpenRouter · unit/model:free'); // old model still shown
    expect(text()).not.toContain('Active AI model updated');
    expect(text()).not.toContain('Refresh');
    expect(text()).not.toContain('Reopen');
  } finally {
    vi.useRealTimers();
  }
});

it('explains a credential-blocked deferred choice instead of repeating the promise', async () => {
  vi.useFakeTimers();
  try {
    await changeProvider('groq');
    mock.active.mockClear();
    // Backend reports a structurally valid but no-longer-current binding.
    mock.active.mockResolvedValue({
      ...active(), pending_activation: 'blocked_credential',
    });
    await settle(() => view.root.findByType(ActiveCloudSetup)
      .props.onVerified('groq/new', true));
    expect(mock.active).not.toHaveBeenCalled();

    for (let i = 0; i < 6; i++) await advancePolls(POLL_MS);
    expect(text()).toContain('provider key changed');
    expect(text()).toContain('previous model active');
    expect(text()).not.toContain('will activate automatically');
    expect(text()).not.toContain('Refresh');
    expect(text()).not.toContain('Reopen');
  } finally {
    vi.useRealTimers();
  }
});

it('does not silently default to Ollama when active selection is invalid', async () => {
  await settle(() => view.unmount());
  mock.active.mockRejectedValueOnce(new Error('damaged active provider binding'));
  await settle(() => { view = create(<AIModelsSettings />); });
  expect(view.root.findByProps({ id: 'settings-ai-provider' }).props.value).toBe('');
  expect(text()).toContain('active model could not be verified');
  expect(mock.restore).not.toHaveBeenCalled();
});

it('Recommended for Sythion: one click mounts the recommended verification for nvidia', async () => {
  await settle(() => view.root.findByProps({ id: 'settings-recommended' }).props.onClick());
  const picker = view.root.findByProps({ id: 'mock-cloud-picker' });
  expect(picker.props['data-provider']).toBe('nvidia');
  expect(picker.props['data-phase']).toBe('recommended');
  expect(picker.props['data-quick']).toBe('nvidia/nemotron-3.5-bolt-e4b');
  expect(view.root.findByProps({ id: 'settings-ai-provider' }).props.value).toBe('nvidia');
  expect(text()).toContain('Recommended for Sythion');
  // No setup/onboarding inference calls are made to stage the quick path.
  expect(mock.select).not.toHaveBeenCalled();
  expect(mock.inference).not.toHaveBeenCalled();
  expect(mock.restore).not.toHaveBeenCalled();
});

it('Recommended for Sythion: preserves the deferred activation notice without a status re-check', async () => {
  await settle(() => view.root.findByProps({ id: 'settings-recommended' }).props.onClick());
  mock.active.mockClear();
  await settle(() => view.root.findByType(ActiveCloudSetup)
    .props.onVerified('nvidia/nemotron-3.5-bolt-e4b', true));
  expect(text()).toContain('will activate automatically when current work finishes');
  expect(text()).toContain('Recommended model selected');
  expect(text()).not.toContain('Refresh');
  expect(text()).not.toContain('Reopen');
  expect(mock.active).not.toHaveBeenCalled();
});

it('Recommended for Sythion: does not claim success when the nvidia key is missing', async () => {
  // beforeEach reports nvidia credential_configured: false. AIModelsSettings
  // must hand that case to the recommended phase (which prompts for the key)
  // and must never fabricate an activated model locally.
  await settle(() => view.root.findByProps({ id: 'settings-recommended' }).props.onClick());
  const picker = view.root.findByProps({ id: 'mock-cloud-picker' });
  expect(picker.props['data-provider']).toBe('nvidia');
  expect(picker.props['data-phase']).toBe('recommended');
  expect(text()).not.toContain('Active AI model updated');
  expect(text()).toContain('OpenRouter · unit/model:free');
});
