# JARVIS Acceptance-Test Map

**Status:** Gates marked `passing` have an executable suite in
`backend/tests/acceptance/` (run from `backend/` via
`python -m pytest tests/acceptance/`). Gates marked `partial` have the
sidecar-side coverage but still need the packaged Tauri shell or a manual
clean-machine check. Tests marked `planned` must exist and pass before their
milestone can be accepted.

| Area | Acceptance test | Gate | Status |
| --- | --- | --- | --- |
| Task ownership | A second request while one task runs returns `TASK_ACTIVE` and cannot queue or duplicate work | B | passing |
| Intent routing | Clarification replies use the same task ID; corrections require cancel then replacement | B | passing |
| Cancellation | Cancellation is durable, checked before OS calls, and prevents the next action | B | passing |
| External state | Every mutation commits `action_started` before execution and records a fresh verifier result | C | planned |
| Recovery/undo | Crash after `action_started` marks the task `interrupted` and action `uncertain`; explicit resume re-observes before mutation | B/C | planned |
| Permissions/identity | Apps use registered identities; arbitrary executables, arguments, Run dialogs, and elevation are denied | C | planned |
| Data lifecycle | LocalAppData storage has user-only ACLs; secrets and prohibited content are absent from DB, events, logs, and Cloud context | A/B | partial |
| Secret storage | API keys are write-only via Windows Credential Manager; the value is never returned by an endpoint, stored in SQLite, or written to `.env` | A | passing |
| GUI reliability | Locked, elevated, stale, missing, disconnected, or browser targets receive no input | D | planned |
| Tool discovery | Model context is policy-filtered and bounded; it never contains full directories, raw UI trees, screenshots, or thinking | B | passing |
| Target validation | Empty, wildcard, out-of-root file targets and invalid app/package IDs and missing GUI window titles are rejected deterministically before any OS action | B | passing |
| Brain contract | Model-visible tools are the policy-filtered read-only subset exposed directly (no `execute_tool` wrapper); tool output reaches the model only as a bounded `model_summary`; context carries the current task state; reasoning/thinking fields are stripped from model responses | B | passing |
| Skill lifecycle | Skills validate restrictive references, pin versions, require explicit enablement, and stop before an unstarted disabled step | F | planned |
| Model failure | Invalid, parallel, malformed, unavailable, rate-limited, stale-model, and exhausted-budget decisions cause no repeated mutation or local fallback | B | passing |
| Per-task budgets | Cloud request, input/output token, action, repair, and elapsed-time budgets are tracked per task; exhaustion fails the task honestly with the matching code and records an evidence-safe snapshot event | B | passing |
| Desktop lifecycle | Packaged app enforces single instance, launches one sidecar, shuts it down cleanly, and recovers stale leases | A | partial |
| Sidecar handshake | Launcher writes a private per-launch token file; sidecar reads it, binds an ephemeral loopback port, and writes port+token back for the launcher | A | passing |
| Cloud capability | Onboarding lists authenticated model IDs and proves one fake read-only native tool call before the provider is usable | B | passing |
| Cloud onboarding | Selecting an arbitrary/stale model ID is rejected; the exact returned ID is persisted as non-secret profile metadata and takes effect on restart | B | passing |
| Local API trust | Wrong token, wrong origin, non-loopback client, and unauthenticated WebSocket cannot invoke or observe sidecar operations | A | passing |
| UIA threading | All UIA calls run only through one COM MTA worker; handles are reacquired for each fresh observation | D | planned |
| Voice assets | STT/TTS assets never download at startup; explicit installation verifies pinned version, license, and checksum | E | planned |
| Event stream | Slow/disconnected clients cannot delay work; replay from `after_sequence` reconstructs exactly the committed timeline | B | passing |
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
