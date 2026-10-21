VERDICT: ACCEPT

# Implementer report 015 round 1 — operator tooling resolves endpoint from handshake file

Commit: `691adbd81b6ced0c537018beba5edd476dff766e` (branch `orchestra/015-operator-endpoint`, off `master`).

## Scope

Allowed files changed only:
- `backend/cli.py`
- `scripts/smoke_test.py`
- `cli.py`
- `src-tauri/resources/backend/cli.py` (vendor mirror)
- `docs/OPERATIONS.md`
- `backend/tests/test_cli_endpoint_resolution.py` (new)

No changes to `backend/core/storage.py`, `backend/core/handshake.py`, `backend/core/config.py`, or `backend/main.py`.

## Change

All three operator surfaces now derive the backend port from the handshake file via `storage.handshake_path()` instead of hardcoding `127.0.0.1:8000`:
- `backend/cli.py`: `_get_token_and_url()` reads `sidecar.json` from `storage.handshake_path()` and uses its `port`; no `8000` fallback if the file is missing or invalid.
- `scripts/smoke_test.py` and root `cli.py`: resolver helpers (`resolve_handshake_path`, `resolve_base_url`, `get_base_url`) honour explicit `--url`/`--port`, otherwise read the handshake. Token loading and `X-Control-Token`/`Origin` behaviour are untouched.
- Vendor mirror updated to byte-identical copy.
- `docs/OPERATIONS.md` documents handshake-based port resolution and `--url`/`--port`.

## Acceptance criteria

1. **No hardcoded endpoint in scripts/root CLI.** PASS
   - `grep -rn "127.0.0.1:8000" scripts cli.py` returns 0.
2. **Shared resolver uses handshake file.** PASS
   - New test sets `SYTHION_DATA_DIR` to a temp profile and writes port `54321`; all resolvers agree on path and base URL.
3. **No network I/O in resolver tests.** PASS
   - `backend/tests/test_cli_endpoint_resolution.py` only reads the handshake file.
4. **Auth unchanged.** PASS
   - Existing token helpers and `X-Control-Token` behaviour remain intact.
5. **New test assertions.** PASS
   - 1 test, 14 assertions; `1 passed` in `0.14s`.
6. **Classification.** PASS
   - `classify.py --repo ... master...orchestra/015-operator-endpoint` reports `tier: NORMAL`.
7. **Collection count.** PASS
   - `python -m pytest backend/tests --collect-only -q`: `1622 tests collected` (baseline 1621 + 1).

## Limits / honesty

- `backend/cli.py` and its mirror still `sys.exit(1)` on a missing/invalid handshake rather than raising, matching existing operator UX; hidden callers that want exceptions should handle the process exit.
- Smoke/root CLIs use a lazy `BASE_URL` plus `get_base_url()` because handshake files may not exist at import time; module-level `BASE_URL` resolves eagerly only when the handshake is present.
- Standalone scripts fall back to a storage-equivalent env resolution path only when `backend.core.storage` cannot be imported; the packaged sidecar path resolution still goes through `storage.handshake_path()` when available.
- Existing docs still mention port 8000 in adjacent launcher examples unrelated to this defect.

## Files changed

- `backend/cli.py`
- `scripts/smoke_test.py`
- `cli.py`
- `src-tauri/resources/backend/cli.py`
- `docs/OPERATIONS.md`
- `backend/tests/test_cli_endpoint_resolution.py` (+1)