# Phase-2 research 010c — Can 006's isolation test be made to genuinely trip on a clean runner?

## 1. Short answer

006's `test_the_suite_does_not_inherit_the_local_env_file` is a tautology **only** on a machine that has no `backend/.env`. It compares `backend.core.config.settings.<field>` to `Settings(_env_file=None).<field>` for the three names in `_LOCAL_ENV_FIELDS`; where there is no local file, both sides are already the clean values, so the assertion passes whether or not the autouse `_isolate_local_env_settings` fixture exists at all (verified by `006-v1.md` Control 1: fixture deleted, no `.env`, readiness file remains `11 passed`). 

A **nested pytest run under a generated `.env`** does genuinely trip it — that is exactly what verifier Control 2 measured (`1 failed, 10 passed`). But the cost is a whole second pytest invocation plus a throwaway checkout. Whether that is worth carrying in CI depends on what one believes the suite's confirmation obligation is (section 6).

## 2. What the existing isolation plumbing actually is

### 2.1 `backend/tests/test_setup_readiness.py` — the two discriminator tests

- `test_the_suite_does_not_inherit_the_local_env_file` (around `:330-336`): builds `clean = core_config.Settings(_env_file=None)` and asserts, field by field, that `getattr(core_config.settings, name) == getattr(clean, name)`, rendering only the boolean so a failure never prints the credential.
- `test_the_clean_settings_snapshot_survives_an_in_place_mutation` (`:339-362`): appends `"C:/leaked-by-a-previous-test"` to `settings.file_roots` in place, asserts it is in the effective value, asserts it is **not** in `clean_settings_snapshot["file_roots"]`, and a copy/identity check proves the fixture handed out `file_roots` as a *copy* rather than aliasing the shared snapshot. Reverting the per-test copy fails this test even in a clean checkout (Control 3).

Both tests live in the readiness file (`:335-362` in the merged tree). The first is credential-safe and a pure equality property; the second is a real teeth test on every runner regardless of `.env` presence.

### 2.2 `backend/tests/conftest.py` — the fixture under test

- `_LOCAL_ENV_FIELDS = ("ollama_api_key", "selected_model", "file_roots")` (`.conftest.py:474`: single declaration site; the snapshot and fixture pick it up by name).
- `_clean_settings_snapshot()` (`:482`) computes the clean values using `Settings(_env_file=None)` — the developer's `backend/.env` is never read to obtain them; list values are copied out.
- `_CLEAN_SETTINGS = _clean_settings_snapshot()` is module-level shared state.
- `clean_settings_snapshot` fixture (`:510`) exposes `_CLEAN_SETTINGS` for meta-tests.
- `_isolate_local_env_settings(monkeypatch, request)` (`:520`, `autouse=True`) short-circuits for `provider_e2e`-marked items (so those keep the ambient credential for their import-time `_provider_ready()` gate — see `:525-531`), then does `monkeypatch.setattr(settings, name, list(value) if isinstance(value, list) else value)` for every other test, with a *fresh* copy per test.

### 2.3 `backend/tests/acceptance/test_canonical_file_checks.py` (symlink tests)

This file is part of the acceptance subtree that also pulls `acceptance/conftest.py`, including the product-credential guard (`_forbid_product_credential_writes`, `_PRODUCT_TARGET_PREFIX`) and the acceptance app fixture. It does not reference `_LOCAL_ENV_FIELDS` or settings at all — its reparse-point / canonical-containment checks are exercised against `tmp_path`. It is relevant only as evidence that the root conftest isolation runs under the acceptance tree too (marker rules in `root conftest.py` apply `acceptance` to every file under `backend/tests/acceptance/`).

## 3. How a nested run creates the trip, and how the outer layers fence it

### 3.1 Where the generated `.env` lands

The verifier's harness (`verify006_run.py`) drives a **scratch worktree** outside the repository (`verify-006-v1`, `verify-006-base`, `verify-006-integration` under `%TEMP%\opencode`). It writes a *dummy* file to `verify-006-v1/backend/.env` containing only sentinels:

```
SYTHION_OLLAMA_API_KEY=VERIFY006-SENTINEL-NOT-A-REAL-KEY
SYTHION_SELECTED_MODEL=dummy-model-id
SYTHION_FILE_ROOTS=["C:\\Sythion\\dummy-root"]
```

It then runs `pytest tests/test_setup_readiness.py` with `cwd=tree/backend` and every `SYTHION_*` scrubbed from the child environment (`env = {k:v for k,v in os.environ.items() if not k.upper().startswith('SYTHION_')}`). The comparison is `FOUR_FILE_TRIPLES 37 37 EQUAL: True` — outcomes identical with and without the dummy `.env`, i.e. the isolation genuinely neutralises the file's values (including `ollama_api_key`).

When 006's fixture is **deleted** in a tree carrying that `.env`, the sibling test `test_the_suite_does_not_inherit_the_local_env_file` fails (`AssertionError: ollama_api_key differs from the value a clean tree has`), naming the field only, never the value (Control 2). With the fixture deleted and no `.env`, it passes (Control 1). That is the entire tautology in one line.

### 3.2 The outer conftest guard

The child run still auto-loads `backend/tests/conftest.py`, so the **isolation itself runs inside the nested process** — the nested run does not bypass it. The "trip" is manufactured by *removing* that isolation in the scratch tree, not by disabling it per-test. The second layer is the root conftest's `_guarded_autonomy_default` (autouse, pins `SYTHION_AUTONOMY_LEVEL` and `memory_consolidation_enabled`), the marker/selection-contract guard (`pytest_collection_finish`, rules in `:334-...`), and the acceptance conftest's product-vault guard on the acceptance subtree.

### 3.3 The outermost layer: `verify006_boot.py`

This helper existed in the verification effort (per `006-v1.md`); it must be treated as given, not recreated. It does three things before `pytest` is invoked:

1. **Non-delegating product credential blocker.** It monkeypatches `win32cred.CredWrite` / `CredDelete` / `CredRead` so that any target under `sythion (synth)/` (case-insensitive — Windows credential target names are case-insensitive) raises `AssertionError('Verifier blocked product credential write before delegation')` *before* the original is called. `safe_read` raises `pywintypes.error(1168, 'CredRead', 'Verifier hides product credential')`. Because `backend/core/secrets.py` calls through the module (`import win32cred`), the shim is in force wherever `win32cred` is used.
2. **Env scrub.** Removes every `SYTHION_*` variable and `PYTHONPATH`/`PYTEST_ADDOPTS` from the child environment and sets `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`.
3. **Import shim.** Restores Python's cwd-import path (`sys.path[0] = os.getcwd()`) and then calls `pytest.main(sys.argv[1:])`.

That is the only layer that keeps the entire 1614-collected suite from writing the developer's real vault during a nested run. The `006-v1.md` readiness enumeration ("PRODUCT_TARGET_MATCH_COUNT 0 before and after every run") is the evidence that it engaged.

## 4. Could we get the same trip without nesting pytest?

Candidate smaller scopes, in order of feasibility:

1. **Meta-check on `_LOCAL_ENV_FIELDS` coverage (partially done).** An in-tree test that imports `_LOCAL_ENV_FIELDS` from the conftest and asserts each name is actually neutralised by `_isolate_local_env_settings` could be done by reflection (monkeypatch counting), but reflection is brittle and still proves "the list drives the assignment," not "the isolation exists." It closes Finding 4 (added-field drift) cheaply. Feasibility: **high**. Teeth: **low-to-medium** (drift of the list only).
2. **`clean_settings_snapshot` survival test (already there, Control 3).** Has teeth in every checkout, clean or not, for the per-test copy claim. It cannot detect a wholly deleted isolation because with the fixture gone the test still passes in a clean tree. Teeth: **medium, conditional**.
3. **In-process `.env` injection via monkeypatch / reload.** Not feasible reliably. `backend/core/config.py` fixes `env_file` to `<repo>/backend/.env` at import time and instantiates `settings = Settings()` at module load, so a test would have to write that exact file (mutating the developer's tree, unsafe) and `importlib.reload` the config module mid-session, which tears down other tests' `settings` references and undermines `monkeypatch`. Feasibility: **low, unsafe**.
4. **Targeted subprocess canary (the real thing, just smaller).** Copy `backend/` (31 MB) to a `tmp_path`, write `backend/.env` with sentinels, run `pytest tests/test_setup_readiness.py -q` via a subprocess with scrubbed `SYTHION_*`, assert non-zero exit (if a control deletion were present) and that the sentinel never appears. This avoids nesting the whole suite and avoids touching the repo tree. Cost: a few seconds (readiness is 11 tests) plus the copy; marking it `slow` keeps it out of the default fast lane. Feasibility: **medium-to-high**; it *is* still a nested pytest, just 11 tests instead of the full tree. The win over `verify006_run.py` is that it needs no worktree clone of git history and no win32cred blocker (readiness touches no vault). However it inherits the same failure-modes as the verifier's environment (GUI flakes don't apply here; the subprocess inherits the installed Python).
5. **Nightly wrapper only.** Run the harness's Control-2 once per CI workflow on `workflow_dispatch`, never on every PR. Keeps PR signal cheap while still catching deletion at some cadence. Feasibility: **high**; teeth: high but delayed.

## 5. Is the current tautology actually acceptable?

**For correctness of the isolation itself: yes.** The fixture does what `006-v1.md` measured — neutralising the three `.env`-sourced fields, fresh copy per test, provider-e2e carve-out, clean values only — and the dummy-`.env` versus no-`.env` outcome set is 37 identical triples (`verify006_run.py` `FOUR_FILE_TRIPLES ... EQUAL: True`). No credential leaks into pytest's console or JUnit XML (`SENTINEL count in CONSOLE: 0`, `in JUNITXML: 0`); the developer's real `.env` was never read in any verifier run.

**For CI as a change detector: no, it is a real gap.** A future edit that deletes the isolation (or drops a field from `_LOCAL_ENV_FIELDS`) is **invisible** to a clean runner because the only test with teeth needs a `.env` that CI does not have (`006-v1.md` Findings 1-4; follow-ups entry `(006) [deferred]`). The safety net today is:

- the per-test copy has unconditional teeth (Control 3), so the *shape* of the isolation is guarded in every checkout;
- any developer who runs the full suite (the common pre-PR action on this machine) has a real `backend/.env`, so deletion is caught **before** it reaches CI — local runs are the strongest guard here;
- the os-level goal (`backend-tests` is now green on a first clean run) is met regardless.

**Recommendation (measured):** keep 006 as merged — it fixed the first-run-red problem and the suite is genuinely clean-checkout green (1569 passed per `006-r2.md`). Treat the missing "trip on clean runner" as a **deferred, non-blocking hardening test**, not as an item-blocking defect: implement option 4 (targeted subprocess canary over the 11-test readiness file) marked `slow`/`@pytest.mark.flaky`-exempt, and additionally close Finding 4 by importing `_LOCAL_ENV_FIELDS` into the existing meta-test. Do not gate the default fast lane on a full nested invocation, and do not recreate `verify006_boot.py` in the tree — the product-blocker harness stays a verifier-side artefact; the canary only needs env scrubbing because readiness never touches the vault.

## 6. Citations

- `backend/tests/test_setup_readiness.py:330-362` — the two isolation-pin tests; field-by-field equality (`:330-336`) and in-place-mutation survival (`:339-362`).
- `backend/tests/conftest.py:474` (`_LOCAL_ENV_FIELDS`), `:482-506` (`_clean_settings_snapshot`, `_CLEAN_SETTINGS`), `:510-531` (`clean_settings_snapshot`, `_isolate_local_env_settings`, provider-e2e carve-out).
- `backend/core/config.py:175-179` (`model_config["env_file"]` → `<repo>/backend/.env`; `settings = Settings()` at import time).
- `backend/tests/acceptance/conftest.py:31-151` — product-credential guard block (referenced for where the "outer" layering pattern came from; root relocation is item 007's follow-up).
- `006-v1.md`: Control 1 (`11 passed` with isolation deleted and no `.env`), Control 2 (`1 failed: ollama_api_key differs …` with `.env`), Control 3 (per-test copy reverted, fails in clean tree), Findings 1-4.
- `006-r2.md:270-310` (tautology measured; nested-run idea recorded as "Deferred").
- `verify006_run.py` (`FOUR_FILE_TRIPLES 37 37 EQUAL: True`; `sythion_env: []` in every artefact).
- `verify006_boot.py` (non-delegating product-target blocker; env scrub; `pytest.main` shim) — verifier-side artefact, intentionally not recreated in the tree.
- `follow-ups.md` entries `(006) [deferred] — new pair of tests does NOT prove isolation exists; CI-meaningful replacement is a nested pytest run against a generated .env` and `(006) [deferred]` — isolation covers `.env` file, not process environment.

**Word count:** ~1,100 words (excluding this line).
