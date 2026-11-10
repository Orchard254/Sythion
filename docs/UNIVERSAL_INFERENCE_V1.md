# Universal Inference V1: provider transport foundation

Status: IMPLEMENTED AS AN INERT, OFFLINE-TESTED-BY-DESIGN MODULE. NOT ACTIVE.

Three trusted OpenAI-compatible clouds share a single adapter:
- OpenRouter: https://openrouter.ai/api/v1
- NVIDIA NIM: https://integrate.api.nvidia.com/v1
- Groq: https://api.groq.com/openai/v1

Secrets are stored under separate scoped keys openrouter_api_key,
nvidia_api_key and groq_api_key via the existing write-only vault.
No arbitrary URL, environment credential shortcut, automatic retry, fallback
or model selection is introduced in this phase.

The adapter returns original OpenAI chat-completion JSON, already parsed by
ModelDecisionEngine's existing OpenAI-compatible response handling.
Actual function calling is required: the adapter demands a small named
tool-call probe and exact arguments, rather than trusting model metadata.
That probe is per model adapter instance, with a bounded negative cache.
Failed provider requests never authorize task execution.

Current use is in isolated deterministic integration tests only:
python -m pytest -q backend/tests/test_openai_compatible_provider.py

Before enabling it for Sythion: integrate scoped model/provider selection,
explicit and persisted provider identity, validated inference readiness,
onboarding, all chat/decision/background paths, and a commit-bound live
capability/evidence test against a real account. Do not wire task execution
to a provider until these conditions are satisfied.

Provider-specific official references:
https://openrouter.ai/docs/api/api-reference/chat/send-chat-completion-request
https://docs.api.nvidia.com/nim/reference/llm-apis
https://console.groq.com/docs/api-reference

### Owner-invoked compatibility probe (does not enable the provider)

From repo root, list published free OpenRouter model IDs without storing a key:

```powershell
python -m scripts.probe_cloud_provider --provider openrouter --list --free-only --allow-network
```

Then try a specific model ID from the catalog:

```powershell
python -m scripts.probe_cloud_provider --provider openrouter --model AUTHOR/MODEL:free --prove-tools --allow-network
```

The command asks for a hidden API key (never passes it as a CLI argument).
It sends two harmless model-only requests for a named function call and a
synthetic completion decision. **No real Sythion tool or task executes.**
Unverified or incompatible models fail closed. Cloud requests consume actual
provider quota. To use an already-provisioned build-scoped vault key, add
`--use-vault` instead; the vault belongs to this process/build identity.
Never add provider keys to `.env` or git. Model admission to live task
execution still requires a separate, end-to-end verified integration.

### Distinguishing Windows hidden input from model incompatibility

getpass never echoes pasted or typed characters. The probe now prints
'Nonempty API key input received' only after it has read a nonempty value.
OpenRouter publishes an authenticated GET /api/v1/key. Verify the supplied
credential separately **without model inference**:

```powershell
python -m scripts.probe_cloud_provider --provider openrouter --verify-key --allow-network
```

AUTH PASS means the key is accepted by OpenRouter. It does NOT mean a free
model has available quota, supports tool forcing, or meets our task-control
contract. HTTP 401 means the key is rejected; HTTP 429 indicates a rate limit.
Now --prove-tools reports sanitized categories such as provider HTTP 400,
provider HTTP 429, missing tool calls, or incorrect arguments instead of a
single opaque error. HTTP 400 can have several causes (including unsupported
tool_choice); raw provider details and keys are deliberately not printed.
Never retry a model request automatically or skip capability gates.

### Owner evidence and 429 handling

Owner Windows logs on commit 7dcb709: `--verify-key` returned AUTH PASS,
and 30/30 targeted adapter + probe CLI tests passed. The next Gemma
4 26B A4B free model capability probe returned provider HTTP 429. This
**does not** prove a tool-call incompatibility or a bad API key.
OpenRouter states free model variants are subject to 20 requests/minute
and 50 requests/day for unfunded accounts and that failed requests count.
Upstream endpoint capacity may separately cause 429. See:
https://openrouter.ai/collections/free-models
https://openrouter.ai/pricing
Review usage in https://openrouter.ai/activity; do not loop retries.

### Explicit cloud ModelClient, still not active

`CompatibleCloudModelClient` now implements the existing reasoning
`ModelClient` interface and delegates to the fixed-endpoint compatible
adapter. It must be injected explicitly; Sythion's default factory still
constructs Ollama Cloud. The reasoning engine no longer constructs an
unrelated Ollama adapter merely to populate a compatibility field when a
different client is supplied. No provider preference is persisted or
selected from frontend yet. Before enabling in tasks, onboarding must
bind provider ID, model ID, credentials, verified inference and tool
capabilities to the same profile and reject stale evidence. D-002 still
blocked on a genuine working cloud inference provider.

### Provider staging (inactive, offline-only)

The authenticated backend now exposes inert preview routes:

- `GET /api/v1/inference-preview/providers`: fixed provider list with
  build-scoped credential-presence booleans, current active provider
  reported as Ollama Cloud, and staged candidate metadata.
- `POST /api/v1/inference-preview/stage`: exactly one `provider_id` from
  openrouter, nvidia, groq and exact model ID; rejects unknown fields,
  unsafe IDs, and unknown providers. Writes `staged_cloud_selection` to
  the existing build-isolated profile only. Never stores API credentials.

A staged model is explicitly **NOT active or verified**. Both endpoint
responses fail closed with `ready_for_execution=false`; no provider
network requests are made by these preview routes. The active Ollama
selected model, inference marker, onboarding, and task/client execution
remain untouched. Staging deliberately does not imply model-tool capability.

Windows regression gate:

`python -m pytest -q backend/tests/test_inference_provider_preview.py backend/tests/test_config_selected_model_isolation.py backend/tests/test_setup_readiness.py backend/tests/test_cloud_model_client.py --disable-warnings`

The next phase should bind provider + model + credential + successful
real capability probe to an execution-ready evidence marker scoped to
the current build/profile. Until all chat, task and memory calls can
follow the selected provider safely, keep task admission on Ollama only.

### Explicit one-shot staged capability probe (not active inference)

`POST /api/v1/inference-preview/probe` accepts the exact staged provider_id
and model_id with `confirm_network_request: true`. The provider key must
already be configured in this build's write-only secret store (use
`PUT /api/v1/secrets/{provider}_api_key` through the authenticated API).
The probe sends one harmless synthetic function-call request. Every call
consumes provider quota and is made only on explicit request.

Success returns `tool_probe_verified: true` for that request only,
plus `active: false`, `ready_for_execution: false` and
`evidence_persisted: false`. No task, system tool, or model decision
is executed; no readiness flag, credential or active provider changes.
403/401, 429, network-unknown, and malformed tool responses are mapped
to fixed categories without reflecting provider text. The staged identity
is checked before and after the call to reject an in-flight change.

To unblock task execution, subsequent work must create a durable
provider-scoped verified state (binding credential version + model +
capability evidence) and wire all inference entry points to that
active provider only after passing real model capability checks.

Offline Windows gate:
`python -m pytest -q backend/tests/test_inference_provider_preview.py backend/tests/test_openai_compatible_provider.py backend/tests/test_setup_readiness.py --disable-warnings`

### Frontend first-run cloud preview

From the first setup screen the owner may expand 'Explore alternative cloud
providers (preview)' to select OpenRouter, NVIDIA NIM or Groq. Credential
writes use the existing `PUT /api/v1/secrets/{provider}_api_key` route and
build-scoped write-only secret store (Windows Credential Manager for production,
non-persistent memory in isolated testing); the password input is cleared after
a successful write. Model IDs can be staged explicitly with the
`/inference-preview/stage` route. A checkbox requires fresh, per-action
consent before one real synthetic function-call probe.

The preview's success text means only one tool-call probe passed; it never
claims that any provider is active, ready for task execution, or suitable
for all task types. It does not change the existing setup/readiness gate.
HTTP status messages are bounded and never reflect upstream bodies or
typed key values. Changing provider or model clears the consent and
prior local probe result. Initial disclosure only fetches credential
presence and staged nonsecret metadata, not model inference.

Frontend targeted validation:
`npm run test --prefix frontend -- src/components/CloudProviderPreview.test.tsx src/screens/Setup.test.tsx src/services/api.test.ts`
`npm run build --prefix frontend`

### Provider-scoped tool evidence v1 (not active; owner verification pending)

Staging now generates an opaque 128-bit revision on every stage operation,
including restaging an identical model. The private backend helper
`backend/core/cloud_probe_evidence.py` can persist a verified synthetic
function-call observation in the existing local settings table, bound
to the revision, exact provider and model ID, current build-scoped key
(via HMAC-SHA256) and a 1-hour expiry. Key changes, restaging, profile
tampering, clock rollback, database outages and expired evidence all
fail closed. Neither a key nor a model response is persisted.

This evidence is *not* proof of chat support, context quality, health,
model availability, normal task execution, or permissions; it MUST NOT
activate a provider or unlock setup on its own. Route integration will
be a separate gate after these offline tests pass.

`python -m pytest -q backend/tests/test_cloud_probe_evidence.py backend/tests/test_inference_provider_preview.py backend/tests/test_profile_atomic_durable_save.py --disable-warnings`
