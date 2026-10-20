# Testing

## Quick Start

```bash
# From repository root
make test              # Full backend suite
make test-fast         # Fast: skip slow/gui/network
make test-acceptance   # Acceptance gates only
make test-unit         # Unit tests only (skip acceptance)
make test-cov          # With coverage report
```

The measured numbers are in "Current verified baseline" below, and nowhere
else in this file.

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
- The suite runs under the repository-root `pytest.ini`. It pins
  `testpaths = backend/tests` and `asyncio_mode = strict`, and it is the
  single source of truth for the marker vocabulary, so the configuration is
  pinned rather than merely reported.
- The selection contract is enforced mechanically: a full-tree collection
  aborts with exit code 4 when a marker is applied to a test outside its
  declared population, or when a declared marker selects nothing.

### Current verified baseline

- Full backend suite: **1568 passed, 45 skipped** (1613 collected)
- Acceptance suite: **191 passed, 2 skipped**

Measured on CPython 3.14.7 / Windows by
`python scripts/measure_test_baseline.py` on 2026-10-20.
CI pins Python 3.11.

The 2 skipped acceptance tests are the symbolic-link privilege skips in
`backend/tests/acceptance/test_canonical_file_checks.py`.

The block above is a measurement, not a typed number. Reproduce it, and prove
that it still holds, with:

```bash
python scripts/measure_test_baseline.py                   # measure, print JSON
python scripts/measure_test_baseline.py --check           # do the documents agree?
python scripts/measure_test_baseline.py --check-collected # cheap drift check
```

`--check` re-measures and compares every baseline document against the
result; `--check-collected` re-measures only the collected count (a
`--collect-only` pass), which is the number that moves whenever a test is
added or removed.

### Common Test Commands

Every target below exists in the repository `Makefile`. The runtime column is
measured where a measurement exists and labelled otherwise: runtimes are
machine-specific.

| Command | What it selects | Runtime |
|---------|-----------------|---------|
| `make test` | The full backend suite (every test under `backend/tests`) | 377.6 s (reference machine) |
| `make test-fast` | The suite without the slow tests, the tests that need a live interactive desktop, and the provider-backed network tests | machine-specific (not measured here) |
| `make test-acceptance` | The acceptance sub-suite (`backend/tests/acceptance`) | 19.9 s (reference machine) |
| `make test-unit` | The suite without the acceptance sub-suite | machine-specific (not measured here) |
| `make test-gui` | The tests that need a live interactive Windows desktop and the real UI Automation stack (`backend/tests/acceptance/test_gui_fixture.py`) | machine-specific (not measured here) |
| `make test-network` | The provider-backed E2E tests; skipped unless `SYTHION_RUN_PROVIDER_E2E=1` and provider credentials are configured, so the target never makes a credentialed provider call by itself | machine-specific (not measured here) |
| `make check-test-selections` | Verifies that every `-m` expression the Makefile uses selects a non-empty set of tests | machine-specific (not measured here) |
| `make test-cov` | Coverage report (HTML + terminal) | machine-specific (not measured here) |
| `make test-file FILE=...` | One test file | machine-specific (not measured here) |
| `make test-fn FILE=... FN=...` | One test function | machine-specific (not measured here) |

### Pytest Markers

The ten canonical markers are declared in the repository-root `pytest.ini`.
The population column is what the marker actually selects today, measured on
2026-10-20.

| Marker | Select | Deselect | Population today |
|--------|--------|----------|------------------|
| `slow` | `pytest -m slow` | `pytest -m "not slow"` | 1 test |
| `flaky` | `pytest -m flaky` | `pytest -m "not flaky"` | 0 tests - declared, not applied to any test |
| `integration` | `pytest -m integration` | `pytest -m "not integration"` | 0 tests - declared, not applied to any test |
| `acceptance` | `pytest -m acceptance` | `pytest -m "not acceptance"` | 193 tests - exactly the tests under `backend/tests/acceptance/` |
| `gui` | `pytest -m gui` | `pytest -m "not gui"` | 4 tests - the live-GUI modules registered in `backend/tests/conftest.py` |
| `network` | `pytest -m network` | `pytest -m "not network"` | 5 tests - exactly the `provider_e2e` tests |
| `process_kill` | `pytest -m process_kill` | `pytest -m "not process_kill"` | 8 tests - real Windows process-kill tests, run explicitly with `SYTHION_RUN_PROCESS_KILL=1` |
| `provider_e2e` | `pytest -m provider_e2e` | `pytest -m "not provider_e2e"` | 5 tests - real provider-backed specialist E2E tests, run explicitly with `SYTHION_RUN_PROVIDER_E2E=1` |
| `concurrency` | `pytest -m concurrency` | `pytest -m "not concurrency"` | 53 tests - applied at module level to `backend/tests/test_phase105_concurrency.py` and `backend/tests/test_phase1075_hardening.py` |
| `gated_soak` | `pytest -m gated_soak` | `pytest -m "not gated_soak"` | 1 test - the synthetic learning-soak workload in `backend/tests/test_phase115_soak.py`; despite the name it is NOT gated by an environment flag and runs in the default suite |

### Test Fixtures (in `backend/tests/conftest.py`)

| Fixture | Description |
|---------|-------------|
| `test_db` | Fresh in-memory SQLite session |
| `test_client` | FastAPI TestClient with test DB |
| `task_runner` | TaskRunner with lease acquired |
| `tool_executor` | ToolExecutor with all tools registered |
| `acceptance_control_token` | Standard token for acceptance tests |

Two autouse fixtures run for every test: one pins `autonomy_level=guarded`
and disables memory consolidation (and clears `SYTHION_AUTONOMY_LEVEL`), the
other stops the suite from inheriting a developer's gitignored `backend/.env`
so that a clean checkout and a developer machine run the same tests.

## Frontend

```bash
cd frontend
npm run build      # tsc + vite build (must pass before release)
npm run lint       # eslint
npm test           # vitest, non-watch (the CI frontend step)
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

This is a MANUAL check: it needs a running sidecar and a reachable provider,
and **no executed run of it is recorded in this repository**. It is therefore
not a result this repository can quote, and it is not part of the measured
baseline below.

## CI Pipeline

`.github/workflows/ci.yml` (the merged workflow):

- **Triggers**: `push` to `[master, main]`, `pull_request`, and
  `workflow_dispatch`.
- **Job `backend-tests`** (`windows-latest`): Python 3.11, dependencies
  installed from `backend/requirements.txt`, then `python -m pytest tests -q`
  run from the `backend/` directory. The import gate
  (`python -c "import backend.main; import backend.api.routes"`) runs from the
  repository root, because `backend/api/routes.py` imports `backend.*`
  absolutely.
- **Job `provider-e2e`** (`windows-latest`): reachable only by a manual
  dispatch with `vars.RUN_PROVIDER_E2E == '1'`, in the `provider-e2e`
  environment, with `SYTHION_RUN_PROVIDER_E2E=1` and the provider secret
  injected at runtime. It never fires on a push or a pull request.
- **Job `frontend`** (`windows-latest`): Node 20, `npm ci`, `npm run lint`,
  `npm run build`, and the vitest step `npm test`.
- **Job `tauri-build`** (`windows-latest`): builds the shell with
  `npx --prefix frontend tauri build --no-bundle`. Its "Assert the shell
  binary was produced" step is a `Test-Path` probe: it prints True/False and
  does NOT currently gate the job on a missing binary.

## Makefile Commands

```bash
make test                   # Full backend suite (377.6 s, reference machine)
make test-fast              # Fast suite (machine-specific)
make test-acceptance        # Acceptance gates only (19.9 s, reference machine)
make test-unit              # Unit tests only (machine-specific)
make check-test-selections  # Prove every -m expression selects a non-empty set
make test-cov               # Coverage report (HTML + terminal)
make build                  # Build frontend + backend
make tauri-build            # Desktop shell (Rust + MSVC required)
make smoke                  # Live smoke test against a running sidecar
make dev-sidecar            # Start dev sidecar (port 8000)
make clean                  # Clean build artifacts
make help                   # Show all targets
```

## Notes

- pytest runs in strict asyncio mode; the mode is now **pinned** by the
  repository-root `pytest.ini` (`asyncio_mode = strict`), not merely reported
- SQLite in-memory (`StaticPool`) isolates every test database
- `conftest.py` pins `autonomy_level=guarded` + disables memory consolidation
- `SYTHION_AUTONOMY_LEVEL` env var is cleared to prevent test leakage
- `memory_consolidation_enabled` is forced `False` to avoid Cloud calls in tests
- Reusable fixtures: `test_db`, `test_client`, `task_runner`, `tool_executor`, `acceptance_control_token`
- The suite's warning count moves between runs of an unchanged tree, so it is
  not a baseline: the measurement script never publishes it
- The measurement is a completed, fault-free run or it is nothing: a run that
  crashed, faulted natively or failed publishes no counts at all
