# Sythion AI provider and model UX — first integration slice

## Product boundary
Onboarding is the only first-use configuration flow. Settings → AI Models is the only post-onboarding configuration destination. The main AI Assistant has no provider/key/model setup controls. Chat UI sends messages to whichever runtime has been explicitly admitted; it must not silently turn model verification into task authorization.

## User flow
1. Onboarding: select **Ollama Cloud, OpenRouter, NVIDIA NIM or Groq** directly (no hidden alternate-provider preview).
2. Ollama retains the existing checked onboarding path while its unified wizard is redesigned in a later change. Settings can inspect/save Ollama credentials and select/verify a model.
3. Cloud providers share one surface in onboarding and Settings: enter a provider key only if needed → **Connect and load models** (one click for a new credential; model names load automatically when a saved key exists) → choose **Free models** or **All models** → choose a model once. Selection initiates qualification of both plain-text chat and a harmless, forced function call (up to **two** cloud inference requests if new). Only successful evidence for both checks admits the onboarding user into the isolated answer-only conversation; Settings displays the verified result.
4. Previously verified identity/key/model can continue on explicit model selection without spending duplicate inference requests. Text evidence and tool-call evidence are separately HMAC-bound to provider, model, stage generation and key, and expire after one hour. If only text proof is current, Sythion runs the missing tool check without resending text; if neither is current it runs the text check then the harmless tool-call probe. No inference runs on page load, catalog refresh, filtering or mere credential entry. A failure has a visible, manual **Retry selected model** action, never an automatic retry. Unsupported function calling yields a reason to select another model; 429/503 must not be misrepresented as confirmed incompatibility.
5. Model names and `:free` labels are advisory; pricing, capacity and eligibility are not guaranteed. Default to the free filter; showing all models is an explicit user choice. One selected model may cost up to two provider requests to qualify. No separate model-search field, duplicate selected-model field, testing consent checkbox or test button. Credentials are write-only, and provider errors are sanitized.
6. Cloud inference remains in the **answer-only** isolated runtime. No tasks, tools, files, memory writes, provider auto-activation, background inference or persistent cloud transcript. Ollama's existing onboarding gates still control access to the task-capable main Assistant.

## Deferred architecture work — do not silently claim complete
- Replace Ollama's older four-step wizard with the same concise provider/model configuration experience, preserving safe permission/retention setup and optional voice testing.
- Route selected, verified *conversation* providers through one unified Assistant shell while keeping all task, tool, file and memory capabilities independently gated server-side. The selection check now validates text and **one synthetic function call**, not reliability under production tool use, task execution, memory or autonomous actions. Even if both pass, a cloud provider is **not** an executable agent; runtime authorization and integration are separate implementation milestones.
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

## Owner screenshot acceptance and next capability gate (2026-11-13)
Owner demonstrated in the running Windows desktop app: choosing an OpenRouter NVIDIA Nemotron model initiated automatic text verification, entered isolated cloud conversation and returned a successful answer to `hello`. The owner accepted the selection experience but expected a unified main Assistant interface eventually. They requested automatic compatibility checking for Sythion's tool needs and a user-readable reason when a model fails. The next change adds automatic text + synthetic function-call checks; **no Windows tests for these new changes yet**. Tool failures block onboarding admission and explain selection of another model; unknown provider/network outcomes are not classified as definitive model incompatibility.
