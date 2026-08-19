# Backend Services Overview

## Purpose

This directory contains service-layer code for Sythion's trusted runtime core
and its early intelligence-layer scaffolding.

Do not treat every service here as the same kind of component. Some are pure
runtime services, some are bridge modules, and some belong to higher-order
intelligence behavior.

For the authoritative classification, see
[`../../docs/CODEBASE_BOUNDARIES.md`](../../docs/CODEBASE_BOUNDARIES.md).

## Runtime-core services

These services are closest to execution truth:

- `task_runner.py` — task state transitions, lease handling, durable events
- `retention.py` — retention windows, complete-record purge, receipts
- `validation_report.py` — read-only validation/reporting surface
- `voice_pipeline.py` — local voice lifecycle inside the runtime boundary

## Runtime/intelligence bridge services

These mix execution concerns with higher-order reasoning and should be handled
carefully:

- `model_engine.py` — model orchestration and decision parsing; now delegates
  prompt construction to `model_prompt_contract.py` and context assembly to
  `model_context_builder.py`
- `model_prompt_contract.py` — system prompt / model-facing runtime contract
- `model_context_builder.py` — previous-task continuity, memory/context
  injection, and overall assembly of model request context
- `conversation_guidance_adapter.py` — recent conversation replay,
  conversation-state formatting, speech-act analysis, and response-strategy
  guidance for model requests
- `task_execution_loop.py` — runtime execution loop; now delegates post-task
  intelligence hooks to `post_task_intelligence.py`
- `skill_engine.py` — stable compatibility facade for the skill system
- `skill_runtime.py` — declarative workflow validation, version storage,
  allowed-tool filtering, pinned execution, and pause semantics
- `skill_proposals.py` — verified-trace proposal generation, evidence growth,
  explicit review, and optional auto-promotion

The remaining high-value bridge-cleanup targets are the modules that still mix
runtime orchestration with higher-order guidance.

## Intelligence-layer scaffolding

These services support the broader intelligence direction without owning the
runtime truth contract:

- `conversation_state.py`
- `speech_act_classifier.py`
- `response_strategy.py`
- `memory_consolidation.py`
- `post_task_intelligence.py`
- `tool_authoring.py`

They may guide or enrich behavior, but they must not bypass runtime policy,
verification, or audit.

## Development guidance

When editing services:

1. Preserve the trusted runtime boundary.
2. Keep execution truth separate from conversational or strategic heuristics.
3. Prefer extracting mixed concerns into smaller modules over deepening a
   monolithic bridge file.
4. Keep tests and docs aligned with the current architecture.

## Related documents

- `../../docs/ARCHITECTURE.md`
- `../../docs/INTELLIGENCE_ARCHITECTURE.md`
- `../../docs/CODEBASE_BOUNDARIES.md`
- `../../docs/PROJECT_STATUS.md`
