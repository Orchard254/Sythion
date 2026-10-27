VERDICT IMPROVE

SHA: eb68ddbb3b17e14c2318dea30113281f02c3bd24 (base 84efd2819c9a8131b366adae4546d2779a82ba76).
Independent detached worktree: C:/Users/orcha/AppData/Local/Temp/opencode/sythion-voice-install-review. Inspected refs, implementation report, exact diff, callers and bundled dependency source. No merge or product edits.

## Blocking findings

1. **P1 — Worker is not COM/thread safe.** `backend/api/routes.py:2398` moves the entire installer into an arbitrary pool thread. Both installer exit paths (`backend/core/voice_assets.py:184-185,205`) call `asset_status()` -> `_detect_tts_voice()` -> `pyttsx3.init()/getProperty("voices")/stop()` (116-126,144-156). Neither path initializes COM on that worker. Bundled `src-tauri/resources/python-runtime/Lib/site-packages/comtypes/__init__.py:169-176` initializes only the first importing thread; subsequent imports do not initialize other workers. Bundled `pyttsx3/drivers/sapi5.py:43,100-102` creates/calls a COM voice without a per-call initialization guard. An earlier status/readiness request or GUI use can already have imported COM elsewhere. Enumeration failure is swallowed into an empty `tts_voice` (voice_assets.py:154-156), so a successful install can silently report TTS unavailable rather than raise 502.

   Additionally bundled `pyttsx3/__init__.py:4,19-24` caches engines process-wide by driver, not thread; `backend/services/voice_pipeline.py:178-188` retains such an engine. A worker can therefore reuse an event-loop-owned engine, and status requests still enumerate on the event loop (`routes.py:2339,2385`). Initializing COM alone does not resolve cross-apartment engine sharing. `gui_tools.py:30-36` also sets global MTA import flags, so assuming a fresh default STA import is unsafe. Minimal bounded direction: offload only serialized download/checksum/extraction, and keep TTS status on its existing owning thread; alternatively explicitly own all SAPI activity on one initialized, serialized COM thread, with engine lifetime/cleanup confined there. Do not approve the whole-installer offload as currently written.

2. **P2 — Newly concurrent installs share/destructively reuse staging.** Previously this synchronous handler serialized these calls on one event loop; pool offload allows two requests to execute `_extract_stt_model` concurrently. `voice_assets.py:242-247` uses only model name + process ID for staging and removes an existing staging directory; lines 283-291 remove/replace the common destination and clean the same staging directory. Two explicit install requests can erase each other's in-progress extraction or race publication/cleanup. There is no installer lock. Serialize the entire install/check/publish transaction (including recheck after lock acquisition); unique staging alone does not serialize destination replacement. This is a static reachable race, not a reproduced failure.

## Scope / contract / tests

- Production diff is exactly one route call replacement; existing local-control middleware attachment (`backend/main.py:240-247`) and route registration are unchanged. No added download trigger or authentication bypass identified by this diff.
- Awaited exceptions still map to the existing 502/detail, and `VoiceAssetsResponse(**status)` remains outside the try as before. COM enumeration exceptions remain silently degraded inside the helper.
- New regression replaces the entire installer with a fake: useful coverage of offload/heartbeat and exception mapping, but exercises neither COM nor concurrent installation. It calls the handler directly, not the authenticated HTTP boundary. **Not executed; no pass claimed.**

## Separate read-only layout diagnosis (pre-existing, not introduced here)

`voice_assets.py:260-273` selects the named root or one immediate non-hidden directory; it does not recursively discover nested model roots. The reported exact message is raised only at lines 275-281: the selected root lacks `am/final.mdl` or specifically `graph/HCLG.fst`. This is not evidence of checksum failure. `stt_model_installed():105-112` additionally requires `graph/words.txt`.

Likely format mismatch: small Vosk models can use the split graph `graph/HCLr.fst` + `graph/Gr.fst`, rather than `HCLG.fst`; the pinned name is `vosk-model-small-en-us-0.15` (35-37). The current predicate rejects any such valid split layout. Existing fixtures synthesize only HCLG + words (`backend/tests/test_voice_assets.py:18-24`), so they cannot establish compatibility with the actual pinned distribution. Minimal proposal: one shared root validator for extraction and installed-status, accepting the loader-supported static or split graph layouts and their required companion files; align fixtures with the pinned distribution. Only add bounded nested-root discovery if actual archive evidence warrants it.

**Limit:** no archive fetched/opened, private files read, or Vosk loader executed; actual pinned archive member names and the user's selected root were not verified. Thus the hard-coded rejection mechanism is confirmed from source; attribution to the actual archive's split graph remains a likely explanation, not a locally verified fact.

## Verification limits

Static review only. No tests, builds, syntax execution, downloads, provider probes, COM/audio execution, app interaction, or user-profile mutation. Bundled Python dependency code was inspected as source; the exact dependency versions loaded by the user's running process are unverified. No claim that native Start conversation/Network Error is fixed or that voice installation caused it. No packaging mirror update in this commit.
