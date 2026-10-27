# Bounded onboarding repair — 2026-10-23

Role: Planner only. Status: plan delivered, **not implemented or verified**.
Base inspected: `master`, `7a9c2f5ea5cefa67edde14465890145ce9b74b47`.
Inputs: `briefs/onboarding-repair-plan-20261023.md`, investigator `reports/onboarding-dev-e2e-20261023.md`, current source, tests, board, goal, retained launch/CDP/cleanup scripts. Investigator critique appended to `critiques.md` before designing this repair; ACCEPT means bounded evidence, not completed onboarding.

## Goal, authority and budget

Unblock the existing real text-first onboarding into a persistent companion conversation without weakening runtime safety. The historical runtime-only framing in goal.md is not a mandate to remove the user's companion vision, nor permission to redesign it here. No production builds, installs, downloads, capture, pushes, normal-profile or vault changes.

Exactly **two proposed repair items**, in order: **023** discovery, then **024** honest safe setup. Board contains 001–009 and 017–022 (some gaps), backlog ends at 022; neither 023 nor 024 is currently allocated. These are proposals, not board reservations: orchestrator must recheck IDs before dispatch. Do not reopen 021 or modify historical backlog statuses. This plan does not edit board/backlog.

Historical budget is already exceeded: goal.md:155–165 says 6 merged items (raised from 5), but board.json contains **12 merged item IDs**. Items 020 and 021 also record two merges each; counting IDs already proves the overrun without treating repeated merges as new items. Review/verify round limits were also exceeded historically (020 round 4; 021 round 4 / verifyRounds 3). Preserve that record; do not silently extend it. User's new authorization supports a **new bounded onboarding run, max 2 merged repair items**, separate from historical consumption. Stop at that budget. Max two review and two verify rounds per new item; further work requires an explicit new disposition, not an invisible extension.

## Evidence and smallest-safe decision

1. Investigator real UI: Find available models -> GET `/api/v1/models` 503, `'list' object can't be awaited`; exact beam selection and inference via alternate credential form -> 200; Continue -> PUT `/settings` 400, forbidden `autonomy_level`; downstream speech/finish/handoff blocked (report:53–77). These are source-backend/Vite failures, not inferred from the installed package.
2. `backend/runtime/model_provider.py:27–29` defines synchronous `list_models`; `backend/adapters/ollama_adapter.py:414–427` returns a list and can block waiting on network. `backend/api/routes.py:1993` incorrectly awaits that list. Preserve the sync provider boundary; offload the blocking call from this async endpoint.
3. Additional **source-proven, not browser-reproduced** discovery mismatch: route returns `{"models": models}` containing provider dictionaries; `frontend/src/services/api.ts:513–517` claims `Promise<string[]>` but returns those dictionaries unchanged. Setup.tsx:136,142 consumes them directly as option IDs/React children. The alternate credential endpoint already returns IDs (`routes.py:2146`), explaining why that path worked. Fixing only await risks exposing object-valued options/render failure. Pin the mismatch with a service regression, do not claim its browser symptom was previously observed.
4. `Setup.tsx:148–150` submits autonomy+retention through generic settings, then calls the **already existing** `POST /setup/safe-settings`. `settings_store.py:25–36,109–113` intentionally rejects autonomy; keep this boundary. `setupService.applySafeSettings` already exists (`api.ts:419–421`), so **no new route or request shape is needed**.
5. Existing safe-settings route (`routes.py:2285–2290`) merely writes a marker; readiness (`:2308–2313`) trusts the marker. This is a source-confirmed honesty defect: removing the forbidden UI update alone would claim unestablished safety/retention.
6. Actual authority is **not a settings_metadata row**. Default `settings.autonomy_level` is guarded (`core/config.py:22`); runtime uses `os.environ.get("SYTHION_AUTONOMY_LEVEL", settings.autonomy_level)` in model visibility/prompt (`services/model_engine.py:178,197`), approval (`services/task_execution_loop.py:695–709`) and capabilities (`tools/system_tools.py:809`). A DB `autonomy_level=guarded` cannot change those consumers. Do not mutate environment or settings singletons through onboarding. **Validate effective authority is exactly guarded; reject autonomous/unknown/empty values honestly**, preserving explicit operator configuration. The default guarded path completes; an explicitly autonomous process cannot truthfully pass a panel claiming guarded permissions. No central authority refactor is needed.
7. Retention consumer `services/retention.py:38–47` reads persisted `retention_days`, default 90. `set_setting` stages writes and caller owns commit (`settings_store.py:49–60`); `update_settings` commits internally (`:100–123`). Thus stage retention=90 and marker using internal setters in one endpoint transaction, not a committing generic update followed by another marker commit. Do not invoke purge or alter retention machinery.

## 023 — Discovery contract, from provider to selector

- Tier: **sensitive** (classifier SENSITIVE minimum); areas: **api, security**. Local reversible public endpoint/client behavior; no external configuration change.
- Dependency: first; normal discovery must work without re-entering a secret before final E2E. Both items touch routes.py; implement serially, branch 024 from merged 023 to avoid shared-file drift.
- Exact product/test allowlist:
  - `backend/api/routes.py` — GET `/models` function and necessary framework threadpool import only.
  - `backend/tests/test_onboarding_model_discovery.py` — new isolated regression tests.
  - `frontend/src/services/api.ts` — `modelService.available` normalization only.
  - `frontend/src/services/modelDiscovery.test.ts` — new tests through real service/client, adapter fixture permitted.
  - `frontend/src/screens/Setup.test.tsx` — discovery selector regression using actual object-to-ID boundary behavior, no production screen edit for this item.
- Implementation: `await run_in_threadpool(provider.list_models)` (Starlette/FastAPI supported helper), preserve provider-neutral access and existing `{"models": [...]}` response. Preserve existing provider-call exception mapping to HTTP 503 and auth/origin middleware; no async facade conversion or broad offload rewrite of other routes.
- Normalize provider records to nonempty string IDs in the client using `name`, then `model`, with runtime type checks; retain legitimate string entries for compatibility, discard invalid/non-ID entries rather than stringify objects. Preserve order/exact IDs (including `beam`); Setup already deduplicates. Do not rename aliases or silently substitute another model.

### Acceptance and independent proof gates

1. New authenticated HTTP test: synchronous fake provider returns actual dictionaries including beam; GET `/models` is 200 with the unchanged object payload. The test **fails on base with 503 vs 200**, passes on fix. No AsyncMock for `list_models`.
2. Client test through real `modelService.available` consumes that exact HTTP-shaped fixture and returns `['beam', ...]`, not objects; name/model fallback, strings, empty/invalid entries pinned. **Fails on base on array contents**, passes on fix. Render selector test proves exact selectable option ID; final real UI rerun covers React DOM, not only mocks.
3. Thread-offload proof: invoke actual async route with a sync fake recording thread identity and `asyncio.get_running_loop` status; prove call runs outside the route's event-loop thread. Use bounded event synchronization/timeouts, not network sleeps. A concurrent loop heartbeat must progress while fake discovery waits. Base fails; deleting offload on fixed tree also fails. Never hang the verifier indefinitely.
4. Fake provider raises bounded `RuntimeError('discovery unavailable')`: 503 and existing detail contract preserved, no fake empty success. Empty provider list remains 200 empty list. Anonymous/wrong-token requests remain 401; untrusted mutation origin gate remains covered by existing trust tests. Rejected request must not invoke provider. Do not print actual key/token.
5. In isolated real UI with preconfigured testing env key, Find available models reaches a string-ID selector including exact beam, **without Connect/save re-submission**. Selection, test, readiness survive refresh. No provider interface, credential persistence, health logic, inference parser or middleware changes.

## 024 — Existing safe-settings operation becomes truthful and atomic

- Tier: **sensitive** (classifier SENSITIVE minimum); areas: **api, access, security, data**. Named invariant: **SAFE_SETUP_IS_EFFECTIVE_GUARDED_AND_ATOMIC_90_DAY_CONFIG**.
- Exact product/test allowlist:
  - `backend/api/routes.py` — existing safe-settings/readiness functions and at most a small shared private predicate for effective guarded authority; no new endpoint/schema.
  - `backend/tests/test_onboarding_safe_settings.py` — new API/transaction/persistence/authority tests.
  - `backend/tests/test_setup_readiness.py` — additive regressions or expectation updates genuinely required by truthful safe readiness; never relax existing assertions.
  - `frontend/src/screens/Setup.tsx` — safe-settings Continue handler and removal of unused settingsService import only.
  - `frontend/src/screens/Setup.test.tsx` — replace obsolete forbidden-update assertion; add failure, stale-readiness, text-first/no-capture gates.
- No edits to `settings_store.py`, config/authority consumers, auth/secrets, schema/migrations, retention service, companion architecture or readiness helper contracts. If implementation needs other files, stop and tighten the plan before editing them.

### Implementation decision

1. Existing POST endpoint checks the exact effective autonomy expression used by runtime before any writes. Non-guarded value -> HTTP **409** with bounded actionable message explaining runtime is not guarded; no setting/marker mutation, no authority reset. UI surfaces error and stays on step 1. This intentionally narrows the old marker-only endpoint's success behavior without changing route/body/success shape (`{"ok":true}`).
2. In guarded mode stage `retention_days="90"` and `setup_safe_initial_config_applied="true"` with `set_setting` then **one commit**. On stage/flush/commit exception call rollback and return non-success (HTTP 500, bounded message, never raw database detail). No success marker on failed first application; a pre-existing committed marker/value remains unchanged, not partially overwritten. DB dependency cleanup alone is not the atomicity proof. Endpoint is idempotent and never launches deletion/purge/download/audio work.
3. Readiness safe_settings_ok must be marker=true **AND current effective autonomy guarded AND explicitly persisted, valid retention=90**. A marker alone, missing/malformed/different retention or autonomous runtime must be false on all three readiness aliases. Recheck current truth on every request, including after restart. Exact 90 matches the current UI promise; a later owner retention change makes this onboarding promise unfulfilled until reapplied, not permanently certified by history. Do not silently certify defaults from malformed values via retention fallback. Preserve full_ready's other checks and text-first separation; absent STT/TTS remains false/full_ready false.
4. UI removes generic settings update altogether, calls existing applySafeSettings, checks `ok===true`, then refreshes and requires `canStartChat(report)` before advancing. Neither HTTP success with false ok, readiness false, refresh failure nor API rejection can advance. Never set safe readiness optimistically or swallow failure. Optional microphone remains optional. Existing finish recheck remains mandatory.

### Acceptance and independent proof gates

1. API regression with guarded authority, initial retention=30/marker absent: POST succeeds; separate DB session observes retention=90 and marker=true; real retention reader returns 90. Base fails retention assertion. Repeat POST is idempotent and preserves unrelated settings, model/inference evidence and grants. Generic PUT autonomy still 400 and a mixed valid+forbidden batch changes nothing (including retention).
2. Authority matrix explicitly overrides suite guarded fixture **inside tests**: env unset + settings guarded -> success; env autonomous + settings guarded ->409; env guarded + settings autonomous ->success (runtime's exact precedence); env unset + settings autonomous ->409; unknown/empty ->409. Verify no env/settings mutation and no DB changes on rejection. Add forged DB autonomy='guarded' case: it cannot override actual autonomous authority. Existing visibility and approval tests must still pass; directly verify guarded excludes dangerous model-visible tools and consequential unapproved action requires approval. Tests of readiness cannot substitute for tests of policy behavior.
3. Inject failures after retention staged, after marker staged, and at flush/commit (before durable commit), on fresh and previously configured DB. Each returns non-success; a **new session on file-backed temporary SQLite** proves exact pre-request values remain; rollback observed, no pending session write leaks, retry with fault removed succeeds. Never inject a fictitious error after a successful durable commit and demand impossible rollback of that commit.
4. Persist through session/engine recreation on the same isolated file DB, then subprocess backend restart with same isolated profile and configured env test key: marker/retention and inference/model evidence survive; readiness honest. Memory secret store deliberately is not durable; no claim of restart vault persistence. Change authority to autonomous on restart -> safe false; change retention to30 -> safe false; marker-only and malformed values ->safe false. Verify all readiness aliases agree. Base fails marker-only truth tests.
5. Frontend regression: generic settings mock rejects any autonomy write, valid safe endpoint establishes readiness; Continue reaches step 2 without generic PUT. **Base fails by remaining on step 1**. Test API rejection, ok=false, refresh rejection, and refresh safe=false each stays step 1 with visible error. Verify request/refresh/advance order. Existing Setup.test:52 currently asserts the forbidden call; replace that false contract rather than retaining a mock that accepts it.
6. Text mode with STT absent/TTS empty finishes via real API-backed UI without install/capture/tts calls or permission prompts. `full_ready` stays false; `canStartChat` is true only with real model+safe evidence. Start conversation reaches actual companion/control-center conversation surface; send exactly one minimal read-only greeting, observe real response in same conversation and refresh persistence. Do not substitute `/chat/completions`, inject React state or fake readiness. Record blocked stages honestly if any independent defect appears; do not expand this item blindly.

## Shared safety, commands and independent roles

Each item: Implementer gets its own branch/worktree, then independent Reviewer and Verifier each their own worktree at the **same quoted reviewed commit SHA**. Reviewer audits allowed diff versus merge base, applies classifier, and verifies named safety invariant. Verifier independently runs new regressions on pre-item base with **only test additions copied as bytes**, then on fixed tree; behavioral assertion failure required, not import/fixture errors. Do not cherry-pick product fix onto base negative control. Pin 024's base after 023 merge. Report all attempts and per-test deltas, not just a green final total. Unit tests use inert credentials/MemoryStore, never the developer key. Keep test isolation/vault-write guards intact; do not copy `.env` to worktrees for suites.

Commands (separate invocations with tool workdir set to the specified worktree, not shell-chained):

```text
# Repository root, item 023
python -m pytest backend/tests/test_onboarding_model_discovery.py backend/tests/test_control_boundary.py -q
# Repository root, item 024
python -m pytest backend/tests/test_onboarding_safe_settings.py backend/tests/test_setup_readiness.py backend/tests/test_settings_api.py backend/tests/test_settings_store.py backend/tests/acceptance/test_importance_classifier.py backend/tests/test_tools.py -q
# frontend directory, each item
npm exec -- vitest run src/services/modelDiscovery.test.ts src/screens/Setup.test.tsx src/services/api.test.ts
npm test
npm run lint
npm exec -- tsc --noEmit
# Repository root, once per final reviewed tree and after merge; serialize suites
python -m pytest backend/tests -q --junitxml=<unique-approved-temp-artifact-path>
```

First inspect suite safeguards and existing dependencies; no install command, `npm run build`, Vite production bundle, Cargo/Tauri build or staging script. Do not invoke 023-only new test filenames before they exist. Existing historical native SQLite/UIA crashes and contention flakes are not waived: retain logs, independently compare same failure on base; report inconclusive rather than asserting green. Full suites run serially to avoid known machine contention. Minimal iteration tests first. Reviewer+Verifier must each identify invariant by name and confirm local/reversible change, pinned test, independent proof, no production data/config/credentials touched (sensitive gate's four conditions). No merge until ACCEPT + FIXED. Committer merges locally, reruns suites; recovery is **normal git revert only**, including `git revert -m 1 <merge>` for a merge. Never reset-hard, checkout-discard, clean, broad deletion, force-push, or revert unrelated user work.

Orchestrator finally runs read-only skill `scripts/status.py` and `scripts/verify.py` at `C:\Users\orcha\.config\opencode\skills\dev-orchestrator` against `.orchestra`; report pre-existing history/state audit warnings rather than rewriting history to hide them. New item plans/reports/reviews/verifications and synchronized new board/backlog entries are orchestrator/role bookkeeping, outside product allowlists. This Planner does not dispatch or reserve those roles.

## Isolated real browser rerun / retained artifacts

Retained scripts examined (not executed here): `T= C:\Users\orcha\AppData\Local\Temp\opencode\onboarding-dev-e2e-20261023-sol`; `launch.py`, `cdp.py`, `cleanup.py`. They use actual Vite+backend+Chrome CDP, secret from existing testing env privately, memory secrets, explicit profile/DB/home, scrub inherited overrides, matching generated control token, loopback-only ports and password-empty screenshot guard. No browser response interception. Headless DOM is real UI evidence, not Tauri/device proof.

Verifier must copy **only these three scripts** to a new uniquely named approved temp directory U (do not copy private browser cache/profile/logs), then audit/adapt script copies: launch.py's ROOT must point at verifier's fixed worktree; credential lookup may point privately to existing authorized original `backend/.env` without copying/printing it. cdp.py's optional connect-env path is not needed for saved-key discovery; if used in a separate bounded case, audit its fixed credential path too. Explicitly pin `SYTHION_AUTONOMY_LEVEL=guarded` in the dev child and record this assumption (original launcher scrubs it but .env can still supply settings). Preserve matching auth/origin and all isolation guards. Assert ports free; if occupied stop, do not kill user's processes. Use a fresh profile for base and fixed reproductions, never reuse old artifacts as fresh evidence.

Example reproducible commands after adapting ROOT and creating U (PowerShell variable U points at the new directory):

```powershell
python "$U\launch.py"
python "$U\cdp.py" navigate unused 8 01-initial
python "$U\cdp.py" js "Array.from(document.querySelectorAll('button')).find(b=>b.innerText==='Find available models').click()" 12 02-models
python "$U\cdp.py" js "(()=>{const e=document.getElementById('setup-model');e.value='beam';e.dispatchEvent(new Event('change',{bubbles:true}));})()" 1 03-beam
python "$U\cdp.py" js "Array.from(document.querySelectorAll('button')).find(b=>b.innerText==='Test selected model').click()" 30 04-inference
python "$U\cdp.py" js "Array.from(document.querySelectorAll('button')).find(b=>b.innerText==='Continue').click()" 5 05-talk-choice
python "$U\cdp.py" js "Array.from(document.querySelectorAll('button')).find(b=>b.innerText==='Continue').click()" 2 06-text-summary
python "$U\cdp.py" js "Array.from(document.querySelectorAll('button')).find(b=>b.innerText==='Start conversation').click()" 5 07-conversation
python "$U\cdp.py" reload unused 5 08-refresh
python "$U\cleanup.py"
```

Waits are upper observation windows, not assertions of completion: inspect sanitized DOM/status and real readiness before next click, bounded polling if needed. Add a DOM-driven actual composer input/change/send step after inspecting rendered selectors; submit one greeting only, no tool task/audio, verify reply/persistence before refresh. Record exact executed composer command in verification report rather than invent a selector here. Add controlled backend restart subcase with same isolated profile and separately owned-process ledger; never blindly rerun launcher while ports remain bound. No repeated blind inference/provider retries. Keep read-only anonymous readiness401 proof and path/status evidence (never headers/tokens/query secrets); screenshots before/after each real step. Guard against secret field screenshots and scan shareable artifacts for credential bytes privately.

Always cleanup in finally: retained cleanup validates owned root command identity, collects descendants, closes owned Chrome, terminates only owned processes, removes only owned sidecar handshake and reports remaining PIDs/port closure. Note its CDP Browser.close occurs after root checks: rerun only while ledger identifies this run's Chrome on19323; do not send Browser.close to an unrelated replacement listener. New restart ledger must track all still-owned roots. Keep sanitized report/screenshots/UI JSONL, private artifacts local; no blanket recursive deletion.

## Source vs packaging — separate follow-up, not a repair claim

Current dirty `src-tauri/resources/backend/api/routes.py` is pre-existing user/investigator work and **must remain untouched**. Planner measured both current source/staged files SHA256 `5c0dcafad5270182461eae3f47ad261b92252d9f4a56997eac51491fd10614d3`: current working files match. `git diff` shows staged-resource dirt adds the already-source-fixed inference parser; committed staged resource lacks that fix. Thus prior installed 502 may involve stale packaging, but current real source discovery/Continue failures exist independently. Do not label current dirty mirror stale by assumption, overwrite it, or claim packaged app fixed after source tests. After these repairs source/mirror drift will need a separately authorized packaging refresh and packaged-launch proof.

No payload rebuild now: stage_python_runtime.py can replace runtime; dependency lock/packaging drift is historical follow-up, not permission to run it. No frontend/dist, embedded Tauri freshness, release resource inventory or installed executable parity is established here. Future release work must pin source, mirror, dist/embedded executable hashes, preserve secret-free payload policy and validate actual packaged onboarding. Merely editing a mirror or passing payload hygiene is not freshness proof.

## Planner checks and limits / next dispatch

Performed: mandatory memory stats/query/prepare, file contexts, project shape, safe-route blast-radius and retention caller query, direct source/test/report/board/script reads; `git status --short`, `git rev-parse HEAD`, read-only budget/ID/hash calculation and staged-route diff. Source graph lacks decorator/HTTP callers; zero graph callers is not no impact. Documentation safety check cannot index new Markdown files; manually reviewed append-only critique and new-plan-only edits. No source code changed, no tests/UI/provider calls/builds/installs run by Planner. Reported E2E remains investigator evidence; future gates above are requirements, not claimed results. Screenshots/private DB not independently examined. No device/audio/production/CI/packaged behavior checked.

Recommended next dispatch: orchestrator accepts/adjusts plan, records new bounded budget and IDs, then Implementer **023** with its exact allowlist and fail-before/pass-after service+HTTP tests. Independent review/verification before local merge; then Implementer024, same gates, final isolated real DOM text-first onboarding and one real minimal conversation. Any new blocker gets evidence and explicit disposition, not automatic scope expansion or a third merged item.
