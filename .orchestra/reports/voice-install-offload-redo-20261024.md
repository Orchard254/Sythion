# Voice install revision round 2

Revision commit: `4a143bc03e4d3724881a77bea04fc2e7039e7fac`
Base commit: `eb68ddbb3b17e14c2318dea30113281f02c3bd24` (reviewed prior tip)
Branch: `voice-install-offload`
Worktree: `C:/Users/orcha/AppData/Local/Temp/opencode/sythion-voice-install-offload`

## Files changed

```
 backend/api/routes.py                              | 5 +-
 backend/core/voice_assets.py                       | 79 ++++++++++-------
 backend/tests/test_voice_assets_install_offload.py | 98 +++++++++++++++++-----
 3 files changed, 130 insertions(+), 52 deletions(-)
```

- `backend/core/voice_assets.py`: introduced `install_stt_assets()` as the STT-only transaction helper; retained `install_voice_assets()` as a synchronous compatibility wrapper that runs the helper and then calls `asset_status()` on the calling thread; added process-local `_STT_INSTALL_LOCK`.
- `backend/api/routes.py`: `voice_assets_install()` now awaits the STT-only helper on `run_in_threadpool`, then computes `asset_status()` on the owning event-loop thread.
- `backend/tests/test_voice_assets_install_offload.py`: inert regression structure for offload/separation and serialization (not executed).

## Invariant reasoning

1. STT-only helper contains filesystem/download/checksum/extract/publish work only. It does not import, instantiate, enumerate, or touch pyttsx3, and does not call `asset_status()` or `_detect_tts_voice()`.
2. TTS status/path remains on the original owning thread: the async route computes `asset_status()` after the awaited worker returns, while the shared `setup_readiness` path already calls `asset_status()` on the event loop as before.
3. Sync callers retain the original status contract because `install_voice_assets()` runs `install_stt_assets()` then returns `asset_status()`.
4. Serialization: the entire STT transaction is guarded by `_STT_INSTALL_LOCK`, with an installed-state recheck after lock acquisition, preventing concurrent extraction/publication races within one process.
5. Worker errors still propagate from `run_in_threadpool` into the existing 502 mapping; `VoiceAssetsResponse(**status)` remains outside the try block.
6. Auth, auto-download consent, route registration, middleware, and other route behavior are unchanged.

## Review blockers addressed

- P1 worker is not COM/thread safe: the whole installer is no longer offloaded. Only the STT filesystem/download/checksum/extract/install/recheck/publish helper runs on the pool thread; TTS enumeration stays on its owning thread. COM initialization of arbitrary workers is avoided.
- P2 concurrent installs share/destructively reuse staging: the STT transaction is now serialized with a lock and rechecks installed state after acquiring the lock, instead of allowing parallel `_extract_stt_model` execution.

## Cross-process coordination residual

`_STT_INSTALL_LOCK` is a process-local `threading.Lock`. It does not coordinate two separate backend/sidecar processes. Cross-process install collisions are not protected by this change; a safe process-level coordinator (or explicit residual acceptance) is still required if that scenario is relevant. No overclaim of cross-process safety is made.

## Archive/layout diagnosis

No safely discoverable pinned Vosk archive or installed model was found in approved, non-private artifacts searched (no `vosk-model-small-en-us-0.15.zip`, extracted `final.mdl`, or related staging archive outside private browser/profile caches). Therefore no validator change was made. The static source still requires `am/final.mdl` and `graph/HCLG.fst`; `stt_model_installed()` additionally requires `graph/words.txt`, and extraction only accepts the named root or one immediate non-hidden directory. The possible split-graph (`HCLr.fst` + `Gr.fst`) format mismatch remains a likely but unverified explanation, not a repaired defect.

## Verification performed

- `python -m py_compile` on `backend/core/voice_assets.py`, `backend/api/routes.py`, `backend/tests/test_voice_assets_install_offload.py`, and `backend/tests/test_voice_assets.py`: passed.
- No test suites, builds, downloads, live API calls, runtime probes, COM/audio execution, user-profile changes, or app interaction were performed, per user constraint.
- The added/updated regression tests were not executed and are not claimed to pass.
- No claim is made that the original Start conversation screenshot issue is fixed.

## Unexecuted limits

- STT serialization thread-safety behavior is only reasoned from source; not executed.
- TTS availability behavior on the owning thread remains OS/engine dependent and unverified.
- Archive layout compatibility remains unverified from actual pinned distribution bytes.
- Packaging mirror does not include this change.