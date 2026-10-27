# Vosk layout fix — recovered feature

Date: 2026-10-24. Source-only; ready for independent review, not runtime-qualified.

## Branch / provenance
- Worktree: `C:\Sythion\sythion-vosk-layout-fix`
- Branch: `feature/vosk-layout-fix-20261024`
- Base/current master: `ce56971df78d05c540cfc6fa4f91c568652da834`
- Prior task left a clean committed feature, not an empty/partial edit: `73bba39bff8ee572652728a15d395affa900d666` (`fix(voice): accept pinned Vosk V2 split graph layout`). Inspected and retained unchanged.
- Resume commit / final HEAD: `fea9ee21659b74e9666d8c2aa2a6a38ad039ea6e` (`test(voice): cover shared split layout rejection gates`). Adds 46 inert regression lines only.
- Final feature worktree clean. No merge/push, master-source edits, packaged-source mirror, or target-app changes. Existing main-tree dirty work preserved; this canonical report is the requested reporting exception to the code allowlist and is not in the feature commits.

## Exact cumulative diff vs base
Only the two allowlisted paths: 210 insertions, 15 deletions.
- `backend/core/voice_assets.py`: 61 changed lines; shared `_vosk_model_dir_complete` used by installed status and extraction. Requires V2 `am/final.mdl`, `conf/model.conf`, MFCC or FBANK config; accepts static `graph/HCLG.fst` OR all of `HCLr.fst`, `Gr.fst`, `disambig_tid.int`. Does not universally require `words.txt`.
- `backend/tests/test_voice_assets.py`: 164 changed lines; split-format fixture, static V2 compatibility, config/partial-graph negatives, installed predicate checks, static extraction. Resume adds seven-way installed/extraction parity through checksum-verified STT-only install: complete split layout and omission of each required acoustic/config/graph file; checks marker/publication outcome.

## Evidence and preserved boundaries
Used local `.orchestra/reports/vosk-layout-investigation-20261024.md`, especially lines 26–55 and 77–126; no new external/provider requests. Its citations are upstream `vosk-api/src/model.cc`, `vosk-space/models.md`, and the public pinned-archive extraction transcript. Evidence supports V2 static/split families, feature-config alternative, mandatory split disambiguation IDs, and graph-embedded output symbols. This does not add legacy flat V1 support.

Pinned URL/version/SHA-256, public status schema, explicit download initiation, process-local install lock/recheck, canonical destination, staging/publication and existing absolute/traversal rejection remain unchanged. Existing safety assertions were not removed. No recursive root discovery added.

Safety caveat: despite the brief's wording, the base extractor has no explicit symlink-denial check (it uses `ZipFile.extractall` after name checks). This feature neither changes nor certifies that pre-existing behavior; do not claim explicit symlink hardening was verified.

## Verification / limitations
- Allowed syntax-only command: `python -c "import ast,pathlib; files=['backend/core/voice_assets.py','backend/tests/test_voice_assets.py']; [ast.parse(pathlib.Path(p).read_bytes(), filename=p) for p in files]; print('Syntax OK: 2 files (AST parse only; no imports or tests)')"` — exit 0, printed that success line.
- `git diff master...HEAD --check` — exit 0, no whitespace errors; name-only diff confirms exactly the allowlist. Structural watcher index includes the new parity method.
- No tests collected/executed, builds, model downloads, native Vosk loading, TTS/mic actions, user-state changes, or app launches. Regression cases are authored only; no test-pass claim.
- Predicate checks layout existence, not binary readability, embedded symbol contents, or acoustic-dependent ivector completeness. Native outcome/user-visible installation success remains unverified. Packaged app remains unchanged and needs a separately authorized build/deployment for effect.
- Commit succeeded; existing post-commit memory hook emitted the known missing `python3` launcher warning. Memory was recorded through CogniGraph instead.
