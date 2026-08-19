# Sythion (Synth) - Trusted Runtime Core for a Larger Intelligence System

> This repository is the **trusted local runtime core** of a larger
> intelligence system. It is not the whole intelligence stack by itself.

[![Backend tests](https://img.shields.io/badge/backend%20tests-683%20passed%20%2F%202%20skipped-green)]()
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

- Full backend suite: **692 passed, 32 skipped** (32 skipped = 27 power/network real-system tests + 3 volume system-audio tests + 2 symlink privilege tests)
- Acceptance suite: **196 passed, 2 skipped**

The two skips are the known symlink-privilege skips in
`backend/tests/acceptance/test_canonical_file_checks.py`.
