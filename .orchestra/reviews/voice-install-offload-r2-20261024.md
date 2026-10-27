VERDICT ACCEPT

Reviewed SHA: `4a143bc03e4d3724881a77bea04fc2e7039e7fac`
Original base: `84efd2819c9a8131b366adae4546d2779a82ba76`
Independent detached worktree: `C:/Users/orcha/AppData/Local/Temp/opencode/voice-install-review-r2`.

**Acceptance is for the static source diff only, not live success.** Read the previous IMPROVE review, redo report, exact base-to-tip backend diff and relevant callers/storage/profile-lock source. No remaining blocking finding in this bounded diff.

## Previous blockers

- **P1 — Worker is not COM/thread safe: addressed statically.** `routes.py:2395-2403` awaits only `install_stt_assets`, then calls `asset_status()` on the request's event-loop thread. Both the installed fast path and installation path in `voice_assets.py:177-207` avoid `asset_status`, `_detect_tts_voice` and pyttsx3. Reachable helpers perform path/ACL, download, checksum, extraction and publication work; storage's Windows ACL code uses win32 security APIs, not SAPI/COM. TTS remains on the existing status thread. This avoids the newly introduced arbitrary-worker COM/engine-sharing hazard; it does not establish general COM correctness or eliminate pre-existing process-wide pyttsx3 engine-sharing risks elsewhere.
- **P2 — Newly concurrent installs share/destructively reuse staging: addressed within one process.** `_STT_INSTALL_LOCK` at `voice_assets.py:33,186-207` covers the installed-state recheck, download, checksum, extraction, destination replacement, marker write and temporary cleanup. The second non-forced caller rechecks after acquiring the lock and can return without re-extracting. The synchronous wrapper (`210-221`) uses the same helper, preserves archive/force arguments, and returns full status on its caller's thread.

## Contracts and residuals

- Worker exceptions are awaited and retain the existing 502/detail text; response model and successful fields are unchanged. Auth middleware/registration remain unchanged (`main.py:240-247`); no new startup/import download trigger or consent bypass is introduced.
- **Minor error-boundary difference:** `asset_status()` is now outside the route's try (`routes.py:2402`), whereas it formerly ran inside the wrapped installer. Unexpected status/path exceptions therefore no longer receive the install-specific 502 mapping. Ordinary TTS enumeration exceptions are still swallowed by `_detect_tts_voice`. This is not a blocker for the bounded offload repair, but full exception-behavior equivalence must not be claimed.
- The lock is **process-local only**. Normal sidecar startup acquires the profile lock and refuses a second instance (`main.py:64-71`), retaining it until shutdown (`228-230`), matching the Windows single-runner contract. Direct library callers or processes bypassing that lifecycle remain uncoordinated and can race destination replacement. This is not cross-process installer protection; the profile lock's broader correctness was not qualified here.
- Existing `test_voice_assets.py` calls the retained sync wrapper; its archive/checksum/traversal/no-op fixtures remain structurally compatible. New route mocks patch the exact `backend.core.voice_assets` attributes imported inside each handler invocation; no stale `install_voice_assets` mock mismatch found.
- **Test coverage limit:** the new serialization test replaces the real helper with a fake that itself acquires the lock. It would not detect removal of locking/recheck from the production helper. Offload/status-thread and worker-error checks are useful source-level test designs, but none were executed; direct handler calls do not test HTTP authentication. Real-helper serialization/recheck and no-TTS coverage remain unproved dynamically.

## Unresolved diagnosis / verification limits

- ZIP validation is unchanged: extraction accepts the named root or one immediate non-hidden directory and requires `am/final.mdl` plus `graph/HCLG.fst` (`voice_assets.py:283-304`); installed-status additionally requires `graph/words.txt` (`107-118`). A split-graph layout would still be rejected. No actual pinned archive/member evidence was examined, so the proposed layout mismatch remains an unresolved explanation, not a verified diagnosis or fix.
- No tests, builds, syntax execution, downloads, network requests, API/runtime probes, COM/audio execution, native app interaction or user-profile changes were performed by this reviewer. The implementer's reported py_compile result was not independently reproduced. No source edits, merge or packaged-backend update was made.
- **The native Start conversation / Network Error screenshot is not proved fixed.** Neither runtime responsiveness, successful asset installation, TTS availability nor packaged/live behavior is established by this acceptance.
