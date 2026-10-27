# voice-install-offload-20261024

## Commit
- Branch/worktree: `voice-install-offload`
- Base SHA: `84efd2819c9a8131b366adae4546d2779a82ba76`
- Feature commit SHA: `eb68ddb` (`fix(voice): offload voice asset install from event loop`)

## Changed files
- `backend/api/routes.py` (1 line changed)
- `backend/tests/test_voice_assets_install_offload.py` (new, bounded regression; not executed per user request)

## Exact diff
```
 backend/api/routes.py | 2 +-
 1 file changed, 1 insertion(+), 1 deletion(-)

diff --git a/backend/api/routes.py b/backend/api/routes.py
@@ -2395,7 +2395,7 @@ async def voice_assets_install():
     from backend.core.voice_assets import install_voice_assets

     try:
-        status = install_voice_assets()
+        status = await run_in_threadpool(install_voice_assets)
     except Exception as e:
         logger.error("Voice asset installation failed: %s", e)
         raise HTTPException(status_code=502, detail=f"Voice asset install failed: {str(e)}")
```
`run_in_threadpool` was already imported at `backend/api/routes.py:13` (`from starlette.concurrency import run_in_threadpool`), so no new import was added.

## Reasoning
The approved change converts the existing `POST /api/v1/voice/assets/install` handler from a synchronous in-event-loop `install_voice_assets()` call to `await run_in_threadpool(install_voice_assets)`, matching the existing provider-discovery offload pattern. Schemas, response mapping, 502 error mapping, status/auth, and explicit owner-initiated install behavior are preserved. The installer module itself was not changed.

## Thread-safety inspection
Static inspection found no request DB/audio/display mutation in `install_voice_assets`: it performs optional download via `urllib`, checksum, extraction with `zipfile`/`shutil`/`os.replace`, then returns `asset_status()`, which calls `_detect_tts_voice()` using `pyttsx3`/COM. This is a potential worker-thread hazard: `comtypes` initializes COM only in the thread where it is first imported; a worker thread may lack an initialized COM apartment unless COM initialization already occurred there. No COM initialization guard was added because the approved plan limits edits to the route, and this remains unverified. Static source review only; no execution.

## Unverified / uncertainty
- The regression test was added but not executed; only `py_compile` syntax checks were run.
- Native real COM/TTS execution in a Starlette worker thread was not verified.
- The exact screenshot root cause remains unverified: a statically blocking install does not prove the "Start conversation" Network Error occurred during that blocking interval. Axios client timeout versus Network Error was not captured, and the native button was not replayed. Prior tiny calls/runtime reads are evidence only for isolated endpoint success.
- No packaged mirror (`src-tauri/resources/backend/...`) was modified.
- No merge or push was performed.
