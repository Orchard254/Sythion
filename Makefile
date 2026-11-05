# Sythion (Synth) - Test & Development Commands
# Usage: make <target>
#
# Every test target below states WHAT IT SELECTS. The marker sets are
# defined by the root pytest.ini and applied by backend/tests/conftest.py;
# `make check-test-selections` proves that each -m expression below selects a
# real, non-empty set of tests.
#
#   make test                  - the full backend suite (every test under backend/tests)
#   make test-fast             - the suite without the slow, live-GUI and network tests
#   make test-acceptance       - the acceptance sub-suite (backend/tests/acceptance)
#   make test-unit             - the suite without the acceptance sub-suite
#   make test-gui              - the tests that need a live interactive desktop
#   make test-network          - the provider-backed E2E tests
#   make check-test-selections - verify that every -m expression selects a non-empty set
#   make lint                  - run linting (if configured)
#   make build                 - build frontend + backend
#   make clean                 - clean build artifacts

.PHONY: test test-fast test-acceptance test-unit test-gui test-network check-test-selections lint build clean stage-clean-runtime generate-clean-installer-config clean-source-build tauri-clean-build

# Full backend suite - every test under backend/tests.
test:
	python -m pytest backend/tests -q

# Fast test: the suite without the slow tests, the tests that need a live
# interactive desktop and the provider-backed network tests.
test-fast:
	python -m pytest backend/tests -q -m "not slow and not gui and not network"

# Acceptance tests only: the sub-suite under backend/tests/acceptance.
# Equivalent to `-m acceptance`; the selection-contract guard in
# backend/tests/conftest.py enforces that the two select the same tests.
test-acceptance:
	python -m pytest backend/tests/acceptance -q

# Unit tests only: everything except the acceptance sub-suite.
test-unit:
	python -m pytest backend/tests -q -m "not acceptance"

# GUI tests only: the tests that need a live interactive Windows desktop and
# the real UI Automation stack - the modules registered in LIVE_GUI_MODULES in
# backend/tests/conftest.py (currently backend/tests/acceptance/test_gui_fixture.py).
# `-rs` reports the skip reason when no interactive desktop is available
# instead of silently reporting it as a pass.
test-gui:
	python -m pytest backend/tests -q -m gui -rs

# Network tests only: the provider-backed E2E tests. They are skipped unless
# SYTHION_RUN_PROVIDER_E2E=1 and provider credentials are configured, so this
# target never makes a credentialed provider call by itself. `-rs` reports
# the skip reason.
test-network:
	python -m pytest backend/tests -q -m network -rs

# Verify that every marker expression this Makefile uses selects a non-empty
# set of tests. pytest exits 5 for an empty selection, so an empty selection
# fails this target instead of passing silently.
check-test-selections:
	python -m pytest backend/tests -m "not slow and not gui and not network" --collect-only -q
	python -m pytest backend/tests -m "not acceptance" --collect-only -q
	python -m pytest backend/tests -m gui --collect-only -q
	python -m pytest backend/tests -m network --collect-only -q

# Run a single test file
test-file:
	@echo "Usage: make test-file FILE=backend/tests/test_work_state.py"
	python -m pytest $(FILE) -q

# Run a single test function
test-fn:
	@echo "Usage: make test-fn FILE=backend/tests/test_work_state.py FN=test_task_gets_objective"
	python -m pytest $(FILE)::$(FN) -q -v

# Coverage report
test-cov:
	python -m pytest backend/tests --cov=backend --cov-report=term-missing --cov-report=html

# Lint (if ruff/flake8 configured)
lint:
	@echo "No linter configured yet. Add ruff/flake8 if needed."

# Build frontend
build-frontend:
	cd frontend && npm run build

# Build backend (import check)
build-backend:
	python -c "import backend.main; print('Backend import OK')"

# Full build
build: build-backend build-frontend

# Tauri desktop shell build (requires Rust + MSVC)
tauri-build:
	npx --prefix frontend tauri build --no-bundle

# Stage an isolated clean runtime/backend payload from source only
stage-clean-runtime:
	python scripts/stage_clean_runtime.py --allow-network

# Generate the alternate Tauri config that points at the isolated payload
generate-clean-installer-config: stage-clean-runtime
	python scripts/generate_clean_installer_config.py --output-dir build/clean-installer --resources-dir build/staging/clean-runtime-payload

# Isolated clean source build validation (no tracked src-tauri/resources dependency)
clean-source-build: generate-clean-installer-config
	@echo "Isolated clean installer configuration generated at build/clean-installer/tauri.conf.json"

# Build the desktop shell using the isolated clean source configuration (dev/unverified)
tauri-clean-build: clean-source-build
	npx --prefix frontend tauri build --no-bundle --config build/clean-installer/tauri.conf.json

# Clean build artifacts
clean:
	rm -rf frontend/dist frontend/node_modules/.cache
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".coverage" -exec rm -rf {} + 2>/dev/null || true

# Run smoke test against running sidecar
smoke:
	python scripts/smoke_test.py --all

# Start development sidecar
dev-sidecar:
	python scripts/launch_sidecar.py --port 8000 --dev --no-open

# Help
help:
	@echo "Available targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-20s %s\n", $$1, $$2}'

## Help descriptions
test: ## Run the full backend test suite
test-fast: ## Run the backend suite without the slow, live-GUI and network tests
test-acceptance: ## Run the acceptance sub-suite (backend/tests/acceptance)
test-unit: ## Run the backend suite without the acceptance sub-suite
test-gui: ## Run the tests that need a live interactive desktop
test-network: ## Run the provider-backed E2E tests (skipped without credentials)
check-test-selections: ## Verify that every -m expression selects a non-empty set
test-file: ## Run a single test file (FILE=...)
test-fn: ## Run a single test function (FILE=... FN=...)
test-cov: ## Run tests with coverage report
lint: ## Run linter (if configured)
build: ## Build frontend + backend
clean: ## Clean build artifacts
smoke: ## Run smoke test against running sidecar
dev-sidecar: ## Start development sidecar
tauri-build: ## Build Tauri desktop shell (requires Rust + MSVC)
