# Independent Vosk layout review — 2026-10-24

**VERDICT: ACCEPT** — `fea9ee21659b74e9666d8c2aa2a6a38ad039ea6e`, limited to the confirmed-layout invariant. No blocking findings in the accumulated delta.

## Identity and scope
- Original base / verified merge-base: `ce56971df78d05c540cfc6fa4f91c568652da834`.
- Reviewed exact detached HEAD in own clean worktree: `C:/Users/orcha/AppData/Local/Temp/opencode/vosk-layout-review-20261024`.
- Full base-to-tip diff: only `backend/core/voice_assets.py` and `backend/tests/test_voice_assets.py`; 210 insertions, 15 deletions. `git diff --check` produced no errors. Read implementation and upstream-evidence investigation reports locally; no network requests.

## Findings and safety reasoning
- Shared `_vosk_model_dir_complete` (`voice_assets.py:107-150`) accepts the documented pinned split graph only with `HCLr.fst`, `Gr.fst`, `disambig_tid.int`, `am/final.mdl`, `conf/model.conf`, and MFCC or FBANK configuration. Missing required components reject; a complete static HCLG V2 layout remains accepted.
- Installed status (`:153-156`) and extraction (`:336-339`) invoke the same predicate. Removing universal `words.txt` presence is justified by the locally recorded upstream loader evidence: HCLG/Gr may supply output symbols, and the pinned archive transcript omits that file.
- Inert regression additions cover split acceptance, every required split/core-file omission, static compatibility, feature-config alternative, installed/extraction parity, and publication/marker results. Existing checksum/traversal assertions remain. These are source-reviewed assertions, not passing-test evidence.
- Manifest URL/version/SHA-256, checksum-before-extraction, explicit install/download boundary, process-local lock and in-lock recheck, canonical destination, staging/publication, and existing absolute/traversal member rejection are unchanged. No new recursive discovery or automatic download path.
- Base extraction already lacks explicit symlink denial; this feature neither introduces that omission nor hardens/certifies symlink handling. Existing process-local-only locking and publication behavior are preserved, not newly certified.

## Explicit limits / residuals
- Source-static review only: no tests collected/executed, builds, downloads/network, app launches, native/provider calls, microphone/TTS actions, user-state changes, source edits, merge, or push. Only this requested report was added in the main tree; existing dirty work was left intact.
- Predicate verifies file presence, not binary validity, embedded symbol contents, or acoustic-dependent i-vector completeness. Static graphs without embedded symbols still need a usable words fallback at native load. Legacy flat V1 support is not added; requiring V2 configuration deliberately rejects prior incomplete synthetic layouts.
- Archive member evidence is the prior report's public transcript, not a newly opened/hash-verified real archive. No native Vosk success, original Network Error resolution, successful real installation, or provider/runtime behavior is established.
- Packaged source/binary is unchanged; deployment and user-visible effect require separately authorized work.
