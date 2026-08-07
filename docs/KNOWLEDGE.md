# Knowledge Graph: Sythion (Synth)

Persistent memory for cross-session context. Not a changelog — a memory hub.

---

## Project Overview

Sythion is a personal desktop assistant (Tauri + React + Python/FastAPI sidecar).
Goal: autonomous operation on a server, operating other devices, seeking approval
ONLY for genuinely important things.

**Owner directive**: "the system can only be given autonomy it can audit."

---

## Constraints (non-negotiable)

1. **core/ is the bottom layer** — must never import api/ or tools/. Policy
   shared by schemas + tools lives here (hotkeys, importance classifier, config).
2. **Dual import convention** — modules use try/except to import both
   `backend.*` and top-level (`tools.*`, `api.*`, `core.*`).
3. **Tool contract** — BaseTool subclasses define name, description,
   input_schema (Pydantic v2), execute, verify, get_policy_requirements.
4. **Importance classifier is the single policy gate** — not the model's
   in-context judgment. Classifies ROUTINE/CONSEQUENTIAL/FORBIDDEN.
5. **Autonomy levels** — guarded (default, most tools hidden) vs autonomous
   (all tools visible, consequential actions execute directly).
6. **Session is process-lifetime** — restart loses conversation context.
   Cross-restart memory is only via memory_store/memory_recall.

---

## Current State

### Built (layer 1: trust)
- Memory persistence + consolidation (background LLM distillation)
- Memory edges with decay + owner review
- Skill proposals auto-enable after N verified traces (L2)
- Tool-authoring pipeline with validation/sandbox/approval (L3)
- Importance classifier (deterministic policy gate)
- system_volume tool (Core Audio COM)
- File roots live grant system (no restart needed)
- Session ID persists across restarts
- Validation dump report endpoint

### Designed (not built)
- Voice-first approval architecture (entry 37)
  - Problem: approval flow is text-based, voice needs conversation-based
  - Question: inline vs sub-task vs pre-cached auto-approve
  - Status: design pending

### Roadmap
1. Trust layer — largely built ✓
2. Importance/approval gate — built ✓
3. Server readiness — not started (real auth, remote transport, long-horizon scheduling)
4. Multi-device — not started (device registry, per-device roots, remote transport)

---

## Component Graph

```
importance_classifier
  ├── supersedes → requires_confirmation (ad-hoc check)
  ├── integrates with → task_execution_loop._execute_tool()
  ├── determines → needs_approval
  └── classifies → ROUTINE / CONSEQUENTIAL / FORBIDDEN

task_execution_loop
  ├── uses → importance_classifier
  ├── uses → model_engine
  ├── uses → tool_executor
  ├── parks in → WAITING_FOR_USER (when needs_approval)
  └── resumes via → /tasks/{id}/reply

voice_pipeline (separate from task loop)
  ├── TTS → SAPI5 via pyttsx3
  ├── STT → local whisper
  └── NOT connected to → approval flow (gap)

memory_consolidation
  ├── runs after → task completion
  ├── distills → conversation turns → durable memory ops
  ├── gated by → confidence==high, DELETE never auto-applied
  └── uses → adapter.chat() (plain, no tools)

skill_engine
  ├── L2: auto-enables proposals after N traces
  ├── re-validates against current toolset at promotion
  └── rejected signatures never re-proposed

tool_authoring (L3)
  ├── validates → AST (no forbidden imports), interface compliance
  ├── sandbox-tests → restricted namespace
  ├── stages → owner approval via /tools/proposals/{id}/approve
  └── verified end-to-end (model proposes, validation catches)

system_volume
  ├── uses → Core Audio COM via comtypes
  ├── read-only, risk_level: none
  └── fills gap in known_gaps (was: file_search, system_volume, ...)
```

---

## Key Decisions (why)

| Decision | Why |
|---|---|
| comtypes not ctypes | ctypes is forbidden by security contract (no raw FFI) |
| importance_classifier in core/ | Bottom layer — schemas + tools both need it, no cycles |
| Auto-approve ROUTINE actions | Minimize friction in voice-only mode |
| Memory consolidation gated by confidence | Prevent memory rot, duplicates, contradictions |
| Session ID persists | Continuity across restarts without manual intervention |
| File roots = env + live grant union | No restart needed, user can grant from dashboard |
| num_ctx = 65536 | Above input cap (50K), below full 256K, bounds GPU cost |

---

## Known Traps

- Stale docs: README test count (74) is wrong, suite is 640+
- `uvicorn --reload` mid-task: old process drains while new one starts
- Config singleton captures env at import time — deleting env var doesn't reset
- GUI fixture test sensitive to user input (don't type during test run)
- Win11 Notepad save dialog caption may not contain "notepad"

---

## Open Questions

- Does MiniMax m3 respect gui_hotkey schema (description + items)?
- Ollama Cloud failure bodies discarded — provider error detail never logged
- Win11 Notepad save-prompt dialog caption — drives re-probe strategy

---

## Gaps (fixable, not by design)

file_search → window_list → power/network family with confirmations.
system_volume shipped. OCR not bundled (gui_observe use_ocr=false).

---

## Verification Discipline

1. Facts come from code paths, passing tests, live logs, or live repro.
2. Before changing anything, re-verify the surrounding contract.
3. Fix at the layer where the contract was violated — never patch the symptom.
