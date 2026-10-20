# Sythion (Synth) - Trusted Runtime Core for a Larger Intelligence System

> This repository is the **trusted local runtime core** of a larger
> intelligence system. It is not the whole intelligence stack by itself.

[![Backend tests](https://img.shields.io/badge/backend%20tests-1569%20passed%20%2F%2045%20skipped-green)]()
[![Boundary](https://img.shields.io/badge/boundary-runtime%20core-blue)]()
[![Platform](https://img.shields.io/badge/platform-Windows%2010%2F%2F11-lightgrey)]()

## What this repo is

Sythion provides the execution-facing local runtime on Windows 10/11:

- Tauri desktop shell + React dashboard
- one fixed Python/FastAPI sidecar
- authenticated local control boundary
- durable task/event ledger
- policy-gated tool execution
- evidence and verification after actions
- cancellation, replay, retention, and recovery primitives
- local voice and dashboard flows inside the runtime boundary

## What this repo is not

This repository is **not** the whole future intelligence system. The broader
vision may include richer conversation, planning, memory, and multi-device
agency, but those layers must sit above this runtime rather than bypassing it.

## Source of truth

Start here:

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — runtime contract
- [`docs/INTELLIGENCE_ARCHITECTURE.md`](docs/INTELLIGENCE_ARCHITECTURE.md) — system layering
- [`docs/CODEBASE_BOUNDARIES.md`](docs/CODEBASE_BOUNDARIES.md) — module classification and refactor map
- [`docs/PROJECT_STATUS.md`](docs/PROJECT_STATUS.md) — current verified status
- [`docs/ROADMAP.md`](docs/ROADMAP.md) — near-term priorities and repo scope boundaries
- [`docs/DECISIONS.md`](docs/DECISIONS.md) — compact index of active architectural decisions
- [`docs/QUALITY_GATES.md`](docs/QUALITY_GATES.md) — definition of a professional change in this repo
- [`docs/OPERATIONS.md`](docs/OPERATIONS.md) — startup, smoke-test, and validation runbook
- [`docs/acceptance-test-map.md`](docs/acceptance-test-map.md) — release gates

## Development

### Backend tests

From repo root:

```bash
python -m pytest backend/tests -q
```

From `backend/`:

```bash
python -m pytest tests -q
```

See [`docs/TESTING.md`](docs/TESTING.md) for the current baseline and related
commands.

### Frontend

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

## Configuration

See [`CONFIGURATION.md`](CONFIGURATION.md).

Important current rules:

- production secrets should be handled through the runtime's write-only secret
  flow / Windows secret storage, not by treating `.env` as the production path;
- onboarding should select an exact authenticated model ID rather than relying
  on a hard-coded model string;
- approved file roots and voice/privacy settings are part of the runtime
  boundary and should be treated as first-class configuration.

## Historical reports

The following files are retained for historical context only and should not be
used as the current status baseline:

- `FINAL_STATUS.md`
- `PHASE3_REPORT.md`
- `docs/PROGRESS_REPORT.md`

## Current verified baseline

- Full backend suite: **1569 passed, 45 skipped** (1614 collected)
- Acceptance suite: **191 passed, 2 skipped**

Measured on CPython 3.14.7 / Windows by
`python scripts/measure_test_baseline.py` on 2026-10-20.
CI pins Python 3.11.

The two skipped acceptance tests are the known symlink-privilege skips in
`backend/tests/acceptance/test_canonical_file_checks.py`.

The block above is a measurement, not a typed number:
`python scripts/measure_test_baseline.py --check` re-measures and fails if
this document and the other baseline documents stop agreeing with the
measurement.
