# Independent static Vosk layout verification — 2026-10-24

## Scoped verdict

**FIXED — STATIC ONLY:** `PINNED_VOSK_SPLIT_GRAPH_IS_RECOGNIZED_CONSISTENTLY` is established by code reasoning at **`fea9ee21659b74e9666d8c2aa2a6a38ad039ea6e`**, relative to base **`ce56971df78d05c540cfc6fa4f91c568652da834`**. No blocking discrepancy was found for this named invariant. Native installation/loading outcome remains awaiting confirmation; this is not a runtime or user-visible fix verdict.

## Independence, identity, and evidence

- Examined exact detached HEAD in my own worktree: `C:/Users/orcha/AppData/Local/Temp/opencode/vosk-layout-static-20261024`. `git rev-parse HEAD` returned the full tip above; `git merge-base <base> HEAD` returned the full base above.
- Read the full cumulative delta and both base and branch `backend/core/voice_assets.py`, plus the branch test source. The delta is exactly `backend/core/voice_assets.py` and `backend/tests/test_voice_assets.py`: 210 insertions, 15 deletions.
- Read local investigation, implementation, and review reports: `.orchestra/reports/vosk-layout-investigation-20261024.md`, `.orchestra/reports/vosk-layout-fix-20261024.md`, and `.orchestra/reviews/vosk-layout-review-20261024.md`. Independently traced the branch/base code rather than adopting the review verdict.
- Upstream layout/native-loader evidence is inherited from the investigation's recorded citations and member transcript, not newly fetched or independently archive-verified here. In particular, the documented pinned distribution has `am/final.mdl`, `conf/model.conf`, `conf/mfcc.conf`, and the three split-graph files below, without `HCLG.fst` or `words.txt`.
- Observed static Git checks: `git diff --check <base> HEAD --` exited successfully with no output; verifier worktree `git status --porcelain=v1` was empty. These are repository checks, not test results.

## Explicit proof of the invariant

All source line references below are to branch `backend/core/voice_assets.py`.

Define presence conditions using exactly the helper's `isdir`/`isfile` semantics:

```text
D = model directory exists
A = am/final.mdl exists as a file
C = conf/model.conf exists as a file
F = conf/mfcc.conf OR conf/fbank.conf exists as a file
S = graph/HCLG.fst exists as a file
R = graph/HCLr.fst exists as a file
G = graph/Gr.fst exists as a file
I = graph/disambig_tid.int exists as a file
P = D AND A AND C AND F AND (S OR (R AND G AND I))
```

1. **Shared supported format:** `_vosk_model_dir_complete` (`:107-150`) implements P: early false returns for absent D/A/C/F, then true for S, otherwise true only for all R/G/I. `stt_model_installed` (`:153-156`) is `D AND P`, equivalent to P because P already requires D. Extraction calls that same helper on the discovered extracted model root (`:321-339`). Thus installed and extracted layouts use one predicate, not independently drifting file lists.
2. **Pinned documented split layout succeeds at both layout gates:** its documented files make D/A/C/F/R/G/I true; S may be false. P is therefore true. Neither `words.txt` nor HCLG is consulted on this split branch. This removes the base's unavoidable split-layout rejection: base installed status required A/HCLG/words, while base extraction required A/HCLG.
3. **Incomplete layouts fail closed within the supported presence contract:** absent model directory, acoustic file, or model config returns false before graph acceptance. Absence of both feature configs also returns false; omitting MFCC alone does not reject when FBANK is present, intentionally. With no HCLG, omission of any split component makes the R/G/I conjunction false. With no graph family complete, P is false. On extraction this raises `VoiceAssetVerificationError` before destination removal, publication, or marker creation (`:336-347`), and staging cleanup still runs (`:348-349`). Existing destination contents are not removed by this layout-rejection path.
4. **Static graph compatibility retained:** D/A/C/F/S makes P true without requiring split companions. A partial split family alongside a complete static family is valid under this OR contract, not an incomplete-layout escape. V2 static models with the needed configs remain accepted, with MFCC or FBANK. Former config-less synthetic layouts no longer pass; legacy flat V1 support is not added.
5. **`words.txt` is not a universal presence requirement:** this matches the investigation's recorded loader evidence that HCLG/Gr may carry output symbols and the pinned transcript omits the file. It does not establish that arbitrary graph contents carry usable symbols: graphs lacking embedded symbols still require the native words fallback.

## Safety unchanged relative to base, not newly certified

- Manifest name/version/URL/license/SHA-256 are unchanged (`:40-45`), as are digest computation/comparison (`:90-104`). `install_stt_assets` still verifies the archive before calling extraction (`:234-239`). No checksum bypass was introduced; test monkeypatches affect synthetic fixtures only.
- Explicit installation/download boundary and import behavior are unchanged. The helper only inspects filesystem presence; it adds no download or invocation path. Existing external consent/routing code is outside the two-file delta, so this change does not add or remove an authorization gate.
- The same process-local `_STT_INSTALL_LOCK` (`:31-33`) surrounds the transaction, including the in-lock installed-plus-marker recheck and force behavior (`:224-227`). Cross-process locking is not provided or newly claimed.
- Archive member normalization and rejection of leading slash or `..` path segments before `extractall` are byte-for-byte unchanged (`:311-319`). This verifies preservation of those checks, not comprehensive platform/path-security correctness.
- Named-root or single immediate non-hidden directory discovery, canonical publication destination, staging cleanup, destination replacement, and marker-after-move ordering remain unchanged (`:302-349`, apart from the predicate call). No recursive root search was added. The existing replacement sequence is not newly certified as interruption-safe or transactionally atomic in all failure modes.
- **Symlink limit:** the base has no explicit symlink-denial check, and neither does the branch. `isfile`/`isdir` presence checks can follow filesystem links. No symlink hardening, universal traversal guarantee, or legacy protection that did not exist is claimed.
- Marker presence remains separate from installed layout status (`:176-184`); it is not a fresh integrity check of every installed file. That behavior is unchanged.

## Authored regression assertions versus observed execution

Reviewed, **not executed**: split fixtures (`backend/tests/test_voice_assets.py:18-27`), static fixture (`:30-37`), split/static acceptance and FBANK alternative (`:201-223`), missing configuration/graph negatives (`:225-258`), installed predicate checks (`:260-279`), static install assertion (`:281-288`), and seven-way split presence/omission parity with publication/marker assertions (`:290-334`). Existing checksum mismatch, traversal, no-download, and verified-install no-op assertions remain in source.

Those tests use placeholder bytes, not real acoustic/graph assets. Their existence supports intended regression coverage only. **No tests were collected or run; no passing-test or successful-install observation is reported.**

## Limits and handoff

- This predicate checks file presence, not nonempty/readable/valid model binaries, configuration semantics, embedded output symbols, or acoustic-dependent **i-vector** requirements. The investigation records i-vector companions in the pinned archive; a truncated model retaining the minimal P files can still pass while failing native load. The docstring's “complete” wording is limited to this structural predicate, not full loader readiness.
- No model archive was downloaded, opened, or hash-verified here. No network requests, builds, application/backend imports or launches, native Vosk loading, microphone/TTS/provider actions, installation, or target-user/app-state mutation occurred. Git worktree setup and this requested canonical report are the only workspace artifacts created.
- The original **Network Error**, real installation success, native speech recognition, and packaged deployment are **not verified or claimed resolved**. Packaged source/binaries are outside this delta and unchanged; any deployed effect requires separately authorized work.
- No source edits, merge, push, or cleanup of other work were performed. The verifier worktree remains detached and clean; the report is written only to the requested main-tree `.orchestra/verifications/` location.
