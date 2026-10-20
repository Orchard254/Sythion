# Startup rendering and installed-path evidence (1366x768)

Captured on the reported desktop resolution (primary display: 1366x768).

All screenshots are of the **packaged** Windows build
(`src-tauri/target/release/sythion.exe`, the same binary the NSIS installer
ships), launched with an isolated profile so first-run setup is reached.

| | |
|---|---|
| Before | `before-packaged-1366x768.png` (whole screen) and `before-packaged-1366x768-window.png` (Control Center only) |
| After | `after-packaged-1366x768.png` (whole screen) and `after-packaged-1366x768-window.png` (Control Center only) |
| Companion revealed | `after-companion-activated-1366x768.png` |
| Data-directory conflict | `after-data-dir-conflict-dialog.png` |
| Installed app, first run | `installed-first-run-1366x768.png` |
| Installed app, Limited mode | `installed-limited-companion-1366x768.png` |
| Installed app, **no credentials** | `installed-first-run-no-credentials-1366x768.png` and `installed-limited-mode-no-credentials-1366x768.png` |

## Installed application without credentials

The last two screenshots come from the NSIS-installed application launched
with `SYTHION_SECRET_BACKEND=memory`, a controlled credential provider: the
backend uses the in-memory store, so it cannot read the current Windows user's
Credential Manager entries. The readiness report then shows
`ollama_api_key_ok: false`, `ollama_health_ok: false`,
`selected_model_ok: false` and `full_ready: false`, and the "Enter Sythion
(Full Ready)" action stays disabled. Nothing in the user's vault is read,
written, displayed or exported.

## Before

The packaged build showed all three reported defects at once:

1. **The setup screen was clipped to the top of the window.** Only the
   "First-run setup" heading and a sliver of the sub-heading were painted; the
   rest of the viewport was blank. The window enumeration shows the Control
   Center at `130,0 1216x788`.
2. **A blank terminal window was in front of the application.** Its window
   title is the bundled runtime's `python.exe`, which is the sidecar Sythion
   itself starts:
   `class=CASCADIA_HOSTING_WINDOW_CLASS title='C:\Sythion\sythion\src-tauri\resources\python-runtime\python.exe'`
3. **The Companion overlay was already on screen** while setup was still
   incomplete: `class=Tauri Window title='Sythion Companion' rect=982,364 360x380`.

## After

1. **The full setup screen renders** in the viewport: panels 1-3 with their
   inputs and actions, panel 4 below the fold and reachable through the
   scrollbar. The window is `81,0 1216x732`, which fits the work area
   (`1366x720`, the desktop minus the 48px taskbar).
2. **No console window** is created. The only terminal on screen is the
   user's own shell.
3. **No Companion overlay** while setup is incomplete.

## Companion after the setup gate clears

`after-companion-activated-1366x768.png` shows the state after the setup gate
is dismissed ("Continue in Limited - Text-First mode"): the Control Center is
fully live and the Companion overlay is back at its approved position and size
(`982,364 360x380`), with its design unchanged.

## Conflicting profile directory

`after-data-dir-conflict-dialog.png` shows the fail-fast diagnostic when
`SYTHION_DATA_DIR` and `Sythion (Synth)_DATA_DIR` name different directories.
The application refuses to start and names both variables and both paths.

## Installed application

`installed-first-run-1366x768.png` and `installed-limited-companion-1366x768.png`
are the **NSIS-installed** application, installed with
`/S /D=<isolated directory>` and launched with only `SYTHION_DATA_DIR` set to an
isolated profile.

- First run: the setup screen renders in full, the window is `81,0 1216x732`,
  no console window and no Companion overlay.
- After the setup gate clears: the Control Center is live and the Companion
  overlay is at `982,364 360x380`.
