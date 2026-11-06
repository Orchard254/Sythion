# Sythion Codebase Boundaries

## Purpose

This document classifies the current codebase into:

1. **Trusted runtime core**
2. **Runtime/intelligence bridge modules**
3. **Intelligence-layer scaffolding**
4. **Historical or cleanup targets**

It exists to stop architecture drift from reappearing as "just one more
feature" in the wrong layer.

## 1. Trusted runtime core

These modules define or enforce the local execution contract.

### Boundary, policy, storage, and safety

- `backend/core/auth.py`
- `backend/core/budgets.py`
- `backend/core/config.py`
- `backend/core/db_manager.py`
- `backend/core/handshake.py`
- `backend/core/hotkeys.py`
- `backend/core/importance_classifier.py`
- `backend/core/logging_config.py`
- `backend/core/profile.py`
- `backend/core/profile_lock.py`
- `backend/core/secrets.py`
- `backend/core/session.py`
- `backend/core/settings_store.py`
- `backend/core/storage.py`
- `backend/core/voice_assets.py`

### API and data contract

- `backend/api/routes.py`
- `backend/api/schemas.py`
- `backend/models/database.py`
- `backend/main.py`

### Tool execution substrate

- `backend/tools/executor.py`
- `backend/tools/registry.py`
- `backend/tools/system_tools.py`
- `backend/tools/file_tools.py`
- `backend/tools/app_tools.py`
- `backend/tools/package_tools.py`
- `backend/tools/gui_tools.py`

The web surface is not a tool module: it is the browser bridge
(`backend/api/browser_bridge_routes.py` and
`backend/services/browser_bridge_service.py`).

### Runtime orchestration and lifecycle

- `backend/services/task_runner.py`
- `backend/services/retention.py`
- `backend/services/validation_report.py`
- `backend/services/voice_pipeline.py`

These files should remain execution-truth oriented: policy, verification,
auditability, target validation, lifecycle, and side effects.

## 2. Runtime/intelligence bridge modules

These modules sit on the seam between reasoning and execution. They are the
most important refactor targets because they currently mix concerns.

### `backend/services/model_engine.py`

Current role:
- tool-visible system prompt construction
- model response parsing and decision validation
- orchestration of the model contract

Why it is a bridge module:
- part of it belongs to trusted runtime contract (what the model is allowed to
  see and call);
- it still owns the bridge from runtime state into model decisions.

Current split already completed:
- context assembly moved into `backend/services/model_context_builder.py`
- prompt contract moved into `backend/services/model_prompt_contract.py`

Recommended future split:
- tool exposure / visibility policy
- model response parsing / validation
- any remaining reasoning-side helper logic

### `backend/services/model_context_builder.py`

Current role:
- previous-task continuity heuristics
- selective memory injection
- formatting of replayed tool/event context for the model
- delegation point for conversation-facing guidance blocks

Why it is a bridge module:
- it shapes model-visible reasoning context using durable runtime state, but it
  does not own execution truth.

### `backend/services/conversation_guidance_adapter.py`

Current role:
- recent conversation replay for the current session
- conversation-state formatting

Classification:
- intelligence-facing adapter layered above runtime truth, because it packages
  conversational guidance without owning execution or audit facts

### `backend/services/task_execution_loop.py`

Current role:
- runner-driven task execution loop
- repeat guards / cancellation checks
- tool/skill execution orchestration
- runtime-side terminal-state handling

Why it is a bridge module:
- the main loop is runtime core;
- it still calls into higher-order post-task hooks, though those hooks are now
  delegated to a separate service.

Current split already completed:
- post-task conversation persistence, memory consolidation, and proposal growth
  moved into `backend/services/post_task_intelligence.py`

Recommended future split:
- keep the execution loop runtime-focused;
- continue moving bridge behaviors behind explicit adapters or callbacks.

### `backend/tools/memory_tools.py`

Current role:
- stable compatibility facade for legacy memory-tool imports
- re-exports persistent memory tools and runtime activity recall from separate
  modules

Why it remains bridge-facing:
- existing callers/tests still import one memory-tools surface,
- but the implementation boundary is now explicit underneath it.

### `backend/tools/persistent_memory_tools.py`

Current role:
- persistent preference/profile memory tools
- relation graph tools over durable memory facts
- deterministic safety gates that reject live system state/device identity from
  being persisted as false durable memory

Classification:
- runtime/intelligence bridge leaning durable-memory, because it mutates only
  Sythion's internal memory store but shapes cross-task continuity

### `backend/tools/activity_recall_tool.py`

Current role:
- read-only recall over runtime history (`TaskEvent` + `ConversationTurn`)
- bounded summaries of past activity without leaking raw params/output

Classification:
- runtime-history recall surface, distinct from durable user memory

Recommended rule:
- keep deterministic safety gates in persistent memory tools;
- avoid letting durable memory or history recall become a parallel truth source
  over live tools.

### `backend/services/skill_engine.py`

Current role:
- stable compatibility facade for skill imports used by routes, tests, and the
  task loop
- re-exports runtime and proposal modules without owning the mixed logic itself

Why it remains a bridge-facing surface:
- callers still interact through one legacy import path,
- but the implementation boundary is now explicit underneath it.

### `backend/services/skill_runtime.py`

Current role:
- declarative workflow validation
- policy-filtered allowed-tool computation
- skill/version persistence helpers
- pinned execution and pause semantics

Classification:
- runtime/intelligence bridge leaning runtime, because it executes validated
  workflows and records audit-visible task behavior

### `backend/services/skill_proposals.py`

Current role:
- verified-trace extraction
- proposal generation and evidence accumulation
- explicit approval/rejection
- optional auto-promotion after re-validation

Classification:
- higher-order bridge/growth module, because it reasons over prior successful
  behavior to grow future capabilities without weakening runtime policy

## 3. Intelligence-layer scaffolding

These modules are appropriate for the broader intelligence direction, but they
should remain layered above runtime truth.

- `backend/services/conversation_state.py`
- `backend/services/memory_consolidation.py`
- `backend/services/tool_authoring.py`

### Notes

- `conversation_state.py` is the conversational-state module; the
  conversation-guidance adapter (`conversation_guidance_adapter.py`) is what
  formats it for a model request.
- `memory_consolidation.py` is higher-order memory distillation logic and must
  stay best-effort, never execution-critical.
- `post_task_intelligence.py` is an intelligence hook layer invoked after
  terminal runtime states; it should remain strictly best-effort.
- `tool_authoring.py` is an experimental capability-growth surface and should be
  treated as advanced intelligence/agency scaffolding, not a casual extension
  point.

## 4. Historical or cleanup targets

### `backend/services/README.md`

This file had drifted badly:
- old state-machine labels;
- old prototype paths;
- old phase framing;
- stale examples and next steps.

It should describe the current service layout, not an older prototype.

### Older milestone/status docs

These are intentionally retained as archives, but must stay clearly labeled as
non-authoritative:
- `docs/HISTORICAL_REPORTS.md` — consolidated archive of former root-level
  historical reports
- `docs/PROGRESS_REPORT.md`

## Practical refactor order

### Step 1 — low-risk structure clarity

- keep authoritative docs current;
- keep service/module READMEs aligned with live code;
- tag bridge modules explicitly in docs and memory.

### Step 2 — split bridge concerns

- extract conversation-guidance assembly from `model_engine.py`;
- extract post-task intelligence hooks from `task_execution_loop.py`;
- keep new boundary clarity between `skill_runtime.py` and
  `skill_proposals.py`, with `skill_engine.py` as compatibility facade.

### Step 3 — protect the runtime boundary

As intelligence deepens, do not allow:
- unverified model narration to replace tool evidence;
- memory to override live runtime truth;
- growth/proposal systems to bypass review, policy, or verification.

## Rule of thumb

If a module can cause OS-visible effects, task-state transitions, or audit-log
facts, it belongs to the runtime core.

If a module mainly improves conversation, planning, memory interpretation,
strategy, or future agency, it belongs to the intelligence layer or the
runtime/intelligence bridge.
