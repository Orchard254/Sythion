# Vosk model layout investigation — evidence and proposed validator correction

Date: 2026-10-24
Scope: read-only source/public evidence; no archive downloaded, no tests/builds/product edits.

## Bottom line

The pinned distribution is **Vosk V2 (directory-per-model)** and uses the **split graph** (`graph/HCLr.fst` + `graph/Gr.fst`), not one static `graph/HCLG.fst`. It also **does not contain `graph/words.txt`** (and not `graph/HCLG.fst`), because `Gr.fst` carries the output symbol table and native load makes `words.txt` optional.

Current code rejects a correctly installed/extracted official model twice:

- `_extract_stt_model` accepts only `am/final.mdl` + `graph/HCLG.fst` (`voice_assets.py:298-304`; packaged copy parallel `.../resources/backend/core/voice_assets.py:275-281`).
- `stt_model_installed` additionally requires `graph/words.txt` (`voice_assets.py:107-118`; packaged copy parallel `.../resources/backend/core/voice_assets.py:102-113`).

So a valid pinned install cannot pass either the extraction validator or installed-status predicate, matching the reported install/STT-unavailable failure. This is a format/layout validator defect, not evidence of checksum failure; it does not by itself prove or fix the native Vosk runtime issue.

## Credible source evidence

Pinned manifest in `backend/core/voice_assets.py:40-45` / packaged `.../resources/backend/core/voice_assets.py:35-40`:
- Name: `vosk-model-small-en-us-0.15`
- Version: `0.15`
- URL: `https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip`
- SHA-256: `30f26242...2498`
- Archive size observed in upstream model metadata: 41,205,931 bytes, matching manifest comment (`...41,205,931-byte distribution zip...`).

Actual official archive member layout (public unzip transcript, Vosk model example notebook; URL cited below; content observed 2026-10-24):
```text
vosk-model-small-en-us-0.15/
  README
  am/final.mdl
  conf/model.conf
  conf/mfcc.conf
  graph/disambig_tid.int
  graph/HCLr.fst
  graph/Gr.fst
  graph/phones/word_boundary.int
  ivector/splice.conf
  ivector/global_cmvn.stats
  ivector/final.mat
  ivector/online_cmvn.conf
  ivector/final.dubm
  ivector/final.ie
```
Notable absences in the same transcript: `graph/HCLG.fst`, `graph/words.txt`, `graph/phones.txt`.

Sources:
1. Alphacephei official model page/upstream model metadata: `https://alphacephei.com/vosk/models/...`; model-list JSON `https://alphacephei.com/vosk/models/model-list.json` (observed `vosk-model-small-en-us-0.15`, 41,205,931 bytes, non-obsolete) — fetched 2026-10-24. Zip URL matches `STT_MODEL_URL`.
2. Public archive extraction transcript containing the file listing above: `https://gist.github.com/johnidm/65fe554bac438b0cb9fbdec80b46d3c6` (dated/active May 2025) — fetched 2026-10-24. Archive from this transcript is only used for member-name evidence, not executable validation; it shows the exact pinned archive layout.
3. Official Vosk model-structure documentation: `https://raw.githubusercontent.com/alphacep/vosk-space/master/models.md` — fetched 2026-10-24. Documents V2 layout, `am/final.mdl`, `conf/model.conf`, `conf/mfcc.conf`, optional ivector directory, `graph/HCLG.fst`, and split `graph/HCLr.fst` + `graph/Gr.fst`.
4. Native Vosk loader ground truth: `https://raw.githubusercontent.com/alphacep/vosk-api/master/src/model.cc` — fetched 2026-10-24. Governing constructor:
   - V2 path requires `am/final.mdl` **and** `conf/model.conf`.
   - Feature configuration must be `conf/mfcc.conf` **or** `conf/fbank.conf`; otherwise native load errors `Failed to find feature config file`.
   - Graph branch: if `graph/HCLG.fst` exists it is loaded; otherwise `graph/HCLr.fst` and `graph/Gr.fst` are both loaded and `graph/disambig_tid.int` is mandatory (`ReadIntegerVectorSimple` → `KALDI_ERR` on failure).
   - `graph/words.txt` is read only when neither HCLG nor Gr supplies `OutputSymbols()` (loader assigns `word_syms_` from HCLG/Gr first; falls back to `word_syms_rxfilename_`). The published small model’s `Gr.fst` supplies symbols, so `words.txt` is correctly absent.
   - I-vector files under `ivector/` are conditional on the acoustic model’s ivector dimension; the pinned small model publishes them and uses them. They are supporting companion files and native hard-fails at load if absent in that case.

## Current defect and fixed artifact root

Relevant code (`C:\Sythion\sythion\backend\core\voice_assets.py`, reviewed 2026-10-24):

- `stt_model_installed()` lines 107-118 rejects any model lacking `graph/words.txt` or using split graph:
  ```python
  am = .../am/final.mdl
  graph = .../graph/HCLG.fst
  words = .../graph/words.txt
  ```
- `_extract_stt_model` lines 298-304 rejects archive roots lacking `graph/HCLG.fst`:
  ```python
  if not (
      os.path.isfile(.../am/final.mdl)
      and os.path.isfile(.../graph/HCLG.fst)
  ): ...
  ```

This also means the extracted root is accepted only through one of two graph families while requiring the optional `words.txt`; the reverse situation in the official archive (split graph, no `words.txt`) is rejected. The same behavior exists in the packaged copy (`src-tauri/resources/backend/core/voice_assets.py` lines 102-113 and 275-281).

## Proposed minimal shared change

Introduce **one** shared predicate used by both extraction and installed status. Do not change checksum verification, traversal checks, staging/publication, lock, or vault/app handling.

Recommended helper (conceptual):

```python
def _vosk_model_dir_complete(model_dir: str) -> bool:
    if not os.path.isdir(model_dir):
        return False

    am_dir = os.path.join(model_dir, "am")
    conf_dir = os.path.join(model_dir, "conf")
    graph_dir = os.path.join(model_dir, "graph")

    # Vosk V2 constructor + feature config.
    if not os.path.isfile(os.path.join(am_dir, "final.mdl")):
        return False
    if not os.path.isfile(os.path.join(conf_dir, "model.conf")):
        return False
    feature_ok = (
        os.path.isfile(os.path.join(conf_dir, "mfcc.conf"))
        or os.path.isfile(os.path.join(conf_dir, "fbank.conf"))
    )
    if not feature_ok:
        return False

    if os.path.isfile(os.path.join(graph_dir, "HCLG.fst")):
        return True

    # Dynamic/split graph family (pinned small model).
    if (
        os.path.isfile(os.path.join(graph_dir, "HCLr.fst"))
        and os.path.isfile(os.path.join(graph_dir, "Gr.fst"))
        and os.path.isfile(os.path.join(graph_dir, "disambig_tid.int"))
    ):
        return True

    return False
```

Then:
- `stt_model_installed()` becomes: `os.path.isdir(model_dir) and _vosk_model_dir_complete(model_dir)`.
- `_extract_stt_model()` post-extract check becomes the same `_vosk_model_dir_complete(extracted)`.
- Remove the hard-coded `graph/words.txt` requirement. No replacement `words.txt` check.
- `words.txt` may optionally be present (official docs list it generically), but it is **not required** for the pinned loader path because `Gr.fst` provides symbols.
- Keep `graph/HCLG.fst` accepted as the static family so legitimate static V2 models remain compatible.
- Do not require `graph/phones.txt`, `graph/phones/word_boundary.int`, rescore, rnnlm, ivector, or README files existence for the predicate? Correction: require only graph companions already proven mandatory by native code above. For the pinned small model, the archive includes i-vector files; extension would likely be safer to also require `ivector/final.ie` for this model, but that would narrow acceptance to i-vector-trained models. The strictest pinned-model predicate can include that check; a generic V2 predicate should keep it optional because non-i-vector models are valid Vosk models. Recommend keeping the minimal predicate above, then either:
  - strict variant: additionally require `ivector/final.ie` and its six companion files for `stt_model_installed` only after profiling against the pinned archive; or
  - leave i-vector absent to native loader, which will fail loudly before recognition.

Fixture update:
- Replace synthetic `_make_zip` in `backend/tests/test_voice_assets.py` (`_make_zip`, lines 18-24) with the actual V2 pinned layout (`am/final.mdl`, `conf/model.conf`, `conf/mfcc.conf`, `graph/HCLr.fst`, `graph/Gr.fst`, `graph/disambig_tid.int`) rather than `graph/HCLG.fst` + `graph/words.txt`.
- Add traversal and partial-layout negative cases covering missing `disambig_tid.int`, missing `HCLr.fst`, missing `Gr.fst`, missing `HCLG.fst`, and missing conf/am core files.
- Keep pinned SHA/checksum behavior unchanged. Synthetic fixtures must override SHA via the existing monkeypatch pattern; real checksums stay pinned and are not bypassed.
- Apply the same source changes to the packaged backend copy (`src-tauri/resources/backend/core/voice_assets.py`) at its parallel locations so installed app behavior matches source.

## Compatibility and safety impact

- Fixes the extraction gate and installed-status gate for the actual pinned distribution without relaxing checksum verification or traversal protection.
- Preserves security gates: `_extract_stt_model` still rejects absolute/traversal members before extract, single-directory discovery remains bounded, and `_expected_checksum`/marker logic remain unchanged.
- A correctly installed official small model becomes reportable as installed and STT-loadable, so `VoiceSessionManager`/`VoskSTT._load()` can attempt native model initialization from `stt_model_path()`.
- Does **not** assert the native model loaded successfully, only that the directory is complete enough for Vosk V2. Runtime load still occurs later in `voice_pipeline.VoskSTT._load()`.
- Does not close the separate COM/TTS status and installer-lock issues (pre-existing review P1/P2), does not change offload routing, packaging, or native code.
- Does not claim the user-visible native bug is fixed; only removes the layout rejection from the validator.

## Unresolved facts / caution

- Public archive member evidence comes from a public teaching notebook transcript, not a byte-exact hash or archive open by us; checksum pinned to the archive but not to every decompressed member. No model archive was downloaded as prohibited.
- `graph/phones.txt` and `graph/words.txt` may still be present in some distributions; this predicate deliberately does not require them because native source allows symbol fallback and small model does not publish `words.txt`.
- Whether validation should enforce i-vector files for the pinned model is not fully resolved by native constructor alone; it depends on `AmNnetSimple` ivector dimension. The actual pinned archive includes them; the minimal predicate leaves them to native enforcement.
- Packaged app remains the old source/binary; source change must be rebuilt/packaged separately before runtime effect.
- Native runtime success (GPU/CPU, model path, Vosk library) remains unverified by this investigation; only layout/source evidence is established.

## Recommended next implementation (outside this report)

Limited to shared validator + inert fixture alignment + packaged-source mirror parity; then run the existing `backend/tests/test_voice_assets.py`, including a split-graph fixture, but no model download, build, or live install.
```

Need ensure citations exact and report concise but detailed enough. Fine.

Before final, maybe call memory_checkpoint? AGENTS says end session call memory_checkpoint_task with verified test output. But we didn't run tests. Should call? It asks summary, test_baseline. Could append memory fact. Since task says no tests/builds; no verified test output. Maybe checkpoint with summary and state. But we are subagent; maybe not needed. AGENTS protocol at end calls checkpoint. We can call memory_checkpoint_task with summary/test_baseline referencing no tests (read-only). Do we have permission? Yes. This marks work. But user says report only. Might be useful. However additional tool call okay. Let's call memory_write or checkpoint? The protocol says end session checkpoint. We can do:
```
memory_checkpoint_task(summary="Read-only investigation...", test_baseline="No tests/builds/downloads run (read-only evidence)", components=[...], decisions=[...])
```
Optional failures none. Possibly use memory_write instead? The protocol explicitly asks checkpoint. Let's call it. Need arguments correct. Let me inspect tool signature maybe checkpoint_task. Could call via execute.