"""Opt-in live reasoning E2E (NVIDIA only, Nemotron preferred).

Default: SKIPPED. Run with SYTHION_RUN_REASONING_E2E=1 plus a real scoped
NVIDIA credential. Ollama Cloud and Groq are intentionally out of scope for
live testing by owner direction; the registry still contains them for offline
conformance. A model may be pinned with SYTHION_E2E_MODEL_NVIDIA; otherwise a
usable Nemotron chat model is preferred. No credential value is printed or
persisted by this module, and no action/tool execution is attempted.
"""

import os

import pytest

from backend.adapters.openai_compatible_adapter import OpenAICompatibleCloudAdapter
from backend.core.secrets import resolve_compatible_provider_key
from backend.services.reasoning_dispatch import (
    AttentionSignals,
    ReasoningPurpose,
    admit_reasoning_job,
    as_text_result,
    dispatch_reasoning,
    handoff_reasoning_result,
)

pytestmark = [
    pytest.mark.provider_e2e,
    pytest.mark.network,
    pytest.mark.skipif(
        os.environ.get("SYTHION_RUN_REASONING_E2E", "").strip() != "1",
        reason="opt-in live reasoning E2E",
    ),
]


LIVE_PROVIDERS = ["nvidia"]
PREFERRED_NVIDIA_MODEL = "nvidia/nemotron-3-ultra-550b-a55b"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_id", LIVE_PROVIDERS)
async def test_shared_reasoning_contract_live(provider_id):
    credential = resolve_compatible_provider_key(provider_id)
    if not credential:
        pytest.skip(f"no scoped credential configured for {provider_id}")

    listing = OpenAICompatibleCloudAdapter(
        provider_id, "__e2e_catalog__", api_key=credential)
    try:
        models = await listing.list_models()
    finally:
        await listing.close()
    advertised = list(dict.fromkeys(
        value
        for entry in models
        if isinstance(entry, dict)
        for key in ("model", "name", "id")
        for value in [entry.get(key)]
        if isinstance(value, str) and value and value != "__catalog_discovery__"
    ))
    override = os.environ.get(
        f"SYTHION_E2E_MODEL_{provider_id.upper().replace('-', '_')}", "").strip()
    preferred = []
    if provider_id == "nvidia":
        if PREFERRED_NVIDIA_MODEL in advertised:
            preferred.append(PREFERRED_NVIDIA_MODEL)
        preferred.extend(
            mid for mid in advertised
            if "nemotron" in mid.lower()
            and not any(s in mid.lower() for s in ("safety", "embed", "guard"))
        )
    candidates = list(dict.fromkeys(
        ([override] if override else []) + preferred + advertised))
    if not candidates:
        pytest.skip(f"no model advertised by {provider_id}")

    admission = admit_reasoning_job(
        ReasoningPurpose.CONVERSATION,
        timeout_seconds=60.0,
        provider_id=provider_id,
        signals=AttentionSignals(has_new_input=True, provider_available=True),
    )

    # A catalog may include completion-only or retired models that answer
    # /models but reject /chat/completions. Opt-in E2E probes a small bounded
    # set of candidates; this is test setup, not production fallback.
    raw = None
    chosen = None
    for model_id in candidates[:5]:
        adapter = OpenAICompatibleCloudAdapter(
            provider_id, model_id, api_key=credential)

        async def call(current=adapter):
            return await current.chat(
                [{"role": "user", "content": "Reply with the single word: ready"}],
                max_tokens=32,
            )

        try:
            raw = await dispatch_reasoning(admission.job, call)
            chosen = model_id
            break
        except Exception:
            pass
        finally:
            await adapter.close()

    if chosen is None:
        pytest.skip(f"no chat-capable model responded for {provider_id}")

    content = (raw.get("choices") or [{}])[0].get("message", {}).get("content", "")
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    result = handoff_reasoning_result(
        as_text_result(admission.job, content), admission.job)
    assert result.provider_id == provider_id
    assert result.value.strip()
