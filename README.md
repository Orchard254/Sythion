# Sythion (Synth) - Trusted Runtime Core for a Larger Intelligence System

> This repository is the **trusted local runtime core** of a larger
> intelligence system. It is not the whole intelligence stack by itself.

## The goal

The long-term vision is Synth: an intelligent, always-available AI companion,
inspired by the helpful AIs in movies, running in its own isolated system. Synth
is intended to converse, remember useful information, understand goals, make
plans, and help carry them out while keeping the user informed and respecting
appropriate permissions and safety boundaries.

This repository builds a trusted Windows foundation for that vision: the
Companion and Control Center, together with the runtime that controls actions,
records activity, and checks results. Conversation, memory, planning, and agent
subsystems exist in the code, but the complete vision is not finished or proven
to work end to end. The details of future isolation and autonomy remain to be
worked out.

[![Backend tests](https://img.shields.io/badge/backend%20tests-1569%20passed%20%2F%2045%20skipped-green)]()
[![Boundary](https://img.shields.io/badge/boundary-runtime%20core-blue)]()
[![Platform](https://img.shields.io/badge/platform-Windows%2010%2F%2F11-lightgrey)]()

## Architecture principle: reasoning is a system resource

**Synth is the persistent system; an LLM is a replaceable reasoning processor,
analogous to a CPU rather than the whole computer.** The runtime owns goals,
memory, commitments, scheduling, capabilities, permissions, work state and
evidence. It assembles relevant authorized context, invokes a model for bounded
reasoning when useful, validates the result, and controls any subsequent action.
An available Synth need not make continuous model calls.

This is the **target architecture**, not a claim that the existing Assistant,
task and goal flows already share one scheduler or that all autonomous
capabilities are implemented. A model's response is not execution evidence or
authenticated user consent.

See [reasoning architecture](SYNTH_REASONING_ARCHITECTURE.md) for the detailed
processor boundary and incremental acceptance plan, and
[project understanding](SYNTH_PROJECT_UNDERSTANDING.md) for product intent versus
observed implementation.

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
lives in the source tree, tests, and runtime behavior.

## What this repo is not

This repository is **not** the whole future intelligence system. The broader
vision may include richer conversation, planning, memory, and multi-device
agency, but those layers must sit above this runtime rather than bypassing it.

## Source of truth

Source code, tests, scripts, configuration and verified runtime behavior are
authoritative for implemented behavior. The README and linked design documents
record intent and evidence with explicit limitations; documentation alone is
not proof that a capability is shipped.

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

See the test commands below for the current baseline.

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

See the authoritative configuration definitions in
[`backend/core/config.py`](backend/core/config.py) and the active runtime
settings paths; configuration must be verified against current source.

Important current rules:

- production secrets should be handled through the runtime's write-only secret
  flow / Windows secret storage, not by treating `.env` as the production path;
- onboarding should select an exact authenticated model ID rather than relying
  on a hard-coded model string;
- approved file roots and voice/privacy settings are part of the runtime
  boundary and should be treated as first-class configuration.

## Recorded test baseline

> This is a recorded baseline from the noted measurement date, not a fresh
> verification. For current state, use the source and tests; the counts
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
