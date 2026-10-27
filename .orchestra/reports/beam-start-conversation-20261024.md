# Beam investigation: Start conversation Network Error (packaged desktop)

Date: 2026-10-24  
Scope: read-only targeted investigation; no product edits or broad tests/runs performed.

## Exact operation triggered by the button

`frontend/src/screens/Setup.tsx:184`: the `Start conversation` button calls `finish()` (`:91`).

`finish()`:
1. `refresh()` → `setupService.readiness()` → `GET /api/v1/setup/readiness` (`frontend/src/services/api.ts:394-397`).
2. Validates `canStartChat(report)`.
3. `localStorage.setItem('sythion.replyMode', ...)`, app-store resets, then `onComplete(replyMode === 'text')`.
4. `App.tsx:218-222` writes `sythion.textFirst`, calls `setRoute('assistant')`, clears `setupNeeded`; assistant screen mounts and fires `/tasks`, `/agent`, `/agent/health`, `/version`, `/voice/assets` via React Query.

The displayed `Network Error` is Axios's no-response error (`frontend/src/services/api.ts:129-143`; no `.response`). That means either the frontend never received an HTTP response (dead sidecar, browser-blocked request, failed preflight) or the bridge base URL is invalid.

## Observed evidence

- Packaged runtime identity during this run:
  - main shell process: `src-tauri/target/release/sythion.exe`
  - sidecar manifest: `C:\Users\orcha\AppData\Local\Sythion (Synth)\sidecar.json` reports port `56566`
  - sidecar log tail: started at `12:04:26`, reached application startup at `12:04:28`, then attempted a voice asset download at `12:06:27`
  - at `12:09:20`: `backend.api.routes / Voice asset installation failed: Archive does not contain a usable Vosk model layout`
  - no process is listening on `127.0.0.1:56566` now; there is no uvicorn/python child process for the sidecar in the process list
  - screenshot was captured at `12:15:49`, after the sidecar had already stopped
- Source/resources synchronization:
  - `backend/api/routes.py` and `backend/core/auth.py` are hash-identical between source and `src-tauri/resources/backend/...`
  - `backend/main.py` is the only runtime file with drift: resource lacks 33 lines that are purely the vault-write marker/exception-path logic; resource main still has the same startup path.
- Historical/development comparison:
  - `onboarding-dev-e2e-complete-20261023.md` proves the same source flow passes against dev Vite + backend, including `/setup/readiness`, safe settings, Start conversation, and assistant chat.
  - Memory #1120 describes an already-fixed build-flag/CORS origin mismatch via `tauri::is_dev()`; source still contains that derivation (`src-tauri/src/sidecar.rs:434-461`) and unit coverage. It should not be blindly reapplied.

## Probable cause (best current evidence)

The displayed network error is most likely a symptom of the packaged sidecar being dead during the click, not an initial CORS/origin defect:
- the UI had already passed live `/setup/readiness` enough to paint checks green, indicating the bridge was alive earlier;
- the last backend activity was the failed optional STT download at `12:09:20`;
- all subsequent API calls reached a closed port (for example the readiness refresh or assistant `/tasks` queries) and therefore surface as axios `Network Error`;
- `frontend/src/hooks/useBackend.ts` continues to treat `isConnected` as true unless `useAppInit` runs again, so stale success masks the offline runtime until the first failing request.

This differs from the earlier dev-mode pass, which exercised only text-first onboarding and did not trigger packaged runtime voice asset download/state.

## Unverified / evidence limitations

- The failed request URL, status/CORS details, webview Origin, and console were not captured: the sidecar was already closed before investigation, and no live network trace exists.
- It is not proven whether the sidecar exited specifically because of the voice archive miss, memory pressure, or user action; the backend error was handled normally and should not itself terminate uvicorn.
- `backend/main.py` drift is limited to exception marker code and is unlikely to explain this failure; nevertheless it should be reconciled.
- Whether the optional speaker/microphone flow was actually invoked before Start could not be confirmed from logs or screenshot (image content was not inspectable from this model).

## Cheapest safe diagnostic (one)

Re-run the packaged app once with no optional STT installation and a fresh disposable profile/data directory, open WebView2 DevTools via `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--auto-open-devtools-for-tabs`, then perform only the text-first path and click `Start conversation` once. Capture the failed request's URL, Origin, HTTP status/preflight result, and backend `sidecar.err.log` around the click. This isolates whether the failure reproduces with a live sidecar. Do not repeat full regression or mutation of the normal profile.

## Candidate minimal repair if the diagnostic confirms total request failure

Pending diff review:
- Runtime robustness: make `useAppInit`/`useHealth` mark `isConnected=false` after an actual failed health probe instead of only at initial init, so `/Start conversation` cannot attempt API calls against a dead sidecar without surfacing the offline screen (`frontend/src/screens/Setup.tsx`, `frontend/src/hooks/useAppInit.ts`).
- Sidecar liveness: add a bounded sidecar respawn/recovery path in the shell watcher for a process that died after startup (`src-tauri/src/sidecar.rs:564-593`), or explicitly disable/reject optional voice install unless its downloaded archive passes the pinned layout check.
- Product UX: have `SetupScreen.finish` map `AxiosError` with no response to an explicit "Sythion backend stopped; retry connection" message instead of allowing raw `Network Error` (`frontend/src/screens/Setup.tsx:46-51`, `frontend/src/services/api.ts:114-143`).

## Candidate minimal repair if the live-sidecar diagnostic shows the sidecar is healthy

Then the request is browser-blocked / bridge-specific. Compare the actual webview Origin from DevTools with `PACKAGED_APP_ORIGINS` in `backend/core/auth.py:16-20` and `development_mode_flag()` output (`src-tauri/src/sidecar.rs:451-461`). If they disagree, the smallest fix is likely in the trust-list/feature-flag boundary, not in frontend/API code. Also reconcile `backend/main.py` resource drift while there.
