# JARVIS Architecture Decision Record

**Status:** Phase -1 decision record accepted; Phase A remediation in progress.
The Rust-free sidecar foundation (per-launch token handshake, ephemeral port
file, profile single-instance lock, LocalAppData storage with user-only ACLs,
version endpoint, desktop frontend build/lint) is implemented and tested. The
packaged Tauri shell that drives the handshake is the remaining Phase A item.

## Decision

JARVIS v1 is a Windows 10/11, standard-user personal desktop assistant. It is
a visible, single-task application: the owner submits a typed or in-window
voice request, watches durable events, can stop the work, and receives only
verified outcomes or an explicit failure/uncertainty.

The product is not an autonomous agent, browser agent, shell, service, remote
control system, scheduler, background listener, or self-modifying system.

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
| Fallback database/log directory is `~/.jarvis` | Required LocalAppData location and ACL policy are not enforced | Move storage, create user-only ACLs, and test failure handling |
| Cross-platform app and GUI subprocess paths exist | v1 Windows-only and `winget`-only subprocess boundary is violated | Remove or isolate non-Windows paths; register Windows apps by identity only |
| GUI fallbacks use generic input simulation | No proven UIA MTA ownership, target freshness, integrity, or one-action safety | Rebuild GUI adapter around a dedicated UIA worker and fixture-only tests |
| Optional/stub voice components and unpinned downloads | Voice lifecycle, local-only processing, and asset integrity are unproven | Select, pin, checksum, and explicitly install assets before voice milestone |
| Documentation reports conflicting phase/test counts | The release status is not reliable | Replace Phase claims only after the listed gates are executed |

## Cloud onboarding (implemented)

Onboarding (`GET /api/v1/onboarding`, `POST /api/v1/onboarding/models`)
lists the authenticated Cloud model IDs and requires selection of one exact
returned ID; an arbitrary, stale, or retyped ID is rejected. Only the
non-secret model ID is persisted, to `profile.json` under the user-local data
directory (`backend/core/profile.py`). `resolve_selected_model` is the single
resolution point: environment override first, then persisted profile metadata.

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
  `C:\Ai\JARVIS_FINAL_HANDOFF_BRIEF.md`
- Executable verification plan:
  `C:\Ai\jarvis\docs\acceptance-test-map.md`
