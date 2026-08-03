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
| Read-only slice | A task runs end-to-end through a real read-only system tool with recorded evidence and verified completion; mutating tools stay out of the model-visible subset | B | passing |
| External state | Every mutation commits `action_started` before execution and records a fresh verifier result | C | partial |
| Controlled file mutation | Writes are atomic (temp + `os.replace`), refuse clobber without overwrite, cancel before the final replace leaves prior content intact, and record fresh post-mutation verification for create/write/move/copy/delete | C | passing |
| Canonical file identity | File execution re-verifies containment via real OS handles (short-name/case safe) and rejects targets that are symlinks/junctions/reparse points; recycle-bin deletes record recoverability | C | passing |
| Recovery/undo | Crash after `action_started` marks the task `interrupted` and action `uncertain`; explicit resume re-observes before mutation | B/C | planned |
| Permissions/identity | Apps use registered identities; arbitrary executables, arguments, Run dialogs, and elevation are denied | C | passing |
| Unsaved work | A still-open window after graceful `WM_CLOSE` is surfaced as an unsaved-work prompt and never auto-answered as Save/Discard | C | passing |
| Package restrictions | Install/uninstall use fixed-argument winget with exact ids; agreements/elevation are never auto-accepted; other package managers are removed | C | passing |
| Data lifecycle | LocalAppData storage has user-only ACLs; secrets and prohibited content are absent from DB, events, logs, and Cloud context | A/B | partial |
| Secret storage | API keys are write-only via Windows Credential Manager; the value is never returned by an endpoint, stored in SQLite, or written to `.env` | A | passing |
| GUI reliability | Locked, elevated, stale, missing, disconnected, or browser targets receive no input | D | planned |
| Tool discovery | Model context is policy-filtered and bounded; it never contains full directories, raw UI trees, screenshots, or thinking | B | passing |
| Target validation | Empty, wildcard, out-of-root file targets and invalid app/package IDs and missing GUI window titles are rejected deterministically before any OS action | B | passing |
| Brain contract | Model-visible tools are the policy-filtered read-only subset exposed directly (no `execute_tool` wrapper); tool output reaches the model only as a bounded `model_summary`; context carries the current task state; reasoning/thinking fields are stripped from model responses | B | passing |
| Skill lifecycle | Skills validate restrictive references, pin versions, require explicit enablement, and stop before an unstarted disabled step | F | passing |
| Retention & purge | Owner-configurable retention (default 90 days) purges complete task+event records; purge leaves only non-sensitive receipts | F | passing |
| Model failure | Invalid, parallel, malformed, unavailable, rate-limited, stale-model, and exhausted-budget decisions cause no repeated mutation or local fallback | B | passing |
| Per-task budgets | Cloud request, input/output token, action, repair, and elapsed-time budgets are tracked per task; exhaustion fails the task honestly with the matching code and records an evidence-safe snapshot event | B | passing |
| Desktop lifecycle | Packaged app enforces single instance, launches one sidecar, shuts it down cleanly, and recovers stale leases | A | partial |
| Desktop shell build | The Tauri shell compiles end-to-end (strict capabilities/CSP, bridge command, sidecar launcher, global-stop shortcut) on `windows-latest` | A | partial |
| Global stop | `POST /api/v1/stop` cancels the most recent active task without owning the runner lease; the Rust shell registers a global shortcut that calls it | E | passing |
| Sidecar handshake | Launcher writes a private per-launch token file; sidecar reads it, binds an ephemeral loopback port, and writes port+token back for the launcher | A | passing |
| Cloud capability | Onboarding lists authenticated model IDs and proves one fake read-only native tool call before the provider is usable | B | passing |
| Cloud onboarding | Selecting an arbitrary/stale model ID is rejected; the exact returned ID is persisted as non-secret profile metadata and takes effect on restart | B | passing |
| Local API trust | Wrong token, wrong origin, non-loopback client, and unauthenticated WebSocket cannot invoke or observe sidecar operations | A | passing |
| UIA threading | All UIA calls run only through one COM MTA worker; handles are reacquired for each fresh observation | D | passing |
| Voice assets | STT/TTS assets never download at startup; explicit installation verifies pinned version, license, and checksum | E | passing |
| Event stream | Slow/disconnected clients cannot delay work; replay from `after_sequence` reconstructs exactly the committed timeline | B | passing |
| Clean-machine release | Install, launch, stop, restart, upgrade, uninstall, and stale-sidecar recovery pass on an interactive Windows test machine | F | planned |

## Phase A: packaging and ledger spike

1. Tauri starts the fixed sidecar with fresh token and receives ephemeral port.
   *Implemented in Rust (`src-tauri/src/sidecar.rs`): writes the private
   token file, spawns exactly one `uvicorn`, polls `sidecar.json`, and exposes
   the resolved base URL + token via the single `get_bridge_config` command.
   Buildable via `npx --prefix frontend tauri build --no-bundle` (CI:
   `.github/workflows/ci.yml` → `tauri-build`).*
2. Sidecar accepts only loopback, authenticated, packaged-origin requests.
3. A SQLite WAL migration creates tasks, immutable events, lease, skills, and
   settings metadata with short transactions.
4. Single-instance/profile lock and clean shutdown leave no orphan sidecar.
   *The shell keeps the spawned `Child` and kills it on exit.*
5. No real OS mutation is registered or reachable.
   *Capabilities grant only `core:default`; shell/process/fs/automation
   plugins are not installed, so the renderer cannot touch the OS.*

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
- **D:** dedicated MTA UIA worker dispatch; locked-session / secure-desktop
  gate (input-desktop-name based); elevated/UAC/security-surface denial;
  dangerous-hotkey rejection; UIA-pattern-first with no coordinate fallback
  for invoke/set-value; single-shot bounded coordinate input with re-observe;
  bounded observation; OCR unavailability honesty. *Executed against a fake
  driver* (`backend/tests/test_gui_tools.py`, 43 tests) **and against the real
  driver on the dedicated fixture application**
  (`scripts/gui_fixture_app.py` +
  `backend/tests/acceptance/test_gui_fixture.py`, 4 tests, observe/focus/
  invoke/value effects verified). Fixture gate passed; `create_gui_tools()` is
  wired into the sidecar executor for general actions.
- **E:** visible-window voice capture, locally pinned VAD/STT/TTS, editable
  transcripts, local/global stop, privacy UX, and audit viewer. *Global stop:
  `POST /api/v1/stop` (`backend/tests/test_stop_api.py`, 5 tests) plus the
  Rust `Ctrl+Shift+X` shortcut wired to it (`src-tauri/src/main.rs`).*
- **F:** declarative skills, proposed-skill review, retention/purge, installer
  lifecycle, clean-machine verification, signing/distribution plan.
  *Executed: `backend/tests/test_skill_engine.py`, `test_skills_api.py`,
  `test_retention.py`, and `backend/tests/acceptance/test_skill_lifecycle.py`.
  Release/installer plan: `docs/release-plan.md`. Remaining: interactive
  clean-machine release run.*

## Evidence policy

Each passing test must retain only non-sensitive artifacts: test name, build
identifier, timestamp, redacted result, and reproducible steps. Never attach
secrets, raw audio, screenshots, OCR/UI trees, browser queries, password text,
or model reasoning.
