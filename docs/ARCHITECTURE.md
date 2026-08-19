# Sythion (Synth) Architecture Decision Record

**Status:** Phase -1 decision record accepted; Phase A remediation in progress.
The Rust-free sidecar foundation (per-launch token handshake, ephemeral port
file, profile single-instance lock, LocalAppData storage with user-only ACLs,
version endpoint, desktop frontend build/lint) is implemented and tested. The
Tauri shell that drives the handshake is now scaffolded
(`src-tauri/`: Rust sidecar launcher, strict capabilities/CSP, bridge command,
global-stop shortcut) and buildable on `windows-latest` via the CI `tauri-build`
job; the interactive clean-machine release run remains the manual gate.

**Strategic framing:** this repository defines the **trusted runtime core** of a
larger intelligence system. The runtime boundary in this ADR remains strict:
local, auditable, evidence-driven execution on one Windows machine. Higher-order
planning, memory, and conversational intelligence may evolve above this layer,
but they must not bypass its policy, verification, or task-ledger guarantees.
See [`docs/INTELLIGENCE_ARCHITECTURE.md`](INTELLIGENCE_ARCHITECTURE.md).

## Decision

Sythion (Synth) v1, as implemented in this repository, is the Windows 10/11
**trusted local runtime core** and user-facing desktop operator for a broader
intelligence system. At this layer it is a visible, single-task application:
the owner submits a typed or in-window voice request, watches durable events,
can stop the work, and receives only verified outcomes or an explicit
failure/uncertainty.

This runtime layer is not an unbounded autonomous agent, browser agent, shell,
service, remote control system, scheduler, background listener, or
self-modifying system. Broader intelligence may be layered above it, but this
runtime contract remains deliberately narrow.

## Approved target shape

1. A packaged Tauri desktop application hosts the React dashboard and starts
   exactly one fixed Python/FastAPI sidecar.
2. Tauri creates a fresh random control token, passes it only to that sidecar,
   and receives its ephemeral loopback port through a startup handshake.
3. The sidecar binds only to `127.0.0.1`, validates the control token and the
   packaged-app origin for every HTTP and WebSocket request, and owns the
   task runner, SQLite ledger, policy, model adapter, and tool executor.
4. The task runner alone changes task state and appends immutable events. A
   profile-wide lease allows one `running` task and supports safe recovery.
5. The model can select exactly one validated control or policy-filtered tool
   call per turn. It cannot execute actions, override policy, or claim success
   without recorded verification. The model never sees a generic `execute_tool`
   wrapper: only the policy-filtered read-only tool subset plus the four control
   functions (`request_user_input`, `run_skill`, `complete_task`, `fail_task`),
   each with a Pydantic schema. Tool output reaches the model only as a bounded,
   redacted `model_summary`; full audit data stays out of model context.
6. Tools make one bounded action with typed inputs/outputs, deterministic
   policy, fresh verification, cancellation handling, and recovery guidance.
7. Voice capture exists only while the visible dashboard is open and voice
   mode is enabled. VAD, STT, stop detection, and final-result TTS are local.

## Deliberate boundaries

- The frontend has no shell, process, generic filesystem, or automation
  capability. Tauri's fixed sidecar launch is the only child process.
- Only `winget`, using fixed arguments, exact package IDs, and approved
  sources, may be a command-line subprocess tool.
- GUI work targets only visible, foreground, medium-integrity native Windows
  applications. UI Automation runs on one dedicated COM MTA worker; OCR and
  coordinates are tightly bounded fallback mechanisms.
- Persistent data is in user-local application data with user-only Windows
  ACLs. Secrets use Windows Credential Manager or equivalent DPAPI-backed
  storage; they never enter `.env`, SQLite, normal logs, task events, exports,
  or model context.
- Skills are validated declarative workflows over registered tools. They have
  no executable code, expressions, loops, branches, secrets, or automatic
  enablement.

## Current implementation assessment

The checked-in prototype is **not eligible to claim Phase 3 completion** and
must be re-baselined to Phase -1/A remediation. It contains useful exploratory
components, but these gaps prevent a safe milestone gate:

| Finding | Consequence | Required remediation |
| --- | --- | --- |
| No Tauri project or packaged sidecar handshake | Browser dashboard is not the required local trust boundary | Build and test the Tauri shell before enabling mutation tools |
| No packaged token handshake exists yet | The authenticated API boundary now rejects missing/wrong tokens and untrusted origins, but the browser launcher cannot securely supply a per-launch token | Build the Tauri shell and startup handshake before production use |
| API keys are documented/configured via `.env` | Production secret-handling policy is violated | Replace with Credential Manager-backed write-only secret storage |
| Fallback database/log directory is `~/.sythion` | Required LocalAppData location and ACL policy are not enforced | Move storage, create user-only ACLs, and test failure handling |
| Cross-platform app and GUI subprocess paths exist | v1 Windows-only and `winget`-only subprocess boundary is violated | Remove or isolate non-Windows paths; register Windows apps by identity only |
| GUI fallbacks use generic input simulation | No proven UIA MTA ownership, target freshness, integrity, or one-action safety | Rebuild GUI adapter around a dedicated UIA worker and fixture-only tests |
| Optional/stub voice components and unpinned downloads | Voice lifecycle, local-only processing, and asset integrity are unproven | Select, pin, checksum, and explicitly install assets before voice milestone |
| Documentation reports conflicting phase/test counts | The release status is not reliable | Replace Phase claims only after the listed gates are executed |

## Tauri desktop shell (implemented)

The packaged shell (`src-tauri/`) is the only production entrypoint:

- **Sidecar launch + handshake** (`src/sidecar.rs`): writes a fresh random
  token to `%LOCALAPPDATA%\Sythion (Synth)\token.json`, picks a free loopback port,
  spawns exactly one `python -m uvicorn backend.main:app` with
  `SYTHION_HANDSHAKE_TOKEN_FILE` + `SYTHION_PORT` (never the token on the command
  line), and polls `sidecar.json` for the bound port. The spawned `Child` is
  kept and killed on app exit, so no orphan sidecar survives.
- **Bridge command** (`get_bridge_config`): the single IPC surface the
  renderer may call. Returns the sidecar base URL + control token; the frontend
  polls it until the sidecar reports ready (`frontend/src/services/bridge.ts`).
- **Global stop** (`src/main.rs`): registers `Ctrl+Shift+X` via
  `tauri-plugin-global-shortcut`; on press it calls `POST /api/v1/stop` with the
  handshake token. `/stop` cancels the most recent non-terminal task without
  owning the runner lease (the running executor renews the lease itself), so
  the shortcut works exactly when a task is active.
- **Strict capabilities/CSP**: `capabilities/default.json` grants only
  `core:default`; the shell/process/fs/automation plugins are not installed at
  all, so the renderer cannot reach the OS beyond the read-only bridge command.
  `tauri.conf.json` sets a CSP that pins `script-src 'self'` and allows
  `connect-src` only to loopback.

Dev still runs through `start_sythion.cmd` (browser + Vite proxy); it is
explicitly dev-only and does not grant desktop capabilities.

## Cloud onboarding (implemented)

Onboarding (`GET /api/v1/onboarding`, `POST /api/v1/onboarding/models`)
lists the authenticated Cloud model IDs and requires selection of one exact
returned ID; an arbitrary, stale, or retyped ID is rejected. Only the
non-secret model ID is persisted, to `profile.json` under the user-local data
directory (`backend/core/profile.py`). `resolve_selected_model` is the single
resolution point: environment override first, then persisted profile metadata.

## Per-task budgets (implemented)

Each task carries a `TaskBudget` (`backend/core/budgets.py`) tracking Cloud
request count, input/output tokens, executed actions, repair attempts, and
wall-clock elapsed time against the limits in `config.py`. The execution loop
checks budgets before every iteration and records a durable, evidence-safe
`task.budget` snapshot event on terminal states. Exceeding any budget fails the
task honestly with the matching code (`ACTION_LIMIT_EXCEEDED`,
`CLOUD_REQUEST_LIMIT_EXCEEDED`, `INPUT_TOKEN_LIMIT_EXCEEDED`,
`OUTPUT_TOKEN_LIMIT_EXCEEDED`, `ELAPSED_TIME_EXCEEDED`) -- never a retry, a
local fallback, or an unrecorded mutation.

## Target validation (implemented)

Every tool action passes a deterministic, side-effect-free target gate before
any OS call. `BaseTool.validate_targets(input_data)` runs in
`ToolExecutor.execute_tool` after input schema validation and before
`execute()`; it returns an error dict that becomes the tool output without
touching the OS. File tools reject empty paths, wildcards/bulk selection
(`TARGET_REQUIRED`, `TARGET_WILDCARD_REJECTED`), and any path outside the
approved roots (`TARGET_OUTSIDE_ROOTS`). App, package, and GUI targets reject
malformed IDs and missing window/element titles (`INVALID_APP_ID`,
`INVALID_PACKAGE_ID`, `TARGET_REQUIRED`, `ELEMENT_TARGET_REQUIRED`). Policy
gating (e.g. `DIRECT_INSTRUCTION_REQUIRED`, `ELEVATION_NOT_ALLOWED`) still
runs first and stays authoritative.

## Cancellation boundary (implemented)

Cancellation is checked at every boundary before an OS action. The execution
loop re-checks task state after the model call, `_execute_tool` refuses to
commit `action_started` for an already-cancelled task, and the executor calls
`PolicyContext.cancelled_check` after policy + target validation and
immediately before `tool.execute` -- returning `CANCELLED` without touching the
OS. Cancellation and interruption are durable via the immutable event stream
and survive lease takeover.

## Controlled file mutations (implemented)

File writes use atomic temp-file + `os.replace` semantics
(`backend/tools/file_tools.py`): the target only ever shows old or new content,
never a partial write. `file_create`/`file_write` refuse to clobber an existing
file without an explicit `overwrite`, and the active cancellation check is
awaited right before the final replace so a cancel mid-write leaves the prior
content intact (no orphaned temp file). Every mutation records fresh
post-mutation verification by reading the committed bytes back
(create/write/move/copy) or confirming the target no longer exists (delete),
reported in `evidence.verification`.

## Canonical file identity rechecks (implemented)

Beneath the deterministic path-string gate, every file execution re-verifies
containment with real OS handles (`CreateFileW` + `GetFinalPathNameByHandleW`
+ `GetFileInformationByHandle`). The OS-reported final location of the target
(or its nearest existing ancestor for writes) is compared case-insensitively
against each approved root's final location, so 8.3 short-name aliases and case
tricks cannot bypass containment. A target that is itself a symbolic link,
junction, or reparse point is rejected outright (`TARGET_REPARSE_POINT_REJECTED`).
Deletes default to the Recycle Bin (refusing silent permanent fallback) and
record `recoverable` in their evidence.

## Registered application identities (implemented)

App discovery reads the Windows uninstall hives (`app_tools.py`), and
`app_launch` resolves strictly against those registered identities -- the model
can never supply an executable path or command-line arguments, so `app_id` is
validated to a plain registered display name. The resolved executable comes
from the registry and is launched with no arguments and no shell. Graceful
`app_close` posts `WM_CLOSE` and then probes: a still-open window is surfaced
as an unsaved-work prompt (`UNSAVED_WORK_PROMPT`) that the user must answer;
Sythion (Synth) never auto-chooses Save or Discard and never escalates to a force kill.
Non-Windows app paths are removed.

## Package restrictions (implemented)

Package actions are fixed-argument `winget` operations with exact ids and no
free-form flags (`package_tools.py`). Agreement/elevation prompts are never
auto-accepted: when winget requires them it fails and the result is surfaced.
Chocolatey, Homebrew, apt, and any `sudo`/`-y` invocation are removed. The
executor policy gate additionally blocks install/uninstall with
`ELEVATION_NOT_ALLOWED` (standard-user actions only) as defense in depth.

## Reliable Windows GUI automation (implemented)

GUI tools (`backend/tools/gui_tools.py`) are Windows-only in v1. Every UI
Automation call runs on one dedicated background COM MTA worker thread
(`_UIAWorker`, thread name `sythion-uia-mta`), never on the FastAPI event loop
or the dashboard UI thread. Live UIA handles never cross threads: the driver
re-finds the target window from fresh evidence on every call and returns only
plain data; tool code dispatches via `asyncio.to_thread` so the loop is never
blocked.

Safety rules enforced in code:

- Desktop gate: locked sessions or the secure desktop deny every GUI action
  (`SESSION_LOCKED`). The gate uses the *input desktop name* (`Winlogon`/
  `Secure` = locked or UAC secure desktop) rather than `GetForegroundWindow`,
  which can be 0 for legitimate background processes.
- Target window safety: hidden, minimized, elevated (integrity RID >= 12288),
  UAC (`Consent UI Class`), and secure-desktop/security-title surfaces are
  denied (`TARGET_NOT_VISIBLE`, `TARGET_MINIMIZED`,
  `ELEVATED_TARGET_DENIED`, `SECURITY_SURFACE_DENIED`).
- Patterns first: `gui_invoke`/`gui_set_value` use the native UIA
  Invoke/Value patterns and never fall back to a guessed click or coordinate
  entry (`NO_INVOKE_PATTERN`, `NO_VALUE_PATTERN`). Pattern handles returned by
  `GetCurrentPattern` are queried to their concrete comtypes interface before
  use.
- Bounded fallback: `gui_click` is a single physical-pixel click bounded to
  the window rect; `gui_type`/`gui_hotkey` are single input bursts. All
  foreground input tools require the target window to be foreground
  (`FOREGROUND_REQUIRED`), report `inputs: 1`, and return a fresh
  re-observation (`reobserved`) after the input — never a guessed second
  click.
- Hotkeys: `ctrl+alt+delete`, `ctrl+shift+esc`, and `win+l` are rejected
  (`DANGEROUS_HOTKEY_REJECTED`); every key must be on a fixed whitelist.
- Observation is bounded (`_MAX_UI_TREE_DEPTH=3`, `_MAX_UI_ELEMENTS=60`,
  `_MAX_WINDOW_TITLES=20`) and every identity records `hwnd`, `pid`, runtime
  id, and `observed_at`. OCR/visual observation is capped at
  `_OCR_MAX_AGE_SECONDS=5.0`; the OCR engine is not bundled in v1, so
  `gui_observe(use_ocr=True)` returns `available: false` with an honest reason
  instead of guessing or persisting raw screenshots.
- Window discovery uses `TreeScope_Descendants` from the UIA root because on
  some desktop layouts the root's direct child is a virtual desktop pane and
  top-level windows are nested under it.

General enablement gate (TODO #82) passed: `create_gui_tools()` is wired into
the sidecar executor (`main.py`, `api/routes.py`) after the dedicated fixture
application (`scripts/gui_fixture_app.py`) exercised the real driver over the
real MTA worker. GUI tools remain excluded from the model-visible subset by
`requires_screen_access`/`requires_ui_automation`.

Known v1 limitation: coordinate mapping (`_absolute_xy`) normalizes DPI
against the primary monitor's virtual screen; true per-monitor DPI awareness
is deferred.

## Voice and privacy (implemented)

The dashboard owns microphone permission and capture. The sidecar never opens
the microphone: it only receives short mono 16 kHz PCM frames over the
authenticated loopback connection (`POST /api/v1/voice/{session}/audio`) and
returns completed-text segments for the editable transcript.

- **Pinned assets, no startup downloads** (`backend/core/voice_assets.py`): one
  STT model (`vosk-model-small-en-us-0.15`, Apache-2.0) is pinned by version,
  license, and SHA-256 (`30f262…942498`). TTS is the OS-local SAPI5 engine
  (pyttsx3); preferred voices are pinned by name. Nothing downloads at import
  or startup; `POST /api/v1/voice/assets/install` is the single explicit,
  owner-initiated, checksum-verified install path. The install refuses
  tampered archives and path-traversal members and writes a verification
  marker only after a verified extraction.
- **Local VAD + completed-segment STT** (`backend/services/voice_pipeline.py`):
  a session-scoped WebRTC VAD (webrtcvad) assembles utterances; a segment
  completes after a sustained-silence threshold, then the local Vosk STT
  transcribes it. There is no word-by-word interim promise — the transcript is
  a near-live editable draft at segment completion.
- **Local spoken stop**: every completed segment runs through
  `detect_stop_phrase()` (word-boundary check for `stop`, `halt`, `cancel`,
  `stop it`, `please stop`) and the result is returned as `stop_detected` so
  the dashboard can cancel instead of submitting.
- **Raw-audio lifecycle**: raw PCM is held in memory only while an utterance
  is being assembled and is discarded immediately after transcription. A
  separate owner setting (`voice_recording_enabled`) can opt into retaining
  segment audio; session end hard-deletes whatever remains. Raw audio never
  crosses the API boundary.
- **Local TTS**: `POST /api/v1/voice/speak` uses SAPI5 (pyttsx3) only, and the
  endpoint bounds text length to keep final-result narration concise.
- **Settings** (`backend/core/settings_store.py`): whitelisted non-secret keys
  (voice toggles, TTS voice, retention, file roots) persist to the
  `settings_metadata` table; secrets remain in the write-only secret store.

Executed gates: `backend/tests/test_voice_pipeline.py` (22), 
`backend/tests/test_voice_assets.py` (10), `backend/tests/test_voice_api.py`
(10), and `backend/tests/acceptance/test_voice_privacy.py` (5). The acceptance
map "Voice assets" row is marked passing.

## Declarative skills and retention (implemented)

The prototype `skills_system.py` (executable Python handlers) was replaced by
a declarative skill stack: `backend/services/skill_runtime.py` owns validated,
versioned workflow execution over the policy-filtered read-only tool subset,
`backend/services/skill_proposals.py` owns proposal growth/promotion, and
`backend/services/skill_engine.py` remains the compatibility facade used by
existing callers.

- **Restrictive references**: step inputs are literal scalars or exactly one of
  `inputs.<field>` (a declared skill parameter) or
  `steps.<step_id>.output.<field>` (output of an earlier step). Forward and
  unknown references are rejected.
- **Rejected content**: executable code (`eval(`, `exec(`, `__import__`,
  `subprocess`, backticks, …), template/expression syntax (`{{ }}`), nested
  structures, coordinate keys/literals, and secret-shaped keys are refused at
  validation time. Every definition is validated before storage and its
  `definition_hash` is recorded on the version row.
- **Version pinning**: `execute_skill` pins the enabled validated version to
  the task (`tasks.selected_skill_version`). Before every unstarted step the
  pin is re-verified in the DB; if the skill was disabled or the enabled
  version changed, the task transitions to `PAUSED` (`task.paused` event) and
  never runs against a stale definition. Re-enabling lets the owner resume
  (`POST /api/v1/tasks/{id}/resume`).
- **Proposals require review**: a proposed skill is generated only after ≥2
  materially similar *verified successful* traces (`maybe_generate_proposal`,
  called on task completion). Proposals are never executed; they become live,
  versioned skills only through explicit `POST /api/v1/skills/proposals/{id}/approve`
  (or are marked `rejected`). Owner-authored definitions also enter as
  proposals.
- **Retention + purge** (`backend/services/retention.py`): owner-configurable
  `retention_days` (default 90) purges complete task+event records older than
  the window. `POST /api/v1/retention/purge` and
  `POST /api/v1/tasks/{id}`-scoped purge write **non-sensitive purge receipts**
  (`purge_receipts`: task id, reason, time, event count) — never request text.
- **API**: `GET/POST /skills`, `GET /skills/proposals`,
  `POST /skills/proposals/{id}/approve|reject`, `POST /skills/generate`,
  `POST /skills/{id}/state`, `POST /retention/purge`, `GET /retention/receipts`.

Executed gates: `backend/tests/test_skill_engine.py` (38),
`backend/tests/test_skills_api.py` (13), `backend/tests/test_retention.py` (13),
and `backend/tests/acceptance/test_skill_lifecycle.py` (6). The acceptance map
"Skill lifecycle" and "Retention & purge" rows are marked passing. The release
and distribution plan lives in `docs/release-plan.md`.

## Milestone gate

No feature work beyond Phase A may proceed until the owner approves this
record and the acceptance-test map. Existing prototype code remains a source
of implementation lessons, not proof of acceptance.

## Secret storage (implemented)

API keys are no longer documented or configured via `.env`. The production
backend stores secrets in Windows Credential Manager (write-only by contract)
via `backend/core/secrets.py`; the value is never returned by any endpoint,
never written to SQLite, logs, task events, exports, or model context, and is
only resolvable at the one sanctioned point (`resolve_ollama_api_key`). A
non-persistent in-memory backend is used for tests so the real vault is never
touched. Write-only endpoints: `GET /api/v1/secrets`, `PUT
/api/v1/secrets/{name}`, `DELETE /api/v1/secrets/{name}`.

## References

- Product requirements and non-negotiable safety rules:
  `C:\Ai\SYTHION_FINAL_HANDOFF_BRIEF.md`
- Executable verification plan:
  `C:\Ai\sythion\docs\acceptance-test-map.md`
