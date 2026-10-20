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

Two invocations are verified to work on this machine (Windows, CPython
3.14.7):

```bash
# from the repository root
python -m pytest backend/tests -q

# from the backend/ directory
python -m pytest tests -q
```

Both collect the same suite. The second one is what the CI backend job runs
(`.github/workflows/ci.yml`, job `backend-tests`, step "Run backend test
suite").

An earlier revision of this section recorded a `PYTHONPATH=` invocation
naming a checkout that does not exist in this repository. It was removed
rather than kept as a "verified" instruction, because a verified instruction
that cannot work is worse than no instruction.

## Recommended validation sequence after meaningful backend changes

1. run focused tests near the changed module(s);
2. run the full backend suite;
3. if startup/runtime behavior changed, run the live smoke test;
4. update docs and project memory if architectural truth changed.

## Current verified baseline

- Full backend suite: **1569 passed, 45 skipped** (1614 collected)
- Acceptance suite: **191 passed, 2 skipped**

Measured on CPython 3.14.7 / Windows by
`python scripts/measure_test_baseline.py` on 2026-10-20.
CI pins Python 3.11.

The live smoke test (`python scripts/smoke_test.py --all`) is a MANUAL check:
it needs a running sidecar, and no executed run of it is recorded in this
repository. It is not a result this repository can quote, and it is not part
of the measured baseline above.

`python scripts/measure_test_baseline.py --check` re-measures and fails if
this document stops agreeing with the measurement.

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
