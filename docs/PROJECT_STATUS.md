# Sythion (Synth) Project Status

## Status summary

Sythion is being developed as the **trusted runtime core** of a larger
intelligence system.

This repository currently provides:

- a Tauri-hosted local desktop surface and Python/FastAPI sidecar;
- an authenticated local control boundary;
- a durable SQLite task/event ledger;
- bounded tool execution with policy and verification;
- cancellation, replay, retention, and recovery primitives;
- voice and dashboard support within the local runtime boundary;
- early intelligence-layer scaffolding such as conversation state,
  memory consolidation, a first durable objective/plan work-state layer,
  and CLI-visible work-state inspection.

## Product surfaces

The intended product has two complementary surfaces. The runtime serves both;
detailed supervision belongs in the Control Center, not the Companion.

- Companion: unobtrusive always-available presence for typed or spoken
  interaction, editable speech transcription before submission, and clear
  listening/thinking/speaking/available states.
- Control Center: operational surface for tasks, goals, plans, progress,
  agent/specialist activity, permissions, tools, memory, settings, and
  execution evidence.

Visual intent: refined dark neumorphism with graphite surfaces,
mint accents, and strong visibility for warnings and security states.
The Companion communicates presence through animation rather than branding.

## Current architectural source of truth

Use these documents in order:

1. [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) — runtime boundary and execution contract
2. [`docs/INTELLIGENCE_ARCHITECTURE.md`](INTELLIGENCE_ARCHITECTURE.md) — layered system framing above the runtime
3. [`docs/CODEBASE_BOUNDARIES.md`](CODEBASE_BOUNDARIES.md) — current module classification and refactor map
4. [`docs/PROJECT_STATUS.md`](PROJECT_STATUS.md) — current verified repo state
5. [`docs/ROADMAP.md`](ROADMAP.md) — near-term priorities and scope boundaries for this repo
6. [`docs/DECISIONS.md`](DECISIONS.md) — compact index of active architectural decisions
7. [`docs/QUALITY_GATES.md`](QUALITY_GATES.md) — definition of professional change quality in this repo
8. [`docs/OPERATIONS.md`](OPERATIONS.md) — startup, smoke-test, and validation runbook
9. [`docs/acceptance-test-map.md`](acceptance-test-map.md) — release gates and acceptance criteria

Older reports such as `FINAL_STATUS.md`, `PHASE3_REPORT.md`, and
`docs/PROGRESS_REPORT.md` are historical context only.

## Verified test baseline

- Full backend suite: **1569 passed, 45 skipped** (1648 collected)
- Acceptance suite: **191 passed, 2 skipped**

Measured on CPython 3.14.7 / Windows by
`python scripts/measure_test_baseline.py` on 2026-10-20.
CI pins Python 3.11.

The two skipped acceptance tests are the known symbolic-link privilege skips
in `backend/tests/acceptance/test_canonical_file_checks.py`.

`python scripts/measure_test_baseline.py --check` re-measures and fails if
this document stops agreeing with the measurement.

## Release readiness

The repo is **not** represented as a finished general intelligence system and
should not be described as a completed Phase 3 release.

What is true today:

- the runtime core is real, tested, and increasingly hardened;
- the Tauri shell builds in CI;
- the backend suite is broad and passing;
- the interactive clean-machine release gate is still a manual step;
- higher-order intelligence ambitions are being separated cleanly from the
  trusted runtime contract.

## Current direction

Near-term work should continue to professionalize the repo by:

- keeping docs aligned with actual runtime behavior;
- separating runtime-core modules from higher-order intelligence modules;
- preserving verified architectural decisions in project memory;
- strengthening release, packaging, and operator workflows;
- maturing the new durable objective/plan layer so long-horizon work state is
  explicit and auditable;
- deepening intelligence-layer behavior without weakening runtime guarantees.

## Practical reading guide for future contributors / LLMs

If you need to understand the repo quickly:

1. Read `docs/ARCHITECTURE.md`
2. Read `docs/INTELLIGENCE_ARCHITECTURE.md`
3. Read `docs/ROADMAP.md`
4. Read `docs/DECISIONS.md`
5. Read `docs/QUALITY_GATES.md`
6. Read `docs/OPERATIONS.md`
7. Run or inspect `docs/TESTING.md`
8. Treat older milestone reports as historical snapshots, not authority
