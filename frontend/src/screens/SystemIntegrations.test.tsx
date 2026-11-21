import { act, create, type ReactTestInstance, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SystemScreen } from './SystemIntegrations';

const mock = vi.hoisted(() => ({
  health: {
    status: 'ok',
    version: '1.0.0',
    database_connected: true,
    cloud_configured: true,
    selected_model: 'local-llama',
    active_provider_id: 'openrouter',
    active_model_id: 'cloud-model-x',
  } as Record<string, unknown>,
  settings: { selected_model: 'local-llama' } as Record<string, unknown>,
  models: [] as Array<Record<string, unknown>>,
}));

vi.mock('../hooks/useBackend', () => ({
  useHealth: () => ({ data: mock.health, isLoading: false, isError: false }),
  useVersion: () => ({ data: undefined, isLoading: false }),
  useAgentHealth: () => ({ data: undefined, isLoading: false }),
  useAppSettings: () => ({ data: mock.settings, isLoading: false }),
  useModels: () => ({ data: mock.models, isLoading: false }),
  useSecrets: () => ({ data: { configured: ['OPENROUTER_API_KEY'] }, isLoading: false }),
  useTools: () => ({ data: [], isLoading: false }),
  useSkills: () => ({ data: [], isLoading: false }),
  useCapabilityConsistency: () => ({ data: [], isLoading: false }),
  useCapabilities: () => ({ data: [], isLoading: false }),
  useRetentionReceipts: () => ({ data: [], isLoading: false }),
  useVoiceAssets: () => ({ data: undefined, isLoading: false }),
  useRelayAcks: () => ({ data: undefined, isLoading: false }),
}));

vi.mock('../services/api', () => ({
  CLOUD_PROVIDER_LABELS: {
    'ollama-cloud': 'Ollama Cloud',
    openrouter: 'OpenRouter',
    nvidia: 'NVIDIA NIM',
    groq: 'Groq',
  },
}));

vi.mock('../components/DeviceChannelPanel', () => ({ DeviceChannelPanel: () => null }));
vi.mock('../components/RelayAckPanel', () => ({ RelayAckPanel: () => null }));

function nodeText(node: unknown): string {
  if (node === null || node === undefined || typeof node === 'boolean') return '';
  if (typeof node === 'string' || typeof node === 'number') return String(node);
  if (Array.isArray(node)) return node.map(nodeText).join('');
  if (typeof node === 'object' && 'children' in node) {
    return nodeText((node as { children: unknown }).children);
  }
  return '';
}

function badges(row: ReactTestInstance): string[] {
  return row
    .findAll((n) => typeof n.props.className === 'string' && n.props.className.startsWith('badge '))
    .map((n) => nodeText(n).trim());
}

describe('System & Integrations — Models tab active binding', () => {
  let view: ReactTestRenderer;

  beforeEach(() => {
    mock.health = {
      status: 'ok',
      version: '1.0.0',
      database_connected: true,
      cloud_configured: true,
      // Legacy local selection differs from the authoritative cloud binding.
      selected_model: 'local-llama',
      active_provider_id: 'openrouter',
      active_model_id: 'cloud-model-x',
    };
    mock.settings = { selected_model: 'local-llama' };
    mock.models = [
      { id: 'cloud-model-x', provider: 'openrouter', role: 'reasoning' },
      { id: 'local-llama', provider: 'ollama', role: 'local' },
    ];

    act(() => { view = create(<SystemScreen />); });
    const modelsTab = view.root.findAllByType('button').find((b) => nodeText(b) === 'Models')!;
    act(() => { modelsTab.props.onClick(); });
  });

  afterEach(() => { act(() => view.unmount()); });

  const modelRow = (id: string) => view.root
    .findAllByProps({ className: 'list__row' })
    .find((row) => nodeText(row).includes(id))!;

  it('badges selected only the /health active model and labels the local model', () => {
    expect(badges(modelRow('cloud-model-x'))).toEqual(['selected']);
    expect(badges(modelRow('local-llama'))).toEqual(['local']);

    // The legacy selected_model from /health and settings must not badge a row.
    expect(modelRow('local-llama').findAllByProps({ className: 'badge badge--accent' })).toHaveLength(0);
    expect(modelRow('local-llama').findAllByProps({ className: 'rail rail--accent' })).toHaveLength(0);
    expect(modelRow('cloud-model-x').findAllByProps({ className: 'rail rail--accent' })).toHaveLength(1);
  });

  it('keeps the active provider/model text and the local model text visible', () => {
    const text = nodeText(view.toJSON());
    expect(text).toContain('Active AI provider');
    expect(text).toContain('OpenRouter');
    expect(text).toContain('Active AI model');
    expect(text).toContain('cloud-model-x');
    expect(text).toContain('Local model');
    expect(text).toContain('local-llama');
  });
});
