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
| Device WSS gateway | **Implemented 2026-11-20 (skeleton)** | `backend/core/device_scope.py` + `backend/api/device_gateway_routes.py`: `/device/relay/ws` requires a per-device credential and `conversation.write` grant, in a separate loopback-only scope; durable relay messages carry sequence numbers, with ack/poll/send frames. `test_device_gateway_ws.py` (7 passed). TLS/VPN termination remains external. |
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
