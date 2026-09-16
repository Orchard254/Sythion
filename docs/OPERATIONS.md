# Sythion Operations Runbook

## Purpose

This runbook captures the practical operator/developer workflows for this
repository.

Use it for:

- starting the local sidecar in development;
- running live smoke checks;
- validating the backend/frontend/Tauri shell;
- understanding the difference between packaged/runtime vs dev workflows.

For architecture, see [`ARCHITECTURE.md`](ARCHITECTURE.md).
For test coverage, see [`TESTING.md`](TESTING.md).
For configuration, see [`../CONFIGURATION.md`](../CONFIGURATION.md).

## Runtime modes

### 1. Packaged/runtime mode

This is the intended production path.

- the Tauri app is the entrypoint;
- the sidecar is launched through the handshake contract;
- the sidecar binds to loopback only;
- the control token is passed through the launcher/handshake flow;
- secrets belong in the runtime's write-only/Windows secret storage path.

### 2. Development mode

This is the practical engineering path.

- you may run the backend or launcher directly;
- local environment variables may still be used as development convenience;
- validation should still respect the runtime boundary and test gates.

## Common commands

### Backend tests

From repo root:

```bash
python -m pytest backend/tests -q
```

From `backend/`:

```bash
python -m pytest tests -q
```

### Acceptance-only tests

From `backend/`:

```bash
python -m pytest tests/acceptance -q
```

### Frontend build and lint

From `frontend/`:

```bash
npm run build
npm run lint
```

### Tauri shell build

From repo root:

```bash
npx --prefix frontend tauri build --no-bundle
```

## Start the sidecar in development

The closest dev path to the packaged handshake contract is:

```bash
python scripts/launch_sidecar.py --port 8000 --dev --no-open
```

What it does:

1. writes a fresh per-launch token file;
2. chooses a loopback port;
3. starts `uvicorn backend.main:app` with the handshake environment;
4. waits for the sidecar handshake file;
5. prints the resolved port and token prefix.

Useful variants:

```bash
python scripts/launch_sidecar.py --port 8000 --dev
python scripts/launch_sidecar.py --port 8010 --dev --no-open
```

## Live smoke testing

With a sidecar already running:

```bash
python scripts/smoke_test.py --all
```

Other options:

```bash
python scripts/smoke_test.py
python scripts/smoke_test.py --skip-model
```

What it checks:

- backend health;
- DB connectivity;
- Cloud model configuration;
- live task completion path;
- optional `system_time` tool round trip.

## Control-token behavior

The smoke test looks for the control token in this order:

1. `SYTHION_CONTROL_TOKEN` env var;
2. `%LOCALAPPDATA%\Sythion (Synth)\token.json`;
3. legacy `Sythion (Synth)_CONTROL_TOKEN` env var for older local workflows;
4. legacy `~/.sythion/control_token`.

The launcher writes the token file for the current dev/runtime session.

## Windows/shell note for this workspace

When running tests from the host shell in this environment, the path style that
has been verified to work is:

```bash
PYTHONPATH=/c/Ai/sythion:/c/Ai/sythion/backend python -m pytest sythion/backend/tests -q
```

This is a workspace-specific execution detail, not a product requirement.

## Recommended validation sequence after meaningful backend changes

1. run focused tests near the changed module(s);
2. run the full backend suite;
3. if startup/runtime behavior changed, run the live smoke test;
4. update docs and project memory if architectural truth changed.

## Current verified baseline

Most recently verified in this workspace:

- full backend suite: **692 passed, 32 skipped**;
- acceptance suite: **196 passed, 2 skipped**;
- live smoke script: documented as **6/6 live** in `TESTING.md`.

## Manual/operator gates that still matter

These are still important and should be reported honestly:

- the interactive clean-machine release run remains a manual gate;
- warning noise from some dependency/test-client deprecations still exists;
- production usage should prefer the packaged runtime path over ad-hoc dev
  launching.

## Rule for updating this file

Update this runbook when:

- a startup command changes;
- the smoke-test path changes;
- the shell/path quirks for verified execution change materially;
- the recommended validation flow changes.

Keep it operational and concise.
