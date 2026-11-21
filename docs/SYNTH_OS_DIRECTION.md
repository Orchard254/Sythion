# Synth as an always-on system (design direction, 2026-11-19)

This records the owner's direction for evolving Synth from a desktop app into an
always-on system. It is a design note, not an acceptance claim.

## Owner decisions (2026-11-19)

1. **Host:** run on this Windows PC for now. If it proves reliable through real
   use, move it later to a dedicated always-on PC with internet access. No cloud
   core yet.
2. **Phone:** the phone is an endpoint, not the system, and it is a *full
   conversational client* — the way one reaches Gemini, ChatGPT, or Claude. It
   is not merely a briefing/approval console: you talk to Synth there, and that
   conversation is the same conversation you have on the desktop (one Core, one
   memory, one identity). The phone is a window into Synth, not a second Synth.
   Synth may also access and operate the phone itself only under granted
   permissions, because that is where the owner's chats live. The owner may be
   abroad and still talk to Synth, receive updates, and give it goals. Long-term
   example: Synth runs a company and prepares the CEO's reports.
3. **Trust:** progressive, one information at a time. Start with the minimum the
   secretary needs; as behaviour proves predictable, grant more. Eventually it
   may operate without asking. Trust is earned, never assumed.

## Target architecture

- **Synth Core** — always-on local process holding identity, durable state,
  memory, authority, and the trust ledger. The LLM is a replaceable reasoning
  processor the Core calls. No client, token, or model output is the brain.
- **Devices** — the Windows PC, the phone, and any future machines are endpoints
  with an identity, a trust level, granted permissions, capabilities, and a
  last-seen record. They are sensors and actuators, not the system.
- **Client API** — one client-agnostic surface used by the desktop, the
  companion overlay, and the phone. A client cannot widen its own permissions.
  `POST /assistant/turns` is already the conversational contract; the phone can
  consume the same endpoint. The briefing rides on that response, and a future
  morning brief is simply a proactive message in the same conversation
  timeline. The difference from the hosted assistants is that this chat is
  backed by a body: durable state, tools, devices, and evidence — not just text.
- **Core services** — briefing, monitoring, scheduling, recovery, permission /
  trust ledger, device registry, and the reasoning boundary.
- **Briefing service** — assembles what happened and what is blocked from
  authoritative local state. It is read-only and never grants authority.

## What exists today

The *doing* is largely present: 63 tools for files, browser, GUI, apps,
packages, memory, objectives and device status; goals with evidence and
blockers; durable wakes; an owner-gated bounded wake; read-only recovery review
packets; and a versioned authority-free reasoning contract.

**Index-state self-consistency invariant (2026-11-21):**
`DiscussionIndexManager.transition` never leaves a COMPLETE/INSUFFICIENT row
with a stale `error_code`, nor an error row with a stale `result_json`. A fresh
result clears the prior error, a fresh error clears the prior result, and
terminal-success states force-clear errors. Regression tests in
`test_grounded_discussion_index.py`.

**Discussion-context read invariant (2026-11-21):** named-context readers
return one row per distinct context — the membership whose exchange is newest
by `created_at` — and `limit` bounds contexts, not membership rows. A thread
that accrued follow-ups/instructions previously produced one row per membership
ordered by random UUID, so with many memberships the freshest correction was
silently excluded and coverage counted memberships as contexts. Regression test
in `test_discussion_contexts.py`.

**Diagnostic persistence honesty invariant (2026-11-21):**
`diagnose_failed_task` returns honest unavailability when the persistence
commit fails — the rollback removes the `task.diagnostic` evidence event, so
returning the diagnosis text would claim success for unpersisted work and hide
the failure. Regression test in `test_failed_task_diagnostic.py` forces the
final commit to fail after the model call.

**Personal-context framing invariant (2026-11-21):** the
`BEGIN PERSONAL CONTEXT (data)` / `END PERSONAL CONTEXT` guards must both
survive the 24-line bound in `render()`. The END guard was appended last and
sliced away whenever the body filled the budget, so subsequent KNOWN context
(e.g. GOAL_CONTEXT from the cognitive pipeline) was absorbed into the untrusted
data region. The final line slot is now reserved for the terminator. Regression
test in `test_phase7_memory.py`.

**Voice recording honesty invariant (2026-11-21):** when the owner enables the
recording setting, `SegmentResult.audio_bytes_deleted=False` must mean the raw
PCM was actually retained on the session (`recorded_audio`), with
`recorded_bytes` reflecting its real size. Previously the flag and byte counter
were set while the audio was discarded when the local went out of scope. Retained
audio is still hard-deleted at session end, so the privacy contract holds.
Regression test in `test_voice_pipeline.py`.

**Reasoning-accounting invariant (2026-11-21):** `dispatch_reasoning` records a
model as started only AFTER the durable "started" receipt is persisted. If that
receipt fails, no provider call happens, so `model_calls` must not increment,
no `_started` entry may leak, and no `MODEL_STARTED` event may be emitted —
otherwise the effectiveness report falsely claims "model activity without
verified useful work." Regression test in `test_reasoning_dispatch.py`.

**Model-selection evidence invariant (2026-11-21):** after capability and
availability filtering, and when the configured primary is not a candidate,
`ModelSelector.select` picks the best-evaluated model by walking the ranking
that `ModelEvaluator.compare` already sorted (pass_rate → avg_score → attempts
→ model_id). Iterating raw candidate order instead let a worse-evaluated model
with positive evidence beat a strictly better one while the audit still claimed
"highest evaluated evidence." Regression test in `test_phase6_model_selection.py`.

**Purge detach invariant (2026-11-21):** a complete-record purge detaches every
durable reference to the deleted task, including `PlanStep.linked_task_id` and
`PlanStep.evidence_task_id`. These were left dangling (SQLite does not enforce
the FK here), so goal execution binding, restart policy, work preview and
reasoning continuity could read a purged task as still present. Only the columns
matching the purged task are nulled — a step's reference to a different task is
preserved. Regression test in `test_retention.py`.

**Goal-completion authority invariant (2026-11-21):** the
`verify-criteria` endpoint records owner criteria verification but delegates
any completion to `GoalManager.complete_goal`. It never writes `status` itself.
Previously it counted only `pending/in_progress/blocked/failed` steps (omitting
the canonical `ready`/`running` open states) and set `completed` directly, so a
goal with an unstarted or actively running step could be forged to completed
over HTTP with no `goal.completed` ledger event or outcome capture. Regression
tests in `test_goals_api.py` cover both the forged-completion refusal and the
audited happy path.

**Wake in-flight key invariant (2026-11-21):** `_queued_keys` tracks only wakes
with real queued/in-flight work. A key is discarded in `_process`'s `finally`,
which covers both the queue path and `drain_durable`, so a wake resolved after
queue saturation leaves no permanent "ghost" key that would block every later
`submit()` for that key. A DB-deduplicated submission adds no key at all (there
may be no PENDING row to resolve). Regression assertions live in the
saturated-queue scenario of `test_phase45_hardening.py`.

**Bounded-wake recurrence invariant (2026-11-21):** every cycle of the
owner-gated bounded wake loop submits a distinct durable wake key
(`goal.wake:{goal}:{uuid}`). `submit_wake` deduplicates on key existence
regardless of the row's status, so a constant key would let cycle 1's completed
wake suppress cycles 2..N forever — the goal would never be re-woken. The UUID
suffix is unique across restarts because the wake table is durable. Regression
test uses the real `submit()` over two cycles (`test_owner_gated_bounded_wake.py`).

**Memory retention invariant (2026-11-21):** when stale-memory pruning is
over budget, it deletes the *stalest* entries first (ranked by `last_accessed_at
or updated_at`, ascending), never the freshest ones. The old newest-first slice
silently kept the oldest memories and discarded recent ones — a data-loss bug,
now fixed and regression-tested in `test_memory_consolidation.py`. Profile
entries are never pruned and at least `MEMORY_MIN_KEEP` entries always remain.
Pruning runs automatically after tasks when memory consolidation is enabled.

The *system* layer is missing: there is no device registry, no phone client, no
remote transport, no morning brief, and no progressive trust ledger. The
briefing layer is being built first because it is the user-facing foundation.

## Stages (each independently verifiable)

- **Stage 0 — briefing service (implemented 2026-11-20):** deterministic
  blocked-work briefing from local state; "heads up" card in the Assistant
  turn; no new remote surface, no new authority. Built:
  `backend/services/user_briefing.py` (read-only, bounded, sanitized,
  non-authorizing), `GET /api/v1/briefing`, and `briefing` on the Assistant
  turn response. Frontend `BriefingCard` renders the heads-up; every item is
  `authority: none`, `approved: false`, and the card has no approve control.
  Evidence: `test_user_briefing.py` (6 passed), `test_assistant_turn_api.py`
  (turn briefing test), `BriefingCard.test.tsx` (3 passed), 86 frontend tests
  pass, `tsc --noEmit` exit 0. Full backend suite pending.
- **Stage 1 — on-demand + morning brief (implemented 2026-11-20):** a read-only
  briefing endpoint and a scheduled local brief from app-local state. Built:
  `backend/services/morning_brief.py` composes a deterministic brief (goals and
  tasks finished since yesterday, blocked work, Main Agent state, registered
  devices (identity, trust level, last-seen) and local health) with no model call
  and no authority; `MorningBrief` persists exactly one brief
  per local date; `ensure_daily_brief` is idempotent. `GET /api/v1/briefing/morning`
  returns today's brief (stored or freshly built, never persisted by the read
  path). `backend/services/morning_brief_scheduler.py` is an opt-in
  (`morning_brief_enabled`, default OFF) loop gated on `morning_brief_local_hour`
  (default 8), wired into `main.py` lifespan with clean shutdown. The brief is
  attached to the Assistant turn as `briefing.morning_brief` and rendered as a
  read-only proactive card. Evidence: `test_morning_brief.py` (9 passed),
  `test_morning_brief_scheduler.py` (5 passed), `test_user_briefing.py` (6),
  `test_assistant_turn_api.py` (turn briefing), `MorningBriefCard.test.tsx` (2),
  78 frontend tests pass, `tsc --noEmit` exit 0. Full backend suite pending.

  **Device registry in the brief (2026-11-20):** a `Registered devices` section
  reports each device's name, id, trust level and last-seen from the durable
  registry (observation only — trust here is never used as authority by the
  brief). Evidence: `test_morning_brief.py` (11 passed incl. populated/empty
  device cases), affected set 90 passed / 7 skipped (11s).

  **Needs-reconciliation in the brief (2026-11-20):** a `Needs reconciliation`
  section surfaces uncertain remote actions and awaiting permission proposals
  from durable state (observation only — it never resolves an uncertain effect,
  clears a blocker, or approves anything). Evidence: `test_morning_brief.py`
  (13 passed incl. uncertain-action + pending-approval and empty cases),
  affected set 125 passed / 7 skipped (36s).

  **Recent device actions in the brief (2026-11-20):** a `Recent device actions`
  section reports the latest lifecycle states from `RemoteActionManager.list_recent`
  (read-only; a state here is never a decision or authority). Evidence:
  `test_morning_brief.py` (15 passed incl. populated/empty cases), affected set
  104 passed / 7 skipped (17s).

  **Delivery receipts in the brief (2026-11-20):** a `Delivery receipts` section
  reports per-session highest-ack high-water marks from the durable `RelayAck`
  state via `ack_summary` (read-only evidence; a receipt never proves an effect
  or grants authority). Evidence: `test_morning_brief.py` (17 passed incl.
  populated/empty cases), affected set 116 passed / 7 skipped (13s).
- **Stage 2 — device registry (implemented 2026-11-20):** `RegisteredDevice`
  records a stable, non-reversible local id, name, kind, first/last seen and
  capability domains. `backend/services/device_registry.py` upserts this machine
  at startup; re-registration refreshes observation fields but can NEVER raise
  trust. Trust levels are `observed`/`trusted`/`privileged`, set only through
  `PUT /api/v1/devices/{id}/trust` (an authenticated owner action, audited as
  `device.trust_changed`). `GET /api/v1/devices` and `/devices/local` expose the
  registry read-only; `DevicesCard` renders it in the Security screen with
  owner-only trust controls. Evidence: `test_device_registry.py` (8 passed) plus
  startup/health/lifespan/clean-build suites (58 passed, 7 skipped); frontend
  `DevicesCard.test.tsx` (3 passed), `tsc --noEmit` exit 0.
- **Stage 3 — trusted remote endpoint (transport-agnostic core implemented
  2026-11-20):** a remote client identifies as a registered device and presents a
  per-device credential (hash-only storage, revocable, returned once). Access
  requires BOTH an explicit owner grant AND a minimum trust level per capability
  (`briefing.read`/`agent.status` = observed, `devices.read`/`conversation.write`
  = trusted, `goals.submit`/`messages.read` = privileged). `POST
  /api/v1/remote/turns` is the same conversational contract as the desktop turn,
  reachable only after credential + `conversation.write` checks; rejected
  devices never reach the reasoning boundary. Credential issue/revoke and grant
  add/remove are audited owner actions. Evidence: `test_remote_access.py`
  (12 passed), `test_device_registry.py` (8), plus startup/health/clean-build
  suites (90 passed, 7 skipped, 17s).

  **Transport decision (owner delegated, 2026-11-20): first-party relay.** No
  third-party relay service is used — a third party would see chats and
  briefings in transit. The relay is a broker inside the Core: a registered,
  authenticated device opens a durable, device-bound message session
  (`POST /api/v1/remote/sessions`), polls messages and posts text
  (`GET/POST /api/v1/remote/sessions/{id}/messages`), and closes it. Sessions
  expire; a device can only reach its own; messages are durable so an offline
  phone receives what it missed. If internet reachability is wanted later, a
  tunnel simply sits in front of this relay — the protocol does not change.
  Built in `backend/services/relay.py` with `RelaySession`/`RelayMessage` models;
  `test_relay.py` (9 passed); affected set 90 passed / 7 skipped (18s).
  **Phone client UI (minimal, implemented 2026-11-20):** `PhoneRelayScreen` is a
  simple Remote route in the Control Center. It takes a device id + credential,
  opens a relay session, polls durable messages and posts text. The credential
  input is a password field; nothing in the screen can approve an action or
  clear a blocker. `relayService` in `services/api.ts` carries the device headers
  per request — no third party. Evidence: `PhoneRelay.test.tsx` (2 passed),
  affected App/nav set (14 passed), `tsc --noEmit` exit 0. Also fixed a mock
  regression in `App.pending-proposals.test.tsx` from Stage 2's `useDevices`.

  **Reachability is operational, not a code change.** `LocalControlBoundaryMiddleware`
  (`backend/core/auth.py:112-114`) deliberately rejects every non-loopback peer
  for `/api/` with 403. That boundary stays. A phone reaches the relay through
  a tunnel whose local end connects to `127.0.0.1:<port>` on this PC (for example
  an SSH tunnel or an owner-controlled WireGuard peer), so the backend still sees
  a loopback client and the device credential remains the authority. Binding the
  sidecar directly to `0.0.0.0` would break this boundary and is intentionally
  NOT done. No third-party relay service is used at any point.

  **Still open:** setting up that tunnel on the owner's network, and per-source
  message/news connectors (which need granted source access). Both need the
  owner's environment, not code.
- **Stage 4 — phone as endpoint (goal submission implemented 2026-11-20):**
  `POST /api/v1/remote/goals` lets a registered device give Synth a goal. It
  requires a valid device credential and the privileged `goals.submit` grant,
  and uses the exact same validated creation path as the local API (the goal
  creation logic was factored into a shared helper). The device supplies only
  the request, never authority over execution. Evidence: `test_remote_access.py`
  (14 passed) plus goal/turn/startup/health suites (178 passed, 7 skipped,
  24s). **Still open:** per-source connectors (messages, notifications, news),
  operating the phone, and conversational approval binding — these need source
  access and owner decisions, not just code. A free-floating "yes" in chat is
  never itself authority, and model output never becomes approval.
- **Stage 5 — dedicated host:** migrate the always-on Core to a separate
  internet-connected machine once local reliability is demonstrated.

## Non-negotiable invariants

- The LLM never holds authority, identity, or the deciding vote.
- Model output, UI flags, and tool claims never become approval.
- A conversational "yes" is the owner speaking, not the model — but it is only
  authority when bound to a specific, fresh, displayed proposal; an unbound
  "yes" is not approval.
- A briefing is read-only and contains no authorizing language.
- A client cannot expand its own permissions; grants are owner actions, audited.
- The default when uncertain is to ask, never to guess or auto-resume.
- Trust grows only through recorded, predictable behaviour — never silently.
