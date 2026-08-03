# Testing

## Backend tests (single command)

Run from the repository root **or** the `backend/` directory:

```
python -m pytest backend/tests -q        # from repository root
python -m pytest tests -q                # from backend/
```

- Unit + integration + acceptance suites live under `backend/tests/`
  (including `backend/tests/acceptance/`). Deterministic: the live Cloud model
  and OS mutations are stubbed.
- `python -m pytest tests/acceptance -q` runs only the acceptance gates.
- Expect: **349 passed, 2 skipped** (as of Phase A remainder). The 2 skipped are
  the packaged-shell-dependent desktop lifecycle gates.

## Frontend

```
npm run build      # tsc + vite build (must pass before release)
npm run lint       # eslint
```

Run from `frontend/`.

## Tauri desktop shell

```
npx --prefix frontend tauri build --no-bundle   # compile the shell (Rust + MSVC required)
```

Run from the repository root. Requires the Rust toolchain and the Windows
Build Tools (MSVC); the `tauri-build` CI job (`windows-latest`) is the executed
build gate. Runtime config is validated by `npx --prefix frontend tauri info`.

## Live smoke test (sidecar)

```
python scripts/smoke_test.py --all
```

Run from the repository root against a running sidecar
(`python scripts/launch_sidecar.py --port 8000 --dev --no-open`). Currently
6/6 live.

## CI

`.github/workflows/ci.yml` runs, on push/PR to `main`:

1. Backend tests (`python -m pytest backend/tests -q`).
2. Frontend build + lint (`npm run build`, `npm run lint`).
3. Packaging check: `python -c "import backend.main"` import gate and a
   deterministic build check (future: wheel/exe signing).
4. Tauri shell build on `windows-latest` (`npx --prefix frontend tauri build
   --no-bundle`) and an assertion that `src-tauri/target/release/jarvis.exe`
   was produced.

## Notes

- pytest runs in strict asyncio mode; async tests carry an explicit
  `@pytest.mark.asyncio` marker.
- SQLite in-memory (`StaticPool`) isolates every test database.
