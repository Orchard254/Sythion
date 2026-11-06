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

## Product surfaces

Sythion is intended to have two complementary user-facing surfaces. The runtime
in this repository serves both; detailed supervision belongs in the Control
Center, not in the floating Companion.

- **Companion** — unobtrusive, always-available presence for typed or spoken
  interaction, editable speech transcription before submission, and clear
  listening/thinking/speaking/available states. No persistent chat-history
  panes or detailed task logs.
- **Control Center** — operational surface for tasks, goals, plans, progress,
  agent/specialist activity, permissions/policies/tools/capabilities, memory,
  settings, and execution evidence.

Design intent: a refined dark neumorphism with graphite surfaces, subtle raised
and inset depth, mint accents, and strong visibility for warnings and security
states. The Companion communicates presence through its animation and does not
display the Sythion name or logo.

Some surface and finish details remain in progress. Current verified state
lives in `docs/PROJECT_STATUS.md` and `docs/ROADMAP.md`.

## What this repo is not

This repository is **not** the whole future intelligence system. The broader
vision may include richer conversation, planning, memory, and multi-device
agency, but those layers must sit above this runtime rather than bypassing it.

## Source of truth

Documentation authority note: only the documents listed below in this section,
plus `docs/PROJECT_STATUS.md` and `docs/ROADMAP.md`, are current runtime
references. Other files under `docs/` are historical/aspirational design records
and may describe layers V1 explicitly excludes (multi-agent, persistent agents,
vector memory, autonomous monitoring, autonomous planning that bypasses the
runtime). Verify scope and behaviour against source and tests; a banner marks the
most commonly misleading ones. `docs/README.md` is structural grouping only.

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
- [`Documentation index — current references, plans, and historical records`](docs/README.md)

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

## Recorded test baseline

> This is a recorded baseline from the noted measurement date, not a fresh
> verification. For current state, use `docs/PROJECT_STATUS.md`; the counts
> here may conflict with other documents.

- Full backend suite: **1569 passed, 45 skipped** (1648 collected)
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
