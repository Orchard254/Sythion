# JARVIS Acceptance-Test Map

**Status:** Phase -1 gate — implementation direction accepted. Tests marked `planned`
must exist and pass before their milestone can be accepted.

| Area | Acceptance test | Gate | Status |
| --- | --- | --- | --- |
| Task ownership | A second request while one task runs returns `TASK_ACTIVE` and cannot queue or duplicate work | B | planned |
| Intent routing | Clarification replies use the same task ID; corrections require cancel then replacement | B | planned |
| Cancellation | Cancellation is durable, checked before OS calls, and prevents the next action | B | planned |
| External state | Every mutation commits `action_started` before execution and records a fresh verifier result | C | planned |
| Recovery/undo | Crash after `action_started` marks the task `interrupted` and action `uncertain`; explicit resume re-observes before mutation | B/C | planned |
| Permissions/identity | Apps use registered identities; arbitrary executables, arguments, Run dialogs, and elevation are denied | C | planned |
| Data lifecycle | LocalAppData storage has user-only ACLs; secrets and prohibited content are absent from DB, events, logs, and Cloud context | A/B | planned |
| GUI reliability | Locked, elevated, stale, missing, disconnected, or browser targets receive no input | D | planned |
| Tool discovery | Model context is policy-filtered and bounded; it never contains full directories, raw UI trees, screenshots, or thinking | B | planned |
| Skill lifecycle | Skills validate restrictive references, pin versions, require explicit enablement, and stop before an unstarted disabled step | F | planned |
| Model failure | Invalid, parallel, malformed, unavailable, rate-limited, stale-model, and exhausted-budget decisions cause no repeated mutation or local fallback | B | planned |
| Desktop lifecycle | Packaged app enforces single instance, launches one sidecar, shuts it down cleanly, and recovers stale leases | A | planned |
| Cloud capability | Onboarding lists authenticated model IDs and proves one fake read-only native tool call before the provider is usable | B | planned |
| Local API trust | Wrong token, wrong origin, non-loopback client, and unauthenticated WebSocket cannot invoke or observe sidecar operations | A | planned |
| UIA threading | All UIA calls run only through one COM MTA worker; handles are reacquired for each fresh observation | D | planned |
| Voice assets | STT/TTS assets never download at startup; explicit installation verifies pinned version, license, and checksum | E | planned |
| Event stream | Slow/disconnected clients cannot delay work; replay from `after_sequence` reconstructs exactly the committed timeline | B | planned |
| Clean-machine release | Install, launch, stop, restart, upgrade, uninstall, and stale-sidecar recovery pass on an interactive Windows test machine | F | planned |

## Phase A: packaging and ledger spike

1. Tauri starts the fixed sidecar with fresh token and receives ephemeral port.
2. Sidecar accepts only loopback, authenticated, packaged-origin requests.
3. A SQLite WAL migration creates tasks, immutable events, lease, skills, and
   settings metadata with short transactions.
4. Single-instance/profile lock and clean shutdown leave no orphan sidecar.
5. No real OS mutation is registered or reachable.

## Phase B: trusted text core

1. Typed request creates one idempotent task and committed event sequence.
2. Capability probe validates authentication, selected exact model, and one
   fake read-only native tool call.
3. Only validated one-at-a-time decisions are accepted; plain text cannot
   complete a task.
4. One direct, read-only system tool provides bounded, redacted evidence.
5. Stop, interruption, explicit resume, replay, and slow-client behavior pass.

## Phase C through F

- **C:** scoped test-file mutation and registered app launch/focus, each with
  deterministic policy, preconditions, verifier, cancellation, and recovery.
- **D:** only a dedicated Windows fixture application; UIA first, OCR fallback,
  and no browser-page automation.
- **E:** visible-window voice capture, locally pinned VAD/STT/TTS, editable
  transcripts, local/global stop, privacy UX, and audit viewer.
- **F:** declarative skills, proposed-skill review, retention/purge, installer
  lifecycle, clean-machine verification, signing/distribution plan.

## Evidence policy

Each passing test must retain only non-sensitive artifacts: test name, build
identifier, timestamp, redacted result, and reproducible steps. Never attach
secrets, raw audio, screenshots, OCR/UI trees, browser queries, password text,
or model reasoning.
