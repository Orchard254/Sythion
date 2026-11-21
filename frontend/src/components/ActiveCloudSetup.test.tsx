import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ActiveCloudSetup } from './ActiveCloudSetup';

const MODEL_ID = 'unit/model:free';
const PROVIDER = 'openrouter';
const PENDING_MESSAGE =
  'Verified model selected; it will activate automatically when current work finishes.';

const mock = vi.hoisted(() => ({
  status: vi.fn(),
  modelCatalog: vi.fn(),
  saveCredential: vi.fn(),
  stage: vi.fn(),
  chat: vi.fn(),
  probe: vi.fn(),
  assistantContract: vi.fn(),
  activate: vi.fn(),
}));
vi.mock('../services/api', async (importOriginal) => ({
  ...await importOriginal<typeof import('../services/api')>(),
  cloudPreviewService: {
    status: mock.status,
    modelCatalog: mock.modelCatalog,
    saveCredential: mock.saveCredential,
    stage: mock.stage,
    chat: mock.chat,
    probe: mock.probe,
    assistantContract: mock.assistantContract,
  },
  activeInferenceService: {
    activate: mock.activate,
    status: vi.fn(),
    restoreOllama: vi.fn(),
    activateOllama: vi.fn(),
  },
}));

let view: ReactTestRenderer;
let onVerified: ReturnType<typeof vi.fn>;
const settle = async (fn: () => void) => {
  await act(async () => { fn(); for (let i = 0; i < 25; i++) await Promise.resolve(); });
};
const text = () => JSON.stringify(view.toJSON());
const chooseModel = () => settle(() => view.root.findByProps({ id: 'setup-cloud-model' })
  .props.onChange({ target: { value: MODEL_ID } }));

beforeEach(async () => {
  vi.clearAllMocks();
  // Mount reads credentials + catalog; status already reports a verified proof
  // so the verification path goes straight to assistantContract -> activate.
  mock.status.mockResolvedValue({
    providers: [{ provider_id: PROVIDER, credential_configured: true }],
    staged: { provider_id: PROVIDER, model_id: MODEL_ID },
    text_probe_verified: true,
    tool_probe_verified: true,
    ready_for_execution: false,
  });
  mock.modelCatalog.mockImplementation(async (provider: string) => ({
    provider_id: provider, ready_for_execution: false, truncated: false,
    models: [{ id: MODEL_ID, free_variant: true, tools_listed: false }],
  }));
  mock.assistantContract.mockImplementation(
    async (selection: { provider_id: string; model_id: string }) => ({
      ...selection, assistant_contract_verified: true, active: false,
      task_executed: false, evidence_persisted: true,
    }));
  onVerified = vi.fn();
  await settle(() => {
    view = create(<ActiveCloudSetup provider={PROVIDER} phase="model" onVerified={onVerified} />);
  });
});
afterEach(() => { act(() => view.unmount()); });

it('passes pending=true to onVerified, shows the deferred message and no error', async () => {
  mock.activate.mockResolvedValue({
    provider_id: PROVIDER, model_id: MODEL_ID,
    permissions_unchanged: true, pending: true, message: PENDING_MESSAGE,
  });

  await chooseModel();

  expect(mock.activate).toHaveBeenCalledWith({ provider_id: PROVIDER, model_id: MODEL_ID });
  expect(onVerified).toHaveBeenCalledWith(MODEL_ID, true);
  expect(text()).toContain('will activate automatically when current work finishes');
  expect(text()).toContain(PENDING_MESSAGE);
  expect(view.root.findAllByProps({ tone: 'danger' })).toHaveLength(0);
  expect(text()).not.toContain('could not verify');
});

it('keeps the immediate path: onVerified gets pending=false without the deferred message', async () => {
  mock.activate.mockResolvedValue({
    provider_id: PROVIDER, model_id: MODEL_ID, permissions_unchanged: true,
  });

  await chooseModel();

  expect(onVerified).toHaveBeenCalledWith(MODEL_ID, false);
  expect(text()).toContain('Model selected for Sythion');
  expect(text()).not.toContain('will activate automatically');
});

it('does not claim success when the activated identity does not match the selection', async () => {
  // Guard now lives here (ActiveCloudSetup validates activate() before firing
  // onVerified), replacing the redundant AIModelsSettings status re-check.
  mock.activate.mockResolvedValue({
    provider_id: PROVIDER, model_id: 'unit/model:different',
    permissions_unchanged: true,
  });

  await chooseModel();

  expect(onVerified).not.toHaveBeenCalled();
  expect(text()).toContain('could not verify');
});

it('self-retries a transient key save and does not hand work back to the owner', async () => {
  await settle(() => view.unmount());
  // No saved key on arrival, so there is nothing to auto-advance on. Only the
  // post-save status check confirms the credential.
  mock.status
    .mockResolvedValueOnce({
      providers: [{ provider_id: PROVIDER, credential_configured: false }],
      staged: {}, text_probe_verified: false, tool_probe_verified: false,
      ready_for_execution: false,
    })
    .mockResolvedValue({
      providers: [{ provider_id: PROVIDER, credential_configured: true }],
      staged: {}, text_probe_verified: false, tool_probe_verified: false,
      ready_for_execution: false,
    });
  mock.saveCredential
    .mockRejectedValueOnce({ response: { status: 503 } })
    .mockRejectedValueOnce({ response: { status: 503 } })
    .mockResolvedValue(undefined);
  const connected = vi.fn();
  await settle(() => {
    view = create(<ActiveCloudSetup provider={PROVIDER} phase="connect" onConnected={connected} />);
  });
  await settle(() => view.root.findByProps({ id: 'setup-cloud-key' })
    .props.onChange({ target: { value: 'inert-test-key' } }));
  await settle(() => view.root.findAllByType('button')
    .find((n) => JSON.stringify(n.children).includes('Connect and save key'))!.props.onClick());
  expect(mock.saveCredential).toHaveBeenCalledTimes(3);
  expect(connected).toHaveBeenCalledTimes(1);
  expect(text()).not.toContain('Check your account and retry');
  expect(text()).not.toContain('inert-test-key');
});

it('reports a genuine key rejection without retrying or giving a vague retry instruction', async () => {
  await settle(() => view.unmount());
  mock.saveCredential.mockRejectedValueOnce({
    response: { status: 422, data: { detail: 'invalid key' } },
  });
  await settle(() => {
    view = create(<ActiveCloudSetup provider={PROVIDER} phase="connect" />);
  });
  await settle(() => view.root.findByProps({ id: 'setup-cloud-key' })
    .props.onChange({ target: { value: 'inert-test-key' } }));
  await settle(() => view.root.findAllByType('button')
    .find((n) => JSON.stringify(n.children).includes('Connect and save key'))!.props.onClick());
  expect(mock.saveCredential).toHaveBeenCalledTimes(1);
  expect(text()).toContain('Sythion rejected the key');
  expect(text()).not.toContain('Check your account and retry');
});

it('keeps a manual Check again for a quota outcome instead of auto-retrying it', async () => {
  mock.activate.mockRejectedValueOnce({ response: { status: 429 } });

  await chooseModel();

  // Quota is a genuine decision: never auto-retry (could bill quota), but keep
  // the explicit owner action.
  expect(mock.activate).toHaveBeenCalledTimes(1);
  expect(text()).toContain('quota or capacity');
  expect(text()).toContain('Check again');
});

it('keeps a manual Check again for a credential failure (401)', async () => {
  mock.activate.mockRejectedValueOnce({ response: { status: 401 } });

  await chooseModel();

  expect(mock.activate).toHaveBeenCalledTimes(1);
  expect(text()).toContain('Check again');
});

it('auto-retries a transient 502 with no manual Check again button', async () => {
  mock.activate
    .mockRejectedValueOnce({ response: { status: 502, data: { detail: 'bad gateway' } } })
    .mockResolvedValueOnce({
      provider_id: PROVIDER, model_id: MODEL_ID, permissions_unchanged: true,
    });

  await chooseModel();
  await settle(() => {});

  expect(mock.activate).toHaveBeenCalledTimes(2);
  expect(onVerified).toHaveBeenCalledWith(MODEL_ID, false);
  expect(text()).not.toContain('Check again');
  expect(text()).toContain('Model selected for Sythion');
});

it('auto-retries a transient 503 and stops after the bounded number of attempts', async () => {
  mock.activate
    .mockRejectedValueOnce({ response: { status: 503 } })
    .mockRejectedValueOnce({ response: { status: 503 } })
    .mockRejectedValueOnce({ response: { status: 503 } });

  await chooseModel();
  await settle(() => {});

  // 3 total attempts (initial + 2 bounded rechecks), then an honest error —
  // no manual button is required for a transient non-billable failure.
  expect(mock.activate).toHaveBeenCalledTimes(3);
  expect(onVerified).not.toHaveBeenCalled();
  expect(text()).not.toContain('Check again');
  expect(view.root.findAllByProps({ tone: 'danger' })).toHaveLength(1);
});

it('offers a self-healing Check again when a real model decision is required', async () => {
  mock.activate.mockRejectedValueOnce({ response: { status: 422 } });

  await chooseModel();

  expect(text()).toContain('Check again');
  expect(text()).not.toContain('Retry selected model');
});

it('shows free models first by default with no filter control to click', async () => {
  await settle(() => view.unmount());
  mock.modelCatalog.mockImplementation(async (provider: string) => ({
    provider_id: provider, ready_for_execution: false, truncated: false,
    models: [
      { id: 'paid/model', free_variant: false, tools_listed: true },
      { id: MODEL_ID, free_variant: true, tools_listed: false },
    ],
  }));
  await settle(() => {
    view = create(<ActiveCloudSetup provider={PROVIDER} phase="model" onVerified={onVerified} />);
  });

  expect(view.root.findAllByProps({ id: 'setup-cloud-filter' })).toHaveLength(0);
  const options = view.root.findByProps({ id: 'setup-cloud-model' })
    .findAllByType('option').map((o) => o.props.value);
  expect(options).toEqual(['', MODEL_ID, 'paid/model']);
});

it('auto-advances on a backend-confirmed saved key without a confirmation click', async () => {
  await settle(() => view.unmount());
  const connected = vi.fn();
  await settle(() => {
    view = create(<ActiveCloudSetup provider={PROVIDER} phase="connect" onConnected={connected} />);
  });

  expect(connected).toHaveBeenCalledTimes(1);
  expect(view.root.findAllByType('button')
    .some((b) => JSON.stringify(b.children).includes('Use configured key'))).toBe(false);
});

// --- Recommended one-click path (reuses verifyModel's exact proof sequence) --

const RECOMMENDED = 'nvidia/nemotron-3.5-bolt-e4b';
const NVIDIA_PROVIDER = 'nvidia';

it('recommended path: one click runs the full stage/text/tool/contract/activate sequence', async () => {
  await settle(() => view.unmount());
  // No proofs are pre-confirmed, so every step of the existing flow must run.
  mock.status.mockResolvedValue({
    providers: [{ provider_id: NVIDIA_PROVIDER, credential_configured: true }],
    staged: { provider_id: NVIDIA_PROVIDER, model_id: RECOMMENDED },
    text_probe_verified: false, tool_probe_verified: false,
    ready_for_execution: false,
  });
  mock.chat.mockImplementation(async (selection: { provider_id: string; model_id: string }) => ({
    ...selection, assistant_text: 'Hello.', active: false,
    ready_for_execution: false, persisted: false, task_executed: false,
  }));
  mock.probe.mockImplementation(async (selection: { provider_id: string; model_id: string }) => ({
    ...selection, tool_probe_verified: true, active: false,
    ready_for_execution: false, evidence_persisted: true,
  }));
  mock.activate.mockResolvedValue({
    provider_id: NVIDIA_PROVIDER, model_id: RECOMMENDED, permissions_unchanged: true,
  });

  // Mounting the recommended phase is the parent's single click: no second
  // selection or button press is required.
  await settle(() => {
    view = create(<ActiveCloudSetup provider={NVIDIA_PROVIDER} phase="recommended"
      quickModelId={RECOMMENDED} onVerified={onVerified} />);
  });
  await settle(() => {});

  const selection = { provider_id: NVIDIA_PROVIDER, model_id: RECOMMENDED };
  expect(mock.stage).toHaveBeenCalledWith(selection);
  expect(mock.chat).toHaveBeenCalledWith(selection, 'Say hello in one short plain-text sentence.');
  expect(mock.probe).toHaveBeenCalledWith(selection);
  expect(mock.assistantContract).toHaveBeenCalledWith(selection);
  expect(mock.activate).toHaveBeenCalledWith(selection);
  expect(onVerified).toHaveBeenCalledWith(RECOMMENDED, false);
  expect(text()).toContain('Model selected for Sythion');
});

it('recommended path: preserves the deferred activation notice and pending flag', async () => {
  await settle(() => view.unmount());
  mock.status.mockResolvedValue({
    providers: [{ provider_id: NVIDIA_PROVIDER, credential_configured: true }],
    staged: { provider_id: NVIDIA_PROVIDER, model_id: RECOMMENDED },
    text_probe_verified: true, tool_probe_verified: true,
    ready_for_execution: false,
  });
  mock.activate.mockResolvedValue({
    provider_id: NVIDIA_PROVIDER, model_id: RECOMMENDED,
    permissions_unchanged: true, pending: true, message: PENDING_MESSAGE,
  });

  await settle(() => {
    view = create(<ActiveCloudSetup provider={NVIDIA_PROVIDER} phase="recommended"
      quickModelId={RECOMMENDED} onVerified={onVerified} />);
  });
  await settle(() => {});

  expect(mock.activate).toHaveBeenCalledWith({ provider_id: NVIDIA_PROVIDER, model_id: RECOMMENDED });
  expect(onVerified).toHaveBeenCalledWith(RECOMMENDED, true);
  expect(text()).toContain('will activate automatically when current work finishes');
  expect(text()).toContain(PENDING_MESSAGE);
  expect(view.root.findAllByProps({ tone: 'danger' })).toHaveLength(0);
  expect(text()).not.toContain('could not verify');
});

it('recommended path: missing credential prompts for the key once, then continues automatically', async () => {
  await settle(() => view.unmount());
  mock.status
    .mockResolvedValueOnce({
      providers: [{ provider_id: NVIDIA_PROVIDER, credential_configured: false }],
      staged: {}, text_probe_verified: false, tool_probe_verified: false,
      ready_for_execution: false,
    })
    .mockResolvedValue({
      providers: [{ provider_id: NVIDIA_PROVIDER, credential_configured: true }],
      staged: { provider_id: NVIDIA_PROVIDER, model_id: RECOMMENDED },
      text_probe_verified: true, tool_probe_verified: true,
      ready_for_execution: false,
    });
  mock.saveCredential.mockResolvedValue(undefined);
  mock.activate.mockResolvedValue({
    provider_id: NVIDIA_PROVIDER, model_id: RECOMMENDED, permissions_unchanged: true,
  });
  const connected = vi.fn();

  await settle(() => {
    view = create(<ActiveCloudSetup provider={NVIDIA_PROVIDER} phase="recommended"
      quickModelId={RECOMMENDED} onConnected={connected} onVerified={onVerified} />);
  });

  // Honest prompt, not a fabricated success.
  expect(text()).toContain('Save it below and verification continues automatically');
  expect(text()).toContain('NVIDIA NIM');
  expect(mock.activate).not.toHaveBeenCalled();
  expect(mock.stage).toHaveBeenCalledTimes(0);
  expect(onVerified).not.toHaveBeenCalled();
  expect(text()).not.toContain('Model selected for Sythion');

  // Owner supplies the key; saving it triggers verification with no extra click.
  await settle(() => view.root.findByProps({ id: 'setup-cloud-key' })
    .props.onChange({ target: { value: 'inert-test-key' } }));
  await settle(() => view.root.findAllByType('button')
    .find((n) => JSON.stringify(n.children).includes('Connect and continue'))!.props.onClick());
  await settle(() => {});

  expect(mock.saveCredential).toHaveBeenCalledWith(NVIDIA_PROVIDER, 'inert-test-key');
  expect(connected).toHaveBeenCalledTimes(1);
  expect(mock.activate).toHaveBeenCalledWith({ provider_id: NVIDIA_PROVIDER, model_id: RECOMMENDED });
  expect(onVerified).toHaveBeenCalledWith(RECOMMENDED, false);
  expect(text()).not.toContain('inert-test-key');
});
