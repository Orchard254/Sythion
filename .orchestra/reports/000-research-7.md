# Authorized beam diagnostic — 2026-10-23

## Verdict

**Beam answered; the built release backend misparses its answer.** One live chat returned HTTP 200 and native `message.content == "OK"`. The parser extracted directly from `src-tauri/target/release/backend/api/routes.py` returned `""` on that SAME response, triggering its existing empty-response guard (502). The current source/staged helper returned `"OK"`. This establishes a stale built-payload defect, not a verified UI repair.

The pre-existing, unrelated report at this authorized output path is preserved below rather than overwritten.

## Live evidence and isolation

- Existing configured key resolved in memory through `resolve_ollama_api_key(settings.ollama_api_key)`. No key, environment dump, header, control token, thinking text, or raw response body was printed or saved.
- Configured origin verified as `https://ollama.com` with no userinfo, nonstandard port, path, query or fragment. HTTPX used `follow_redirects=False`, `trust_env=False`, connect timeout 15 seconds, request timeout 60 seconds. The product chat method also received `timeout=60` (its own connect timeout is 30 seconds). No redirect was followed.
- `GET /api/tags`: **200**, 23 models, exact ID `beam` listed.
- **Exactly one** `POST /api/chat`: **200**. No retries, capability probes, tools, OS-action requests, or additional chat calls.
- Exact app chat payload: `{"model":"beam","messages":[{"role":"user","content":"Reply with OK"}],"max_tokens":16,"num_ctx":131072,"stream":false}`. This retains the application's top-level `max_tokens` and `num_ctx`; it does not establish that the provider honors those fields as generation limits.
- Read-only resolved selected model: `beam`. Independently instantiated `Settings(_env_file=<built backend>/.env)` matched source origin and resolved key (only equality booleans emitted); both selected-model overrides were `beam`. No actual model-selection mismatch in this probe.
- Response model matched `beam`; `done` was boolean `true`; native content was a nonblank string; **only preview: `OK`**.
- Top-level response field types: `created_at:str`, `done:bool`, `done_reason:str`, `eval_count:int`, `message:dict`, `model:str`, `prompt_eval_cached_count:int`, `prompt_eval_count:int`, `total_duration:int`.
- `message` field types: `content:str`, `role:str`, `thinking:str`. Thinking contents were neither inspected for reporting nor emitted.
- Exact built parser AST statements (`routes.py:2215-2220`) extracted `""`; source helper extracted `"OK"`. Built `:2221-2222` would raise **502, `Inference returned an empty response`**. That is a measured parser result plus a code-derived endpoint status, NOT an HTTP call to the local onboarding endpoint.

### Reproducible diagnostic

Sanitized script retained at `C:\Users\orcha\AppData\Local\Temp\opencode\beam_diagnostic_000_research_7.py`.

Executed once from approved Temp directory:

```powershell
python -B C:\Users\orcha\AppData\Local\Temp\opencode\beam_diagnostic_000_research_7.py
```

Re-running this command makes another live call; this session ran it **once**. The script imports only reviewed config/secrets/storage modules, HTTPX and standard libraries. Backend/core initializers have no startup behavior. It disables bytecode writes, never imports routes/main/database, and never calls the endpoint. It compiles the actual built adapter `chat`/`close` methods into an isolated class and supplies a no-redirect HTTPX client; constructors and singleton initialization are bypassed. It AST-extracts the actual built inline parser and the source helper, executing only those parser statements on the in-memory provider response. Thus it exercises real payload/parsing logic without running application startup or persisting verification metadata.

Profile directory calculation uses pure `resolve_data_dir`, NOT `data_dir()` (which would create directories/change ACLs). No real DB is opened. The script can read only the selected-model field from profile metadata if no configured override exists; here the configured override was `beam`.

Offline supplementary check: instantiate `Settings(_env_file=r'C:\Sythion\sythion\src-tauri\target\release\backend\.env')`, compare base URL and resolver outputs in memory to source settings, emit only equality booleans and model IDs. Both origins equaled the official default and both resolved keys matched. Neither `.env` was displayed or modified.

## Payload identity (SHA-256)

Paths below are relative to `C:\Sythion\sythion`.

| Path | SHA-256 | Parser helper |
|---|---|---|
| `backend/api/routes.py` | `5c0dcafad5270182461eae3f47ad261b92252d9f4a56997eac51491fd10614d3` | present |
| `src-tauri/resources/backend/api/routes.py` | `5c0dcafad5270182461eae3f47ad261b92252d9f4a56997eac51491fd10614d3` | present |
| `src-tauri/target/release/backend/api/routes.py` | `9f07cf314d9bf6e7e7db6cd07e73f6477f59a7a49f816e6f1083acb855797e5e` | **absent** |
| `src-tauri/target/release/sythion.exe` | `ac1f82a20f9467ab9b9bfc94e253d40b94f7ba20a038885cb2e40e5347f55f9d` | n/a |
| `src-tauri/target/release/python-runtime/python.exe` | `31fe10b4ed82a2f960af0ef9931f46a4b84ff286f8b6ffd9dee098cf4f10f17f` | n/a |
| `src-tauri/src/sidecar.rs` | `a3e6efda5cc6ac3b796a4c8923741e77112320c103000040b0eaa82275246a18` | n/a |
| `src-tauri/tauri.conf.json` | `bce3b7ff38b8bf089370948108dbdc0d7f9b674b12b4db523a42db023ab5e595` | n/a |

`adapters/ollama_adapter.py` is identical in source, staging and built payload: `6a203e9f12e603b66a88e38d2c91b0b499e9f79ea251b3bc69ef9ef78c5e066b`.
Source/built `core/config.py` both hash to `6a2c9344ce93e6986e475c17e7917fa973d8d8c3a8ec71160919e00951e78fc0`; source/built `core/secrets.py` both hash to `a4c5ccc2dc5c29a4a957a465881790e7f3cd48b92009fefe4e62fe8c4dd3baa0`.

Reproduce identity checks with `Get-FileHash <path> -Algorithm SHA256` and `Select-String <routes path> -Pattern 'def _extract_inference_text' -Quiet`. Built routes mtime was `2026-10-19T22:35:31.9982259Z`; source/staged routes mtime was `2026-10-22T08:48:24.5300785Z`. Hashes, not timestamps, establish the mismatch.

## Launch resolution and model-selection caveat

Current `sidecar.rs:381-409` checks explicit `Sythion (Synth)_PYTHON`, then `Sythion (Synth)_SIDECAR_DIR`, then packaged `resource_dir/python-runtime/python.exe` plus `resource_dir/backend/main.py`. Packaged resources take priority over a checkout. It sets cwd to that resource directory and executes `python -m uvicorn backend.main:app` (`:495-500`). `tauri.conf.json:22-25` maps staging `resources/backend` to packaged `backend` and `resources/python-runtime` to packaged `python-runtime`.

Consequently, the normal non-overridden **inspected release executable** at `src-tauri/target/release/sythion.exe` resolves its adjacent release `backend`, not the current checkout or staging copy. Both launcher overrides were absent in the diagnostic shell; this is not proof of another launcher's environment. No Sythion app or packaged Python sidecar was running during process inspection (`Get-CimInstance Win32_Process`, names/paths only). No matching desktop shortcut or executable in the few standard install locations checked was found. The user's precise past executable/launch environment therefore remains unverified. Binary hash records identity; it does not prove current Rust source was compiled into that binary.

Both source and built onboarding routes accept request `model_id` to validate/list and label verification, but call `provider.chat` without forwarding it. The provider facade also omits a model override and the adapter uses its selected-model snapshot. A differing request ID or stale singleton could therefore test a different model than the route labels. **This is a code-observed latent mismatch, not the cause measured here:** source/built configured selection and sent/returned model were all `beam`. Current UI source first calls `selectModel(selectedModel)` then `inference(selectedModel)`; packaged UI behavior/cache refresh was not exercised.

## Prior evidence, limitations and next step

Read item 021 implement reports and R-028 evidence in `.orchestra/research.md`. Those records describe the same native-response omission and historical successful beam requests, but were not used as proof that beam works today. Today's independent live request and actual built parser establish the result above.

Not checked: native click-through, live local onboarding HTTP route, startup, installer contents/installed application, packaged Python execution/import resolution, Rust binary/source equivalence, stale `.pyc` effects, all possible launch shortcuts/overrides, provider tools support, general model reliability, or a rebuilt payload. No unit/full suite was run; this was a bounded diagnostic. No app processes were started/stopped, no product files/settings/credentials/DBs were written, no migrations/builds/commits were performed. Writes were limited to this report, the sanitized Temp script, and required CogniGraph observations/checkpoint. The unrelated old report below was preserved.

**User-facing next step:** Beam is responding. Have the release backend resources refreshed/repackaged from the reviewed current source, then verify the exact executable's setup flow. This diagnostic did not perform or authorize that rebuild or claim the UI now works.

---

# Preserved earlier report: Follow-up 006 research: process-environment leakage behind `.env` isolation

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
