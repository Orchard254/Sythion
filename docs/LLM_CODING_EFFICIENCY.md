# LLM Agent Coding Efficiency & Engineering Protocol

This document outlines the engineering principles and execution workflows that allow LLM agents (regardless of model size or capacity) to write high-quality, reliable code efficiently without introducing technical debt or regressions.

---

## 0. Memory-First Protocol (New — Run Before Everything Else)

**Before reading any source file or writing any code**, load the persistent project memory:

```python
import sys; sys.path.insert(0, r'C:\Ai\project-memory')
from memory_store import prepare_task, recall, file_briefing

# Session start: get full architectural briefing
print(prepare_task("intent description", file_paths=["path/to/file_you_plan_to_edit.py"]))
```

This single call returns:
- Architectural context, prior decisions, and known constraints from past sessions
- File impact analysis — all dependencies, linked files, and refactor risks
- Latest verified test baseline

**After completing any coding task**, checkpoint atomically:

```python
from memory_store import checkpoint_task
checkpoint_task(
    summary="What was implemented and why",
    components=[{"content": "Description", "file_path": "path/to/file.py", "importance": 4}],
    decisions=[{"content": "Decision made", "rationale": "Why"}],
    test_baseline="701 passed, 32 skipped",
)
```

---

## 1. Context-First Investigation (Look Before You Leap)

- **Query memory before reading files**: `recall("topic")` retrieves facts about any module or decision without opening files.
- **Read before writing**: When memory doesn't cover it, inspect neighboring files, contracts, and existing test patterns before creating or modifying code.
- **Mirror project style**: Match formatting, naming conventions, typing paradigms (Pydantic v2 `model_fields`, dataclasses, typing hints), and error handling choices.
- **Verify dependencies**: Check `requirements.txt` or `package.json` to confirm available libraries before importing them. Never assume a package exists.

---

## 2. Surgical & Minimal-Diff Modifications

- **Edit, don't rewrite**: Use targeted string replacements (`edit` tool) over full-file overwrites (`write`) whenever modifying existing source code.
- **Preserve existing behavior**: Ensure edits do not unintentionally alter unrelated methods, signatures, or import structures.
- **Keep changes focused**: Implement exactly what is needed to solve the task or fix the contract. Avoid unnecessary stylistic refactoring.

---

## 3. Root-Cause Bug Fixing (Fix Contracts, Not Symptoms)

- **Audit before patching**: When a test or tool execution fails, trace the failure back to the underlying contract violation.
- **Avoid band-aids**: Do not add superficial `try/except` suppressions that hide the true error. Fix the data flow, typing, or state mismatch at its source.
- **Check MRO and execution scopes**: In dynamic execution environments (`exec`/`eval` sandbox, dynamic subclasses), verify object identity, class MRO, and builtin namespaces.

---

## 4. Defensive Runtime Practices

- **Handle schema duality**: Handle both Pydantic models and raw `dict` objects (e.g., check `isinstance(obj, dict)` vs `hasattr(obj, 'model_fields')`).
- **Null & boundary safety**: Guard against `None` values, empty lists, missing dictionary keys, and detached ORM instances (`session.commit()` detaching SQLAlchemy objects).
- **Graceful degradation**: Return structured fallback outputs or safe defaults when optional tools or services are unavailable.

---

## 5. Architectural Boundary Respect

- **Strict dependency layering**: Bottom modules (`core/`, `models/`) must **never** import upper layers (`api/`, `tools/`, `routes/`).
- **Dual import compatibility**: Support both package imports (`from backend.models...`) and direct module imports (`from models...`) via fallback `try/except ImportError`.
- **Single source of truth**: Store shared configuration, permissions, and constants in `core/` to prevent split-brain state.

---

## 6. Self-Verification Loop (Verify Before Claiming Done)

- **Execute tests autonomously**: Run project test suites (`python -m pytest tests -q`) after code modifications.
- **Import/boot check**: Verify new services or imports directly via `python -c "import ..."` with proper `PYTHONPATH`.
- **Anchor new features with tests**: Write unit tests for new modules to prove correctness and protect against future regressions.

---

## 7. Persistent Memory & Intentional Knowledge Transfer

Memory operations are not optional bookkeeping — they are **core workflow steps**:

| Trigger | Action |
|---|---|
| Session start | `prepare_task(intent, file_paths)` — loads full architectural briefing |
| Before opening a file | `file_briefing(path)` — impact analysis + constraints |
| After reading a source file | `remember(category, content, file_path=..., importance=...)` — write findings immediately |
| Before assuming anything factual | `recall("topic")` — query memory before guessing |
| When a fact is wrong/stale | `store.supersede(old_id, new_content)` — never create a duplicate |
| After completing a coding task | `checkpoint_task(summary, components, decisions, test_baseline)` |

**Document rationale**: Explain *why* a fix was made so future LLM instances understand the context and do not undo subtle bug fixes.

---

## Summary Checklist for Every Coding Task

```text
0. MEMORY     → prepare_task(intent, file_paths)  — flight briefing before anything
1. INVESTIGATE → recall(topic) + file_briefing(path) — verify without reading files cold
2. PLAN        → Identify minimal edits needed; trace root cause if bug fixing
3. IMPLEMENT   → Make surgical edits matching existing codebase conventions
4. VERIFY      → Run import checks and execute test suite (pytest/vitest)
5. CHECKPOINT  → checkpoint_task(summary, components, decisions, test_baseline)
```
