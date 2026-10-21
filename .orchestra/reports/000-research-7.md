# Follow-up 006 research: process-environment leakage behind `.env` isolation

## Summary

The isolation shipped in item 006 neutralises a developer's gitignored
`backend/.env` **file**, but not the **process environment**. Pydantic-settings
applies the OS environment (`SYTHION_*`) through a separate source from the
dotenv `env_file`, and conftest's `Settings(_env_file=None)` only disables the
latter. Consequently an exported `SYTHION_OLLAMA_API_KEY` (or
`SYTHION_SELECTED_MODEL`, `SYTHION_FILE_ROOTS`, and any other `SYTHION_`-prefixed
field) still becomes the "clean" baseline the fixture assigns to every test.
Under that condition the suite's documented claim "it can never introduce a
credential into the test process" is not unconditionally true.

## 1. `backend/core/config.py` end to end

- `Settings(BaseSettings)` (`backend/core/config.py:12`) declares all application
  fields. There is no per-field env override or custom source logic in this file.
- `model_config = {"env_prefix": "SYTHION_", "env_file": os.path.join(..., ".env")}`
  (`:173-176`) binds two independent input sources: the dotenv file
  `backend/.env` and every `SYTHION_`-prefixed process environment variable.
- `settings = Settings()` (`:179`) runs once at import time, so both sources are
  frozen into the singleton before any test fixture can intervene.
- Consumers that treat `settings.ollama_api_key` as an ambient credential:
  `resolve_ollama_api_key(settings.ollama_api_key)` (`:211`),
  `resolve_selected_model()` (`:196-200`), and the two route-level readiness
  checks below.
- `env_prefix` means **not just the three isolated fields** can be supplied by a
  `.env`: every field (`ollama_cloud_base_url`, `development_mode`,
  `control_token`, etc.) is reachable through `SYTHION_*`.

Pydantic-settings v2.15.0 precedence (default
`settings_customise_sources`, `main.py`) is:
`init_settings` > `env_settings` (OS `os.environ`) > `dotenv_settings` (env_file)
> `file_secret_settings` > defaults. Passing `_env_file=None` to
`Settings(...)` only selects "no dotenv source" (`main.py:332`, env_file sentinel
`ENV_FILE_SENTINEL`); the `EnvSettingsSource` still calls `parse_env_vars(os.environ, ...)`
(`sources/providers/env.py:86-94`). Therefore `Settings(_env_file=None)` **does**
read and apply `SYTHION_*` from the process environment.

## 2. The current isolator in `backend/tests/conftest.py`

- `_LOCAL_ENV_FIELDS = ("ollama_api_key", "selected_model", "file_roots")`
  (`:474`). Only these three fields are neutralised; everything else in
  `Settings` keeps inheriting `.env` / process-env values.
- `_clean_settings_snapshot()` (`:482-503`) builds `Settings(_env_file=None)`
  (`:498`) and copies the three fields into a dict, shallow-copying lists
  (`:502`). The module-level `_CLEAN_SETTINGS` (`:506`) is computed **at conftest
  import time**.
- The docstring at `:485-489` explicitly warns that this result is "the process
  environment's `SYTHION_*` value when that variable is exported, otherwise the
  field default."
- `_isolate_local_env_settings(monkeypatch, request)` (`:519-542`) is
  `autouse`. It skips `provider_e2e` tests (`:528-536`) so they keep the ambient
  credential; otherwise it runs `monkeypatch.setattr(settings, name, <clean>)`
  for each of the three fields (`:537-542`), with a fresh copy of list values.
- `clean_settings_snapshot` fixture (`:509-517`) exposes the shared snapshot.
- A separate `_guarded_autonomy_default` autouse fixture (`:354-362`) removes
  `SYTHION_AUTONOMY_LEVEL` and pins `autonomy_level` / consolidation, showing the
  suite already knows how to strip process env for individual fields.

## 3. Does `Settings()` normally read the process env? Yes

Citation: `pydantic_settings.sources.providers.env.EnvSettingsSource._load_env_vars`
returns `parse_env_vars(os.environ, ...)` (`env.py:86-94`), and the default
source tuple puts `env_settings` ahead of `dotenv_settings`
(`main.py:442-448`). `Settings(_env_file=None)` therefore disables only the
dotenv source, not `os.environ`.

## 4. Call sites where settings fields are derived from the environment

Direct env reads (`SYTHION_*`):
- `backend/core/config.py:173-176` — `env_prefix="SYTHION_"`, `env_file`.
- `backend/core/secrets.py:114` — `SYTHION_SECRET_BACKEND`.
- `backend/services/model_engine.py:178,197` and
  `backend/services/task_execution_loop.py:695` and
  `backend/tools/system_tools.py:809` — `SYTHION_AUTONOMY_LEVEL`,
  overriding `settings.autonomy_level`.
- Storage/data-dir and shell paths (`storage.py:120-121`, `main.py:163`,
  `handshake.py:30`, `logging_config.py:20`) read `SYTHION_*` directly and are
  not covered by the three-field isolation list.
- `cli.py:28-29`, `verify_tools.py:52/60/101/103` read
  `SYTHION_CONTROL_TOKEN`, `SYTHION_DEVELOPMENT_MODE`,
  `SYTHION_AUTONOMY_LEVEL`.

Credential path:
- `backend/api/routes.py:2007` —
  `api_key_ok = has_secret(OLLAMA_API_KEY) or bool(settings.ollama_api_key)`.
- `backend/api/routes.py:2261` — same shape for `setup_readiness`.
- `backend/api/routes.py:2130,2179` — adapter re-seeded from
  `resolve_ollama_api_key(settings.ollama_api_key)`.
- `backend/main.py:255`, `backend/adapters/ollama_adapter.py:58,437`,
  `backend/core/config.py:211` — resolve through `settings.ollama_api_key`.
- `backend/core/secrets.py:167-179` — `resolve_ollama_api_key(env_override=...)`
  gives explicit env override precedence over the credential store.
- `backend/tests/test_setup_readiness.py` exercises this via `_client`,
  `_clear_local_env_key` (`:205-214`), `_pin_test_credential` (`:217-233`),
  and `test_the_suite_does_not_inherit_the_local_env_file` (`:315-336`).

## 5. Risk to the documented claim

Docstring claim (`conftest.py:524-526`): "Only CLEAN values are ever assigned ...
in a clean checkout ``\"\"``, ``\"\"`` and ``[]`` - so this fixture can never
introduce a credential into the test process."

What holds:
- The **file** `backend/.env` is disabled by `_env_file=None`; a developer's
  `.env` values for the three fields no longer leak.
- `monkeypatch` reverts each assignment at teardown, so no cross-test leakage.
- Provider-e2e tests are carved out (`:528-536`) and list values are copied
  per test (`:538-542`).
- In a **genuinely clean** environment (no `SYTHION_*` exported), the baseline
  is exactly the defaults.

What does not hold unconditionally:
- If `SYTHION_OLLAMA_API_KEY` (or `SYTHION_SELECTED_MODEL`,
  `SYTHION_FILE_ROOTS`) is exported in the shell/CI runner, `_clean_settings_snapshot`
  folds that value into `_CLEAN_SETTINGS`, and the fixture then writes it into
  `settings`. Empirical check (executed):
  ```
  $env:SYTHION_OLLAMA_API_KEY = "sk-test-not-a-real-credential"
  python -c "from backend.core.config import Settings; print(Settings(_env_file=None).ollama_api_key == 'sk-test-not-a-real-credential')"
  # -> True
  ```
  and `settings.ollama_api_key == env value: True`, so the readiness check
  `ollama_api_key_ok` (`routes.py:2261`) is `True` even though no credential was
  stored — the process-inherited value is indistinguishable from a real one.
- `Settings(_env_file=None)` also reads `SYTHION_` values for fields outside the
  three-field list, so other ambient configuration still leaks.
- `test_the_suite_does_not_inherit_the_local_env_file` (`:315-336`) compares
  `settings` to `Settings(_env_file=None)`. Under an exported env var both sides
  agree, so the test passes while masking the leak (a tautology).
- `_default_store()` (`secrets.py:112-123`) still honours `SYTHION_SECRET_BACKEND`,
  so the active secret backend is also ambient-controlled.
- Process-level reads (`SYTHION_AUTONOMY_LEVEL` in model_engine, task loop,
  system_tools) bypass Settings entirely, so even a "neutralised" settings
  singleton does not isolate those paths.

## 6. Suggested boundary for `_isolate_local_env_settings`

It should neutralise **both** delivery channels for the isolated fields, not just
the file:

1. Make the snapshot environment-independent. The clean baseline is the
   declared defaults (`""`, `""`, `[]`), not "whatever the environment says."
   Compute it from field defaults (e.g. `Settings.model_construct(...)` or an
   explicit `_clean_settings_snapshot` that does not consult `os.environ`),
   ignoring process env. Concretely:
   ```python
   def _clean_settings_snapshot():
       defaults = {name: getattr(Settings, name, None) for name in _LOCAL_ENV_FIELDS}
       # or construct with explicit empty values while disabling dotenv:
       clean = Settings(_env_file=None, ollama_api_key="", selected_model="", file_roots=[])
       ...
   ```
   because init kwargs take precedence over the env source.
2. Scrub the process env at test start for the same fields:
   `monkeypatch.delenv("SYTHION_OLLAMA_API_KEY", "SYTHION_SELECTED_MODEL",
   "SYTHION_FILE_ROOTS", raising=False)` (and `SYTHION_AUTONOMY_LEVEL`, already
   handled), so direct `os.environ` readers in model_engine/task loop/tools cannot
   inherit them. Keep the `provider_e2e` carve-out intact for the credential and
   `SYTHION_RUN_PROVIDER_E2E`.
3. Optionally extend the list / make it exhaustive: neutralise every
   `SYTHION_`-prefixed field that the suite assumes defaults for, or set a
   module-level `clean_env` context at collection time. At minimum, document that
   only the three fields are reset, so new env-derived settings are not
   implicitly isolated.
4. Update the discrimination test (`test_the_suite_does_not_inherit_the_local_env_file`)
   to compare against environment-independent defaults, or include a probe that
   fails when `SYTHION_OLLAMA_API_KEY` is exported. Otherwise the check remains a
   tautology.

Test-infrastructure implications:
- Canonical verification must run pytest in a **subprocess** with all
  `SYTHION_*` scrubbed (the verify006 pattern: `env = {k:v for k,v in
  os.environ.items() if not k.upper().startswith("SYTHION_")}`), because the
  live developer shell already has them exported; running in-process cannot
  disprove inheritance. Compare outcomes with/without the scrub.
- Provider-e2e remains an explicit opt-in (`SYTHION_RUN_PROVIDER_E2E=1`) with
  injection at job level, so ordinary CI stays scrubbed.
- Settings is a singleton built at import (`config.py:179`); do not attempt to
  rebuild it per test. Continue to patch attributes and clear env vars, and reset
  both in teardown.
- If env-scrubbing becomes standard, add a collection-time assertion or a
  module-scoped autouse fixture verifying that no `SYTHION_*` value leaked into
  the clean baseline.

## 7. Empirical verification command

Demonstrates process-environment inheritance into Settings (executed above; result
`True`):

```powershell
$env:SYTHION_OLLAMA_API_KEY = "sk-test-not-a-real-credential"
python -c "import backend.core.config as c; print('settings.ollama_api_key == env:', c.settings.ollama_api_key == 'sk-test-not-a-real-credential'); print('Settings(_env_file=None).ollama_api_key == env:', __import__('backend.core.config', fromlist=['Settings']).Settings(_env_file=None).ollama_api_key == 'sk-test-not-a-real-credential')"
Remove-Item Env:\SYTHION_OLLAMA_API_KEY
```

To verify the boundary fix, re-run with the same variable exported and confirm
the suite no longer borrows it (expected: `ollama_api_key_ok` stays `False` in
`test_a_clean_installation_without_credentials_is_not_ready`, and
`Settings(_env_file=None).ollama_api_key` is empty even though the env var is
set).