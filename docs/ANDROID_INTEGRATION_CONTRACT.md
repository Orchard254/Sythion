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
| VPN / TLS remote transport, WSS gateway | Absent | Sidecar is plain HTTP on `127.0.0.1` (`main.py` L484–491). Loopback rule blocks remote peers. |
| Per-device capability discovery endpoint | **Implemented 2026-11-20** | `backend/services/device_capability_discovery.py` + `GET /api/v1/devices/{id}/capabilities`. Merges global status × grants × trust floor into unavailable/restricted/available/granted. `test_device_capability_discovery.py` (7 passed); affected set 79 passed (8s). |
| Remote-action lifecycle state machine | Partial | Coarse `Task.state` + pending_action + AgentWake exist; the proposed received→…→verified chain does not. |
| Remote approval surface | Absent | Permission primitives are foundation-only; no trusted owner approval route for remote actions. |
| Device enrollment + approval Tauri commands | Absent | Only 4 local window/bridge commands today. |

## 4. CHANGE required

1. **Auth scope for remote** — `auth.py` L109–114 rejects all non-loopback peers. Add a **device-authenticated remote scope** keyed by `X-Sythion-Device-Id`/token (pattern: `extract_websocket_token()` at auth.py L54–66). Do **not** weaken the local control boundary.
2. **Push transport** — add device-authenticated WS (or SSE) beside the relay's HTTP polling; local + VPN paths share one protocol.
3. **Capability projection (implemented 2026-11-20)** — `describe_device_capabilities()` merges global status × owner grant × trust floor into `unavailable / restricted / available / granted`, exposed read-only at `GET /api/v1/devices/{id}/capabilities`. `devices.read` truthfully reports `unavailable` because `device_integration` is hard-coded UNAVAILABLE, and `messages.read` reports `unavailable` because it has no implementation. It becomes derived only when the transport and agent actually exist.
4. **Wire approval** — connect `PermissionProposal` + `PermissionDecisionReceipt` to a real owner approval flow with a trusted UI-supplied `decision_id` (never model-derived). A remote client cannot approve its own action.
5. **Tauri + handshake** — add device enrollment, credential issue, grant, approval-surfacing and device-channel config commands; extend the sidecar handshake (backend/core/handshake.py) for device transport config.

## 5. Contract interfaces to be added (design only)

- `GET /api/v1/devices/{id}/capabilities` → `{device_id, trust_level, capabilities: [{capability, status, granted, min_trust, global_capability, global_status, allowed, reason}], note}` — **implemented**; status ∈ unavailable|restricted|available|granted, with `allowed` true only for `granted`.
- Remote-action record keyed by `{action_id, idempotency_key}` with states
  `received → understanding → planned → awaiting_approval → executing → verifying → completed|failed|cancelled`, plus a distinct **uncertain** terminal state that forbids replay until reconciled.
- `POST /api/v1/remote/actions/{id}/approve|deny` — owner-only, bound to a trusted decision id; never reachable by the requesting device.
- Device-authenticated WSS scope with per-message sequence numbers and acknowledgements, reusing the relay's credential + grant checks.

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
