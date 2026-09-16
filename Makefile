# Sythion (Synth) - Test & Development Commands
# Usage: make <target>
#   make test          - run full backend test suite
#   make test-fast     - run backend tests, skip slow/flaky/integration
#   make test-acceptance - run only acceptance tests
#   make test-unit     - run only unit tests (skip acceptance)
#   make test-gui      - run GUI tests only
#   make test-network  - run network tests only
#   make lint          - run linting (if configured)
#   make build         - build frontend + backend
#   make clean         - clean build artifacts

.PHONY: test test-fast test-acceptance test-unit test-gui test-network lint build clean

# Default test command - full backend suite
test:
	python -m pytest backend/tests -q

# Fast test: skip slow, flaky, integration, gui, network tests
test-fast:
	python -m pytest backend/tests -q -m "not slow and not flaky and not integration and not gui and not network"

# Acceptance tests only
test-acceptance:
	python -m pytest backend/tests/acceptance -q

# Unit tests only (skip acceptance)
test-unit:
	python -m pytest backend/tests -q -m "not acceptance"

# GUI tests only
test-gui:
	python -m pytest backend/tests -q -m gui

# Network tests only
test-network:
	python -m pytest backend/tests -q -m network

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
test: ## Run full backend test suite
test-fast: ## Run backend tests, skip slow/flaky/integration/gui/network
test-acceptance: ## Run only acceptance tests
test-unit: ## Run only unit tests (skip acceptance)
test-gui: ## Run GUI tests only
test-network: ## Run network tests only
test-file: ## Run a single test file (FILE=...)
test-fn: ## Run a single test function (FILE=... FN=...)
test-cov: ## Run tests with coverage report
lint: ## Run linter (if configured)
build: ## Build frontend + backend
clean: ## Clean build artifacts
smoke: ## Run smoke test against running sidecar
dev-sidecar: ## Start development sidecar
tauri-build: ## Build Tauri desktop shell (requires Rust + MSVC)