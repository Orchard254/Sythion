# Report: research - 001 - attempt 1

> Follow-up pass on the cycle-1 research. One new candidate, **R-019**, added at the
> top of `.orchestra/research.md`. R-001..R-018 were **not** renumbered, reordered,
> edited or deleted - the Scout's `Status:` lines in them are untouched (verified: 19
> `### R-NNN` blocks, 19 of each field, and the adopt/reject status values are intact).

## What I did

### Read

- `.orchestra/briefs/001-research-1.md`, `.orchestra/goal.md`, `.orchestra/critiques.md`
  (the `2026-10-20 19:10 - implementer - 001 - ACCEPT` and `2026-10-20 18:05 - baseline - 000 - ACCEPT`
  blocks), `.orchestra/reports/001-implement-1.md`, `.orchestra/research.md` (my own file).
- `backend/tests/test_setup_readiness.py` (whole file), `backend/core/config.py`,
  `backend/core/secrets.py`, `backend/api/routes.py` (the readiness and onboarding
  endpoints), `backend/tests/conftest.py`, `backend/tools/file_tools.py` (root handling),
  `backend/core/settings_store.py` (root resolution), `backend/adapters/ollama_adapter.py`.
- `frontend/src/lib/setupReadiness.ts`, `docs/ARCHITECTURE.md` (the secret-storage
  section), `backend/.env.example`, `.gitignore`.
- `backend/tests/acceptance/test_secret_storage.py` (found during the sweep - see Finding 4).

### Method

The brief's question was *mechanism*, not *correlation*: which input makes the test
pass, where is it read, what else is sensitive, and is it a test-isolation defect or
a product defect. So I used a **scratch environment outside the repository** and never
touched a tracked file, `backend/.env`, or the git index.

1. **A scratch copy of the tree** at
   `C:\Users\orcha\AppData\Local\Temp\opencode\envprobe` - `backend/` (minus
   `__pycache__`, `*.db`, **`.env`**), `scripts/`, `src-tauri/` (minus `target` and
   `python-runtime`) and the root `cli.py`. Verified after the copy:
   `Test-Path <scratch>/backend/.env` -> **False**; `backend/.env.example` -> True;
   no `.db` files copied. `1609 tests collected` - identical to the main tree.
2. **A child-process environment runner**
   (`C:\Users\orcha\AppData\Local\Temp\opencode\run_env.py`) that clears every
   `SYTHION_*` variable in the child process and then optionally sets one. Nothing
   outside the child process is changed.
3. **A 2x2 matrix** over {`backend/.env` present / absent} x {env var set / unset}.
4. **A mechanism probe** (`probe_mechanism.py`) that prints only booleans, lengths and
   file paths - **never a credential value**.
5. **A fix-shape probe** (`probe_fixshape.py`) that pins the credential on the
   already-imported settings singleton, to show the proposed fix actually works.
6. **A scope sweep**: grep of `backend/tests/**` for `os.environ` / `os.getenv`, and a
   full-suite run of the `.env`-free scratch tree.
7. **A full-suite run of the `.env`-free scratch tree**, plus a second one with a
   dummy key, to isolate the two variables.

### Files I wrote

- `C:\Sythion\sythion\.orchestra\research.md` - the R-019 block, inserted at the top.
- `C:\Sythion\sythion\.orchestra\reports\research-002.md` - this file.

Nothing else in the repository was created, edited, moved or deleted. No branch, no
commit, no push, no `make clean`, no `scripts/stage_python_runtime.py`, no
`npx tauri build`, no migration, no database reset.

## Evidence

### E1 - The 2x2 matrix: `.env` and the environment variable are alternatives

Command shape (from the repository root, and from the scratch root):
`python <temp>/run_env.py <unset|set> backend/tests/test_setup_readiness.py`.

| # | Tree | `SYTHION_OLLAMA_API_KEY` env var | Output |
|---|---|---|---|
| A | main tree (`backend/.env` present) | unset | `8 passed, 600 warnings in 4.00s` |
| B | main tree (`backend/.env` present) | dummy | `8 passed, 600 warnings in 4.12s` |
| C | scratch tree (**no** `backend/.env`) | unset | `1 failed, 7 passed, 597 warnings in 5.11s` |
| D | scratch tree (**no** `backend/.env`) | dummy | `8 passed, 600 warnings in 4.10s` |

The failure in C is exactly:

```
backend/tests/test_setup_readiness.py:160: AssertionError
>       assert body["full_ready"] is True
E       assert False is True
```

So: **either input alone makes the test pass**; only the complete absence of both
fails it. The `.env` file and the environment variable are two routes to the same
setting, not two independent requirements.

### E2 - Where the input is read, exactly

`backend/core/config.py:173-176`:

```python
model_config = {
    "env_prefix": "SYTHION_",
    "env_file": os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"),
}
```

`backend/core/config.py:179`: `settings = Settings()` - the singleton is built **once,
at import time**.

The probe (`probe_mechanism.py`, outside the repository; it prints no value, only
booleans, lengths and paths):

```
--- MAIN TREE, no env var ---
backend.core.config.Settings.model_config : {... 'env_prefix': 'SYTHION_',
  'env_file': 'C:\\Sythion\\sythion\\backend\\.env' ...}
settings.ollama_api_key is non-empty      : True
settings.ollama_api_key length            : 57
Settings(_env_file=None).ollama_api_key non-empty: False      <-- the value came from the .env

--- MAIN TREE, dummy env var ---
settings.ollama_api_key is non-empty      : True
settings.ollama_api_key length            : 20                  <-- len("dummy-not-a-real-key")
effective value == env var (env wins)   : True                 <-- the env var OVERRIDES the .env
Settings(_env_file=None).ollama_api_key non-empty: True

--- SCRATCH COPY (no .env), no env var ---
env_file: '...\\envprobe\\backend\\.env'
settings.ollama_api_key is non-empty      : False
settings.ollama_api_key length            : 0
```

Three facts fall out: the value is read from the dotenv file when there is no env var;
the **environment variable wins** over the file; and the file is read **once, at
import** - a later env-var change has no effect (verified separately: setting
`SYTHION_OLLAMA_API_KEY` after `import backend.core.config` leaves
`settings.ollama_api_key` empty). That last fact matters for the fix: the test cannot
be repaired by setting an environment variable at runtime; it must pin the attribute.

The consumer, `backend/api/routes.py:2250-2298` (`setup_readiness`):

```
backend/api/routes.py:2261:     key_ok = has_secret(OLLAMA_API_KEY) or bool(settings.ollama_api_key)
backend/api/routes.py:2282:         "ollama_api_key_ok": key_ok,
backend/api/routes.py:2290:     full_ready = all(checks.values())
```

The same expression appears at `backend/api/routes.py:2007` (`onboarding_status`).

### E3 - The test's own defect: nine of the ten readiness checks are pinned, the tenth is borrowed

`test_readiness_states` (`backend/tests/test_setup_readiness.py:144-177`) builds a
fully healthy world for its "healthy" case:

| readiness check | source in the test | pinned? |
|---|---|---|
| `ollama_health_ok` | fake provider, `healthy=True` (`:147`) | yes |
| `selected_model_ok` | `monkeypatch.setattr(core_config, "resolve_selected_model", lambda: selected)` (`:49`, `:148`) | yes |
| `inference_verified_ok` | a real inference posted (`:151`) -> `ollama_inference_verified_model` persisted | yes |
| `safe_settings_ok` | `/setup/safe-settings` posted (`:152`) | yes |
| `stt_model_installed` | `monkeypatch.setattr(va, "stt_model_installed", ...)` (`:154`) | yes |
| `tts_voice_ok` | `monkeypatch.setattr(va, "asset_status", ...)` (`:156`) | yes |
| `resources_ok`, `database_ok`, `sidecar_ok` | hard-coded `True` in the route (`routes.py:2279-2281`) | yes |
| **`ollama_api_key_ok`** | **nothing in the test** | **NO** |

`has_secret(OLLAMA_API_KEY)` is False in the test - the secret store is a fresh empty
`MemoryStore()` (`test_setup_readiness.py:63`, `:145`) - so `key_ok` depends entirely on
`settings.ollama_api_key`, i.e. on the developer's `backend/.env`.

The file already contains the correct pattern, and the two sibling tests use it:
`_clear_local_env_key` (`:195-204`) does
`monkeypatch.setattr(core_config.settings, "ollama_api_key", "")` and is called by
`test_a_clean_installation_without_credentials_is_not_ready` (`:215`) and
`test_the_supported_credential_flow_makes_the_key_configured` (`:237`). Only
`test_readiness_states` - the one test that asserts the POSITIVE state - omits it.

### E4 - Scope: the rest of the suite is not `.env`-sensitive

**Environment variables read directly by tests.** A grep of `backend/tests/**` for
`os.environ|os.getenv` returns only `SYTHION_RUN_PROCESS_KILL`,
`SYTHION_RUN_PROVIDER_E2E` and `TEMP` - none of which is in `backend/.env`.

**The other two `.env` keys.**
- `SYTHION_SELECTED_MODEL` -> `settings.selected_model` -> `resolve_selected_model()`.
  The readiness test pins that function (`test_setup_readiness.py:49`).
- `SYTHION_FILE_ROOTS` -> `settings.file_roots` -> the approved file roots
  (`backend/tools/file_tools.py:181`, `backend/core/settings_store.py:83`). The
  developer's `.env` carries **3** entries here (count only; the paths were not
  printed). Every test that depends on roots pins them: `test_file_roots_grants.py:30`,
  `:59`, `:105`; `test_settings_store.py:101-130`; `test_settings_api.py:60`;
  `test_phase105_concurrency.py:644`; `test_phase1075_hardening.py:268`;
  `test_tools.py:284`; `test_file_search.py:24`; and the acceptance tests patch
  `file_tools._approved_roots`.

**Full-suite runs in the `.env`-free scratch tree.**

- Scratch tree, no env var: `2 failed, 1562 passed, 45 skipped, 407713 warnings in 809.00s`.
  The two failures: `test_setup_readiness.py::test_readiness_states` (this defect) and
  `test_admission_concurrency_process.py::TestAdmissionCrossProcess::test_concurrent_admission_across_processes`
  (`assert not p1.is_alive() and not p2.is_alive(), "child hung"` at
  `test_admission_concurrency_process.py:102`).
- The admission failure is **not** `.env`-sensitive, on four independent grounds:
  1. the test imports `settings` at `test_admission_concurrency_process.py:14` and
     **never uses it** - no credential, no provider, no model;
  2. it fails identically in the scratch tree **with** a dummy
     `SYTHION_OLLAMA_API_KEY` present;
  3. it passes 3/3 in the scratch tree and 3/3 in the main tree when it is not
     competing with a second pytest process for the machine - it is a load-induced
     multiprocessing-barrier flake (30 s `join`), unrelated to the credential;
  4. in a third full-suite run of the scratch tree **with** a dummy key, its only
     failure before the run died was the admission test again - the `F` at 13%
     maps to collection index 215, `backend/tests/test_admission_concurrency_process.py::TestAdmissionCrossProcess::test_concurrent_admission_across_processes`
     (position computed from the 72-character progress rows and the collected
     order) - while **the readiness test passed**.
- The baseline the orchestrator and the baseline agent measured is
  `1564 passed, 45 skipped, 1609 collected`. The `.env`-free scratch tree gives
  `1562 passed + 2 failed = 1564`, i.e. **exactly one test differs because of the
  missing `.env`**, and the second failure is a flake.

**The third run then died with the known native fault.** Exit code `-1073741819`
(`0xC0000005`, access violation):

```
Current thread 0x0000d4b0 [Thread-137 (validate_in_thread)] (most recent call first):
  File "...\sqlalchemy\orm\query.py", line 2823 in first
  File "...\envprobe\backend\agents\delegation.py", line 1358 in get_delegation
  File "...\envprobe\backend\agents\delegation.py", line 1374 in _require
  File "...\envprobe\backend\agents\delegation.py", line 394 in validate
  File "...\envprobe\backend\tests\test_phase95_hardening.py", line 68 in validate_in_thread
```

That is the same fault **class** the baseline agent characterised as "Fault B" under
R-007 - a native sqlite3 access violation under concurrency - in a third test file
(their observation was `test_phase105_concurrency.py::TestBudgetAtomicity::test_concurrent_budget_does_not_exceed`
-> `backend/agents/coordination.py:1127 atomic_commit_spent`). It corroborates R-007
and widens its blast radius; it is **not** a credential issue, and it is why the third
run could not produce a complete summary line.

### E5 - The fix shape works

In the `.env`-free scratch tree, pinning the credential on the already-imported
settings singleton (no file edited - `probe_fixshape.py`):

```
=== SCRATCH (no .env, no env var) + settings.ollama_api_key pinned -> fix shape ===
8 passed, 600 warnings in 4.62s
```

### E6 - Test-isolation defect or product defect? The evidence says test-isolation

The readiness chain is layered, and `ollama_api_key_ok` is only a **presence** check:

- `inference_verified_ok` requires the persisted `ollama_inference_verified_model` to
  equal the selected model (`routes.py:2271-2277`), and that setting is written
  **only after a successful, non-empty inference** (`routes.py:2221-2226`).
- `ollama_health_ok` requires the provider's own health.
- The dashboard is stricter than the backend: `canClaimFullReady`
  (`frontend/src/lib/setupReadiness.ts:27-48`) requires all ten backend checks **plus**
  two voice-verification flags.

So `full_ready` cannot be reached from an unverified credential; the `.env` only
satisfies one of ten presence/health/verification checks. And the no-credential
behaviour - not ready, `ollama_api_key_ok: false` - is the **correct** first-run
answer, asserted on purpose by `test_setup_readiness.py:207-227`. A packaged install
never sees this input at all: the release payload policy forbids a `.env` in the
payload (`scripts/verify_release_payload.py:53-59`, `PROHIBITED_ENV_FILES` /
`ALLOWED_ENV_FILES`).

The one product-side observation that survives is a **stated-contract violation**,
which is the proximate cause of the sensitivity: `backend/api/routes.py:2261` and
`:2007` read `settings.ollama_api_key` **directly** rather than through
`backend.core.secrets.resolve_ollama_api_key(...)`, which
`backend/core/secrets.py:174-175` ("This is the single resolution point for the key;
callers must not read Settings.ollama_api_key directly") and
`docs/ARCHITECTURE.md:357` ("only resolvable at the one sanctioned point
(`resolve_ollama_api_key`)") both name as the one sanctioned resolution point.
Behaviourally the two are equivalent - `has_secret(X) or bool(env)` is exactly
`bool(resolve_ollama_api_key(env))` - so this is an access/contract defect, not a
behavioural one.

## Findings

1. **The mechanism is the pydantic-settings dotenv source, not a secret-store lookup.**
   `backend/core/config.py:173-176` points `Settings` at `<repo>/backend/.env` with
   `env_prefix="SYTHION_"`, and `:179` builds the singleton at import. The env var
   `SYTHION_OLLAMA_API_KEY` overrides the file; either alone satisfies the check; the
   file is read once, at import.

2. **The failing test is `test_setup_readiness.py::test_readiness_states`, and it is
   the only test in the suite whose outcome depends on a developer's `backend/.env`.**
   Its two sibling tests already call the isolation helper `_clear_local_env_key`;
   it alone asserts a positive readiness state without pinning the credential.

3. **It is a TEST-ISOLATION defect.** The product cannot report readiness from an
   unverified credential: the readiness chain requires a persisted, successful
   inference, provider health, a selected model, and (in the dashboard) two further
   voice flags. The no-credential behaviour is the correct first-run answer and is
   asserted on purpose elsewhere in the same file. A packaged install never has a
   `.env` to begin with.

4. **A narrow, separately-fixable product-code observation (contract, not behaviour):**
   `backend/api/routes.py:2261` and `:2007` bypass `secrets.resolve_ollama_api_key`,
   the point `backend/core/secrets.py:174-175` and `docs/ARCHITECTURE.md:357` name as
   the only sanctioned resolution point. Behaviour-preserving to fix, and it puts the
   dependency in one place.

5. **Adjacent finding, NOT this candidate - for the next research cycle.** While
   sweeping for other ambient-state dependencies I found that
   `backend/tests/acceptance/test_secret_storage.py::test_credential_manager_round_trip`
   writes to and then **deletes the developer's real Windows Credential Manager
   entry**: `store = secrets_module.CredentialManagerStore()` (`:139`),
   `store.set(SECRET_NAME, "sk-roundtrip-zz")` (`:140`, target
   `Sythion (Synth)/ollama_api_key`, `backend/core/secrets.py:32-34`),
   `store.delete(SECRET_NAME)` (`:146`). It does not request the
   `memory_secret_store` fixture, and `win32cred` **is** importable on this machine
   (`importlib.util.find_spec('win32cred') is not None` -> True), so
   `_default_store()` (`backend/core/secrets.py:112-121`) returns the real vault
   whenever the store has been reset to `None`. This contradicts
   `docs/ARCHITECTURE.md:358` ("A non-persistent in-memory backend is used for tests
   so the real vault is never touched"). **I did not execute that test against the
   real vault deliberately, and I am not making it a candidate here - this pass was
   scoped to one candidate.** It is a distinct defect from R-019 (different ambient
   source: the OS credential vault, not a gitignored file) and should be researched
   on its own.

6. **The CI consequence is concrete and immediate.** Item 001 repaired the workflow
   so that a push to `master` actually starts the pipeline. The `backend-tests` job
   does a clean checkout, so it has no `backend/.env` - and `test_setup_readiness.py`
   will fail there for exactly the reason reproduced above. The pipeline goes red on
   its first real run for a reason that has nothing to do with the pipeline.

7. **R-007 is corroborated again, in a third test file.** The third scratch-tree run
   died with `Windows fatal exception: access violation` (`0xC0000005`) in native
   sqlite3, reached from `backend/tests/test_phase95_hardening.py:68 validate_in_thread`
   -> `backend/agents/delegation.py:394 validate` -> `:1374 _require` -> `:1358
   get_delegation`. The baseline agent's "Fault B" was the same class of native
   sqlite3 access violation under concurrency, in
   `test_phase105_concurrency.py::TestBudgetAtomicity::test_concurrent_budget_does_not_exceed`.
   Two observers, three test files, one fault class - R-007's blast radius is wider
   than the single test its own report named. I am not filing a candidate for it
   (this pass was scoped to one candidate); it strengthens the existing R-007.

## Not checked

- **I did not execute the failing test against the real Windows Credential Manager.**
  I read `backend/tests/acceptance/test_secret_storage.py` and checked that
  `win32cred` is importable, but I did not call `CredentialManagerStore.get()` or
  `has_secret()` outside the `MemoryStore`, so I never read the developer's stored
  credential. Finding 5's *write/delete* path is established from the source, not
  executed in isolation. The full-suite runs I did perform (in the scratch tree and,
  in cycle 1, in the main tree) did execute that test as part of the suite - I am
  flagging that rather than hiding it.
- **I did not run the GitHub Actions workflow, and I did not create a git worktree.**
  The "fresh checkout" condition was emulated with a file copy outside the repository
  rather than with `git worktree add`, so the working tree and the git index were
  never touched. The consequence: I cannot say that a real `actions/checkout` of
  `master` reproduces the failure byte for byte - only that a checkout without
  `backend/.env` does, which is the same condition.
- **I did not verify the outcome on Python 3.11** (CI's pinned version). Every number
  here is from CPython 3.14.7 on Windows.
- **I did not re-run the full suite in the main tree in this pass.** The
  `1564 passed / 45 skipped / 1609 collected` figure I compare against is the
  orchestrator's and the baseline agent's, not a fresh sample from this pass; my own
  fresh samples are the scratch-tree runs (two completed, one died with the native
  access violation described in E4).
- **I did not check whether the GitHub-side environment carries
  `SYTHION_OLLAMA_API_KEY`.** The `provider-e2e` job injects
  `secrets.OLLAMA_API_KEY` into that one job's environment
  (`.github/workflows/ci.yml:65-70`), so that job's runner would have the credential
  and would pass - but the `backend-tests` job has no such injection, which is the
  job that runs the failing test. I could not read the GitHub repository settings
  from inside the checkout.
- **I did not audit the whole suite for every kind of ambient-state dependence.**
  I swept for `os.environ`/`os.getenv` in tests, for the three `.env` keys, and for
  `settings.ollama_api_key` / `selected_model` / `file_roots` reads, and I ran the
  full suite in a `.env`-free tree. I did not sweep for every other ambient source
  (the real credential vault, the `%LOCALAPPDATA%` profile directory, `TEMP`).
- **I did not decide the fix or make any change.** The candidate carries a *direction*
  only; `backend/tests/test_setup_readiness.py`, `backend/tests/conftest.py`,
  `backend/api/routes.py` and `backend/core/config.py` are untouched by this pass.
- **I did not check whether `backend/.env` is present in the other developers' trees
  or in CI, only that it is gitignored.** `git check-ignore -v backend/.env` and
  `git ls-files -- backend/.env` establish the ignore rule and the absence from the
  index; nothing more.
