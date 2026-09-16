# Sythion Decision Index

## Purpose

This file is a compact index of active architectural and engineering decisions
for this repository.

It is not a replacement for:

- [`ARCHITECTURE.md`](ARCHITECTURE.md)
- [`INTELLIGENCE_ARCHITECTURE.md`](INTELLIGENCE_ARCHITECTURE.md)

Instead, it provides a fast human/LLM scan of the choices that should not be
silently undone.

## Active decisions

### D1. This repo is the trusted runtime core, not the whole intelligence system

**Decision:** Keep this repository as the auditable local runtime core of a
larger intelligence system.

**Why:** The broader vision needs a trustworthy execution substrate. Planning,
conversation, memory, and future agency can grow above this layer, but runtime
truth must stay local, bounded, and evidence-driven.

**Authority:** `ARCHITECTURE.md`, `INTELLIGENCE_ARCHITECTURE.md`

### D2. Runtime truth beats narration

**Decision:** No model, memory layer, or higher-order module may claim success
without runtime evidence.

**Why:** The owner directive is effectively that autonomy must remain auditable.
Verification, durable task events, and policy gates are more trustworthy than
free-form model narration.

**Authority:** `ARCHITECTURE.md`

### D3. Keep the runtime/intelligence split explicit in code

**Decision:** When a module mixes runtime execution concerns with higher-order
intelligence behavior, prefer extracting the boundary into smaller modules while
keeping stable public surfaces where useful.

**Why:** Mixed architecture was a real source of confusion in this repo. Clear
boundaries make the project more professional, safer to evolve, and easier for
future LLMs to inherit correctly.

**Authority:** `CODEBASE_BOUNDARIES.md`

### D4. `skill_engine.py` remains a compatibility surface

**Decision:** Runtime skill execution and higher-order proposal growth are split,
while `backend/services/skill_engine.py` remains the stable import facade.

**Why:** This preserves existing routes/tests/task-loop imports while making the
runtime vs growth boundary explicit.

**Authority:** `CODEBASE_BOUNDARIES.md`

### D5. Durable memory and runtime activity recall are separate concerns

**Decision:** Durable user memory tools and runtime activity-history recall
should live in separate modules, with `backend/tools/memory_tools.py` acting as
compatibility facade.

**Why:** Stored preferences/profile facts are not the same thing as task/event
history. Keeping them separate avoids hidden truth-source confusion and makes
future evolution cleaner.

**Authority:** `CODEBASE_BOUNDARIES.md`

### D6. Conversation guidance should be adapterized, not inlined everywhere

**Decision:** Conversation-state formatting, recent-turn replay, speech-act
analysis, and response-strategy wiring should sit behind explicit adapters when
used to shape model context.

**Why:** These are intelligence-facing guidance concerns, not runtime truth.
The adapter boundary keeps context assembly cleaner and reduces bridge-file
sprawl.

**Authority:** `CODEBASE_BOUNDARIES.md`

### D7. Project memory is a first-class continuity surface

**Decision:** Verified architectural facts and state changes should be written to
project memory as they become true.

**Why:** Future LLMs should inherit verified context instead of rediscovering or
contradicting prior work.

**Authority:** `AGENTS.md`

### D8. Docs must match the code, not aspirations

**Decision:** Authority docs should describe verified reality, not optimistic or
historical status.

**Why:** This repo previously had real drift across status docs, phase labels,
and architecture framing. A professional repo keeps its truth surface aligned.

**Authority:** `PROJECT_STATUS.md`, `QUALITY_GATES.md`

### D9. Durable work-state lives above the task ledger, not inside memory

**Decision:** Explicit objectives and plan steps should be their own durable
projection linked to tasks, with task/events remaining execution truth and
persistent memory remaining a separate recall surface.

**Why:** Continuity, memory recall, and task history were not enough to answer
cleanly what the system is currently working on, what it has completed, and
what remains pending. A dedicated work-state layer makes that state explicit
without letting memory or prompt heuristics become hidden truth sources.

**Authority:** `INTELLIGENCE_ARCHITECTURE.md`

## Superseded patterns to avoid

- Treating the repo as merely a generic desktop assistant
- Letting memory become a hidden truth source over live tools
- Expanding intelligence features by weakening runtime policy or verification
- Reintroducing monolithic bridge modules when a clear boundary exists
- Describing historical milestone reports as current project truth

## How to use this file

When making a meaningful structural change:

1. check whether it reinforces or changes one of these decisions;
2. update this file if the active decision set changed;
3. update project memory with the verified result.
