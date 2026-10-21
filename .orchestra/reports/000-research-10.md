# 010e Research — item 005: operator tooling resolves endpoint from handshake file

## Verdict
The defect reproduces statically: the operator tools hardcode `http://127.0.0.1:8000`, while the sidecar publishes an ephemeral port. On any launch where 8000 is occupied, `scripts/smoke_test.py` and the root `cli.py` talk to whatever listens there, carrying the control token with them. This is fixable without touching auth or the backend core.

## Files found / inspected
- Handshake writer/source of truth: `backend/core/storage.py:195-197` (`handshake_path()` → `<data_dir>/sidecar.json`); `backend/core/handshake.py:23,72-91` (`write_handshake`/`read_handshake`); `backend/main.py:56-59,162-175` calls `read_launch_token()` then `write_handshake(port=settings.port, token=control_token)`. Token origin: launcher token file (`token.json`) or `backend/core/config.py:182-184` (`generate_control_token`).
- Correct consumers: `src-tauri/src/sidecar.rs:406-414,549-555` reads the handshake via `data_dir()` and derives `http://127.0.0.1:{port}` + token.
- Broken operators: `scripts/smoke_test.py:21` `BASE_URL = "http://127.0.0.1:8000/api/v1"`; `cli.py:26` same; `cli.py:28-64` `_load_control_token()` gets a token but never the port. `scripts/smoke_test.py:30-54` `load_control_token()` reads token only. `backend/cli.py:17-41` duplicates `%LOCALAPPDATA%\Sythion (Synth)\sidecar.json` and uses `port` with fallback `8000` (`:39`). Vendored mirror: `src-tauri/resources/backend/cli.py`.
- Docs: `docs/OPERATIONS.md:103-135` documents smoke test and token lookup but not port resolution.

## Code flow observed
Handshake flow: launcher writes `token.json`; uvicorn reads it (`read_launch_token`), binds `settings.port`, writes `sidecar.json` with `{port, token}`. Operator tools should read that file, but currently only the Tauri shell does. `smoke_test` resolves token via env/token file yet sends requests to fixed 8000; root CLI caches token but `BASE_URL` stays 8000; backend CLI reads the file but its hardcoded LOCALAPPDATA path ignores `SYTHION_DATA_DIR`, so a packaged sidecar launched with a custom profile cannot be found.

## Token handling / auth risk
Tokens are carried as `X-Control-Token` only (`smoke_test.py:62`, root `cli.py:125`, backend CLI `_headers`). Token source precedence today: env `SYTHION_CONTROL_TOKEN`/legacy env, then `token.json`/`sidecar.json`, then legacy `~/.sythion/control_token`. Changing source handling would require coordinated edits across `backend/core/handshake.py`, `backend/core/config.py`, `backend/main.py`, and the Tauri writer/reader. Item 005 must not do that (criterion 4): resolver should extract only `port` (and keep existing token helpers untouched). This avoids introducing a new token source or altering what authenticates.

## Dependency on item 008
No hard dependency. Item 005 never imports `backend/runtime/events.py` or `DbEventLedger`; its surface is HTTP tools and path resolution. Item 008 changes sequence allocation in `backend/runtime/events.py` only. The two file sets are disjoint. Operator tools do poll `/tasks/{id}/events`, so event durability affects how reliable task tracing is, but endpoint resolution itself does not depend on ledger behaviour. Sequence 005 after 008 is release/UX ordering, not a code prerequisite.

## Minimal change shape
1. Add a resolver used by all three operators: honour explicit `--url`/`--port`; otherwise call `storage.handshake_path()`, read `sidecar.json`, and fail naming that path if missing/invalid (no fallback to 8000). Import storage read-only (do not edit it).
2. Update `scripts/smoke_test.py` and root `cli.py` to resolve base URL before first request; keep current token code and `X-Control-Token`/`Origin` behaviour.
3. Rewrite `backend/cli.py` to call `storage.handshake_path()` instead of the duplicated derivation.
4. Mirror to `src-tauri/resources/backend/cli.py`; update smoke section in `docs/OPERATIONS.md`.
5. Add `backend/tests/test_cli_endpoint_resolution.py` using a temp `SYTHION_DATA_DIR`, writing port 54321, asserting all resolvers agree on path/base URL and no network I/O.

## Recommended sizing
**M** (medium): the endpoint change is small, but cross-tool import parity, override semantics, an isolated test profile, and vendored-tool mirroring make it more than a one-file edit. Sensitive: none. Auth and schema must remain unchanged.