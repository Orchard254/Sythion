# Vosk layout integration — source only

Date: 2026-10-24. **MERGED, STATIC SCOPE ONLY.** Native installation/loading, original Network Error and screenshot outcome remain unverified. No packaged update or autoupdate.

## Exact identity and scope

- Authorization: `.orchestra/briefs/vosk-layout-integrate-20261024.md` and explicit immutable-SHA user instruction.
- Repository/branch: `C:\Sythion\sythion`, `master`.
- Premerge master / original feature base: `ce56971df78d05c540cfc6fa4f91c568652da834`.
- Exact reviewed feature merged: `fea9ee21659b74e9666d8c2aa2a6a38ad039ea6e`. No moving branch ref used.
- No-ff merge commit / resulting master: `29e6461c3ce225932ad716ab062888228b5ffe71`.
- Ordered parents: `ce56971df78d05c540cfc6fa4f91c568652da834`, `fea9ee21659b74e9666d8c2aa2a6a38ad039ea6e`.
- Merge tree: `db14ca8ed3c48fd84fba6bbb88524efb04e35734`, exactly equal to the reviewed feature tree.
- First-parent delta: only `backend/core/voice_assets.py` and `backend/tests/test_voice_assets.py`, 210 insertions/15 deletions. No report, critique, unrelated file, packaged mirror or release-resource change committed.

## Evidence gates and preservation

- Read the canonical investigation, implementation, independent review and static verifier reports. Appended exactly one critique per report to `.orchestra/critiques.md` before merge, using brief/report/evidence/coverage/consistency/not checked/self-directed/verdict/reason fields. No prior Vosk-layout records existed; existing voice-offload critiques were retained without duplication.
- Each acceptance is limited to `PINNED_VOSK_SPLIT_GRAPH_IS_RECOGNIZED_CONSISTENTLY` and supported V2 presence formats, not runtime success. Investigation drafting residue and excluded packaged-mirror/test recommendations are expressly not adopted.
- Preflight: master/base matched expected SHA; index empty; allowlisted source clean; exact two-path delta; Git whitespace check clean. Existing tracked/untracked dirt was confined to `.orchestra` and was not staged.
- Used `merge --no-ff --no-commit --no-autostash` with the full reviewed SHA. Automatic merge succeeded without conflicts. Before committing, checked MERGE_HEAD, empty unmerged index, exact two staged paths, clean staged whitespace check and exact staged-tree equality to reviewed source.
- Disabled Git hooks with a command-local nonexistent hooks directory for merge and commit, preventing incidental hook execution. Disabled commit signing for the local merge commit. No source/test interpreter or product script was invoked.
- Saved SHA-256 snapshots of all **282** existing orchestration files outside the repository. Verified the original critiques file remained a byte-identical prefix after appending. All **281** other original files stayed byte-identical; all **282** post-critique/premerge files stayed byte-identical through merge and commit. This report is the subsequent new reporting artifact.
- Evidence snapshots: `C:\Users\orcha\AppData\Local\Temp\opencode\vosk-integrate-cd91ea1c4f0b4ce89441310dfe8c483d` (`before.xml`, `premerge.xml`, `critiques-before.bin`). No cleanup of other work performed.
- Postcommit Git checks: ordered parent identities confirmed; merge-versus-reviewed tree diff empty; index empty; product working-tree diff outside `.orchestra` empty; first-parent stat exactly matches reviewed delta.
- Canonical static verifier report preserved with SHA-256 `6E746DD43C75549E1EC9A4ABD6EBE10A8AB0AC217C46CE2DCFD24A01DB9EB246`.
- CogniGraph safety analysis was incomplete for Markdown/new report freshness, not a green certification. Actual Git delta and canonical reports supplied the bounded manual check; no structural/bootstrap program was executed by this committer.

## Explicit limitations and handoff

- **No tests collected or executed, builds, syntax execution, network/downloads, archive opening, app/backend imports or launches, native Vosk, microphone/TTS/provider actions, user-profile changes, or process control. No push.** Prior implementer AST parsing is reported history only, not reproduced evidence or a test pass.
- Shared predicate checks structural presence: V2 acoustic/model/feature configs and either static HCLG or complete HCLr/Gr/disambig family. Both status and extraction use it. It does not certify binary/config validity, embedded output symbols, readable/nonempty files or acoustic-dependent **i-vector** completeness.
- Existing lack of explicit symlink denial remains; filesystem presence checks may follow links. Existing traversal checks, checksum, explicit installation boundary, process-local lock and publication behavior are preserved, not newly security-certified or made cross-process/fully atomic.
- Real archive member evidence is inherited public-transcript evidence, not a newly downloaded/opened/hash-verified model. Native installation, loading, recognition, original Network Error resolution and screenshot remain **awaiting confirmation**.
- Packaged source/binaries and the current running executable are unchanged by this work. No staging/adjacent-release resources were touched; deployment requires separate authorization. This merge alone has no deployed user-visible effect.
- Reporting artifacts remain uncommitted alongside all existing orchestration work. Only the exact reviewed two-file source/test delta entered the merge.
