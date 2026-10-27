# Development onboarding E2E — 2026-10-23

## Verdict: FAIL — two reproduced product blockers

Real source backend + Vite + real headless Chrome UI testing was performed, not a demo or API-only substitute. Exact `beam` selection and live inference **passed**. The visible flow cannot finish: model discovery fails on the saved/environment-key path, and Continue fails even after successful inference. No product source edits, builds, commits, dependency installs, downloads, microphone recording, or normal-profile/vault mutations were performed by this investigation.

## Environment and safety

- Source revision: `7a9c2f5ea5cefa67edde14465890145ce9b74b47` in `C:\Sythion\sythion`.
- Pre-existing dirty state, unchanged at cleanup: modified `.orchestra/reports/000-research-7.md`, modified `src-tauri/resources/backend/api/routes.py`; untracked `.orchestra/briefs/000-research-7.md`, `.orchestra/briefs/onboarding-dev-e2e-20261023.md`, `.orchestra/reports/beam-diagnostic-adjudication.md`. The staged-resource modification was not used: the server imports `backend.main` from source.
- Artifact root `T` below means `C:\Users\orcha\AppData\Local\Temp\opencode\onboarding-dev-e2e-20261023-sol`.
- Disposable application profile: `T\profile`; database: `T\profile\sythion.db`; logs: `T\profile\logs`; legacy home redirected to `T\home`; browser profile: `T\browser-profile`.
- Before starting, confirmed ports 8000, 3000, 19323 free and printed the isolated paths. Canonical profile override explicitly set; inherited canonical/legacy Sythion overrides scrubbed. A marker in the new profile and redirected home prevent legacy-data import.
- `SYTHION_SECRET_BACKEND=memory`. Existing `backend/.env` testing credential loaded privately in the launcher process and supplied in child environment, never argv/report/output. `SYTHION_SELECTED_MODEL` explicitly empty, so selection must come from onboarding, not the existing environment. Provider fixed to official `https://ollama.com`.
- Retained per-launch authentication and `SYTHION_DEVELOPMENT_MODE=true`, using supported origin `http://127.0.0.1:3000`. Anonymous GET to backend `/api/v1/setup/readiness` independently returned **401**. No CORS/auth modification.
- Background orchestration and memory consolidation disabled for this run. Voice disabled at startup; no microphone or speaker test invoked.
- No key was printed. The one temporary populated password field was never photographed: the screenshot helper refuses capture until that field is empty. Credential submission succeeded and the UI cleared it before capture.
- Existing real profile/vault were not enumerated or read to establish a before/after snapshot. The non-mutation claim is based on the enforced isolated paths, memory secret backend and scoped actions, not an asserted vault comparison. A private equality scan found no testing credential bytes in the isolated main DB or application logs.

## Tooling and actual commands

Desktop browser tool discovery succeeded, but `browser.tabs.open({url:'about:blank'})` returned exactly:

> [browser.disconnected] No desktop browser is connected to this session. Open this session in the desktop app and wait for it to connect. Then call browser.tabs.list({}). Repeating browser actions while disconnected will not help.

Did not ask the user to connect it or repeatedly retry. Existing Python Playwright/Selenium were absent; existing Chrome, Python `websocket`, `psutil`, `dotenv`, uvicorn and frontend Vite dependencies were present. Used Chrome DevTools Protocol over local WebSocket, with an isolated **headless** browser. This proves real DOM rendering/actions/network responses, not desktop-window, Tauri, audio or physical-device behavior. All screenshots are genuine Chrome `Page.captureScreenshot` output.

Commands executed (all foreground orchestration commands completed):

```text
python T\launch.py
python T\cdp.py navigate unused 8 01-initial
python T\cdp.py js <click Find available models> 12 02-models
python T\cdp.py connect-env unused 12 03-connected
python T\cdp.py js <select exact beam and dispatch change> 1 04-beam-selected
python T\cdp.py js <click Test selected model> 30 05-beam-tested
python T\cdp.py js <click Continue> 5 06-talk-choice
python T\cdp.py reload unused 5 07-refresh-persistence
python T\cdp.py js <read-only readiness and voice assets using existing frontend API client> 1
python T\cleanup.py
```

`launch.py` contains the exact non-secret startup configuration and commands. It starts source uvicorn on `127.0.0.1:8000`, the existing `frontend/node_modules/vite/bin/vite.js --host 127.0.0.1 --port 3000 --strictPort`, and installed Chrome with `--headless=new`, unique `--user-data-dir`, `--remote-debugging-address=127.0.0.1 --remote-debugging-port=19323`. It passes the generated matching backend/Vite control token only through process environment. A test-only logging filter removes the backend's startup token-prefix log. No production bundle or Rust/Tauri build was run.

The CDP helper drives real input change/click handlers; it does not intercept HTTP responses, replace backend implementations, alter React state or bypass readiness. Network evidence stores only URL paths/statuses, never headers, query tokens or bodies. `connect-env` reads the existing credential into its own memory, fills the actual form, clicks its existing button, then drops its local value. No secret is embedded in the script.

## Stage-by-stage evidence

All screenshot filenames below resolve under `T\`. Sanitized DOM text and captured API statuses are also in `T\ui-evidence.jsonl`. Filename `06-talk-choice.png` records a **failure to reach** talk choice, not a successful step 2.

| Stage | Status | Actual result / evidence | Screenshot |
|---|---|---|---|
| Safe startup and authentication | PASS | Owned source backend/Vite/Chrome started; authenticated health/setup reads 200, anonymous readiness 401 | `01-initial.png` |
| Initial empty-profile setup | PASS | Welcome/step 1, empty password field, no preselected model, Continue disabled | `01-initial.png` |
| Environment credential readiness | PASS | “A key is already saved”; key readiness true without entering it initially. Wording means configured, not proof of persistent vault storage | `01-initial.png` |
| Find available models | FAIL | Click returned `GET /api/v1/models` **503**; UI: `Failed to fetch models: 'list' object can't be awaited`; no selector appeared | `02-models.png` |
| Alternate existing Connect and save key path | PASS | Same testing credential submitted only to memory store; `/onboarding/credentials/test` **200**; 23 real cloud model IDs appeared; field cleared | `03-connected.png` |
| Select exact model `beam` | PASS | Actual select change; Test selected model enabled | `04-beam-selected.png` |
| Test selected model | PASS | `/onboarding/models` **200**, `/onboarding/inference` **200**, `/setup/readiness` **200**; “Synth answered the connection test. You can continue.” | `05-beam-tested.png` |
| Continue / safe setup | FAIL | `PUT /api/v1/settings` **400**; UI: `Unknown or non-configurable setting: autonomy_level`. Remained on step 1; safe-settings request never reached | `06-talk-choice.png` |
| Speech choice / consent / install UI | BLOCKED | Step 2 cannot be reached through real flow; source inspection only is not UI execution | none |
| Speech asset availability | PASS (status query only) | Read-only `/voice/assets` **200**: STT absent/unverified, TTS voice empty; downloads-on-startup/import false. This is not speech functionality verification | none |
| Speech download / recognition / microphone / playback | NOT TESTED | No 41 MB model download, dependency install or ambient recording; no fabricated consent or audio success | none |
| Finish setup / Companion / Control Center transition | BLOCKED | Step 1 Continue prevents entry; readiness safe-settings false | none |
| First minimal read-only conversation message | BLOCKED | No conversation handoff; no substitute direct-chat call made | none |
| Browser refresh persistence | PASS (partial) | Refresh preserved `beam`, “Your connection is ready”, enabled Continue; still on setup. DB contains inference evidence for `beam` | `07-refresh-persistence.png` |
| Cleanup | PASS | Owned roots and descendants stopped; all three ports closed; token-bearing test handshake removed | process output |

One explicit tiny inference test was initiated (`Reply with OK`, max_tokens 16). The real runtime also performs its native-tool health probe automatically; that probe is cached per model. No repeated model tests, direct diagnostic chat, task message or blind chat retry was initiated. Provider wire requests were not separately instrumented, so an exact wire-level chat count is not claimed. Tests finished; no pending live calls remain.

Read-only readiness after refresh:

```json
{"full_ready":false,"text_first_available":true,"selected_model":"beam","inference_verified_model":"beam","checks":{"resources_ok":true,"database_ok":true,"sidecar_ok":true,"ollama_api_key_ok":true,"ollama_health_ok":true,"selected_model_ok":true,"inference_verified_ok":true,"stt_model_installed":false,"tts_voice_ok":false,"safe_settings_ok":false}}
```

SQLite `settings_metadata` contained only `ollama_inference_verified_model=beam`; neither safe-settings marker nor retention update was committed. No claim that browser refresh proves backend-restart credential persistence: the secret store is deliberately nonpersistent, and no backend restart test was performed.

## Findings and bounded fix proposals — NOT implemented

### 1. Saved/environment-key discovery always fails on sync facade

- Reproduction: start isolated empty profile with valid env testing key → Find available models.
- Expected: authenticated model list, including exact `beam`, appears for selection.
- Actual: HTTP 503 and Python await error shown in screenshot `02-models.png`.
- Root cause: `backend/api/routes.py:1986-1997`, specifically line 1993, awaits `provider.list_models()`. The provider-neutral method is synchronous: `backend/adapters/ollama_adapter.py:414-427` returns a concrete list, matching `backend/runtime/model_provider.py`'s sync contract. Other onboarding routes correctly call the facade without awaiting (`routes.py:2032,2136,2255`). This is an application contract mismatch, not provider/network unavailability.
- Proposed fix: call the synchronous provider contract correctly, preferably offloaded from the async route using the framework's threadpool helper; preserve error mapping and provider-neutral boundary. Do not change auth or the provider interface merely to mask this route bug.
- Regression acceptance: synchronous fake provider returning actual list makes authenticated GET `/models` return 200 and correct payload; provider exception still maps to documented error; unauthenticated requests still 401; real UI Find available models with preconfigured key reaches selector without resubmitting a secret.

### 2. Continue sends forbidden autonomy setting and blocks all remaining onboarding

- Reproduction: complete valid key/model/inference steps → Continue.
- Expected: safe initial settings are applied and step 2 “Choose how to talk” appears.
- Actual: HTTP 400 and `Unknown or non-configurable setting: autonomy_level`; step 1 remains visible (`06-talk-choice.png`).
- Root cause: `frontend/src/screens/Setup.tsx:147-151` sends `{autonomy_level:'guarded',retention_days:90}` to settings before applying the safe marker or advancing. `backend/core/settings_store.py:25-36` deliberately excludes autonomy from public allowed keys; lines 109-113 reject it. Route `backend/api/routes.py:1975-1978` maps validation to 400. `Setup.tsx:49` surfaces the error and prevents the rest of the callback.
- Proposed fix: reconcile the onboarding safety operation with backend authority rather than broadly allowing arbitrary autonomy changes. A bounded design is a dedicated server-side safe-setup operation that establishes/validates guarded behavior and retention=90 and only then writes its readiness marker; have the UI call that operation. Merely adding `autonomy_level` to the generic whitelist is unsafe without verifying actual runtime enforcement; merely suppressing the error or setting the marker does not prove guarded permissions.
- Regression acceptance: real API-backed Continue advances to step 2; actual guarded behavior/90-day retention and marker are verified; failed application cannot mark readiness or advance; generic settings still reject unauthorized keys; text-only finish and first read-only conversation work without microphone consent; refresh maintains honest readiness. Follow with isolated real UI verification, not just mocked frontend service tests.

### Speech observations (not a proven new bug)

Read-only asset status reported pinned `vosk-model-small-en-us-0.15` absent and no detectable TTS voice in this test environment. `backend/core/voice_assets.py:35-40` pins a 41,205,931-byte archive. Installation is explicit (`:172+`), so none was attempted. Source `frontend/src/screens/Setup.tsx:168-173` defines the optional microphone/install controls, but these were unreachable. No conclusion about their actual download consent, successful install, physical microphone, SAPI playback or Tauri capture behavior can be drawn here.

## Cleanup and retained evidence

- Owned root PIDs: backend **14828**, Vite **55556**, Chrome **62944**. URLs were `http://127.0.0.1:8000`, `http://127.0.0.1:3000`, local CDP on 19323. None remains running.
- Cleanup verified each root's command identity, gathered only its descendants, closed Chrome via CDP, terminated remaining owned processes, waited for exit, and confirmed **remaining_owned_pids=[]** and ports **8000/3000/19323 closed**. Did not kill pre-existing Python/node/Sythion/browser processes.
- The unique disposable profile, browser profile, helper scripts, sanitized screenshots, DOM/status evidence and private local runtime logs remain under `T` for investigation. Do not distribute browser-profile/cache or private runtime artifacts; share screenshots/report/sanitized evidence only. The owned token-bearing `profile\sidecar.json` was removed. No broad recursive deletion was used.
- All writes outside the temp root in this task are this report and CogniGraph findings. No unrelated working-tree edits were reverted or overwritten.
- No test suite/build/release/packaged-app validation was run. The actionable next step is a scoped implementation/review of the two contract mismatches, followed by the same isolated real UI flow; this investigation itself did not fix them.
