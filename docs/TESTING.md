# Testing

## Quick Start

```bash
# From repository root
make test              # Full backend suite (692 passed, 32 skipped)
make test-fast         # Fast: skip slow/flaky/integration/gui/network
make test-acceptance   # Acceptance gates only (196 passed, 2 skipped)
make test-unit         # Unit tests only (skip acceptance)
make test-cov          # With coverage report
```

## Backend Tests

Run from the repository root **or** the `backend/` directory:

```bash
# Full suite
python -m pytest backend/tests -q

# From backend/ directory
python -m pytest tests -q
```

- Unit + integration + acceptance suites live under `backend/tests/`
  (including `backend/tests/acceptance/`). Deterministic: the live Cloud model
  and OS mutations are stubbed.
- `python -m pytest tests/acceptance -q` runs only the acceptance gates.
- **Current verified baseline**: **692 passed, 32 skipped** for
  the full backend suite, and **196 passed, 2 skipped** for the acceptance
  suite. The 2 skipped acceptance tests are the symbolic-link privilege skips
  in `backend/tests/acceptance/test_canonical_file_checks.py`.

### Common Test Commands

| Command | Description |
|---------|-------------|
| `make test` | Full backend suite (~55s) |
| `make test-fast` | Fast suite (~25s, skips slow/flaky/integration/gui/network) |
| `make test-acceptance` | Acceptance gates only (~10s) |
| `make test-unit` | Unit tests only (skip acceptance) |
| `make test-gui` | GUI tests only |
| `make test-network` | Network-dependent tests only |
| `make test-cov` | Coverage report (HTML + terminal) |
| `make test-file FILE=...` | Run single test file |
| `make test-fn FILE=... FN=...` | Run single test function |

### Pytest Markers

| Marker | Select | Deselect |
|--------|--------|----------|
| `slow` | `pytest -m slow` | `pytest -m "not slow"` |
| `flaky` | `pytest -m flaky` | `pytest -m "not flaky"` |
| `integration` | `pytest -m integration` | `pytest -m "not integration"` |
| `acceptance` | `pytest -m acceptance` | `pytest -m "not acceptance"` |
| `gui` | `pytest -m gui` | `pytest -m "not gui"` |
| `network` | `pytest -m network` | `pytest -m "not network"` |

### Test Fixtures (in `conftest.py`)

| Fixture | Description |
|---------|-------------|
| `test_db` | Fresh in-memory SQLite session |
| `test_client` | FastAPI TestClient with test DB |
| `task_runner` | TaskRunner with lease acquired |
| `tool_executor` | ToolExecutor with all tools registered |
| `acceptance_control_token` | Standard token for acceptance tests |

## Frontend

```bash
cd frontend
npm run build      # tsc + vite build (must pass before release)
npm run lint       # eslint
```

## Tauri Desktop Shell

```bash
npx --prefix frontend tauri build --no-bundle   # compile shell (Rust + MSVC required)
```

Run from repository root. Requires Rust toolchain + Windows Build Tools (MSVC).
The `tauri-build` CI job (`windows-latest`) is the executed build gate.
Runtime config validated by `npx --prefix frontend tauri info`.

## Live Smoke Test (Sidecar)

```bash
python scripts/smoke_test.py --all
```

Run from repository root against a running sidecar
(`python scripts/launch_sidecar.py --port 8000 --dev --no-open`).
Currently 6/6 live.

## CI Pipeline

`.github/workflows/ci.yml` runs on push/PR to `main`:

1. Backend tests (`python -m pytest backend/tests -q`)
2. Frontend build + lint (`npm run build`, `npm run lint`)
3. Packaging check: `python -c "import backend.main"` + deterministic build
4. Tauri shell build on `windows-latest` (`npx --prefix frontend tauri build --no-bundle`)
   Asserts `src-tauri/target/release/sythion.exe` produced

## Makefile Commands

```bash
make test            # Full backend suite (~55s)
make test-fast       # Fast suite (~25s)
make test-acceptance # Acceptance gates only (~10s)
make test-unit       # Unit tests only
make test-cov        # Coverage report (HTML + terminal)
make build           # Build frontend + backend
make tauri-build     # Tauri shell (Rust + MSVC required)
make smoke           # Live smoke test against running sidecar
make dev-sidecar     # Start dev sidecar (port 8000)
make clean           # Clean build artifacts
make help            # Show all targets
```

## Notes

- pytest runs in strict asyncio mode; async tests carry `@pytest.mark.asyncio`
- SQLite in-memory (`StaticPool`) isolates every test database
- `conftest.py` pins `autonomy_level=guarded` + disables memory consolidation
- `SYTHION_AUTONOMY_LEVEL` env var is cleared to prevent test leakage
- `memory_consolidation_enabled` is forced `False` to avoid Cloud calls in tests
- Reusable fixtures: `test_db`, `test_client`, `task_runner`, `tool_executor`, `acceptance_control_token`