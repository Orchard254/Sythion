# JARVIS Release Plan

**Status:** Plan (Phase F). The sidecar backend, dashboard, voice, and skills
are feature-complete and test-backed; the packaged desktop shell is scaffolded
(`src-tauri/`, CI-buildable via the `tauri-build` job). Remaining gates: the
executed clean-machine release run and the signing/update pipeline.

## 1. Distribution model

- **App:** Tauri 2.x desktop shell (`frontend/` React app) that launches one
  fixed Python sidecar (`scripts/launch_sidecar.py`).
- **Runtime:** A pinned, signed Python 3.11 embedded runtime + required wheels
  packaged with the app. No user-installed Python dependency.
- **Signing:** All artifacts signed with an EV code-signing certificate
  (Microsoft Trusted Root). Unsigned builds are dev-only and must never ship.
- **Channel:** Single stable channel at first; MSIX + signed installer.

## 2. Installer (first install)

1. MSIX or WiX bundle runs per-user (`perMachine=false`) — no admin required.
2. Installs the signed Tauri exe, the embedded Python runtime, and the sidecar
   payload under `%LOCALAPPDATA%\JARVIS`.
3. Creates the `%LOCALAPPDATA%\JARVIS` profile (DB, logs, settings) with
   user-only ACLs (existing `backend/core/storage.py` behavior).
4. Registers a single auto-start entry so the tray app resumes the sidecar on
   sign-in (no service, standard-user only).
5. First launch performs the sidecar handshake (fresh per-launch control
   token) and the Cloud capability probe.

## 3. Upgrade

1. New versions are delivered as a signed installer/MSIX with a version bump;
   `GET /api/v1/version` drives the in-app update prompt.
2. Upgrade preserves `%LOCALAPPDATA%\JARVIS` (DB + settings + voice assets) —
   never re-installs the STT/TTS model or re-runs onboarding.
3. `db_manager._apply_migrations()` applies additive SQLite migrations at
   startup; no destructive schema change may ship.
4. The single-instance/profile lock prevents upgrade-over-running-instance;
   the updater stops the sidecar cleanly (lease released), replaces files, and
   relaunches.

## 4. Uninstall

1. MSIX/WiX uninstall removes app files and the auto-start entry.
2. **Keeps** `%LOCALAPPDATA%\JARVIS` profile data by default (user data is not
   silently destroyed); the uninstaller offers an explicit "also delete my
   data" checkbox, which then runs the full-record purge before deletion.
3. Sidecar is stopped cleanly before payload removal; stale leases are
   reclaimed by the next launch.

## 5. Code signing

- EV code-signing certificate from a Microsoft Trusted Root CA.
- Backend wheels are built and signed in CI (deterministic builds,
  `signtool` on the exe/dll/wheel artifacts).
- The manifest/handshake files never embed the certificate or any secret.
- Dev builds run with `JARVIS_DEV` and are visibly unsigned (no impersonation
  of the signed channel).

## 6. Distribution

- Stable channel artifact: signed installer + MSIX + release notes
  (user-visible change log per milestone).
- No telemetry; updates are opt-in and check `GET /api/v1/version`.
- A staged rollout with pinned minimum versions; downgrades are blocked.
- SBOM (pip wheels + frontend deps) attached to each release.

## 7. Remaining gates before release

- [x] Packaged Tauri shell (Phase A remainder): capabilities, CSP, shell/process
      denial, single sidecar, global-stop shortcut. *(`src-tauri/` scaffolded;
      `npx --prefix frontend tauri build --no-bundle` compiles it; CI
      `tauri-build` job is the executed build gate)*
- [ ] Interactive clean-machine run of all mandatory acceptance tests.
- [ ] EV signing pipeline + reproducible builds in CI.
- [ ] Version-check + update UX wired to `GET /api/v1/version`.
- [ ] Bundle the pinned Python runtime + wheels with the signed installer so
      the packaged shell's `python -m uvicorn` launch needs no user Python.
