# Sythion (Synth) Project Status

## User-outcome checklist â€” updated 2026-11-03

**Goal:** A dependable, always-available AI on Windows initially, using cloud
models and current internet information, with continuity and useful autonomous
work inside user-controlled access toggles. Long term: a genuinely isolated
system. Irreversible actions are excluded. Availability does not require
continuous inference.

This checklist is the current progress summary. Older descriptions and suite
counts below are historical evidence, not verification of the current source state.
Check a user outcome only after a representative end-to-end demonstration;
source implementation, focused tests, integration, and deployment are distinct.

| User outcome | Current status | Evidence needed to finish |
|---|---|---|
| I can converse naturally and give follow-ups without losing their order or task. | Partial: focused conversation/follow-up work exists; integrated experience not reverified. | A real multi-turn project with corrections, interruption, and resumption. |
| Synth remembers my project accurately across restarts, without inventing personal facts or authority. | Partial; durable history foundations exist, memory authority and recall ACL gaps remain. | Restart continuity, controlled durable-memory writes, and principal-scoped recall tests. |
| Synth uses cloud models and fresh web information without leaking my private data. | Partial: provider integration exists; safe research and egress behavior not demonstrated end to end. | Current sourced research, scoped data handling, outage and credential-isolation checks. |
| I can leave Synth available on Windows all day and reach it when needed. | Not demonstrated; no verified always-on deployment or soak run. | Measured idle/active soak, sleep/network recovery, clean shutdown and supervised restart. |
| Synth carries a goal forward in bounded background sessions without constant supervision. | Partial: wake/session foundations exist; protected work remains blocked. | One useful long-running goal with measured bounds, durable progress and honest blockers. |
| My access toggles are real permissions, and turning one off prevents new covered actions. | Blocked: authenticated operator issuance and enforcement are unimplemented. | Principal/scope binding, revocation fence, resource-bound enforcement, adversarial tests. |
| Pause/stop works, and crashes never silently repeat uncertain actions. | Partial: controls/recovery primitives exist; cross-process fencing and uncertainty gaps remain. | Process-kill and cancellation tests around actual effect windows without bypassing authorization. |
| I can tell what is available, running, completed, blocked or uncertain. | In progress: integrated UI/lifecycle experience under verification. | Independent review and verification, then conflict-safe integration and user-facing observation. |
| Synth stays predictable during cloud outages and within resource/cost limits. | Partial: bounded retry foundations exist; runtime-wide resilience/budgets incomplete. | Outage/rate-limit/cancellation tests and measured session/daily limits. |
| Installation and updates are reproducible and do not damage unrelated files. | Blocked: destructive staging, runtime/provenance and clean-machine release gaps remain. | Non-destructive packaging, dependency authenticity and clean-machine validation. |
| Synth is genuinely isolated from the rest of my system except through granted interfaces. | Not implemented as the target isolation boundary. | Verified OS/process sandbox and controlled filesystem/network interfaces. |

### Verified foundations, not completed product promises

- [x] Focused source memory-tool containment: independently accepted 10-test evidence.
- [x] Response-engine validation: independently accepted 67-test evidence.
- [x] Transcript persistence fixture corrections: independently accepted 2-test evidence.
- [x] Packaging secret-classification/copy callback fixes: independently accepted 23-test evidence.

These results cover their specific changes only, not the whole current source,
packaged application, or release.

### Current work and exact next step (2026-11-06)

Functional-first scope: open the app, complete the useful 30-minute Python
checklist, apply a natural 15-minute correction, and keep persisted replies
visible and recoverable.

Independent evidence so far:
- Frontend `Assistant.test.tsx`: 51 passed (observed by coordinator).
- Backend `test_task_api_response.py` and `test_assistant_turn_api.py`: 42 passed
  (verified by coordinator; isolated DB/model mocks only, no provider, server
  restart, live database, or installed-app claim).
- Interpret timestamp-tie regression fixed in `backend/api/routes.py`: equal
  `ConversationTurn.created_at` values now break ties by descending ID, matching
  the canonical assistant-turn path, preventing misordered recap context for
  repeated turns. Other model-side JSON/conversation degradation remains a
  separate, unfixed defect.
- Real isolated source/model evidence observed through `http://localhost:3000`:
  - clean setup inference returned HTTP 502 (empty model body);
  - setup gate no longer hardcodes a 16-token cap and now uses the provider's
    established normal default. Controlled Beam probe showed `done_reason=length`
    and empty content at a `16` token cap (`16` eval tokens; 52 thinking
    characters), while the same `Reply with OK` prompt returned non-empty `OK`
    with a `256` cap. Focused regression and existing onboarding tests now
    pass (`30 passed` in
    `backend/tests/test_onboarding_inference_response_shape.py` and
    `backend/tests/test_setup_readiness.py`, 16 warnings).
  - restarted actual isolated endpoint had one HTTP 502 ReadError transport
    failure during inference, so live setup readiness remains unverified.
  - first actual Beam assistant answer returned HTTP 200 and was persisted;
  - second follow-up returned HTTP 503 `json_malformed` (not fixed by the
    timestamp-tie change).
- The user's reported historical later-chat failure remains evidence of a
  repeated-turn/context degradation issue; it is not explained by the fixed
  timestamp-ordering defect.
- The user's reported historical later-chat failure remains evidence of a
  repeated-turn/context degradation issue; it is not explained by the fixed
  timestamp-ordering defect.
- Source UI observed through `http://localhost:3000` only, not an installed
  application, release artifact, deployment, or end-to-end completed workflow.

Not demonstrated: end-to-end follow-up success after correction, installed
application, release artifact, 24-hour availability, soak/crash recovery, or
live-provider robustness. Focused tests are bounded evidence, not full product
proof, and no user outcome is marked complete here.

Agents report directly and share memory; this existing status document is the
single human-readable checklist, not a new orchestration framework. Update it
when outcomes or verification change. No persistent agent runner, push, release,
installation, or 24/7 operation is claimed.

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
  and CLI-visible work-state inspection;
- durable complete pure-conversation history (`conversation_exchanges`), exposed
  as `GET /api/v1/conversations` with cursor pagination. These records are
  deliberately separate from truncated episodic summaries, which remain
  compression context rather than a transcript.
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

1. [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) â€” runtime boundary and execution contract
2. [`docs/INTELLIGENCE_ARCHITECTURE.md`](INTELLIGENCE_ARCHITECTURE.md) â€” layered system framing above the runtime
3. [`docs/CODEBASE_BOUNDARIES.md`](CODEBASE_BOUNDARIES.md) â€” current module classification and refactor map
4. [`docs/PROJECT_STATUS.md`](PROJECT_STATUS.md) â€” current verified repo state
5. [`docs/ROADMAP.md`](ROADMAP.md) â€” near-term priorities and scope boundaries for this repo
6. [`docs/DECISIONS.md`](DECISIONS.md) â€” compact index of active architectural decisions
7. [`docs/QUALITY_GATES.md`](QUALITY_GATES.md) â€” definition of professional change quality in this repo
8. [`docs/OPERATIONS.md`](OPERATIONS.md) â€” startup, smoke-test, and validation runbook
9. [`docs/acceptance-test-map.md`](acceptance-test-map.md) â€” release gates and acceptance criteria

## Verified test baseline

- Full backend suite: **1583 passed, 45 skipped** (1628 collected)
- Acceptance suite: **191 passed, 2 skipped**

Note: this is a recorded historical snapshot, not a freshly verified baseline. Counts differ across docs; see the [documentation index](README.md) for the known discrepancy. Run the maintained verification before relying on these counts.

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

## Conversation history and retention

- `ConversationExchange` stores each complete user message and assistant reply.
- `GET /api/v1/conversations` returns newest-first, complete exchanges only.
  Use `limit` (1-200) and the opaque `next_cursor` for older pages. Legacy
  `conversation_turns` are not transcripts and are excluded.
- Retention is manual and owner-triggered through `POST /api/v1/retention/purge`.
  There is no automatic startup purge. The configured window controls what a
  manual purge removes, and the response reports `purged_turns` and
  `purged_exchanges` separately.
- The Assistant screen no longer treats a conversation response as a task and
  no longer loses it when the polled task list refreshes.
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
