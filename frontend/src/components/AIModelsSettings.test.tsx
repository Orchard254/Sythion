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
      data-phase={String(props.phase)} />,
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

it('does not claim a successful Ollama Cloud switch if verification changes the active model', async () => {
  await changeProvider('ollama-cloud');
  mock.active.mockResolvedValueOnce(active('openrouter', 'unit/model:free'));
  await settle(() => view.root.findByType(ActiveCloudSetup).props.onVerified('gemma4:31b'));
  expect(mock.restore).not.toHaveBeenCalled();
  expect(text()).toContain('active model changed');
  expect(text()).not.toContain('Active AI model updated');
});

it('does not silently default to Ollama when active selection is invalid', async () => {
  await settle(() => view.unmount());
  mock.active.mockRejectedValueOnce(new Error('damaged active provider binding'));
  await settle(() => { view = create(<AIModelsSettings />); });
  expect(view.root.findByProps({ id: 'settings-ai-provider' }).props.value).toBe('');
  expect(text()).toContain('active model could not be verified');
  expect(mock.restore).not.toHaveBeenCalled();
});
