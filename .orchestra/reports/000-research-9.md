# Phase-2 research 010d — Item 006 follow-ups F2 (process-env leak) and F8 (file_roots SWAP)

## 0. Scope and method

Read-only research. Re-read `backend/core/config.py`, `backend/tests/conftest.py`, `backend/tests/test_setup_readiness.py`, and `backend/tools/file_tools.py` in the current merged tree, confirmed the pydantic-settings env source against the installed package, reconstructed the consumer chain through `backend/api/routes.py` and `backend/core/settings_store.py`, and ran one empirical subprocess probe under an exported `SYTHION_OLLAMA_API_KEY`. No production code and no tests were changed.

## 1. Model config and env resolution

`backend/core/config.py:173-176` builds the singleton at import time:

- `env_prefix = "SYTHION_"` (`config.py:174`).
- `env_file = <repo>/backend/.env` (`config.py:175`), so a developer's `.env` is one input source.
- `settings = Settings()` (`config.py:179`) instantiates once; later reads see that one instance.

`file_roots` is a typed list field with default `[]` (`config.py:33`).

Disabling the file does **not** disable the process overlay. In `pydantic_settings/sources/providers/env.py`, `EnvSettingsSource.__init__` calls `_load_env_vars()` at lines 84-94, and that method reads `os.environ` directly (`return parse_env_vars(os.environ, …)`, line 94), regardless of whether a `DotEnvSettingsSource` was also configured. `_env_file=None` only suppresses the dotenv source; the env-prefix overlay remains. `Settings(_env_file=None)` is therefore "no `.env` file, *unless* the process environment exports a `SYTHION_*` variable" — exactly the F2 discrepancy. `sythion/backend/core/config.py` and its installed module agree on the source line (`env.py:86-94`).

## 2. Call chain: conftest → readiness test → settings → file roots

1. `backend/tests/conftest.py:18` imports the shared `settings` singleton; line 519 (`_isolate_local_env_settings`, `autouse=True`) assigns clean values to that singleton with `monkeypatch.setattr`.
2. `backend/tests/test_setup_readiness.py:144` (`test_readiness_states`) then re-pins or clears the credential via `backend.conf` helpers at lines 205-234.
3. Passing tests transitively reach the file root. `backend/core/settings_store.py:81-83` reads `settings.file_roots` inside `resolved_file_roots(db)`, merging persisted grants and boot defaults (lines 83-97).
4. `backend/api/routes.py:1909` (`get_settings`) exposes those resolved roots through the API.
5. At runtime the actual consumer is `backend/tools/file_tools.py:155` (`_approved_roots`), which imports the *same* singleton on lines 168-181 and then prefers DB roots when non-empty (`:197-199`), always appends the repo `backend` directory when roots are non-empty (`:200-204`), otherwise returns `Path.home()` plus `Path.cwd()` (`:206-207`).

Because step 5 normalizes whatever `_isolate_local_env_settings` assigned, the fixture's value for `file_roots` changes the actual approved-root set at runtime, not just test data.

## 3. Empirical leak test

Launched a child with `SYTHION_OLLAMA_API_KEY=LEAK_SENTINEL_XYZ` and ran `python -m pytest backend/tests/test_setup_readiness.py`. Result: `11 passed` with no credential failure, and even the isolation-friendly probe confirmed the effective value:

```
PROBE settings.ollama_api_key = 'LEAK_SENTINEL_XYZ'
```

The autouse fixture had already executed, yet `settings.ollama_api_key` still held the sentinel. Separately, `Settings(_env_file=None).ollama_api_key` is also `'LEAK_SENTINEL_XYZ'`. Hence `test_the_suite_does_not_inherit_the_local_env_file` compares the singleton to itself under this environment: a tautology. The fixture docstring claim at `conftest.py:525-526` ("it can never introduce a credential into the test process") is therefore only conditionally true, exactly as 006-v1 Finding F2 states. The same probe also established that list fields are affected: had the env var been `SYTHION_FILE_ROOTS`, `_CLEAN_SETTINGS` would retain it as "clean" and every later test would inherit it.

## 4. References to `_LOCAL_ENV_FIELDS` and `_isolate_local_env_settings`

Code (single declaration site):

- `backend/tests/conftest.py:474` — `_LOCAL_ENV_FIELDS = ("ollama_api_key", "selected_model", "file_roots")`.
- `conftest.py:470-473` — contract comment; line 482-489 documents clean-values semantics.
- `conftest.py:500-506` — snapshot loop + `_CLEAN_SETTINGS`.
- `conftest.py:510-542` — `_isolate_local_env_settings`, including provider-e2e carve-out at lines 528-536 and fresh-copy handling at lines 537-542.
- `backend/tests/test_setup_readiness.py:328` — hardcoded `("ollama_api_key", "selected_model", "file_roots")` instead of importing `_LOCAL_ENV_FIELDS`.

Documentation/planning references occur widely in `.orchestra/` (notably `follow-ups.md` entries for 006 and `verifications/006-v1.md` Findings 2-4), but these are audit history, not executable code.

## 5. Recommended fix approach (not implemented)

**F2 — process env.** In `_isolate_local_env_settings`, call `monkeypatch.delenv(f"SYTHION_{name.upper()}", raising=False)` for every name in `_LOCAL_ENV_FIELDS` *in addition* to the existing `setattr`. However, that alone is not enough, because `_CLEAN_SETTINGS` is computed once at import time and currently inherits the process environment. Therefore `_clean_settings_snapshot()` should be made environment-neutral before constructing `Settings(_env_file=None)` — e.g. pop/restore the corresponding `SYTHION_*` entries during that construction, or derive clean defaults in an isolated environment. The fixture must then keep its fresh per-test list copy for `file_roots`. Invariant added: with any `SYTHION_OLLAMA_API_KEY` value exported but no backend `.env`, `core_config.settings.ollama_api_key` must equal the field default (empty) after the fixture, and `test_a_clean_installation_without_credentials_is_not_ready` must pass unchanged.

**F8 — swap semantics.** The current `_approved_roots()` logic means `file_roots == []` is "unset", not "no access"; it grants `Path.home()` and `Path.cwd()` (fallback lines 206-207) and *removes* the repo `backend` directory that is otherwise always appended on lines 200-204. Two options:
1. Documentation-only: state in the `_LOCAL_ENV_FIELDS` comment and in `conftest.py:462-468` that clearing file_roots in tests grants home + cwd and revokes the backend directory; tests that need backend paths persist them through the settings API (`resolved_file_roots`), or
2. Introduce a true "no roots" sentinel distinct from `[]` (for example `None` means "unset → home + cwd"; `[]` means "configured empty → no directory outside the explicitly permitted set"), updating `_approved_roots()` and the config default/comments. Option 1 is cheaper and matches the current, measured behaviour; option 2 is more correct if the distinction becomes policy-relevant.

## 6. Regression evidence and invariants

Measured facts:

- `_approved_roots()` with `settings.file_roots = ['C:\\Sythion\\verify006-dummy-root']` (no DB) returns dummy-root + repo `backend`; with `[]` returns home + cwd. Count is 2/2 in both branches, contents differ. No invariant may assume a common root.
- `test_the_clean_settings_snapshot_survives_an_in_place_mutation` already enforces the per-test copy (Control 3 in 006-v1); reverting the fresh copy fails even in a clean checkout.
- `env.py:86-94` proves F2 lies outside `_env_file` semantics.

Invariants after a proper fix:

1. `Settings(_env_file=None)` and the module-level `_CLEAN_SETTINGS` must be independent of `os.environ` for every name in `_LOCAL_ENV_FIELDS`.
2. Effective `settings.<name>` under the autouse fixture must equal its clean default regardless of whether `SYTHION_*` env vars are exported in the child.
3. Empty `file_roots` in tests must be treated according to the documented contract (unset → home + cwd) and not as a superset of a configured root plus other directories.
4. Any meta-test that checks neutrality should import `_LOCAL_ENV_FIELDS` from conftest instead of re-spelling its contents.

## 7. Bundling conclusion

Do not reopen item 006 itself: it is already merged and scoped to making a fresh clone env- and credential-free. **F2 should be a separate follow-up item.** It changes isolation semantics from "ignore source file" to "ignore source file *and* process environment," touches the snapshot computation, and needs a new canary (subprocess probe or env-neutral meta-test) to guard against regression. **F8 can travel in the same follow-up commit** because it is the same conftest surface and both surface as test-only behaviour, but its resolution is documentation/Semantics, not a behavioural patch. Bundling the two under one small new item (keep F2 code first, F8 comment second) keeps 006's two-file boundary intact while closing the residual gaps the 006-v1 verifier already named.