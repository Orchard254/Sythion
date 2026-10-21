# Report: implement - 018 - attempt 1

- Item: 018 - "The vault-write guard must not be swallowed by the FastAPI global exception handler" (R-026)
- Branch: `orchestra/018-vault-guard-surface` (from `master` = `0e14168`)
- Worktree: `C:\Users\orcha\AppData\Local\Temp\opencode\wt-018`
- Commit SHA: `70010b3` (`fix(018): surface a blocked product-vault write as 409 VAULT_WRITE_BLOCKED`)
- Commit contents: `33 0 backend/main.py`, `15 4 backend/tests/conftest.py`,
  `297 0 backend/tests/test_vault_guard_exception_surfaces.py`.
- NOT pushed.
- Files changed (exactly three, `git diff --name-only master..HEAD`):

  ```
  backend/main.py
  backend/tests/conftest.py
  backend/tests/test_vault_guard_exception_surfaces.py
  ```

  No file under `backend/api/`, `backend/core/`, `backend/models/`, `backend/migrations/`,
  `src-tauri/`, `docs/` or `frontend/` is in the diff. `backend/core/secrets.py` is
  byte-identical to base (not touched at all).

## What changed

### 1. `backend/main.py` - the marker contract (production side)

Three module-level constants, added next to the other module-level configuration
(above `app = FastAPI(...)`), with a comment block that states the contract and
names its producer:

```python
VAULT_WRITE_VIOLATION_ATTR = "sythion_vault_write_violation"
VAULT_WRITE_VIOLATION_STATUS = 409
VAULT_WRITE_VIOLATION_ERROR = "VAULT_WRITE_BLOCKED"
```

`global_exception_handler` gained **one** branch, strictly ABOVE the existing
500 branch, entered only on a POSITIVE marker check:

```python
    if getattr(exc, VAULT_WRITE_VIOLATION_ATTR, False):
        return JSONResponse(
            status_code=VAULT_WRITE_VIOLATION_STATUS,
            content=ErrorResponse(
                error=VAULT_WRITE_VIOLATION_ERROR,
                detail=str(exc),
                code=str(VAULT_WRITE_VIOLATION_STATUS)
            ).model_dump()
        )
    return JSONResponse(          # <- UNCHANGED, byte for byte
        status_code=500,
        content=ErrorResponse(
            error="INTERNAL_ERROR",
            detail=str(exc),
            code="500"
        ).model_dump()
    )
```

The diff adds 33 lines and modifies **zero** lines of the pre-existing 500
branch. `ErrorResponse` was already imported; no new import was needed.

### 2. `backend/tests/conftest.py` - the guard side

- `ProductCredentialWriteAttempt` declares the marker at CLASS level, so no
  construction path can forget it:

  ```python
      sythion_vault_write_violation = True
  ```

- The docstring paragraph that said a vault write through `backend.main.app`
  "would get a 500 response instead of this exception ... the test would not go
  red" was REPLACED with the new contract (409 `VAULT_WRITE_BLOCKED` through the
  HTTP layer, and the exception still propagates so the test still goes red).

- **Nothing else in the guard was touched**: the prefix snapshot, the
  case-insensitive comparison, the fail-closed rule, the "never touches
  `secrets_module._store`" property, `_reject`, `_target_of`, `guarded_write`,
  `guarded_delete` and the `monkeypatch.setattr` calls are unchanged. The diff
  is 15 insertions / 4 deletions: 8 lines for the class attribute and its
  comment, 7 lines for the new docstring paragraph, 4 lines for the replaced
  one.

Neither side imports the other. Verified by grep over every `*.py` under
`backend/` excluding `backend/tests/`: **0 matches** for `backend.tests`,
`from .tests`, `import tests`.

### 3. `backend/tests/test_vault_guard_exception_surfaces.py` (NEW)

The four tests the plan names:

| test | what it pins |
|---|---|
| `test_product_vault_write_through_http_surfaces_as_blocked_not_generic_500` | the reproduction: the EXISTING route `PUT /api/v1/secrets/ollama_api_key` against the REAL `CredentialManagerStore` surfaces as `409 VAULT_WRITE_BLOCKED` / `code "409"`, the product target is named in `detail`, and `status != 500` and `not 2xx` are asserted explicitly. Plus the two non-vacuity proofs: a spy around the ALREADY-GUARDED `win32cred.CredWrite` records exactly one attempt, whose target is `Sythion (Synth)/ollama_api_key`; and a read-only fingerprint (presence + SHA-256 of the blob, never the blob) of the REAL product credential is identical before and after. |
| `test_genuine_fault_on_the_same_route_is_still_a_generic_500` | the 500 contract is PRESERVED, not assumed: the same route, the same client, a store whose `set()` raises `RuntimeError("simulated vault failure")` -> `500` + `INTERNAL_ERROR` + `"500"`. |
| `test_vault_write_violation_still_raises_out_of_a_default_test_client` | the guard keeps its teeth: with the default `TestClient(app)` (`raise_server_exceptions=True`) the same blocked write still raises `ProductCredentialWriteAttempt` OUT of the request. |
| `test_violation_marker_contract_is_shared_by_handler_and_guard` | the drift pin: `backend.main.VAULT_WRITE_VIOLATION_ATTR == "sythion_vault_write_violation"`, the guard declares EXACTLY that name at class level, a violation instance satisfies `getattr(exc, ATTR, False)`, and an ordinary exception does NOT (so the branch can never fire by accident). |

The file also contains the **vault sentinel** (see below) and an autouse
fixture that fails any test in which the sentinel was reached.

## Safety: the vault sentinel (MANDATORY requirement)

While the new test was developed and run, `win32cred.CredWrite` and
`win32cred.CredDelete` were replaced with **recording sentinels** BEFORE the
conftest guard was installed, so the guard's captured "original write" IS the
sentinel and the real Windows Credential Manager was unreachable.

Mechanism (in the new test file, at IMPORT time - i.e. before any fixture
runs, therefore before the conftest guard fixture captures the API):

```python
if win32cred is not None and os.environ.get("SYTHION_VAULT_SENTINEL", "").strip() == "1":
    _install_vault_sentinels()
```

The sentinel records every call and raises - it never delegates, so the real
vault cannot be reached while it is armed. It is opt-in via
`SYTHION_VAULT_SENTINEL=1` because the acceptance round trip
(`backend/tests/acceptance/test_secret_storage.py::test_credential_manager_round_trip`)
legitimately needs the real API; the sentinel-armed session therefore excludes
that ONE test (it is run separately, without the sentinel, and it is the
pre-existing test that writes and deletes the TEST-SCOPED target
`Sythion (Synth) [TEST]/ollama_api_key`).

**Sentinel-reached count: 0 (zero), in every run.** The autouse fixture in the
new file asserts it after every test and prints the count:

```
[vault-sentinel] armed=True reached=0 hits=[]
```

No write to, and no delete from, the real Windows Credential Manager was
performed by the new test. The new test never ran against the real vault.

## Test output (pasted)

### 1. The new test file + item 014's regression test, sentinel armed

```
$ cd C:\Users\orcha\AppData\Local\Temp\opencode\wt-018
$ $env:SYTHION_VAULT_SENTINEL='1'
$ python -m pytest backend/tests/test_vault_guard_exception_surfaces.py backend/tests/test_vault_guard_root_scope.py -v -s -p no:warnings
backend/tests/test_vault_guard_exception_surfaces.py::test_product_vault_write_through_http_surfaces_as_blocked_not_generic_500 PASSED[vault-sentinel] armed=True reached=0 hits=[]
backend/tests/test_vault_guard_exception_surfaces.py::test_genuine_fault_on_the_same_route_is_still_a_generic_500 PASSED[vault-sentinel] armed=True reached=0 hits=[]
backend/tests/test_vault_guard_exception_surfaces.py::test_vault_write_violation_still_raises_out_of_a_default_test_client PASSED[vault-sentinel] armed=True reached=0 hits=[]
backend/tests/test_vault_guard_exception_surfaces.py::test_violation_marker_contract_is_shared_by_handler_and_guard PASSED[vault-sentinel] armed=True reached=0 hits=[]
backend/tests/test_vault_guard_root_scope.py::test_vault_guard_blocks_product_write_at_root_scope PASSED
============================== 5 passed in 1.12s ==============================
```

### 2. `backend/tests/acceptance/test_secret_storage.py` (real vault, no sentinel - it needs it)

```
$ python -m pytest backend/tests/acceptance/test_secret_storage.py -q -p no:warnings
..........                                                               [100%]
10 passed in 0.71s
```

### 3. The verbatim response bodies (throwaway probe, deleted before the commit)

```
BLOCKED-STATUS: 409
BLOCKED-BODY: {'error': 'VAULT_WRITE_BLOCKED', 'detail': "test
backend/tests/test_zz_tmp_probe_018.py::test_probe_bodies attempted to write the product
credential target 'Sythion (Synth)/ollama_api_key'. Tests must never write to or delete
targets under 'Sythion (Synth)/' (the comparison is case-insensitive, because Windows
credential target names are); use a test-scoped prefix instead.", 'code': '409'}
GENUINE-STATUS: 500
GENUINE-BODY: {'error': 'INTERNAL_ERROR', 'detail': 'simulated vault failure', 'code': '500'}
SENTINEL-HITS: []
```

### 4. Reproduction on BASE (master `0e14168`, new test file copied in, sentinel armed)

```
$ cd C:\Users\orcha\AppData\Local\Temp\opencode\wt-018-base
$ $env:SYTHION_VAULT_SENTINEL='1'
$ python -m pytest backend/tests/test_vault_guard_exception_surfaces.py -q -s -p no:warnings
F[vault-sentinel] armed=True reached=0 hits=[]
.[vault-sentinel] armed=True reached=0 hits=[]
.[vault-sentinel] armed=True reached=0 hits=[]
F[vault-sentinel] armed=True reached=0 hits=[]
>       assert response.status_code == 409, (
            f"VAULT_WRITE_BLOCKED, got {response.status_code} "
            f"{body.get('error')!r}"
E       AssertionError: expected the blocked vault write to surface as 409 VAULT_WRITE_BLOCKED, got 500
'INTERNAL_ERROR'
E       assert 500 == 409
E        +  where 500 = <Response [500 Internal Server Error]>.status_code
backend\tests\test_vault_guard_exception_surfaces.py:221: AssertionError
>       assert main_module.VAULT_WRITE_VIOLATION_ATTR == "sythion_vault_write_violation"
E       AttributeError: module 'backend.main' has no attribute 'VAULT_WRITE_VIOLATION_ATTR'
backend\tests\test_vault_guard_exception_surfaces.py:285: AttributeError
FAILED backend/tests/test_vault_guard_exception_surfaces.py::test_product_vault_write_through_http_surfaces_as_blocked_not_generic_500
FAILED backend/tests/test_vault_guard_exception_surfaces.py::test_violation_marker_contract_is_shared_by_handler_and_guard
2 failed, 2 passed in 1.28s
```

The test FAILS on base (observed status 500 / `INTERNAL_ERROR`) and PASSES on
the branch (observed 409 / `VAULT_WRITE_BLOCKED`).

### 5. Full suite, branch (sentinel armed, the one real-vault test deselected)

```
$ python -m pytest backend/tests -q -p no:warnings --deselect backend/tests/acceptance/test_secret_storage.py::test_credential_manager_round_trip
1582 passed, 45 skipped, 1 deselected in 269.12s (0:04:29)
```

### 6. Full suite, BASE (`0e14168`, unchanged tree)

```
$ python -m pytest backend/tests -q -p no:warnings
1579 passed, 45 skipped in 264.48s (0:04:24)
```

The SECOND base run (the JUnit one) had one extra failure -
`backend/tests/acceptance/test_gui_fixture.py::test_observe_finds_fixture_window`,
the known-flaky live-GUI test - which PASSED on the branch in the same window
and PASSES on base when re-run alone (`4 passed in 1.90s`). See the per-test
comparison below.

Delta: +4 collected (the four new tests, all passing), skipped unchanged at 45,
zero failures attributable to this item on either tree. The one deselected test
in run 5 is `test_credential_manager_round_trip`, which passed in run 2 (10
passed) and in the base run 6.

## Per-test comparison (JUNIT, criterion 5)

Both full-suite runs were repeated with `--junitxml` so the comparison is
PER TEST, not from totals:

- base: `base-junit.xml` (1624 cases)
- branch: `branch-junit.xml` (1627 cases)

```
$ python compare_junit.py base-junit.xml branch-junit.xml
base cases: 1624   branch cases: 1627

only in base (1):
    ('backend.tests.acceptance.test_secret_storage', 'test_credential_manager_round_trip') passed
only in branch (4):
    ('backend.tests.test_vault_guard_exception_surfaces', 'test_genuine_fault_on_the_same_route_is_still_a_generic_500') passed
    ('backend.tests.test_vault_guard_exception_surfaces', 'test_product_vault_write_through_http_surfaces_as_blocked_not_generic_500') passed
    ('backend.tests.test_vault_guard_exception_surfaces', 'test_vault_write_violation_still_raises_out_of_a_default_test_client') passed
    ('backend.tests.test_vault_guard_exception_surfaces', 'test_violation_marker_contract_is_shared_by_handler_and_guard') passed

outcome changed (1):
    ('backend.tests.acceptance.test_gui_fixture', 'test_observe_finds_fixture_window') failure -> passed

base outcomes:   {'passed': 1578, 'skipped': 45, 'failure': 1}
branch outcomes: {'passed': 1582, 'skipped': 45}
```

Reading:

- The ONLY new tests are the four in the new file, and all four PASS.
- `test_credential_manager_round_trip` is "only in base" only because it was
  DESELECTED from the branch's sentinel-armed session (it needs the real
  `win32cred`); it PASSED in the branch's separate acceptance run
  ("10 passed") and in the base run.
- The single outcome change is the known-flaky LIVE-GUI test
  `backend/tests/acceptance/test_gui_fixture.py::test_observe_finds_fixture_window`,
  which FAILED on base and PASSED on the branch. Per the plan's R8, a failing
  run is re-run before a delta is attributed to the item; re-running it alone
  on the BASE worktree gives `4 passed in 1.90s`, i.e. the failure is
  environmental flakiness (it needs a live interactive Windows desktop and the
  real UI Automation stack), not a regression. It has no reference to
  credentials, the app, or the exception handler. The earlier, non-JUNIT base
  run of the same tree had NO failure at all (1579 passed, 45 skipped), and the
  branch run in the same time window as the base failure passed it.
- No pre-existing test changed outcome otherwise; the skipped count is
  identical (45) on both trees.

## Recorded drift / not done

- **Vendored release mirror not updated (intentional, per criterion 4).**
  `src-tauri/resources/backend/main.py` is now 33 lines behind
  `backend/main.py` (the three constants + the 409 branch). The plan records
  that the mirror is a build artifact re-copied by
  `scripts/stage_python_runtime.py::stage_backend()` at staging time, that the
  staging copy excludes `tests/` entirely, that
  `scripts/verify_release_payload.py` enforces payload hygiene rather than
  source/mirror parity, and that the new branch is unreachable in a packaged
  build (the guard and the test suite are not shipped), so the drift has no
  functional effect. It belongs to the next payload refresh.
- **No documentation was touched.** The contract is documented in the two
  changed files' comments and in the new test file's module docstring. A
  grep over `docs/` found no statement of the old "indistinguishable 500"
  behaviour that would now be wrong.
- **The new branch is dead code in a packaged build** (the violation only
  exists inside the test suite, which the payload excludes). Accepted
  deliberately - it is the price of not weakening the 500 contract.

## What was NOT checked

- **The real Windows Credential Manager was never written to by the new test,
  and the new test was never run against the real vault.** The evidence for
  "the real vault was untouched" is (a) the sentinel count of 0, (b) the
  `CredWrite` spy showing exactly one blocked attempt at the product target,
  and (c) the before/after fingerprint of the real product credential being
  identical. I did not independently enumerate the whole vault to prove no
  other credential changed.
- **No live HTTP server (uvicorn) was used.** The 409 was observed through
  `TestClient`, not over a real socket; the plan's claim that a real HTTP
  client sees the same body follows from Starlette's `ServerErrorMiddleware`
  using the same handler, but it was not measured here.
- **`raise_server_exceptions=True` was observed to raise
  `ProductCredentialWriteAttempt` out of the request**, but the plan's
  statement that the *response* is still generated in that mode was not
  separately asserted (the exception is what the test asserts).
- **The `SYTHION_VAULT_SENTINEL=1` path is opt-in.** A normal (non-sentinel)
  run of the new test file exercises the same four tests against the real
  `win32cred` as the guard's captured original; in that mode the guard still
  blocks the write before delegation, but the sentinel tripwire is not armed.
  I did not run the new file in that non-sentinel mode against the real vault.
- **No frontend / Tauri client was exercised** against the new 409 body. The
  plan states that the React frontend does not read `error` / `code` /
  `INTERNAL_ERROR`; I did not re-verify that by grep or by running the app.
- **The `--deselect`ed acceptance round trip** was run in a separate session
  without the sentinel; it was not run in the same session as the
  sentinel-armed suite (it cannot be: it needs the real API).
- **The full-suite per-test JUnit comparison** covers the two runs above; the
  one failing test on the BASE tree
  (`test_gui_fixture.py::test_observe_finds_fixture_window`) was re-run alone
  on the base tree and passed, so it is recorded as environmental flakiness
  (a live interactive desktop / UI Automation test), NOT as a delta of this
  item. It was not re-run a second time on the branch beyond the two full
  branch runs, both of which passed it.
- **Packaging / staging was not exercised**: `scripts/stage_python_runtime.py`
  and `scripts/verify_release_payload.py` were not run.

## Safety statement

- The real Windows Credential Manager was never written to or deleted from by
  the new test. The sentinel stood in for `win32cred.CredWrite` /
  `win32cred.CredDelete` during every run of the new test file and was reached
  **0** times.
- The one test in this session that touches the real vault is the PRE-EXISTING
  `backend/tests/acceptance/test_secret_storage.py::test_credential_manager_round_trip`,
  which writes and then deletes the TEST-SCOPED target
  `Sythion (Synth) [TEST]/ollama_api_key` and was already part of the suite
  before this change.
- No destructive commands, no push, no deploy. `master` was not touched: the
  branch was cut from it and the base measurement was taken in a throwaway
  detached worktree.
