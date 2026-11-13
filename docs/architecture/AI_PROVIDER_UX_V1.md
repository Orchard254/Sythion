# Sythion AI provider and model UX — first integration slice

## Product boundary
Onboarding is the only first-use configuration flow. Settings → AI Models is the only post-onboarding configuration destination. The main AI Assistant has no provider/key/model setup controls. Chat UI sends messages to whichever runtime has been explicitly admitted; it must not silently turn model verification into task authorization.

## User flow
1. Onboarding: select **Ollama Cloud, OpenRouter, NVIDIA NIM or Groq** directly (no hidden alternate-provider preview).
2. Ollama retains the existing checked onboarding path while its unified wizard is redesigned in a later change. Settings can inspect/save Ollama credentials and select/verify a model.
3. Cloud providers share one surface in onboarding and Settings: enter a provider key only if needed → **Connect and load models** (one click for a new credential; model names load automatically when a saved key exists) → choose **Free models** or **All models** → choose a model once. The selection explicitly triggers a single text-only qualification request when verification is not current. A successful onboarding check immediately enters the isolated answer-only conversation; a successful Settings check saves the verified selection.
4. Previously verified identity/key/model can continue on explicit model selection without spending a duplicate inference request. All proof is rechecked against the backend. Selecting an unverified model is itself the user-driven initiation of one text probe; it may consume quota. No test runs on page load, catalog refresh, filtering or mere credential entry. A failure has a visible, manual **Retry selected model** action, never an automatic retry.
5. Model names and `:free` labels are advisory; pricing, capacity and eligibility are not guaranteed. Default to the free filter; showing all models is an explicit user choice. No separate model-search field, duplicate selected-model field, testing consent checkbox or test button. Credentials are write-only, and provider errors are sanitized.
6. Cloud inference remains in the **answer-only** isolated runtime. No tasks, tools, files, memory writes, provider auto-activation, background inference or persistent cloud transcript. Ollama's existing onboarding gates still control access to the task-capable main Assistant.

## Deferred architecture work — do not silently claim complete
- Replace Ollama's older four-step wizard with the same concise provider/model configuration experience, preserving safe permission/retention setup and optional voice testing.
- Route selected, verified *conversation* providers through one unified Assistant shell while keeping all task, tool, file and memory capabilities independently gated server-side. The current automatic selection check validates plain text only; it does not demonstrate suitability for tools, task execution, memory or autonomous actions. A cloud provider is not an executable agent merely because text chat or a synthetic tool-call probe passed.
- Move technical model probes and provider HTTP diagnostics to an expandable **Advanced diagnostics** area under Settings. Retire the legacy Cloud Text Chat navigation route only after migration and deep-link compatibility tests.
- Test restart/reload, credential rotation, expired evidence, provider switch, quota failure, unknown transport outcome and mixed-model history; no automatic retries or hidden paid requests.
- Run the Windows full frontend/backend acceptance matrix and installed-app manual checks before any release claim. GitHub Actions runs cannot currently start because the account's Actions budget is $0; this is a **CI-not-run** exception, never a green CI signal. New selection-driven UI behavior needs fresh Windows test/build results; prior results validate the preceding commit, not these revisions.

## Owner-supplied regression evidence for superseded PR #8
Owner ran 107 frontend cases: **102 passed, five failed** in newly added main-screen-entry tests. All five failed because `mock.cloudStatus` was undefined. `npm --prefix frontend run lint` succeeded with a nonblocking TypeScript parser version warning, while `npm --prefix frontend run build` failed with eight TS2339 errors from that missing mock. PR #8 was closed without merge because both its screen placement and its tests were unsuitable.

## Acceptance of this PR
No local Windows tests have been reported for this new branch. Verify:
```powershell
npm --prefix frontend test -- src/screens/Setup.test.tsx src/screens/Settings.test.tsx src/components/CloudConnectionSetup.test.tsx src/components/CloudProviderPreview.test.tsx src/screens/CloudConversationOnly.test.tsx src/screens/Assistant.test.tsx
npm --prefix frontend run lint
npm --prefix frontend run build
```
Review UI with no real cloud inference necessary. Do not merge unless local checks pass and cloud-only onboarding still works without an Ollama credential.
