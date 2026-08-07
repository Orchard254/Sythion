# Repository Guidelines

Sythion (Synth) is a Windows 10/11 personal desktop assistant: a Tauri + React dashboard (`frontend/`, `src-tauri/`) that launches one fixed Python/FastAPI sidecar (`backend/`), which owns the task loop, SQLite ledger, policy, Ollama Cloud adapter, and tool executor. Loopback-only, one running task at a time (lease), verified-outcome-only results.

## Working Principles

Audit before you fix. When a task or test fails, trace it to the contract it violated and the layer where the fix belongs — do not patch the symptom. Verify changes against the real design and with an import/boot check, not just green tests. Never add code that trades one problem for another.

Research before you fix. Do the reading/search first (repo evidence, docs, web) before proposing or applying a fix — never depend only on training data. Then check the fix against the design before and after.

Proceed autonomously. Ask for approval only when it is genuinely necessary (unclear direction, irreversible/expensive change, a decision that reverses an explicit owner choice). Otherwise prefer to act carefully and report what you did. When you must ask, ask one focused question with a recommendation.

Before trusting any fact about this repo, read `docs/KNOWLEDGE.md` — it is the living log of verified facts (each with its verification method) and open questions. Keep it current: when you verify something, add or update an entry; never leave an assumption unmarked.

## Project Structure & Module Organization

- `backend/core/` is the bottom layer (config, auth, storage, policy constants such as `hotkeys.py`). It must never import `api` or `tools`. Policy that both schemas and tools need lives here so the model-facing schema description and runtime enforcement share one source.
- `backend/api/schemas.py` is DTOs/validation only (Pydantic v2, use `model_fields`). `backend/tools/` implements `BaseTool` tools (name, description, `input_schema`, `execute`, `verify`, `get_policy_requirements`, `validate_targets`).
- `backend/services/task_execution_loop.py` runs the model decision loop; `model_engine.py` builds context and parses decisions; `skill_engine.py` proposes declarative skills from traces.
- Modules import both `backend.*` and top-level (`tools.*`, `api.*`, `core.*`) via `try/except ImportError`. Keep new modules importable both ways.
- Ollama Cloud tool-call `arguments` must be JSON objects, never strings; replayed tool turns persist real params as dicts.
- The config singleton (`backend/core/config.py`) captures `SYTHION_AUTONOMY_LEVEL` at import time.

## Build, Test, and Development Commands

- Backend tests (CI-faithful, no PYTHONPATH needed): `cd backend; python -m pytest tests -q`. Single file: `python -m pytest tests/acceptance/test_brain_contract.py -q`.
- Dev server (from repo root): `python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000`.
- Ad-hoc scripts that `import backend.*` need `PYTHONPATH=<repo root>;<repo root>\backend`.
- Frontend: `cd frontend; npm run dev` / `npm run lint` / `npm run build` (tsc + vite) / `npm test` (vitest).

## Testing Guidelines

- pytest + pytest-asyncio; acceptance tests must be deterministic (background model calls stubbed).
- `backend/tests/conftest.py` pins `settings.autonomy_level="guarded"` for the whole suite; autonomous-path tests opt in via monkeypatch.
- Tool tests substitute fake win32/driver modules with monkeypatch (see `tests/test_gui_tools.py`, `tests/acceptance/test_app_identities.py`).

## Commit & Pull Request Guidelines

- Commit messages use a `Phase X: <summary>` prefix (see `git log`). No PR templates.
- README test counts are stale: the suite is ~408 tests, not 74.
