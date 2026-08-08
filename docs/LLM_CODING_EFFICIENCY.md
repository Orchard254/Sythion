# LLM Agent Coding Efficiency & Engineering Protocol

This document outlines the engineering principles and execution workflows that allow LLM agents (regardless of model size or capacity) to write high-quality, reliable code efficiently without introducing technical debt or regressions.

---

## 1. Context-First Investigation (Look Before You Leap)

- **Read before writing**: Always inspect neighboring files, contracts, and existing test patterns before creating or modifying code.
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

- **Record key decisions**: Write significant architectural choices, bug fixes, and constraints into project memory immediately (`memory_write`).
- **Document rationale**: Explain *why* a fix was made so future LLM instances understand the context and do not undo subtle bug fixes.
- **Supersede outdated facts**: When design decisions change, supersede outdated memory entries (`memory_supersede`) to prevent conflicting guidance.

---

## Summary Checklist for Every Coding Task

```text
1. INVESTIGATE  → Read relevant files, contracts, and existing test patterns.
2. PLAN         → Identify minimal edits needed; trace root cause if bug fixing.
3. IMPLEMENT    → Make surgical edits matching existing codebase conventions.
4. VERIFY       → Run import checks and execute test suite (pytest/vitest).
5. RECORD       → Save implementation insights & decisions to project memory.
```
