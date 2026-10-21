# Phase-2 research: follow-up 007 F5 — relocate the credential-vault guard to the root conftest

Branch context: `orchestra/007-no-real-vault-writes` (guard exists, acceptance-scoped);
002 + 006 have landed on root `backend/tests/conftest.py`. This report is read-only
research and proposes the implementation task. No guard file was modified.

## 1. Conclusions up front

- Relocation is viable and is the correct follow-up: the guard body is self-contained and only
  depends on `secrets_module` (imported in the acceptance conftest at line 23) plus the pytest
  `monkeypatch` fixture request; the root conftest is free of any overlapping fixture/marker.
- The guard block occupies **lines 31-151** of `backend/tests/acceptance/conftest.py` (start at
  the `# --- Product-credential write guard ---` header on line 31, end at the bare `yield` on
  line 151; separator blanks are 152-153). That is 121 source lines.
- Exactly **two** live code files import from `backend.tests.acceptance.conftest`, and **neither**
  imports the guard symbols — only `ACCEPTANCE_CONTROL_TOKEN` (verified in
  `backend/tests/acceptance/test_local_api_trust.py:11` and
  `backend/tests/acceptance/test_voice_privacy.py:15`).
- Root `backend/tests/conftest.py` defines **none** of the objects the guard references.
- **Double-load hazard is real unless removal accompanies the copy**: pytest auto-loads the root
  conftest for every test under `backend/tests/` (including `acceptance/`) *and* additionally
  auto-loads `backend/tests/acceptance/conftest.py` only for the acceptance subtree. Copying
  without removing yields **two** autouse fixtures wrapping `win32cred` in the acceptance tree.
  Recommendation: **copy the whole guard block to root and delete it from acceptance outright**;
  no re-export is needed because there are zero importers of the guard symbols.
- Sensitive-gate result: **no** non-acceptance test is expected to write to a product-prefixed
  credential in CI, so **no allow-list carve-out is required**.

## 2. Guard block location and anatomy

File: `C:\Sythion\sythion\backend\tests\acceptance\conftest.py` (215 lines).

- `from backend.core import secrets as secrets_module` is at line 23; `ACCEPTANCE_CONTROL_TOKEN`
  is at line 28. The guard begins at line 31.
- Import-time snapshot of the production prefix: line 44
  `_PRODUCT_TARGET_PREFIX = secrets_module._CM_TARGET_PREFIX`.
- Case-folded comparison constant: line 58 `_PRODUCT_TARGET_PREFIX_LOWER`.
- Exception class: lines 61-68 `ProductCredentialWriteAttempt(AssertionError)`.
- Autouse function-scoped fixture: lines 71-151 `_forbid_product_credential_writes(request, monkeypatch)`,
  installed with `@pytest.fixture(autouse=True)` at line 71.
- `_reject()` is at lines 105-124 and implements the fail-closed rule
  `target.lower().startswith(_PRODUCT_TARGET_PREFIX_LOWER)` at line 117 (plus the unreadable-target
  rejection at line 110).
- `guarded_write` / `guarded_delete` are at lines 141-147; the `monkeypatch.setattr` calls are
  at lines 149-150; the single `yield` is at line 151. No teardown code after `yield`.
- Reads (`CredRead`) are deliberately **not** intercepted; only `CredWrite`/`CredDelete` are.

The import-time snapshot is load-bearing: the round-trip test (`test_secret_storage.py:160`) does
`monkeypatch.setattr(secrets_module, "_CM_TARGET_PREFIX", _TEST_TARGET_PREFIX)`; a call-time read
would disarm the guard. Any relocated copy must keep lines 44 and 58 verbatim at module scope.

## 3. Test files importing `backend.tests.acceptance.conftest`

Grep over `C:\Sythion\sythion`:

- `backend/tests/acceptance/test_local_api_trust.py:11`
  `from backend.tests.acceptance.conftest import ACCEPTANCE_CONTROL_TOKEN`
- `backend/tests/acceptance/test_voice_privacy.py:15`
  `from backend.tests.acceptance.conftest import ACCEPTANCE_CONTROL_TOKEN`

No test references `ProductCredentialWriteAttempt`, `_forbid_product_credential_writes`,
`_PRODUCT_TARGET_PREFIX`, or `_PRODUCT_TARGET_PREFIX_LOWER` anywhere. `ACCEPTANCE_CONTROL_TOKEN`
is a fixture-agnostic constant unrelated to the guard, so moving the guard does not disturb
either importer. Grep also confirmed zero matches for `canonical_file_checks`-driven credential
access and zero direct `win32cred` usage outside the acceptance conftest.

- `backend/tests/acceptance/test_canonical_file_checks.py` exercises only file-tool containment
  (symlink/short-name/out-of-root rejections). It never touches the credential vault and does **not**
  call `_forbid_product_credential_writes` even indirectly.

## 4. Root-conftest shared-function analysis

File: `C:\Sythion\sythion\backend\tests\conftest.py` (542 lines). Grep for
`secret|vault|credential|CM_TARGET|win32cred|_forbid_product` yielded **no** guard-related
definitions; the only hits were two prose mentions inside `_isolate_local_env_settings` docstring
region (lines 526, 535) describing credentials generally. None of the following exist at root:

- no `secrets_module` import,
- no `_PRODUCT_TARGET_PREFIX` / `_PRODUCT_TARGET_PREFIX_LOWER`,
- no `ProductCredentialWriteAttempt`,
- no `_reject` / `_target_of` / `guarded_write` / `guarded_delete`.

Root currently owns: marker vocabulary + selection-contract guard (`pytest_configure` 137,
`pytest_itemcollected` 175, `pytest_collection_finish` 334), `_guarded_autonomy_default` autouse
(line 354), reusable DB/client fixtures (lines 365-457), and `_isolate_local_env_settings`
autouse (line 519). There is no name overlap with `_forbid_product_credential_writes`, so the
copy imports cleanly. The relocated block will need its own `secrets` import added near the
existing import block at lines 17-21, following the same `try: backend.core... except ImportError`
pattern.

## 5. Pytest auto-load behaviour (double-load determination)

- Root `backend/tests/conftest.py` is an ancestor of every collected module under
  `backend/tests/`, so pytest auto-loads it for all 1611 tests, including everything in
  `backend/tests/acceptance/`.
- `backend/tests/acceptance/conftest.py` is auto-loaded only for modules under
  `backend/tests/acceptance/`.
- Therefore the acceptance subtree currently gets **both** conftests: root (autouse autonomy +
  env isolation + selection hooks) and acceptance (app/client fixtures + the vault guard).
- Consequence: simply copying the guard to root while leaving it in acceptance gives the
  acceptance subtree **two** autouse `_forbid_product_credential_writes` fixtures. Each
  independently calls `monkeypatch.setattr(win32cred, "CredWrite", ...)` at setup; the
  inner/outer ordering is fixture-arbitrary and turns the "original" captured by the later
  fixture into the earlier fixture's wrapper — i.e. a guard that can no longer reach the real
  vault, or double-raised blocks. This is why the follow-up must **remove** the block from
  acceptance, not just copy it.

## 6. Accuracy check — memory / structural index / product-credential references

- `memory_get_file_context` was queried for both `sythion/backend/tests/acceptance/conftest.py`
  and `sythion/backend/tests/conftest.py`; the structural index confirms the symbol list used
  above (e.g. `ProductCredentialWriteAttempt` 61-68, `_forbid_product_credential_writes` 72-151,
  plus the root selection-guard functions).
- Canonical product prefix definition:
  `backend/core/secrets.py:29` `_CM_TARGET_PREFIX = "Sythion (Synth)/"`; consumed at
  `backend/core/secrets.py:32-33` (`_cm_target`), `backend/core/secrets.py:71` (`TargetName`),
  and `backend/core/secrets.py:84` (`CredDelete`). The acceptance guard snapshots it at
  `acceptance/conftest.py:44`.
- Test-scoped prefix and composed target:
  `backend/tests/acceptance/test_secret_storage.py:40` `_TEST_TARGET_PREFIX`, asserted at
  `:171-173` against the import-time snapshot.
- URL-encoded variants (`Sythion%20%28Synth%29`, `Sythion (Synth)%2F...`, etc.) were grepped
  across the repo and returned **zero** matches. The only other spelling is the lower-cased
  product target `sythion (synth)/...`, which the guard rejects.
- Packaging copies exist at `src-tauri/resources/backend/tests/conftest.py` (153 lines) and
  `src-tauri/resources/backend/tests/acceptance/conftest.py` (91 lines). They are **not** part of
  the pytest tree collected by `pytest.ini:testpaths` and are out of scope for this follow-up;
  if the desktop ships them, refresh them in the same commit.

## 7. Sensitive-gate scan (allow-list check)

Searched the whole `backend/tests/` tree for credential-vault producers:

- `win32cred` usage in tests lives only in `acceptance/conftest.py` (the guard) and
  `acceptance/test_secret_storage.py` (the round-trip, which pins a **test-scoped** prefix at
  line 160). No other test imports `win32cred`.
- `CredentialManagerStore()` construction in tests is the round-trip test only; other credential
  callers in `backend/api/routes.py` (`set_secret` at 1953 and `validate` path at 2128) are
  exercised in tests via `MemoryStore` (`test_setup_readiness.py` and `test_secret_storage.py`
  use `set_secret_store_for_testing(MemoryStore())`).
- Provider E2E tests only call `resolve_ollama_api_key` (read-only).
- No test invokes the secret endpoints against the real store.

Conclusion: **no non-acceptance CI test writes to (or deletes) a product-prefixed credential**.
The relocation needs **no allow-list carve-out**. The guard's fail-closed rule will still permit
the round-trip (whose target is under `_TEST_TARGET_PREFIX`, which does not start with
`_PRODUCT_TARGET_PREFIX`) and will still forbid the product prefix globally.

## 8. Expected diff shape

- `backend/tests/acceptance/conftest.py`: remove lines 31-151 (and the now-redundant blank
  separator lines 152-153); file drops ~121 lines. Do not leave a re-export stub — there are
  zero importers of the guard symbols.
- `backend/tests/conftest.py`: add the `secrets` import (5 lines, matching the existing
  `backend.core.config` pattern) and append the 121-line guard block at module scope, after the
  current line 542 or among the other constants. Optionally reword "acceptance test ..." to
  "test ..." in the rejection messages; cosmetic only.
- `backend/tests/test_vault_guard_root_scope.py` (new, ~45 lines): exercise the guard outside
  the acceptance tree. Sketch:

```python
import pytest, win32cred
from backend.core import secrets as secrets_module
from backend.tests.conftest import ProductCredentialWriteAttempt

_PRODUCT = "Sythion (Synth)/ollama_api_key"

def test_vault_guard_blocks_product_write_at_root_scope():
    try:
        import win32cred
    except ImportError:
        pytest.skip("pywin32 not available")
    cred = {"Type": win32cred.CRED_TYPE_GENERIC, "TargetName": _PRODUCT,
            "CredentialBlob": b"x", "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE,
            "UserName": "", "Comment": "probe"}
    with pytest.raises(ProductCredentialWriteAttempt):
        win32cred.CredWrite(cred, 0)   # guard fires before delegating
    cred["TargetName"] = "sythion (synth)/ollama_api_key"
    with pytest.raises(ProductCredentialWriteAttempt):
        win32cred.CredWrite(cred, 0)
    try:
        win32cred.CredDelete(_PRODUCT, win32cred.CRED_TYPE_GENERIC, 0)
    except Exception:
        pass
```

Assertions to add in IMPL verification: the new test passes under root scope; the entire suite
still records 0 product-prefixed attempts (spy probe); with the guard removed in a scratch copy
the same probe exits 0 and reaches the API layer (teeth check).

## 9. Recommendation

Dispatch an IMPL task (scope: test-only) that (a) copies the guard block verbatim to root
`conftest.py` with an added `secrets` import, (b) deletes the block from
`acceptance/conftest.py` outright with no re-export, and (c) adds
`backend/tests/test_vault_guard_root_scope.py` with the probe above. Mark follow-up 007 F5 as
in progress; verify with the existing two-layer spy measurement run under root conftest only.
