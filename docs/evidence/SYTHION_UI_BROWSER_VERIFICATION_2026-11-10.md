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
