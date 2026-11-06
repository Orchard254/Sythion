# Historical Reports (Archive)

> **Historical archive only.** This file consolidates engineering history that
> was formerly scattered in three root-level Markdown reports. It is **not** the
> current status, release readiness, test baseline, architecture authority, or
> task plan.
>
> Current authority remains:
> `docs/ARCHITECTURE.md`, `docs/INTELLIGENCE_ARCHITECTURE.md`,
> `docs/CODEBASE_BOUNDARIES.md`, `docs/PROJECT_STATUS.md`,
> `docs/ROADMAP.md`, `docs/DECISIONS.md`, `docs/QUALITY_GATES.md`,
> `docs/OPERATIONS.md`, and `docs/acceptance-test-map.md`.
>
> The full, unedited original reports are preserved in Git history. No separate
> backup copies are maintained.

## Provenance

This archive consolidates content formerly held in these root-level reports:

- `FINAL_STATUS.md`
- `PHASE3_REPORT.md`
- `PHASE13B_VERIFICATION_REPORT.md`

An additional historical verification snapshot is retained separately as
[PHASE13B_ACCEPTANCE.txt](PHASE13B_ACCEPTANCE.txt). It is historical-only evidence and is not
current project status, release readiness, or test execution evidence.

Use this file only to understand historical evolution and discarded or
superseded implementation snapshots. Do not use it to infer present-day runtime
behaviour, current counts, or release readiness.

## Early runtime-core milestones (formerly `FINAL_STATUS.md`)

The earliest report described Sythion as a local runtime core with LLM task
orchestration, GUI automation, and a voice pipeline. Its useful historical
points are:

- The prototype was organized around a task runner, model engine, tool execution,
  SQLite ledger, and a browser dashboard.
- Reported tool categories included system, file, application, package, GUI, and
  voice operations.
- Claimed security controls included:
  - single active decision enforcement;
  - repair limiting;
  - per-task action budgets;
  - lease management;
  - immutable/event-sourced task auditing;
  - per-tool policy metadata;
  - post-execution verification;
  - dangerous-hotkey blocking;
  - voice activity detection;
  - loopback-only API binding.
- Phase 3 follow-up work was tracked at that time for crash recovery,
  interrupted-task resumption, local voice integration, credential storage, and
  dashboard/timeline work.

The original report also contained status tables, line counts, test totals, and
deployment steps. Those details are intentionally omitted here because they are
historical snapshots and can mislead when compared with the current repository.

## Phase 3 historical work (formerly `PHASE3_REPORT.md`)

The Phase 3 report described two broad areas of historical work:

### Crash recovery

- Added task/lease tracking so a stale runner lease could be detected.
- Marked a task `interrupted` after stale-lease detection.
- Recorded audit events for interruption.
- Exposed a task-resume path that re-observed before continuing.
- Documented the intended crash model: crash, lease expiry, stale-lease
  detection, interruption, manual or guided resumption.

### Voice pipeline

- Added platform-specific voice processing components, including:
  - WebRTC VAD with energy-based fallback;
  - Whisper STT with Windows and Linux/macOS backends;
  - Windows SAPI5 TTS through `pyttsx3`;
  - cross-platform system TTS fallbacks;
  - browser microphone capture/transcription flow in the dashboard;
  - task resume controls in the dashboard.

Those details are historical implementation context. Current voice behaviour,
dependencies, model selection, and runtime policy are defined by the current
source tree and authoritative docs.

## Phase 13B historical verification (formerly `PHASE13B_VERIFICATION_REPORT.md`)

The Phase 13B report recorded a design decision and verification narrative for
separating conversational runtime from task execution. Its historically useful
content was the invariant and routing model:

- Ordinary conversation was routed to a dedicated conversation runtime instead
  of creating a task.
- Conversation responses were persisted without a task ID.
- The invariant `CONVERSATION != TASK` was used to protect learning evidence,
  budgets, leases, objectives, and post-task intelligence from conversational
  turns.
- Non-conversation interaction types, such as informational, action, and goal
  requests, continued to create tasks and enter the protected runtime.

The original report claimed a complete production-ready verification. That
assertion is historical only and must not be treated as current verification.
Confirm present behaviour from current source, routes, services, tests, and
authoritative docs instead.

## How to use this archive

- Read this file for background and migration history only.
- For current behaviour, use source code, tests, and authoritative docs.
- Do not carry forward historical status assertions, test totals, readiness
  claims, platform-specific setup shortcuts, or obsolete implementation
  details into new work unless re-verified against the current tree.
