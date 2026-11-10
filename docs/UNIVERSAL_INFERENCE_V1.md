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
