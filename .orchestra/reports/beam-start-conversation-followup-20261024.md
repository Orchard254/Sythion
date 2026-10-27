# Beam followup diagnostic: packaged Start conversation

Date: 2026-10-24  
Scope: one isolated native diagnostic plus read-only static checks; no broad tests, builds, production edits, commits, vault mutation, or respawn changes.

## Correction of earlier overclaim (previous report)

The previous report stated that the screenshot was captured after the sidecar had stopped and that all subsequent requests reached a closed port. That is **not proven** by the evidence in hand.

- Current closed port `127.0.0.1:56566` proves only current liveness, not when the sidecar exited.
- There is no captured sidecar exit timestamp, no client request timestamp, and no network trace. The sidecar log ended at `12:09:20` with a normal handled `Voice asset installation failed: Archive does not contain a usable Vosk model layout` error; the route returns `HTTP 502`, which should not terminate uvicorn. Whether the process exited immediately, later, or was stopped by external/user action is unknown.
- The processes observed today could be a different lifecycle than the click; direct HTTP observations below show why liveness alone should not be treated as proof of behaviour at the click.

## Isolated diagnostic performed

Launch identity and isolation:
- Launch PID 648: `src-tauri/target/release/sythion.exe`, started `12:31:13` local.
- Fresh test profile root: `%TEMP%\opencode\sythion-iso-20261024123113`.
- Isolation verified before launch: `SYTHION_DATA_DIR` and a separate `logs` directory pointed only at that fresh root; legacy `Sythion (Synth)_DATA_DIR` was removed from the child environment; `SYTHION_AUTONOMY_LEVEL=guarded`; `background_orchestration_enabled` stayed at the default false; ambient microphone/speech download was not exercised; no real profile or vault was mutated.
- `SYTHION_SECRET_BACKEND=memory` so the secret backend was in-process only. The test key used was supplied through the environment and was not written to Windows Credential Manager.
- Accessibility/background defaults were left false.
- WebView2 argument: `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--auto-open-devtools-for-tabs`.

Process limitation:
- The existing 11888 Sythion process held the WebView2 user-data directory and the global stop shortcut. By design a second UI did not take over that instance. The probe's shell log records `failed to create webview: WebView2 error ... 0x8007139F` and exited with its own sidecar left isolated. The existing user app and process 11888 were not stopped.
- The isolated shell still spawned exactly one sidecar. Its handshake succeeded in one progression: listen port 54156, health `healthy`, database connected, selected model present, cloud configured true.

Direct invocation from a packaged webview origin:
- All requests used Origin `tauri://localhost` and the per-launch `X-Control-Token`.
- Health: `200`, selected model present, cloud configured true.
- Setup readiness: `200`; after the two tiny live verification calls (safe settings and one inference call) all readiness checks except optional `stt_model_installed` were true.
- Text-chat route surface tested: `/api/v1/tasks`, `/agent`, `/agent/health`, `/version`, `/voice/assets` all returned `200`.
- Preflight (`OPTIONS`) from `tauri://localhost` returned `access-control-allow-origin: tauri://localhost`.
- `http(s)://localhost:3000` origins were rejected with `403`, confirming this binary was running in packaged trust mode rather than development mode.
- The optional STT install endpoint was not invoked, to avoid a multi-minute download and repository mutation; its blocking behaviour is identified statically below.
- All cleanup of the probes's own PIDs (`648` and sidecar `11112`) was completed. Port 54156 is closed, and the user profile/process 11888 remains running.

## Route hash evidence (source vs staged vs released backend)

- `backend/api/routes.py` = source/staged `src-tauri/resources/backend/api/routes.py` = released `target/release/backend/api/routes.py`: `3882031E4FD36788292DDBC74F116CAC1C810011D6172D15F573A8A69755A47B`.
- `backend/main.py`: source `277375E59AB8C7A20FE824BEBB386DE382E6299BB38CBC598C1945272CA1EA18`; staged/released `6051943499AC94A876A6BFD0BD8455336992B96BA3BB6F3C5AD69CE646CC2495` (the known 33-line vault-write marker drift).
- The memory #1120 build-mode/CORS fix is present in this binary's runtime behaviour: packaged origins work and development origins are rejected.

## Root cause for the observed Network Error

The evidence supports a blocking optional-voice-install path rather than an initial trust-list defect:

1. `POST /api/v1/voice/assets/install` in `backend/api/routes.py` is an `async def` that calls `install_voice_assets()` synchronously. That function calls `urllib.request.urlretrieve()` and extraction, a continuous blocking operation; in the user run it took from `12:06:27` to `12:09:20` (about 2 minutes 53 seconds).
2. During that time uvicorn's request path in that handler cannot accept or complete other event-loop work; requests sent while it is blocked can return as Axios `Network Error` even though the process has not exited.
3. This matches the user sequence: icon green showed the bridge used earlier; the optional model import could be started on the same connection; then required API responses were stalled until the archive validation finally failed and the route returned `502`.
4. The sidecar process was not stopped by that route. The product-level "Network Error" is therefore most consistent with a synchronous request stalling the event loop (and/or a later unrelated sidecar exit that the current evidence cannot timestamp), not with a failed origin lookup.
5. The stale-`isConnected` behaviour in `frontend` is a secondary display defect: once the request fails, the UI still thinks live mode is available until another init.

Minimal exact edit proposed:
```python
# backend/api/routes.py
@router.post("/voice/assets/install", response_model=VoiceAssetsResponse)
async def voice_assets_install():
    from backend.core.voice_assets import install_voice_assets
    import asyncio

    try:
        status = await asyncio.to_thread(install_voice_assets)
    except Exception as e:
        logger.error("Voice asset installation failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Voice asset install failed: {str(e)}")
    return VoiceAssetsResponse(**status)
```

Secondary display-only improvement:
- In `frontend/src/hooks/useAppInit.ts`, mark `isConnected=false` when a post-init health probe fails, so no API burst is attempted against a stalled/dead sidecar before the offline screen renders.

Recommended residual handling:
- Reconcile the documented 33-line `backend/main.py` drift in the packaged resource.
- Add a backend unit/E2E assertion that `/voice/assets/install` does not execute blocking network calls on the event loop.
- Consider adding a sidecar liveness heartbeat/watchdog if stronger evidence shows the process exits separately from the blocked request, but that change is not yet justified by the evidence captured here.

## Residuals

- We could not open the packaged WebView2 UI a second time because the existing user app owns the shared WebView2 environment; the global shortcut conflict and `0x8007139F` error are environmental single-instance constraints, not sidecar defects. The process `11888` was intentionally preserved.
- No actual frontend Start-conversation click was replayed. Direct endpoint evidence supports the text path, not that button.
- The precise exit timestamp of the original sidecar remains unknown and is still an evidence gap.