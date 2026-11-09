# Native recovery presence protocol — foundation only

**Status:** Offline contract implemented 2026-11-09; no production native verifier, route, consent, clearance or replay.

This document describes the narrow boundary required before a trusted desktop
host can make a credible human-presence assertion during recovery review.

## Why a separate native boundary is required

- The per-launch `X-Control-Token` proves access to the local control API,
  **not** identity, Windows Hello verification, or human consent.
- Session lineage proves that Synth minted a conversation session, **not** a
  human's identity or authority over a task.
- The recovery-review snapshot SHA-256 revision is a **change detector**,
  **not** an attestation or permission.
- A button click, model suggestion, LLM tool result, arbitrary HTTP
  `approved: true` flag, and unsigned recovery note cannot authorize an
  uncertain external effect.

## Implemented offline server-side contract

Module: `backend/services/recovery_native_challenge.py`.

The `NativePresenceChallengeBroker` is **default disabled**. Without a
separately provisioned native-host key, it refuses challenge issuance and
assertion verification.

A host-installed broker can issue an opaque 256-bit random challenge bound to:

1. Exact task identity (no free-form action parameters).
2. The latest server-computed `sha256:` recovery evidence revision.
3. The fixed purpose `recovery_review_only`.
4. A bounded monotonic lifetime (default 90 seconds, at most 120 seconds).
5. A single process-local, single-use challenge slot.

The one-shot signing message is domain-separated
(`SYTHION/NATIVE_RECOVERY_PRESENCE/v1`), length-prefixed, and includes the fixed
outcome label `native_os_verification_succeeded`. The API caller must not
select an outcome label. A future native verifier must sign **only** after
receiving the explicit OS verification result `Verified`.

Verification uses constant-time HMAC-SHA-256 comparison against an independently
provisioned per-launch host/backend key (minimum 32 bytes). Three failed
valid-format attempts exhaust a challenge; a changed authoritative evidence
revision consumes it; expired and used challenges cannot be replayed. Pending
challenge count is bounded (64), and restart forgets all pending challenges.

The currently returned `NativePresenceObservation` acknowledges only
**authenticated host-message integrity**. It explicitly sets:

- `authenticated_human = false`
- `independent_effect_verified = false`
- `recovery_cleared = false`
- `authorization_issued = false`
- `automatic_replay_allowed = false`

These flags remain false even in simulated-success tests. This module is not
imported by the current resume route or TaskRunner and has **no** authority to
change task/goal state, execute tools, approve actions, or clear uncertainty.

## Future trusted Windows integration (not implemented)

1. Implement a native, window-owned Windows Hello user-consent verification in
   Tauri. **Desktop apps must use the window-aware WinRT interop method**
   `IUserConsentVerifierInterop::RequestVerificationForWindowAsync`, bound to
   the actual Tauri HWND. The UWP-only `RequestVerificationAsync` overload is
   not a substitute for desktop interop. Treat cancel, device busy/unavailable,
   policy-disabled, and verification failure as denial.
2. Create an independent per-launch attestation key *in the native host*, never
   in JavaScript, model context, normal API tokens, or log files.
3. Provision the backend with its verifier key through an explicitly
   authenticated and access-controlled local host-to-sidecar mechanism.
   This channel, identity and deployment ACLs need security review. An
   ordinary environment variable, webview-posted key, or request header is
   not automatically trustworthy.
4. Obtain a server-issued challenge for the *exact* currently reviewed task
   and revision; present purpose-specific Windows UI. Native host must sign
   only on the OS's `Verified` result.
5. Backend must recompute the current revision from **authoritative database
   state under the task writer lock**, never trust an HTTP-provided 'current'
   revision. Check the one-time challenge and audited provenance there.
6. **Separately** reconcile ambiguous external effects through independently
   verified target-system evidence and authenticated owner intent; neither
   a successful Windows Hello prompt nor a host signature proves an external
   action is safe to retry.
7. Add an audited, nonce- and task-scoped decision receipt and explicit UI.
   Only then consider any controlled reopening of work. Even then, preserve
   existing consequence policy and authorization gates.

Do not turn this module into a public verification or clearance endpoint ahead
of those steps. Never allow the UI or LLM to supply the host signing key.

## Verification and limits

Locally executed in a partial isolated Python runtime:

- 39 pure challenge-contract test cases: missing host, wrong task/revision,
  expiry, single use, process restart, wrong key/signature, attempt limit,
  capacity, malformed input, non-authorizing result, and stale-revision
  invalidation.
- 4 synthetic SQLite composition cases: bind to current revision; reject
  version change between challenge and verification; reject caller approval
  flags; fail closed with missing native host.
- The combined focused/synthetic backend suite had 353 passing tests.
- No Windows Hello, Tauri/Rust compilation, native prompt, real app profile
  authentication, full ORM/API suite, or actual consent verification ran.
  **OS-backed human authentication remains unimplemented**.

The module deliberately requires an injected trusted host key. Unit tests
simulate signing to test protocol integrity; simulated signatures are **not**
human verification or production security evidence.
