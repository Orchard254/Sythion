# Report: commit - 018 - attempt 1

- Item: 018 - "The vault-write guard must not be swallowed by the FastAPI global exception handler" (R-026)
- Role: committer (VCS only - merges; no push)
- Reviewed commit: `3359610` (`33596103a1ca0b7074e89d1c1ff00c96a449a840`), branch `orchestra/018-vault-guard-surface`
- Verification: `VERDICT: FIXED` (`verifications/018-v1.md`); review r1 `IMPROVE` -> r2 `ACCEPT` (`reviews/018-r2.md`)
- **Merge commit: `25a9c029d80fad353e2dae1b892e57fbe2814fd2` (short `25a9c02`)**
  - parents: `0e14168a64632790c0bf8f765f5034cc73e5c305` (= master, the first parent) and
    `33596103a1ca0b7074e89d1c1ff00c96a449a840` (= the branch tip, the second parent)
- NOT pushed. No deploy, publish, migration, dispatch or secret rotation.

## What I did

1. Read the brief, the plan (`plans/018.md`), the verification (`verifications/018-v1.md`),
   both implement reports (`reports/018-implement-1.md`, `reports/018-implement-2.md`)
   and the round-2 review (`reviews/018-r2.md`).
2. Rebased `orchestra/018-vault-guard-surface` onto master @ `0e14168` - clean (a no-op:
   the branch base already was master's HEAD).
3. Merged the branch into master with `--no-ff` - clean (ort strategy, 0 conflicts).
4. Re-ran the full backend suite ON MASTER with the vault sentinel armed, and the real-vault
   acceptance file separately WITHOUT the sentinel.
5. Recorded the merge in `board.json` (item 018 -> `merged` + `mergeSha`), `backlog.md`
   (018 -> `merged`), and appended a committer entry to `log.md` and a committer critique
   entry to `critiques.md`.
6. Removed the item's worktrees (wt-018, wt-018-review, wt-018-review2, wt-018-verify) and
   pruned the worktree list. The branch ref was KEPT for provability.
7. Did **not** add the two `(018)` follow-up entries - the orchestrator had already recorded
   them in `follow-ups.md` (verified: two `(018)` lines present, unchanged by me).

## Evidence

### 1. Rebase (clean)

```
$ cd C:\Users\orcha\AppData\Local\Temp\opencode\wt-018
$ git rebase master
Current branch orchestra/018-vault-guard-surface is up to date.
REBASE_EXIT: 0
$ git rev-parse HEAD
33596103a1ca0b7074e89d1c1ff00c96a449a840
```

`git merge-base master HEAD` = `0e14168a64632790c0bf8f765f5034cc73e5c305` = master's HEAD
before the merge, so the branch was already on master's tip and the rebase had nothing to
replay. No conflict, no manual resolution, branch tip unchanged.

### 2. Merge (clean, `--no-ff`)

```
$ cd C:\Sythion\sythion
$ git merge --no-ff --no-edit -m "Merge branch 'orchestra/018-vault-guard-surface' (018 vault-write guard surfaces as 409 VAULT_WRITE_BLOCKED)" orchestra/018-vault-guard-surface
Merge made by the 'ort' strategy.
 backend/main.py                                    |  33 +++
 backend/tests/conftest.py                          |  19 +-
 .../tests/test_vault_guard_exception_surfaces.py   | 298 +++++++++++++++++++++
 3 files changed, 346 insertions(+), 4 deletions(-)
 create mode 100644 backend/tests/test_vault_guard_exception_surfaces.py
MERGE_EXIT: 0

$ git log -1 --format="%H%n%P%n%s"
25a9c029d80fad353e2dae1b892e57fbe2814fd2
0e14168a64632790c0bf8f765f5034cc73e5c305 33596103a1ca0b7074e89d1c1ff00c96a449a840
Merge branch 'orchestra/018-vault-guard-surface' (018 vault-write guard surfaces as 409 VAULT_WRITE_BLOCKED)
```

Exactly the three files the plan and acceptance criterion 4 permit. Nothing under
`backend/api/`, `backend/core/`, `backend/models/`, `backend/migrations/`,
`backend/tests/acceptance/`, `src-tauri/`, `frontend/` or `docs/`. `backend/main.py` has
**0 deletions**, so the generic-500 branch is byte-identical to base.

The merged `backend/` tree is **byte-identical** to the reviewed branch tree
(`git diff orchestra/018-vault-guard-surface HEAD -- backend/` -> empty), so the Verifier's
measurements carry over unchanged.

### 3. Full suite ON MASTER, sentinel ARMED - RUN 1 (green, no recovery needed)

```
$ cd C:\Sythion\sythion
$ $env:SYTHION_VAULT_SENTINEL='1'
$ python -m pytest backend/tests -q -p no:warnings --deselect backend/tests/acceptance/test_secret_storage.py::test_credential_manager_round_trip
.......................................................ss............... [  4%]
........................................................................ [  8%]
........................................................................ [ 13%]
........................................................................ [ 17%]
........................................................................ [ 22%]
........................................................................ [ 26%]
........................................................................ [ 30%]
........................................................................ [ 35%]
........................................................................ [ 39%]
..ss................s........ss........................s................ [ 44%]
........................................................................ [ 48%]
........................................................................ [ 53%]
........................................................................ [ 57%]
........................................................................ [ 61%]
........................................................................ [ 66%]
.......................s...........ss...........................        [ 70%]
......................sssssssssssssssssssssssssssssss................... [ 75%]
........................................................................ [ 79%]
........................................................................ [ 84%]
........................................................................ [ 88%]
......................sss............................................... [ 92%]
........................................................................ [ 97%]
...........................................                              [100%]
1582 passed, 45 skipped, 1 deselected in 268.70s (0:04:28)
=== PYTEST_EXIT: 0 ===
=== END 2026-10-22T00:01:11+03:00 ===
```

**Run 1 was green - no native crash, no re-run, no recovery, no merge revert.**
`backend/.env` was present in the main tree. Local interpreter: CPython 3.14.7.
The run was executed in the BACKGROUND (the run took 4 min 28 s) and polled to completion.

Counts are **identical** to the Verifier's pre-merge measurement on `3359610`
(`1582 passed, 45 skipped, 1 deselected`) - delta 0/0.

### 4. Real-vault acceptance file, SEPARATELY and WITHOUT the sentinel

```
$ cd C:\Sythion\sythion
$ Remove-Item Env:\SYTHION_VAULT_SENTINEL -ErrorAction SilentlyContinue
$ python -c "print('SYTHION_VAULT_SENTINEL present:', bool(__import__('os').environ.get('SYTHION_VAULT_SENTINEL')))"
SYTHION_VAULT_SENTINEL present: False
$ python -m pytest backend/tests/acceptance/test_secret_storage.py -q -p no:warnings
..........                                                               [100%]
10 passed in 0.75s
ACCEPTANCE_EXIT: 0
```

**10 passed**, exactly as the brief expected. This test legitimately needs the real Windows
Credential Manager API; it writes and then deletes the **TEST-SCOPED** target
`Sythion (Synth) [TEST]/ollama_api_key` and never the product target.

### 5. Sentinel-reached count: **0**

Measured directly (sentinel armed, `-s` so the fixture's print is visible):

```
$ $env:SYTHION_VAULT_SENTINEL='1'
$ python -m pytest backend/tests/test_vault_guard_exception_surfaces.py backend/tests/test_vault_guard_root_scope.py -v -s -p no:warnings
backend/tests/test_vault_guard_exception_surfaces.py::test_product_vault_write_through_http_surfaces_as_blocked_not_generic_500 PASSED[vault-sentinel] armed=True reached=0 hits=[]
backend/tests/test_vault_guard_exception_surfaces.py::test_genuine_fault_on_the_same_route_is_still_a_generic_500 PASSED[vault-sentinel] armed=True reached=0 hits=[]
backend/tests/test_vault_guard_exception_surfaces.py::test_vault_write_violation_still_raises_out_of_a_default_test_client PASSED[vault-sentinel] armed=True reached=0 hits=[]
backend/tests/test_vault_guard_exception_surfaces.py::test_violation_marker_contract_is_shared_by_handler_and_guard PASSED[vault-sentinel] armed=True reached=0 hits=[]
backend/tests/test_vault_guard_root_scope.py::test_vault_guard_blocks_product_write_at_root_scope PASSED
============================== 5 passed in 1.12s ==============================
```

The same holds for the full-suite run: the new file's autouse fixture **fails any test in
which the sentinel was reached**, and all four of its tests passed in the 1582-test run.

### 6. The real vault was not touched (read-only fingerprint, before and after)

Presence + SHA-256 of the blob, never the blob itself:

```
BEFORE (before the merge, before any run):
'Sythion (Synth)/ollama_api_key'        -> ('absent', None, 'error')
'Sythion (Synth) [TEST]/ollama_api_key' -> ('absent', None, 'error')

AFTER (after the full suite and after the acceptance round trip):
'Sythion (Synth)/ollama_api_key'        -> ('absent', None, 'error')
'Sythion (Synth) [TEST]/ollama_api_key' -> ('absent', None, 'error')
```

The product credential slot was **never written to or deleted from**.

### 7. The merged contract (present in master's tree)

```
$ git grep -n "sythion_vault_write_violation" HEAD -- backend/
HEAD:backend/main.py:54:VAULT_WRITE_VIOLATION_ATTR = "sythion_vault_write_violation"
HEAD:backend/tests/conftest.py:667:    sythion_vault_write_violation = True
HEAD:backend/tests/test_vault_guard_exception_surfaces.py:284:    assert main_module.VAULT_WRITE_VIOLATION_ATTR == "sythion_vault_write_violation"
```

The same string on both sides, no import in either direction.

### 8. Worktrees removed

```
$ git worktree remove C:\Users\orcha\AppData\Local\Temp\opencode\wt-018          -> exit 0
$ git worktree remove C:\Users\orcha\AppData\Local\Temp\opencode\wt-018-review   -> exit 0
$ git worktree remove C:\Users\orcha\AppData\Local\Temp\opencode\wt-018-review2  -> exit 0
$ git worktree remove C:\Users\orcha\AppData\Local\Temp\opencode\wt-018-verify   -> exit 0
$ git worktree prune
$ git worktree list
C:/Sythion/sythion 25a9c02 [master]
```

All four were clean (`git status --short` empty) before removal; nothing was forced.
No `wt-018-*` directory remains under the temp root (the verifier's throwaway
`wt-018-base` tree had already been removed by the verifier).

**Branch ref KEPT** for provability:

```
$ git branch -v --list "orchestra/018*"
  orchestra/018-vault-guard-surface 3359610 docs(018): correct the guard's documented outcome and qualify the opt-in sentinel proof
$ git rev-parse HEAD^2
33596103a1ca0b7074e89d1c1ff00c96a449a840
```

The branch tip **is** the merge's second parent, so the work is provably in master.

### 9. Bookkeeping

- `board.json` items."018": `status` `proposed` -> `merged`, `mergeSha` -> `25a9c029d80fad353e2dae1b892e57fbe2814fd2`,
  `branch` -> `orchestra/018-vault-guard-surface`, `owner` -> `committer-1`, `round` 0 -> 2,
  `verifyRounds` 0 -> 1, `conflicts` 0.
- `backlog.md` item 018: `- Status: proposed` -> `- Status: merged (merge sha `25a9c02`, branch `orchestra/018-vault-guard-surface`, verifier FIXED)`
- `log.md`: committer entry appended.
- `critiques.md`: committer critique entry appended.
- `follow-ups.md`: **not touched** - the two `(018)` entries are already there.

Only these four files were appended to; no history was rewritten.

## Acceptance criteria (backlog 018) - post-merge status

| # | Criterion | Result | Evidence |
|---|---|---|---|
| 1 | A regression test over `TestClient(backend.main.app)` asserts a distinct documented outcome (not a generic 500, not a silently-2xx response); fails before, passes after | MET | `backend/tests/test_vault_guard_exception_surfaces.py::test_product_vault_write_through_http_surfaces_as_blocked_not_generic_500`; Verifier reproduced 2 failed / 2 passed on base and 5 passed on the branch; both non-vacuous by mutation |
| 2 | One documented shape, `ProductCredentialWriteAttempt` recognised consistently between `backend/main.py` and `backend/tests/conftest.py` | MET | marker string identical on both sides (`git grep` - 3 hits, see 7 above); no import in either direction |
| 3 | Genuine unexpected exceptions keep the generic 500; no other response contract changes; `backend/core/secrets.py` unchanged | MET | `backend/main.py` diff has 0 deletions (the 500 branch is byte-identical); `test_genuine_fault_on_the_same_route_is_still_a_generic_500` passes; `backend/core/secrets.py` not in the diff |
| 4 | `git diff --name-only` lists only the three files; no model/migration/schema file touched; no product credential written | MET | merge stat = exactly the three files; product target fingerprint ABSENT before and after (section 6); sentinel reached 0 times |
| 5 | The full suite keeps the pre-change passed/skipped counts | MET | post-merge on master: 1582 passed / 45 skipped / 1 deselected - identical to the Verifier's pre-merge measurement (delta 0/0); acceptance file 10 passed |

## Findings

- **The merge was clean end to end.** The rebase was a no-op (the branch base was already
  master's HEAD), the merge needed no conflict resolution, and the merged `backend/` tree is
  byte-identical to the reviewed branch tree. The Verifier's evidence carries over verbatim.
- **No recovery was needed.** The full suite was green on the first attempt; the machine's
  known native-crash flakiness (COM `0x80040155` bursts / sqlite3 `0xC0000005`) did not fire.
- **The sentinel held.** Reached 0 times, and the product credential slot is verifiably
  untouched (absent before and absent after). The only real-vault operation in this session
  was the pre-existing acceptance round trip at the TEST-SCOPED target, which cleaned up after
  itself.
- **Two bookkeeping notes for the orchestrator:**
  1. `board.json` items."018"."round" was set to 2 (two implement rounds: `70010b3` and
     `3359610`) and "verifyRounds" to 1 (one verification round), matching the 017 entry's
     convention. Change them if the orchestrator counts rounds differently.
  2. `critiques.md` had no round-2 reviewer entry or verifier entry in the working tree when I
     started (the uncommitted diff held only the planner and the r1 reviewer). I appended only
     the committer entry, as the brief requires; the orchestrator may still want to record
     the r2 reviewer and the verifier verdicts.
- **Recorded drift (deliberate, already a follow-up):** the vendored release mirror
  `src-tauri/resources/backend/main.py` is still 33 lines behind `backend/main.py`. Acceptance
  criterion 4 fixed the file set, so the mirror was not touched; the follow-up is already
  recorded in `follow-ups.md` and I did not duplicate it.

## Not checked

- **CPython 3.11** (the version CI pins) - the suite was run on the local CPython 3.14.7 only.
- **The CogniGraph memory server was not connected** in this session: no `memory_*` MCP tools
  were exposed (tool-catalog search returns none) and no `memory_cli.py` exists under
  `C:\Sythion`. The AGENTS.md retrieval protocol (`memory_stats` -> `memory_query` ->
  `memory_prepare_task`) could therefore not be followed. No memory-backed decision was
  taken: every fact in this report was re-derived from the tree and from this session's own
  runs. (The store file `C:\Sythion\.cognigraph\memory.db` does exist.)
- **The new test file was never run WITHOUT the sentinel** (normal-run path). The brief did
  not ask for it and the Verifier explicitly left it unchecked; it is argued from the guard's
  code, not measured.
- **Packaging/staging was not exercised**: `scripts/stage_python_runtime.py` and
  `scripts/verify_release_payload.py` were not run, so the vendored-mirror conclusion comes
  from the Verifier's reading and blob hashes, not from a staging run.
- **No frontend/Tauri client** was exercised against the new 409 body (grep-only conclusion
  from the plan: the React frontend reads no `error`/`code` field).
- **The whole vault was not enumerated** - the "vault untouched" claim rests on the
  before/after fingerprint of the two targets in play plus the sentinel count, not on a full
  enumeration of the credential store.
- **No push, no deploy, no publish, no migration, no dispatch, no secret rotation** was
  performed, and none was simulated.
