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
