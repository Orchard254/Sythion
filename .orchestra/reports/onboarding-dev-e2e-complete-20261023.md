# Development onboarding E2E (completion rerun) — 2026-10-23

## Verdict: PASS — text-first onboarding completed; speech remains optional/untested

Real source backend + Vite + isolated headless Chrome (CDP) executed the repaired onboarding flow. Model discovery now succeeds, Continues (safe setup) succeeds, text mode completes, the assistant/chat surface sends a read-only greeting through the backend ConversationRuntime (completed, logged), page reload stays on the assistant surface, and setup/readiness persistence remains text-first ready. No product source edits, builds, installs, normal-profile/vault mutation, microphone usage, or secret values were exposed.

## Environment and safety

- Source revision: `c304050` (`C:\Sythion\sythion`).
- Artifact root `U`: `C:\Users\orcha\AppData\Local\Temp\opencode\onboarding-e2e-complete-20261023-U`.
- Disposable application profile and database under `U\profile`; browser profile under `U\browser-profile`; legacy home was not redirected globally.
- Existing `backend/.env` testing credential was loaded privately by the launcher and exposed only through the in-memory secret backend (`SYTHION_SECRET_BACKEND=memory`); `SYTHION_SELECTED_MODEL` was forced empty. No key was printed.
- Isolated ports and CDP: backend `127.0.0.1:8000`, Vite `127.0.0.1:3000`, Chrome CDP `127.0.0.1:19323`. Anonymous readiness checked as 401 in the prior run; no auth/CORS change.
- Development mode enabled for the supported Vite origin. Autonomy set to `guarded`. Background orchestration and memory consolidation disabled; voice disabled at startup. No microphone opened, no STT download attempted.
- No screenshot was captured while the password field contained a value. All screenshots are genuine Chrome `Page.captureScreenshot` output.

## Commands used

```text
python U\launch.py
python U\cdp.py navigate http://127.0.0.1:3000 8 01-initial
python U\cdp.py js <click Find available models> 12 02-models
python U\cdp.py js <select exact beam and dispatch change> 2 04-beam-selected
python U\cdp.py js <click Test selected model> 20 05-beam-tested
python U\cdp.py js <click Continue> 8 06-talk-choice
python U\cdp.py js <select typed text; click Continue> 6 07-checklist
python U\cdp.py js <click Start conversation> 10 08-started
python U\cdp.py js <enter read-only greeting; submit> 35 10-conversation-response
python U\cdp.py reload 8 11-refresh-persistence
python U\cleanup.py
```

## Stage evidence and statuses

| Stage | Status | API/UI evidence |
|---|---|---|
| Safe startup | PASS | Backend/Vite/Chrome owned; backend health 200; ports initially free |
| Initial empty-profile setup | PASS | Welcome/step 1; no preselected model; password field empty; Continue disabled |
| Environment credential readiness | PASS | `A key is already saved`; `/setup/readiness` 200 with `ollama_api_key_ok=true` |
| Find available models | PASS | `GET /api/v1/models` 200; `beam` included in the real model list |
| Select exact model `beam` | PASS | Actual select change event; selected value `beam` |
| Test selected model | PASS | `/onboarding/models` 200; `/onboarding/inference` 200; UI `Synth answered the connection test...`; `/setup/readiness` 200 |
| Continue / safe setup | PASS | `/setup/safe-settings` 200; moved to step 2 (Choose how to talk) |
| Select typed text (no microphone) | PASS | Text radio selected; microphone not opened, no STT download |
| Checklist / Start conversation | PASS | All checklist items green; route changed to `#/assistant` |
| Read-only greeting | PASS | Backend log: ConversationRuntime handled `Hello Synth, what can you help me with?` and completed successfully |
| Reload / persistence | PASS | Reload stayed on assistant; setup did not reappear |
| Text-first readiness | PASS | `/setup/readiness` 200: `text_first_available=true`, `selected_model=beam`, `inference_verified_model=beam`, `safe_settings_ok=true` |
| Speech (STT/TTS) | NOT TESTED / UNAVAILABLE | `GET /voice/assets` 200; STT absent/unverified; no TTS voice; no download or recording performed |
| Cleanup | PASS | Owned process and descendant tree terminated; ports closed |

## Screenshot list

Under `U\screenshots\` (genuine Chrome screenshots):

1. `01-initial.png` — safe startup, initial empty profile, key readiness
2. `02-models.png` — Find available models succeeds and selector shows real models including `beam`
3. `04-beam-selected.png` — exact `beam` selected
4. `05-beam-tested.png` — selected model test succeeds
5. `06-talk-choice.png` — successful safe setup moves to talk-choice step
6. `07-checklist.png` — text selected and final checklist ready
7. `08-started.png` — transition to assistant/chat surface
8. `10-conversation-response.png` — assistant surface after greeting submission (backend-confirmed response)
9. `11-refresh-persistence.png` — reload persistence remains assistant surface

Additional diagnostic captures `09-conversation-response.png`, `09b-check.png`, and `99-check-react.png` are retained under the same directory but are not primary flow evidence.

## URLs and statuses

- Frontend chat URL: `http://127.0.0.1:3000/#/assistant`
- Backend health: `http://127.0.0.1:8000/health` — 200
- Model discovery: `GET /api/v1/models` — 200
- Model selection: `POST /api/v1/onboarding/models` — 200
- Model inference: `POST /api/v1/onboarding/inference` — 200
- Safe setup: `POST /api/v1/setup/safe-settings` — 200
- Readiness: `GET /api/v1/setup/readiness` — 200, `text_first_available=true`, `safe_settings_ok=true`
- Voice assets: `GET /api/v1/voice/assets` — 200 (STT uninstalled, TTS voice empty)
- Anonymous readiness was verified as 401 in the prior run; no adjustment was made.

## Readiness persistence after reload

```json
{"full_ready":false,"text_first_available":true,"selected_model":"beam","inference_verified_model":"beam","checks":{"resources_ok":true,"database_ok":true,"sidecar_ok":true,"ollama_api_key_ok":true,"ollama_health_ok":true,"selected_model_ok":true,"inference_verified_ok":true,"stt_model_installed":false,"tts_voice_ok":false,"safe_settings_ok":true}}
```

`full_ready` is false only because speech assets are not installed/unverified. Text-first onboarding is ready and persistent (`text_first_available` true), which is the supported path for this run. No microphone, STT installation, speaker test, or audio playback was performed.

## Cleanup

- Owned root PIDs: backend `7252`, Vite `14084`, Chrome `5240`, plus descendants.
- `remaining_owned_pids=[]`
- Ports `8000`, `3000`, `19323`: closed
- Retained artifact root: `U` (isolated profile, browser profile, screenshots, DOM/status evidence, logs). Token-bearing `profile\sidecar.json` was removed.

## Notes

No product file edits were made. The only new file outside the isolated artifact root is this report. `src-tauri/resources/backend/api/routes.py` remains in its pre-existing modified state from earlier work and was not used (the server imported `backend.main` from source). Old report `.orchestra/reports/onboarding-dev-e2e-20261023.md` was preserved, not overwritten.
