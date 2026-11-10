# Sythion browser UI acceptance and first remediation

## User-supplied OpenCode evidence (source commit 4d62756)

OpenCode exercised the isolated live Vite frontend at port 3000 with a
temporary sandbox backend at port 8001 and a disposable control token. The
original Windows process on port 8000 was not touched. The 94-line detailed
report and screenshots 13–16 are in the owner's local untracked
docs/reports/ directory and are **not included** in this commit. This
document is only a summary of facts supplied by the owner; the author did not
reproduce OpenCode's browser run.

- **D-001 PASS (browser):** Companion POST /api/v1/assistant/turns returned 200
  with a nonempty client_request_id; assistant caption rendered and the draft
  cleared. With the sandbox backend stopped, the offline guard blocked
  submission and retained the draft. No unexpected application console errors
  were observed.
- **D-001 same-identity mid-flight retry:** UNIT PASS only (31/31 focused
  frontend tests across Companion/observation), live network emulation BLOCKED.
- **D-004 PASS (browser):** Active goal with blocked step produced Needs You
  attention. Abandoning it through the UI persisted abandoned status; after
  refresh both attention and Goals in focus were clear. A second active
  blocked goal remained visible as a positive control.
- **D-004 optional freshness note:** Shared objectives list previously remained
  cached up to 15 seconds after mutation. Subsequent code invalidates
  objectives and goals on lifecycle success, pending browser revalidation.
- **NOT TESTED:** installed Tauri overlay/click-through/tray/voice/handshake,
  consequential task execution, mid-flight network drop or native restart.

## New D-002 opt-in task entry (pending Windows/browser testing)

Assistant retains normal Enter/Send chat. The separate Create task button
requires a second explicit confirmation of the exact request text and calls
the existing POST /tasks with interaction_hint=task and a stored idempotency
key. It does **not** bypass the backend's intent interpreter: if the server
routes the request as CONVERSATION, the UI says that no executable task was
created. A recognized Task response opens TaskDetailDrawer and refreshes task
and goal lists. It never calls resume or repeats execution automatically.

Failed/unknown request outcomes retain the draft and request identity and
require explicit user retry. Unavailable/corrupt sessionStorage prevents
submission, so the UI cannot mint a fresh request identity in an uncertain
same-tab retry. Storage durability across a complete Tauri application
relaunch has not been verified; the user must inspect Tasks before requesting
the same work again after an uncertain app shutdown. Stored task identity is
cleared only after an explicit backend task/conversation response. This
feature DOES NOT implement a direct Goal creation form, automatic approval,
or a universal guarantee that all user phrasings become tasks.

Required checks on the updated commit:

1. Frontend Vitest: WorkEntry, Assistant, API; production TypeScript/Vite build.
2. Browser: verify normal Send only calls /assistant/turns, while Create task
   opens confirmation and makes one POST /tasks containing a nonempty
   idempotency_key after confirmation.
3. Use a harmless explicitly approved read-only battery/system-information
   task; inspect real response, task state, evidence and TaskDetailDrawer.
4. If backend classifies as conversation, verify honest no-task UI and no
   false claim of running work.
5. Check disconnected state, cancellation, and retry under available
   network simulation. Never fabricate network-failure results.
6. Confirm Goals/Operations refresh promptly after goal lifecycle changes.
7. Installed Windows/Tauri acceptance remains separately NOT TESTED.

D-003 memory claim reliability, D-005 unread badge, remaining UI defects and
safe goal creation UX remain OPEN. Do not mark D-002 fully resolved from
unit tests or source inspection alone.

## Owner Windows targeted frontend gate: PASS at c5e3874 (2026-11-10)

The owner supplied Windows PowerShell logs for the fast-forward from fa53b7a to c5e3874. The author did not run the Windows commands and has not inspected an installed application.

- Targeted Vitest: 107/107 PASS, 6/6 files, including TopBar (3), WorkEntry (6), Assistant (73), Goals (2), API (21), and format (2).
- npm run build --prefix frontend PASS: TypeScript and Vite 5.4.21; 191 modules transformed without TypeScript errors.
- The separately supplied backend memory-prompt test run at fa53b7a was 2/2 PASS.
- The owner test report contained no failed tests or unhandled error summary. This is a unit/integration/build gate, not live UI acceptance.
- Earlier failures at 4008bc5 and fa53b7a were addressed by test setup, Vitest 1.x mock typings and formatter cleanup, with no production permission-policy change.

### Next D-002 live browser acceptance

Use an isolated sandbox frontend (port 3000) and sandbox backend (8001), without touching the existing port-8000 service. Independently validate normal Send and the confirmed Create task path. Capture POST /tasks body including interaction_hint=task and idempotency_key, verify saved task ID and task evidence timeline, inspect safe permission refusal, and check duplicate creation behavior without intentionally replaying side effects. Keep original evidence/report artifacts in the owner's local untracked docs/reports/ folder.

Release readiness remains BLOCKED on native installed Tauri acceptance, D-002 browser verification, D-003 synchronous verified memory writes, and other outstanding UI acceptance items.

## D-002 owner OpenCode browser gate: BLOCKED by provider HTTP 402

Owner-supplied OpenCode summary (2026-11-10; code tested c5e3874):

- Real Chromium frontend on localhost:3000 and isolated uvicorn backend on :8001; pre-existing :8000 service PID 12704 left untouched. No mocks, no code changes. Report at local docs/reports/SYTHION_D002_LIVE_ACCEPTANCE.md (~21.7KB) with evidence d002-01 through d002-06. These local artifacts are **not** committed or independently opened by this author.
- POST /tasks with interaction_hint=task admitted durable task fdcc6444-addc-4a01-90c2-f6682679e0a6; nonempty idempotency key persisted. Replayed key returned HTTP 200, identical task ID, and no extra effects (1 task and 8 events before/after).
- Task ultimately failed honestly at CLOUD_ERROR from provider HTTP 402; no tool execution or battery read occurred. Assistant turns and conversation-classified creation failed closed without creating tasks.
- Three observed onboarding inference attempts (beam twice; gpt-oss:20b once) resulted in upstream HTTP 402 wrapped in 502. Provider health UNAVAILABLE, readiness ollama_health_ok=false and inference_verified_ok=false despite ollama_api_key_ok=true.
- Setup correctly blocked Continue and kept the main Assistant UI inaccessible; D-002 visible Create task confirmation, drawer, result/evidence, and other UI checks remain **BLOCKED/NOT TESTED**, not failed and not passed.
- A unit test summary of 113 focused passes was reported in browser evidence; this does not replace execution evidence.
- Native installed Tauri acceptance remains NOT TESTED.

### Next unblock

Verify the Ollama Cloud account's billing/credit or usage allowance, or supply an authorized operational cloud credential. Once genuine inference succeeds, rerun only the D-002 acceptance checklist against a fresh isolated sandbox. **Do not** bypass setup, fake readiness, replay consequential tasks, or call task execution 'passed' on deduplication evidence alone. The repository has a cloud-only provider implementation today; a local inference backend would require a separate design and test effort.

## D-002 Cloud billing-policy explanation and follow-up Windows gate

On 2026-11-10, the owner supplied screenshots of an Ollama email entitled
'Reminder: free cloud usage ends today at 5:40 PM PT', sent 2026-11-09.
The email states Free-plan accounts switched to pay-as-you-go on Nov 9,
with credit required for several Cloud models including gpt-oss:20b,
nemotron-3-super and nemotron-3-ultra. Existing credits, when present,
continue to apply. Local Ollama runs remain free. This is owner-supplied
account communication, NOT an independent reading of account balance.

Public official pricing corroborates the pay-as-you-go credit model:
https://ollama.com/pricing (verified 2026-11-10). It lists a credit-based
Cloud pricing model, while locally hosted inference remains available
without cloud usage charges. Official pricing alone does not establish
whether the owner's specific Cloud credit balance is zero or the exact
billing condition for the observed upstream 402.

Observed HTTP 402 in the isolated sandbox is consistent with this policy
transition, and not evidence of a Task or onboarding bypass defect. Task
execution, the real D-002 UI interaction, and native Tauri acceptance remain
blocked/not tested until authorized real inference is available.

Owner Windows log (source commit 39bddf4):
- `npm run test --prefix frontend -- src/lib/modelTestFailure.test.ts src/screens/Setup.test.tsx`: **14/14 PASS** across 2 Vitest files.
- `npm run build --prefix frontend`: **PASS**, `tsc && vite build`, Vite 5.4.21, 192 transformed modules.

Next action: review Ollama account credits/billing or choose an authorized
working Cloud provider. No subscription/billing/account changes were made.
A separate local-inference provider requires product and safety design,
resource validation, new provider adapter and onboarding acceptance. Do not
manipulate `canStartChat`, fake inference verification, or reclassify
credential validity as evidence of execution readiness.
