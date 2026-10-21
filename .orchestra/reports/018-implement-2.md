# Report: implement - 018 - attempt 2 (documentation-only)

- Item: 018 - "The vault-write guard must not be swallowed by the FastAPI global exception handler" (R-026)
- Role: implementer, round 2 (documentation-only correction round)
- Branch: `orchestra/018-vault-guard-surface` (base of this round: `70010b3`)
- Worktree: `C:\Users\orcha\AppData\Local\Temp\opencode\wt-018`
- Commit SHA: `33596103a1ca0b7074e89d1c1ff00c96a449a840` (`3359610`)
  - `docs(018): correct the guard's documented outcome and qualify the opt-in sentinel proof`
- Commit contents: `7 7 backend/tests/conftest.py`, `3 2 backend/tests/test_vault_guard_exception_surfaces.py`
  (10 insertions, 9 deletions - **all inside docstrings**).
- NOT pushed. `master` not touched.

## Statement: no logic changed

**No logic, assertion, contract, test behaviour, marker, sentinel mechanism or file-set
changed in this round.** Both edits are prose inside docstrings:

- `backend/tests/conftest.py` - the docstring of the autouse guard fixture
  `_forbid_product_credential_writes` (its "Known constraint" paragraph).
  `ProductCredentialWriteAttempt`, the class-level
  `sythion_vault_write_violation = True` marker, the prefix snapshot, the
  case-insensitive comparison, the fail-closed rule, `_reject`, `_target_of`,
  `guarded_write`, `guarded_delete` and the `monkeypatch.setattr` calls are
  byte-identical to `70010b3`.
- `backend/tests/test_vault_guard_exception_surfaces.py` - the module docstring's
  third proof bullet. Every import, the sentinel installer, the autouse sentinel
  fixture and all four test functions are byte-identical to `70010b3`.
- `backend/main.py` is **not touched at all** in this round (no diff line, same
  blob as in `70010b3`).

## File set (unchanged - `git diff --name-only master..HEAD`)

```
backend/main.py
backend/tests/conftest.py
backend/tests/test_vault_guard_exception_surfaces.py
```

Exactly the same three files as after round 1. Nothing under `backend/api/`,
`backend/core/`, `backend/models/`, `backend/migrations/`,
`backend/tests/acceptance/`, `src-tauri/`, `frontend/` or `docs/` is in the diff.
The vendored mirror `src-tauri/resources/backend/main.py` was deliberately NOT
updated (F3 follow-up, per the brief).

## The two changes (F1, F2 - exact text from `.orchestra/reviews/018-r1.md`)

### F1 - `backend/tests/conftest.py`, the "Known constraint" paragraph

The old text claimed a 409 response AND a propagating exception, which never
co-occur in either client mode. The first sentence (about
`@app.exception_handler(Exception)`) is kept intact; the last two sentences are
replaced by the review's exact text (re-wrapped to the file's 79-column style,
words unchanged).

### F2 - `backend/tests/test_vault_guard_exception_surfaces.py`, module docstring

The sentinel bullet is now qualified as an opt-in proof, so the docstring no
longer presents the sentinel as an always-on guarantee. Nothing else in the
file changed.

## The exact diff of this commit

```diff
diff --git a/backend/tests/conftest.py b/backend/tests/conftest.py
index 482aded..a0f198a 100644
--- a/backend/tests/conftest.py
+++ b/backend/tests/conftest.py
@@ -689,13 +689,13 @@ def _forbid_product_credential_writes(request, monkeypatch):
       is REJECTED, never delegated to the real vault.
 
     Known constraint: ``backend/main.py`` installs a global
-    ``@app.exception_handler(Exception)``. A test that drives a vault write
-    through ``backend.main.app`` gets a 409 ``VAULT_WRITE_BLOCKED`` response
-    for it, because this exception declares the vault-write-violation marker
-    (``sythion_vault_write_violation``) that the handler looks for - the
-    blocked write is documented instead of looking like an unrelated crash.
-    The exception itself still propagates out of the request, so such a test
-    still goes red instead of passing silently.
+    ``@app.exception_handler(Exception)``. A test that drives a **blocked**
+    vault write through ``backend/main.py.app`` sees one of two outcomes,
+    depending on the client: a default ``TestClient`` raises this exception
+    out of the request, so the test goes red; a client built with
+    ``raise_server_exceptions=False`` - or a real HTTP client - receives the
+    409 ``VAULT_WRITE_BLOCKED`` response and the exception does **not**
+    propagate, so such a test must assert on the status to stay meaningful.
     """
     try:
         import win32cred
diff --git a/backend/tests/test_vault_guard_exception_surfaces.py b/backend/tests/test_vault_guard_exception_surfaces.py
index 2f2e658..11bceb7 100644
--- a/backend/tests/test_vault_guard_exception_surfaces.py
+++ b/backend/tests/test_vault_guard_exception_surfaces.py
@@ -24,8 +24,9 @@ asserted, so the test cannot pass vacuously:
   blocked);
 * a read-only fingerprint of the REAL product credential (presence plus a
   SHA-256 of the blob, never the blob itself) is identical before and after;
-* the sentinel mode below makes the real Windows Credential Manager
-  completely unreachable for the run.
+* in sentinel mode (``SYTHION_VAULT_SENTINEL=1``) the real Windows
+  Credential Manager is completely unreachable for the run - that proof is
+  opt-in, because the acceptance round trip legitimately needs the real API.
 
 Sentinel mode
 -------------
```

Both hunks are inside string literals (docstrings). No statement, expression,
decorator, fixture, import or assertion appears in the diff.

## Test output (pasted)

### 1. The new test file + item 014's regression test, sentinel ARMED

```
$ cd C:\Users\orcha\AppData\Local\Temp\opencode\wt-018
$ $env:SYTHION_VAULT_SENTINEL='1'
$ python -m pytest backend/tests/test_vault_guard_exception_surfaces.py backend/tests/test_vault_guard_root_scope.py -v -s -p no:warnings
============================= test session starts =============================
platform win32 -- Python 3.14.7, pytest-8.4.2, pluggy-1.6.0 -- C:\Users\orcha\AppData\Local\Programs\Python\Python314\python.exe
cachedir: .pytest_cache
rootdir: C:\Users\orcha\AppData\Local\Temp\opencode\wt-018
configfile: pytest.ini
plugins: anyio-4.15.1, asyncio-0.23.8
asyncio: mode=Mode.STRICT
collecting ... collected 5 items

backend/tests/test_vault_guard_exception_surfaces.py::test_product_vault_write_through_http_surfaces_as_blocked_not_generic_500 PASSED[vault-sentinel] armed=True reached=0 hits=[]

backend/tests/test_vault_guard_exception_surfaces.py::test_genuine_fault_on_the_same_route_is_still_a_generic_500 PASSED[vault-sentinel] armed=True reached=0 hits=[]

backend/tests/test_vault_guard_exception_surfaces.py::test_vault_write_violation_still_raises_out_of_a_default_test_client PASSED[vault-sentinel] armed=True reached=0 hits=[]

backend/tests/test_vault_guard_exception_surfaces.py::test_violation_marker_contract_is_shared_by_handler_and_guard PASSED[vault-sentinel] armed=True reached=0 hits=[]

backend/tests/test_vault_guard_root_scope.py::test_vault_guard_blocks_product_write_at_root_scope PASSED

============================== 5 passed in 0.98s ==============================
```

`[vault-sentinel] armed=True reached=0 hits=[]` after every test: the real
Windows Credential Manager was **not** reached.

### 2. `backend/tests/acceptance/test_secret_storage.py` (real vault - WITHOUT the sentinel)

```
$ python -m pytest backend/tests/acceptance/test_secret_storage.py -q -p no:warnings
SYTHION_VAULT_SENTINEL present: False
..........                                                               [100%]
10 passed in 0.66s
```

The env var was removed before this run (verified: `SYTHION_VAULT_SENTINEL
present: False`), because the acceptance round trip legitimately needs the real
Windows API. 10 passed, as required by the brief.

### 3. Scope check after the commit

```
$ git diff --name-only master..HEAD
backend/main.py
backend/tests/conftest.py
backend/tests/test_vault_guard_exception_surfaces.py

$ git log --oneline -3
3359610 docs(018): correct the guard's documented outcome and qualify the opt-in sentinel proof
70010b3 fix(018): surface a blocked product-vault write as 409 VAULT_WRITE_BLOCKED
0e14168 chore(orchestra): record research cycle 4, scout cycle 3, and item 017 merge

$ git status --short
(clean)
```

## Safety statement

- The real Windows Credential Manager was never written to or deleted from by
  this round's work. The new test file was only run with the sentinel ARMED
  (reached 0 times). The only test that touches the real vault is the
  PRE-EXISTING `backend/tests/acceptance/test_secret_storage.py`, which writes
  and then deletes the TEST-SCOPED target `Sythion (Synth) [TEST]/ollama_api_key`
  and was already part of the suite before this change.
- No push, no deploy, no destructive command. `master` was not touched.

## Recorded drift / not done (unchanged from round 1)

- **Vendored release mirror still not updated** (F3, LOW): `src-tauri/resources/backend/main.py`
  remains 33 lines behind `backend/main.py`. The brief records this as a
  follow-up for the next payload refresh; it was deliberately not touched in
  this round.
- **F4 (residual real-vault risk in a normal, non-sentinel run)** - accepted
  precedent, unchanged; no code change permitted in this round.
- **F5, F6, F7, F8** - INFO/NIT items from the review, no action required.

## Not checked in this round

- The full suite was **not** re-run. The change is two docstring paragraphs; the
  three mandated runs (the new file with the sentinel, item 014's regression
  test, and the acceptance file without the sentinel) all pass, and no code
  path was touched. The reviewer's own full-suite measurement
  (`1582 passed, 45 skipped, 1 deselected`) still stands for the branch.
- The new test file was **not** run without the sentinel (per the brief); the
  normal-run path is unchanged from round 1.
- The **memory (CogniGraph) server was not connected** in this session: no
  `memory_*` MCP tools were exposed and no `memory_cli.py` was found, so the
  AGENTS.md retrieval protocol could not be followed. This round needed no
  code exploration - the brief and the review supplied the exact replacement
  text - so no memory-backed decision was taken. The store itself is present at
  `C:\Sythion\.cognigraph\memory.db`.
