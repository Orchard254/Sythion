# Android device integration — integration contract (2026-11-20)

This is the **contract before implementation** requested by the owner's
"Sythion Android Integration — Architecture Proposal". It is based on a
read-only audit of the current repository, not on the older inventory cited in
that proposal. **No Android code is written here.**

## 1. Verified current state (what actually exists)

The older inventory's claim that "device registry and messaging capabilities
were not yet available" is **no longer current**. Both were built into this
repository on 2026-11-20 and are committed to `master`:

| Capability | Current implementation |
|---|---|
| Device identity | `RegisteredDevice` (`backend/models/database.py` L975–992); `backend/services/device_registry.py` — stable hashed id, last-seen, trust ladder `observed/trusted/privileged`, owner-only trust writes. |
| Device credentials / grants | `DeviceCredential` + `DeviceGrant` (database.py L1026–1052); `backend/services/remote_access.py` — hash-only credential returned once, revocable, additive owner grants, six capability floors, `check_access()` = grant AND trust ≥ floor. |
| Messaging / relay | `RelaySession` + `RelayMessage` (database.py L995–1023); `backend/services/relay.py` — device-bound, expiring, durable message sessions with polling. |
| Remote endpoints | `backend/api/routes.py` L3038–3230 — `/remote/sessions`, `/remote/turns`, `/remote/goals`; all non-authorizing (`authorized: False`, `approval_issued: False`). |
| Local control boundary | `backend/core/auth.py` L72–185 — control token + **hard loopback-only peer rule** (L109–114). |
| Capability status (global) | `backend/services/capabilities.py` — statuses AVAILABLE/UNAVAILABLE/DISABLED/DEGRADED/REQUIRES_PERMISSION/REQUIRES_CONFIGURATION; `device_integration` is hard-coded UNAVAILABLE (L200–201). |
| Permission primitives | `backend/runtime/permission_proposal.py`, `permission_decision_receipt.py`, `consequential_approval.py`, `task_ownership.py` — all **foundation-only**, not wired to a remote approval route. |
| Never auto-repeat uncertain action | **Verified present** in `routes.py` L722–765/L1173–1191, `task_ownership.py` L544–548, `consequential_approval.py` L456–466. |
| Native trust | Tauri exposes 4 commands (`src-tauri/src/main.rs` L658–663); Windows Hello attestation in `native_attestation.rs`. |

## 2. REUSE as-is

- Device identity, credential, grant and trust primitives (§1) — no rewrite.
- Relay session/message durability and polling — extend, do not replace.
- Global capability status derivation from `capabilities.py`.
- Immutable `TaskEvent` sequence + `Task.idempotency_key` (database.py L46–108).
- Durable wake / ownership / recovery and the "never rerun uncertain" safeguards.
- Loopback control-token middleware **for the local control plane** — keep intact.

## 3. MISSING — must build

| Item | Status | Note |
|---|---|---|
| Native Kotlin Android agent | Absent | No `.kt/.java/.gradle` anywhere. Six subsystems: identity, communication, accessibility, native actions, observation, permission manager. |
| Accessibility-based visual interaction | Absent | Windows GUI tools are UI Automation/GDI (`gui_tools.py`); no Android accessibility service. |
| Notification observation | Absent | No notification code in any Python module. |
| Consent-gated screen capture + image transport | Partial | Local-only `gui_screenshot`; no remote consent-gated capture. |
| Device WSS gateway | **Implemented 2026-11-20 (skeleton)** | `backend/core/device_scope.py` + `backend/api/device_gateway_routes.py`: `/device/relay/ws` requires a per-device credential and `conversation.write` grant, in a separate loopback-only scope; durable relay messages carry sequence numbers, with ack/poll/send frames. `test_device_gateway_ws.py` (11 passed). `ack` frames are now durable (`RelayAck`, idempotent per session+sequence) and owner-readable at `GET /api/v1/relay-acks`; acks are delivery evidence only, never authority. TLS/VPN termination remains external. |
| Per-device capability discovery endpoint | **Implemented 2026-11-20** | `backend/services/device_capability_discovery.py` + `GET /api/v1/devices/{id}/capabilities`. Merges global status × grants × trust floor into unavailable/restricted/available/granted. `test_device_capability_discovery.py` (7 passed); affected set 79 passed (8s). |
| Remote-action lifecycle state machine | **Implemented 2026-11-20** | `backend/runtime/remote_action_lifecycle.py` + `RemoteAction`/`RemoteActionEvent` models. received→understanding→planned→awaiting_approval→executing→verifying→completed, plus failed/cancelled and a terminal `uncertain` that forbids replay. Approval needs a trusted decision id; verified completion needs evidence; execution is refused while TaskOwnership records an uncertain effect. `test_remote_action_lifecycle.py` (10 passed); affected set 111 passed / 7 skipped (84s). |
| Console visibility of remote actions | **Implemented 2026-11-20** | Owner-only read API `GET /api/v1/remote-actions`, `/uncertain`, `/{id}` with event sequences; device-channel headers refused. `RemoteActionsPanel` renders state in the Control Center Operations screen with uncertain actions first and no approve/replay controls. `test_remote_action_visibility_api.py` (6), `RemoteActionsPanel.test.tsx` (4); affected backend 56 passed / 7 skipped (7s), frontend 13 passed, `tsc` clean. |
| Lifecycle wired into remote paths | **Implemented 2026-11-20** | `remote_action_tracking.py` starts/durable-commits an action before model/goal/WS work, verifies completion with a durable evidence ref (event sequence / goal id / relay message id), and records failures as uncertain terminal states after rolling back partial writes. `/remote/turns`, `/remote/goals`, and the device WS `send` path all return the action id/state; reused idempotency keys on resolved actions are refused with 409 (`terminal_replay_refused` on WS). `test_remote_action_tracking_integration.py` (5 passed); affected set 121 passed / 7 skipped (25s). |
| Trusted owner approval route | **Implemented 2026-11-20** | `GET /api/v1/approvals/pending` and `POST /api/v1/approvals/decide` wire `PermissionProposal` + `PermissionDecisionReceipt` through `ConsequentialApprovalService` with a UI-supplied `decision_id`. Loopback/control-token only, with explicit refusal of device-channel headers. `test_owner_approval_route.py` (9 passed); affected set 80 passed / 7 skipped (62s). |
| Control Center approval UI | **Implemented 2026-11-20** | `PendingApprovalsPanel` in the Security screen reads `GET /approvals/pending` and posts approve/deny to `POST /approvals/decide` with a fresh UI-generated `owner-ui-*` decision id; approve requires a confirmation dialog. `PendingApprovalsPanel.test.tsx` (5), `api.test.ts` service contract (2), `App.pending-proposals.test.tsx` (2); frontend 30 passed, backend 9 passed, `tsc` clean. |
| Device enrollment + approval Tauri commands | Absent | Only 4 local window/bridge commands today. |

## 4. CHANGE required

1. **Auth scope for remote (implemented 2026-11-20)** — `DeviceScopeMiddleware` guards only `/device/*` with credential presence and the same loopback peer rule; the `/api/` control-token boundary in `auth.py` is unchanged. Credential validity and the `conversation.write` grant are checked by the WS endpoint before accept.
2. **Push transport (skeleton implemented 2026-11-20)** — `/device/relay/ws` runs beside the relay's HTTP polling with sequenced frames and ack/poll/send. It is a transport skeleton only: no model call, no authority, no approval; TLS/WSS termination is external.
3. **Capability projection (implemented 2026-11-20)** — `describe_device_capabilities()` merges global status × owner grant × trust floor into `unavailable / restricted / available / granted`, exposed read-only at `GET /api/v1/devices/{id}/capabilities`. `devices.read` truthfully reports `unavailable` because `device_integration` is hard-coded UNAVAILABLE, and `messages.read` reports `unavailable` because it has no implementation. It becomes derived only when the transport and agent actually exist.
4. **Wire approval (implemented 2026-11-20)** — `GET /approvals/pending` and `POST /approvals/decide` expose the real proposal/receipt flow to the trusted owner UI only, behind the loopback control-token boundary, with a UI-supplied `decision_id` bound into the receipt. Device-channel headers are explicitly refused; a remote client cannot approve its own action. The resulting receipt/decision id can be passed to the remote-action lifecycle as its trusted decision id.
5. **Tauri + handshake** — add device enrollment, credential issue, grant, approval-surfacing and device-channel config commands; extend the sidecar handshake (backend/core/handshake.py) for device transport config.

## 5. Contract interfaces to be added (design only)

- `GET /api/v1/devices/{id}/capabilities` → `{device_id, trust_level, capabilities: [{capability, status, granted, min_trust, global_capability, global_status, allowed, reason}], note}` — **implemented**; status ∈ unavailable|restricted|available|granted, with `allowed` true only for `granted`.
- Remote-action record keyed by `{action_id, idempotency_key}` with states
  `received → understanding → planned → awaiting_approval → executing → verifying → completed|failed|cancelled`, plus a distinct **uncertain** terminal state that forbids replay until reconciled — **implemented** as a foundation primitive (no API exposure yet). Reusing an idempotency key returns the same uncertain record; a retry requires a new key.
- `POST /api/v1/approvals/decide` — **implemented** owner-only decision route, bound to a UI-supplied decision id and never reachable by the requesting device (device headers refused; loopback control token required). The remote-action lifecycle consumes that decision id when leaving `awaiting_approval`; no remote device approves its own action.
- Device-authenticated WSS scope with per-message sequence numbers and acknowledgements, reusing the relay's credential + grant checks. **Shell configuration implemented 2026-11-20:** `src-tauri/src/device_channel.rs` writes a private, versioned `device_channel.json` in the profile dir before the sidecar starts, with `enabled`/`relay_enabled`/`ws_enabled` (default enabled for compatibility). Tauri commands `get_device_channel_config` / `set_device_channel_config` expose it to the shell; `backend/core/handshake.py` reads and validates it (missing = current default, corrupt/invalid = fail closed). `DeviceScopeMiddleware` and the WS endpoint refuse the channel when disabled. This config can only disable channels — it never widens the loopback boundary or grants a capability. Evidence: Rust `cargo test --offline device_channel` 4 passed (exit 0); backend config/gateway/startup set 49 passed / 7 skipped (4s).

  **Control Center UI implemented 2026-11-20:** `DeviceChannelPanel` in the System & Integrations runtime tab reads/writes config through `services/deviceChannel.ts` (Tauri commands; browser fallback fails closed). Evidence: `deviceChannel.test.ts` (4), `DeviceChannelPanel.test.tsx` (3), affected frontend set 25 passed, `tsc --noEmit` exit 0.

  **WS reply contract implemented 2026-11-20:** the `/device/relay/ws` `send` path now runs the same turn contract as the HTTP remote paths — it stores the owner message, generates a durable Synth reply via `ConversationTurnEngine`, appends an audited `device.relay_message` event, completes the tracked lifecycle with evidence bound to the reply id and event sequence, and pushes the reply as a sequenced frame (`reply_sequence`). Failures after execution are recorded uncertain, never replayed. Evidence: `test_device_gateway_ws.py` (10 passed), affected set 90 passed / 7 skipped (10s).

  **Owner reconciliation implemented 2026-11-20:** `RemoteActionReconciliation` records an owner decision for an uncertain action with a UI-supplied `decision_id`, an audited `remote_action.reconciled` event, and an optional fresh action created with a NEW idempotency key. The uncertain action stays terminal and is never revived or replayed; replaying its key is refused. Owner-only API `POST /remote-actions/{id}/reconcile` + `GET .../reconciliations` refuse device-channel headers. `RemoteActionsPanel` renders a Reconcile control for uncertain actions with outcome/evidence/new-key fields and a UI-generated decision id. Evidence: backend `test_remote_action_reconciliation.py` (8 passed), affected set 54 passed / 7 skipped (9s); frontend `api.test.ts` + `RemoteActionsPanel.test.tsx` 31 passed, `tsc` exit 0.

  **Actionable briefs implemented 2026-11-20:** `BriefingCard` and `MorningBriefCard` now show navigation affordances — "Review in Control Center" (→ overview, where the reconciliation and device panels live) and "Open approvals" (→ security, where owner decisions live) — shown contextually when there are reconciliation/blocking items or pending approvals. Navigation only; the cards still cannot approve, grant, or clear anything. Evidence: `BriefingCard.test.tsx` (5), `MorningBriefCard.test.tsx` (4), affected frontend set 91 passed, `tsc --noEmit` exit 0.

  **Overview decision counts implemented 2026-11-20:** the Control Center Overview "Needs your decision" stat now folds in owner approval proposals from `GET /approvals/pending` and uncertain actions from `GET /remote-actions/uncertain`, alongside existing proposal/task counts. `lib/decisionCounts.ts` is a pure helper; `decisionCounts.test.ts` (9 passed), affected frontend set 18 passed, `tsc` exit 0. The stat's click now routes contextually via `decisionDestination`: dominant reconciliations → overview, approvals → security, waiting tasks → missions, default security.

  **Reconciliation history in the console implemented 2026-11-20:** `RemoteActionsPanel` renders a read-only history view per action from `GET /api/v1/remote-actions/{id}/reconciliations` (outcome, decision id, evidence, note, fresh action, time), with an explicit empty state. History refreshes after a new reconciliation. Evidence: `RemoteActionsPanel.test.tsx` (8 passed), `api.test.ts` endpoint contract (1), affected frontend set 46 passed, `tsc --noEmit` exit 0.

  **Device navigation from the brief implemented 2026-11-20:** `MorningBriefCard` shows a "View devices" button when the brief contains registered devices, routing to the system screen's device panel (`system`); absent when no devices are registered. Navigation only. Evidence: `MorningBriefCard.test.tsx` (6 passed), affected frontend set 91 passed, `tsc --noEmit` exit 0.

  **Goals/tasks navigation from the brief implemented 2026-11-20:** `MorningBriefCard` shows a single "View goals" button when either the Goals finished or Tasks finished section has reported items, routing to the Goals screen (`missions`); absent when both report "None reported." This completes navigation affordances for every morning-brief section. Evidence: `MorningBriefCard.test.tsx` (15 passed), affected frontend set 101 passed, `tsc --noEmit` exit 0.

  **Agent navigation from the brief implemented 2026-11-20:** `MorningBriefCard` shows a "View agent status" button when its Synth (agent) section reports a non-active state (paused/degraded/shutdown/unknown or not initialized), routing to the System screen's agent-status panel (`system`); absent when the agent is active. Navigation only. Evidence: `MorningBriefCard.test.tsx` (12 passed), affected frontend set 98 passed, `tsc --noEmit` exit 0.

  **Health navigation from the brief implemented 2026-11-20:** `MorningBriefCard` shows a "View diagnostics" button when its Local health section contains a non-healthy check (DEGRADED/UNAVAILABLE/UNKNOWN), routing to the System screen's diagnostics panel (`system`); absent when all checks are healthy. Navigation only. Evidence: `MorningBriefCard.test.tsx` (10 passed), affected frontend set 96 passed, `tsc --noEmit` exit 0.

  **Agent-status navigation from the heads-up implemented 2026-11-20:** `BriefingCard` shows a "View agent status" button for `agent_not_active` items, routing to the System screen's runtime/agent-state panel (`system`). Navigation only. Evidence: `BriefingCard.test.tsx` (6 passed), affected frontend set 94 passed, `tsc --noEmit` exit 0.

  **Brief actionability for device actions implemented 2026-11-20:** `MorningBriefCard` shows a "View device actions" button when the brief has a populated Recent device actions section, routing to the Control Center Operations screen where `RemoteActionsPanel` lives (`overview`); absent when no actions recorded. Navigation only. Evidence: `MorningBriefCard.test.tsx` (8 passed), affected frontend set 93 passed, `tsc --noEmit` exit 0.

  **Phone client on WSS implemented 2026-11-20:** `PhoneRelayScreen` now connects to the device WSS gateway (`/device/relay/ws`) as its primary transport, receiving real-time sequenced reply frames; HTTP relay polling remains as a fallback when WSS is unavailable. `services/deviceRelaySocket.ts` builds the device-scope URL with the credential in the query string (never the control token), parses frames, and is injectable for tests. The client handles the `accepted` frame and renders a tracked-action badge (action state + reply sequence), and every WSS send carries a UI-generated `phone-*` idempotency key that is stable while editing one message and fresh for the next, so phone messages deduplicate like the HTTP remote paths. Evidence: `deviceRelaySocket.test.ts` (5 passed), `PhoneRelay.test.tsx` (6 passed, incl. WSS send with idempotency key, per-message key stability, tracked badge, HTTP fallback), affected frontend set 18 passed, `tsc --noEmit` exit 0.

  **Structured seeded briefs on the phone implemented 2026-11-20:** `PhoneRelayScreen` renders seeded `briefing` / `morning_brief` relay messages as structured cards (headline, labelled rows, read-only note) instead of raw JSON text, via a pure `parseRelayBrief` parser with plain-text fallback for unknown payloads. Evidence: `relayBrief.test.ts` (3 passed), `PhoneRelay.test.tsx` (8 passed incl. structured-card render + no raw JSON), affected frontend set 23 passed, `tsc --noEmit` exit 0.

  **Phone status summary implemented 2026-11-20:** `RelayBriefCard` now renders a compact read-only status strip on morning briefs (overall health, agent state, per-device trust levels) via a pure `briefStatusSummary` helper that reads only the Core-assembled rows and invents nothing. Shown only when status sections exist; never an action or authority. Extended with a read-only `needs decision` count from reconciliation/blocker/permission rows (empty markers excluded), rendered as a `decisions: N` badge. The count includes heads-up item-kind keys (`task_needs_input`/`task_held`/`goal_step_blocked`) so a phone heads-up does not undercount, while `agent_not_active` stays a status item (not a decision), matching the desktop Overview. Evidence: `relayBrief.test.ts` (15 passed), `RelayBriefCard.test.tsx` (8 passed), `PhoneRelay.test.tsx` (10 passed), affected frontend set 74 passed, `tsc --noEmit` exit 0.

  **Heads-up agent action on the phone implemented 2026-11-20:** `briefActions` now maps the `agent_not_active` heads-up item kind to an "Agent status" action routing to `system`, matching the desktop BriefingCard; it dedupes against the `agent` row when both would produce the same action. Evidence: `relayBrief.test.ts` (10 passed), affected frontend set 36 passed, `tsc --noEmit` exit 0.

  **Health/agent actions on the phone brief implemented 2026-11-20:** `briefActions` now adds conditional rules matching the desktop: a non-healthy `system` section yields a "Diagnostics" action and a non-active `agent` section yields an "Agent status" action, both routing to `system`; healthy/active sections yield none. Evidence: `relayBrief.test.ts` (8 passed), `RelayBriefCard.test.tsx` (5 passed), `PhoneRelay.test.tsx` (9 passed), affected frontend set 34 passed, `tsc --noEmit` exit 0.

  **HTTP fallback tracked state implemented 2026-11-20:** `PhoneRelayScreen`'s HTTP send branch now reads `action_state` and `reply.id` from the `POST /remote/sessions/{id}/messages` response and feeds them into the tracked-action badge, so HTTP-only clients see the same tracked state and uncertain→Reconcile affordance as WSS clients. Evidence: `PhoneRelay.test.tsx` (10 passed incl. HTTP uncertain → Reconcile → overview), affected frontend set 70 passed, `tsc --noEmit` exit 0.\n\n  **Phone message dedupe implemented 2026-11-20:** all relay message append paths (WSS frames, initial HTTP poll, recurring HTTP poll) now route through a sequence-id dedupe helper, so a re-delivered frame across a transport switch or reconnect is not shown twice. Evidence: `PhoneRelay.test.tsx` (11 passed incl. re-delivered frame shown once), affected frontend set 75 passed, `tsc --noEmit` exit 0.\n\n  **Control Center relay-ack summary implemented 2026-11-20:** `RelayAckPanel` in the System runtime tab consumes `GET /api/v1/relay-acks`, showing per-session highest-ack high-water marks and recent receipts, read-only, with no controls. Evidence: `RelayAckPanel.test.tsx` (3 passed), `api.test.ts` (2 new service-contract tests), affected frontend set 116 passed, `tsc --noEmit` exit 0.

  **Bounded WSS reconnect implemented 2026-11-20:** when a device socket closes while the owner is still connected, `PhoneRelayScreen` reconnects with bounded exponential backoff (1000/2000/4000ms, max 3 attempts); after the budget it falls back to HTTP polling on the same relay. A successful open resets the backoff and clears the Reconnecting indicator; manual Disconnect and unmount cancel all timers and close the socket. Conversation state and the idempotency cursor survive a reconnect, and the sequence dedupe makes re-delivery safe. Evidence: `PhoneRelay.test.tsx` (15 passed incl. backoff sequence, HTTP fallback, reset, manual-disconnect and unmount cancellation), affected frontend set 79 passed, `tsc --noEmit` exit 0.

  **Actionable uncertain tracked action on the phone implemented 2026-11-20:** `PhoneRelayScreen`'s tracked-action badge now shows a "Reconcile" button only when the reply's `action_state` is `uncertain`, routing to the Control Center reconciliation panel (`overview`); completed outcomes show no affordance. Navigation only — it never auto-replays or auto-resolves the effect. Evidence: `PhoneRelay.test.tsx` (9 passed incl. uncertain → overview and completed → no button), affected frontend set 31 passed, `tsc --noEmit` exit 0.

  **Actionable phone brief implemented 2026-11-20:** `parseRelayBrief` now retains each row's section key/kind, and a pure `briefActions` helper maps reconciliation/blockers → overview, permission approvals → security, devices → system, device actions → overview, and goals/tasks → missions, skipping "empty" sections. `RelayBriefCard` renders those as navigation buttons when given an `onAction` handler; `PhoneRelayScreen` passes its nav setter so the phone brief is as actionable as the desktop brief. Navigation only — never approves, grants, or clears. Evidence: `relayBrief.test.ts` (6 passed), `RelayBriefCard.test.tsx` (4 passed), `PhoneRelay.test.tsx` (8 passed), affected frontend set 30 passed, `tsc --noEmit` exit 0.

  **HTTP fallback dedup implemented 2026-11-20:** `relayService.send` now accepts an optional idempotency key and the phone's HTTP fallback passes the same UI-generated `phone-*` key used on WSS. `POST /remote/sessions/{id}/messages` honors that key through the same tracked lifecycle (durable action, evidence-bound completion, 409 on resolved-key replay), so both phone transports deduplicate identically. Evidence: frontend `api.test.ts` + `PhoneRelay.test.tsx` 36 passed, backend `test_relay.py` 10 passed, affected backend set 79 passed / 7 skipped (15s), `tsc` exit 0.

  **Control Center capability discovery implemented 2026-11-20:** `DevicesCard` now loads `GET /api/v1/devices/{id}/capabilities` on demand and renders each capability's `available / granted / restricted / unavailable` status with its trust floor and reason; status is read-only observation and never changes a grant. Evidence: `DevicesCard.test.tsx` (5 passed), `api.test.ts` service contract (1 new), affected frontend set 47 passed, `tsc --noEmit` exit 0.

  **Control Center access management implemented 2026-11-20:** `DeviceAccessControls` (behind a Manage access toggle in `DevicesCard`) wires the existing owner endpoints: POST/DELETE `/devices/{id}/credential` and PUT/DELETE `/devices/{id}/grants/{capability}`. Issued credentials are shown ONCE with a warning (server stores only a hash); revocation requires confirmation; grant add/remove are explicit owner actions; a device cannot widen its own access. Evidence: `DeviceAccessControls.test.tsx` (4 passed), `api.test.ts` endpoint contract (1 new), affected frontend set 52 passed, `tsc --noEmit` exit 0.

  **HTTP relay gating implemented 2026-11-20:** the shell switch now gates the HTTP relay too. `_remote_device_or_401` reads the config first and returns 503 when `enabled` or `relay_enabled` is false; `/remote/turns` and `/remote/goals` were refactored from duplicate inline auth to use that single gate, so every `/api/v1/remote/*` endpoint is covered. `ws_enabled` affects only `/device/*`. Evidence: `test_remote_relay_gate.py` (5 passed: both disable flags across all remote endpoint families, gate-before-credential, WS flag independence, corrupt config fail-closed); affected set 123 passed / 7 skipped (16s).

## 6. Locked decisions (from the owner's proposal)

- Windows remains the primary intelligence/coordination host; **no second AI Core on Android**.
- Native Kotlin agent, not a web page.
- HTTPS + WebSocket over TLS; local Wi-Fi + private VPN (router-hosted VPN preferred; self-hosted relay only if needed; **no commercial relay dependency**).
- Shared Sythion permission architecture + **independent Android security boundary**.
- Per-phone cryptographic identity; revocation invalidates credentials; a remote connection cannot grant new permissions; notifications/screen content never become unrestricted memory; the phone can disconnect independently; accessibility never bypasses Android security; show when remote control is active.
- Device coordination, not remote control: discover capabilities, select the right device, verify the result.
- **Never automatically repeat an uncertain action** — reconcile first. (Already enforced today; must remain.)

## 7. Phases and acceptance gates

| Phase | Work | Gate |
|---|---|---|
| 1 — Audit | Done (this document) | Interfaces verified against source; older inventory corrected. |
| 2 — Android foundation | Kotlin agent, secure registration | Phone registers with its own identity; revocation works. |
| 3 — Communication | Local + VPN, WSS gateway | Two-way, both paths independently; loopback boundary intact. |
| 4 — Device capabilities | Native actions, accessibility, observation | Capability discovery reports real status; permission revocation immediately disables the operation. |
| 5 — AI orchestration | Plan → execute → verify across devices | Outcome verified, not assumed; uncertain outcomes reported and reconciled. |
| 6 — Reliability & security | Recovery, permissions, audit, revocation | Critical tests below all pass. |

## 8. Critical tests (must pass before integration is called complete)

- Synth discovers and registers the phone.
- Two-way communication works; local and remote paths work independently.
- Synth opens and interacts with a supported application and verifies the result.
- Revoking a permission immediately prevents the corresponding operation.
- A failed command is reported truthfully; a disconnected device cannot receive unauthorized commands.
- The owner can terminate remote access at any time.
- An uncertain action is never auto-repeated.

## 9. Current boundary

Everything in §2 is done and committed (through `1b927b2`). Items in §3–§5 need
the owner's environment (a phone, a network/VPN, granted sources) and are
**intentionally not coded yet**, per the owner's "establish the contract first"
instruction.
