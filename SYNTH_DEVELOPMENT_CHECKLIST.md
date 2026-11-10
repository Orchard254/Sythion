# Synth development checklist

Created: 2026-11-09. Maintainer: coordinating development agent (director).
Product and architecture context: [SYNTH_PROJECT_UNDERSTANDING.md](SYNTH_PROJECT_UNDERSTANDING.md).

## Management contract

This captures all 170 items in the initial conversation backlog. It is the
current working inventory, not a claim that every future requirement or defect
has been discovered. Existing components and historical tests are inputs to
work, not automatic completion. All items begin open pending explicit acceptance.

- Preserve IDs when reordering; add new IDs for newly discovered work.
- The coordinator owns prioritization, delegation, integration and verification.
- To avoid double work, each agent should run only the tests affected by its own
  changes, not the full suite. Broader runs are reserved for baseline work,
  architecture integration milestones, or when targeted evidence is insufficient.
- Operating model: the coordinator primes a subagent with one bounded objective,
  the relevant mental model, current verified state, constraints, scope, file
  ownership, acceptance criteria, and the exact evidence required. The agent
  must not search blindly or guess project intent; it works from that brief and
  codebase evidence. A subagent reports evidence, not conclusions; the
  coordinator integrates and independently verifies anything high-risk.
- Parallel delegates must have non-overlapping work. Coordinator decides
  dependencies, ordering and when to stop instead of letting every agent run a
  broad suite. Delegated agents use the model appropriate to the task, but no
  agent marks work complete; only the coordinator can accept it after checking
  source, tests and runtime behavior.
- Status vocabulary: queued, researching, ready, implementing, verifying,
  blocked, done, deferred. A checked box means done with recorded evidence.
- Link completion to observed commands/results or reviewed design decisions.
  Distinguish synthetic tests, real-model tests and installed-runtime checks.
- Record blockers and required user decisions; do not silently redefine scope
  to turn a partial result into completion. Split items when useful, retaining
  the relationship to their original ID.
- This is a planning artifact, not blanket permission to enable capabilities,
  make purchases, change authority boundaries, install software or publish code.
- Design proposals remain proposals until their intended behavior is settled.
- Work only in the application repository for application delivery; do not
  commit or push the outer workspace.

Labels: Fix = demonstrated problem; Verify = unresolved behavior; Build =
intended capability; Design = unsettled approach/boundary. Refactor, Organize,
Explore, Prototype and Evaluate retain their ordinary meanings.

## Execution strategy

Start with sections 1–2. Prioritize live voice (3), supported by runtime work
(4). Develop coherent autonomous work through sections 4–9. Expand through
10–13 once prerequisite contracts are sound. Packaging and code quality (14–15)
run alongside relevant changes rather than waiting until the end. Keep the OS
direction (16) as research until evidence supports an implementation choice.
Section order is dependency guidance, not a requirement to finish every item in
one section before any independent work can proceed.

## 1. Development foundation

- [ ] S001 — Design: Finalize the LLM understanding document as the shared architectural reference.
- [ ] S002 — Design: Define identity, conversation, goal, project, plan, task, agent, action, memory and evidence relationships.
- [ ] S003 — Design: Separate intended architecture from current implementation.
- [ ] S004 — Design: Maintain dependency-ordered work with explicit completion criteria.
- [ ] S005 — Verify: Establish a current test baseline, including skips and environment-dependent tests.
- [ ] S006 — Verify: Reconcile supported Python, Node and packaged-runtime versions.
- [ ] S007 — Build: Promote appropriate temporary investigation scripts into maintainable regression tests.
- [ ] S008 — Build: Standardize isolated profiles, fake providers, synthetic files and device fixtures.
- [ ] S009 — Build: Record reproducible verification commands and evidence.
- [ ] S010 — Design: Establish non-overlapping ownership for parallel development.

## 2. Correctness and trust gaps

- [ ] S011 — Fix: Prevent task-scoped memory entering another task's model context.
- [ ] S012 — Fix: Apply lifecycle filtering consistently to full-text and linked-neighbor memory retrieval.
- [ ] S013 — Verify: Check memory cache behavior after scope, status and content changes.
- [ ] S014 — Build: Propagate trustworthy task/session access context into model-context construction.
- [ ] S015 — Fix: Validate goal references before treating delegation links as meaningful.
- [ ] S016 — Verify: Investigate progress totals that may include superseded plan steps.
- [ ] S017 — Verify: Check missing session context in task-model calls and conversational-history effects.
- [ ] S018 — Verify: Audit direct execution paths for consistent policy enforcement.
- [ ] S019 — Verify: Distinguish execution success, evidence validation and factual correctness in result integration.
- [ ] S020 — Fix: Prevent deterministic fallbacks being presented as successful model reasoning.
- [ ] S021 — Verify: Review swallowed exceptions that may hide startup, schema or execution failures.
- [ ] S022 — Verify: Establish reachability of legacy PowerShell voice code; fix or retire appropriately.
- [ ] S023 — Build: Add regression coverage for confirmed corrections.

## 3. Natural live voice — first feature priority

- [ ] S024 — Design: Define listening, thinking, speaking, turn-taking and interruption behavior.
- [ ] S025 — Design: Choose speech/model architecture using latency, quality and integration evidence.
- [ ] S026 — Build: Connect capture, transcription, conversation and speech output coherently.
- [ ] S027 — Build: Support continuous turns without mandatory manual draft submission.
- [ ] S028 — Build: Detect turn endings reliably.
- [ ] S029 — Build: Allow speech interruption and conversational redirection.
- [ ] S030 — Build: Separate stop-speaking, stop-listening and cancel-work actions.
- [ ] S031 — Fix/Build: Connect spoken-stop handling to intended Assistant behavior.
- [ ] S032 — Build: Handle final audio frames, ordering and backpressure correctly.
- [ ] S033 — Build: Prevent Synth's speech being interpreted as the user's next instruction.
- [ ] S034 — Build: Preserve conversational context while background work continues.
- [ ] S035 — Build: Recover from capture, transcription, model and playback failures.
- [ ] S036 — Build: Provide clear microphone state and permission controls.
- [ ] S037 — Verify: Test microphone and speaker behavior in the packaged app.
- [ ] S038 — Verify: Measure real-use latency, interruption and sustained conversation quality.

## 4. Persistent runtime

- [ ] S039 — Design: Define continued operation independently of an open interaction window.
- [ ] S040 — Verify: Establish main-window, companion-window and application-exit behavior.
- [ ] S041 — Build: Supervise unexpected sidecar failure appropriately.
- [ ] S042 — Build: Preserve and reconcile work across runtime restarts.
- [ ] S043 — Verify: Test Windows sleep, resume, sign-out, shutdown and reboot behavior.
- [ ] S044 — Verify: Test packaged profile locking and duplicate launch.
- [ ] S045 — Verify: Ensure pending wakes cannot become stranded.
- [ ] S046 — Verify: Reconcile documented wake triggers with production callers.
- [ ] S047 — Build: Expose background-operation controls and current state.
- [ ] S048 — Build: Distinguish available, idle, working, waiting, degraded and stopped states.
- [ ] S049 — Build: Make cancellation and shutdown leave honest, recoverable records.
- [ ] S050 — Build: Expose recovery rather than silently replaying uncertain actions.

## 5. Goals, tasks and agents

- [ ] S051 — Design: Define a shared orchestration contract across Assistant, task and goal entry points.
- [ ] S052 — Build: Connect goal-backed work to specialist delegation.
- [ ] S053 — Build: Preserve goal, step, parent-task and specialist-task links.
- [ ] S054 — Build: Integrate specialist results into plan progress using appropriate evidence.
- [ ] S055 — Design: Decide when to work directly, delegate once or coordinate multiple specialists.
- [ ] S056 — Verify: Review the multi-specialist coordinator before production integration.
- [ ] S057 — Build: Coordinate independent and dependent agent work.
- [ ] S058 — Build: Prevent agents conflicting over shared resources.
- [ ] S059 — Build: Detect incomplete, conflicting or unsupported specialist results.
- [ ] S060 — Build: Retry, reassign or change strategy after delegated failures.
- [ ] S061 — Build: Preserve parent-goal continuity when a specialist pauses or fails.
- [ ] S062 — Verify: Exercise a user-facing goal through planning, delegation, tools, integration and restart.
- [ ] S063 — Verify: Evaluate the goal-agent flow with real providers, not only scripted responses.

## 6. Intended autonomy model

- [ ] S064 — Design: Define recoverability for each action class.
- [ ] S065 — Build: Maintain records sufficient to understand and recover changes.
- [ ] S066 — Build: Add appropriate file, application, configuration and device recovery mechanisms.
- [ ] S067 — Verify: Test restoration, not just backup creation.
- [ ] S068 — Design: Bind permissions to exact actions and targets.
- [ ] S069 — Build: Implement trusted approval-to-execution for consent-required actions.
- [ ] S070 — Build: Keep unavailable-user requests pending while other work proceeds.
- [ ] S071 — Build: Enforce the purchase prohibition across relevant tools and workflows.
- [ ] S072 — Build: Support revocation, pause and cancellation.
- [ ] S073 — Verify: Ensure agents and new capabilities share the authority boundary.
- [ ] S074 — Design: Reconcile execution bounds with autonomy without general operating-budget caps.
- [ ] S075 — Build: Explain why Synth acted, waited or refused.

## 7. Useful initiative

- [ ] S076 — Design: Represent evolving user priorities and interests.
- [ ] S077 — Build: Maintain an agenda beyond the current conversation.
- [ ] S078 — Build: Select useful work from goals, events, unfinished activity and opportunities.
- [ ] S079 — Build: Recognize and propose beneficial new projects.
- [ ] S080 — Build: Start appropriate self-initiated projects within authority.
- [ ] S081 — Build: Explain the benefit of independently started projects.
- [ ] S082 — Build: Reprioritize after user direction or changed circumstances.
- [ ] S083 — Build: Detect stagnant, low-value or conflicting work.
- [ ] S084 — Build: Pause, simplify or retire work that is no longer useful.
- [ ] S085 — Build: Choose between acting, asking, investigating and remaining idle.
- [ ] S086 — Verify: Evaluate initiative by usefulness, not activity volume.

## 8. Memory and long-term continuity

- [ ] S087 — Design: Relate conversations, facts, preferences, project knowledge and task evidence.
- [ ] S088 — Build: Enable cross-session recall through authenticated, scoped access.
- [ ] S089 — Build: Preserve memory provenance and confidence.
- [ ] S090 — Build: Reconcile conflicting or outdated memories.
- [ ] S091 — Build: Apply correction, forgetting and supersession consistently.
- [ ] S092 — Build: Let the user inspect and correct Synth's understanding.
- [ ] S093 — Build: Connect memory to planning, agents and projects without duplicate stores.
- [ ] S094 — Verify: Test recall across actual restarts and long-running projects.
- [ ] S095 — Verify: Evaluate recall relevance, omissions and inappropriate retrieval realistically.
- [ ] S096 — Design: Learn preferences without universalizing every correction.

## 9. Communication grounded in real activity

- [ ] S097 — Build: Generate briefings from durable activity.
- [ ] S098 — Build: Include completed, ongoing, failed and blocked work, discoveries and decisions.
- [ ] S099 — Build: Deliver natural spoken morning briefings.
- [ ] S100 — Build: Support follow-up questions about briefings and evidence.
- [ ] S101 — Build: Distinguish attempted, completed and verified outcomes everywhere.
- [ ] S102 — Build: Notify selectively rather than interrupting for every event.
- [ ] S103 — Build: Explain self-initiated projects and changes in direction.
- [ ] S104 — Build: Keep Companion interaction simple while exposing detailed control elsewhere.
- [ ] S105 — Verify: Keep UI and backend state consistent after reconnects and restarts.

## 10. Capabilities and environmental understanding

- [ ] S106 — Verify: Deeply audit skills, learning and browser integration.
- [ ] S107 — Design: Define uniform capability registration and discovery.
- [ ] S108 — Build: Select tools and specialists required by a task.
- [ ] S109 — Build: Track capability availability, health and limits.
- [ ] S110 — Build: Support reusable skills/workflows with testable contracts.
- [ ] S111 — Build: Improve browser and application interaction through suitable interfaces.
- [ ] S112 — Build: Model permitted software, services and devices.
- [ ] S113 — Build: Detect environmental changes that invalidate plans.
- [ ] S114 — Build: Recover from unavailable tools or choose alternatives.
- [ ] S115 — Verify: Prevent capability expansion bypassing authority or evidence checks.

## 11. Permitted-device control

**Status: deferred.** The owner explicitly deferred device access and phone
access while the immediate goal is to make Synth reliable end to end. Keep these
concepts in the architecture, but do not implement device integrations or a phone
client until the core runtime, voice, goals, agents, memory and recovery are
trustworthy.

- [ ] S116 — Design: Select initial device categories and integration methods.
- [ ] S117 — Build: Register devices and permitted operations.
- [ ] S118 — Build: Read device state and receive relevant events.
- [ ] S119 — Build: Execute permitted commands and verify effects.
- [ ] S120 — Build: Handle disconnected, unavailable or inconsistent devices.
- [ ] S121 — Build: Coordinate device actions with projects and routines.
- [ ] S122 — Build: Support device-specific revocation and recovery.
- [ ] S123 — Verify: Use disposable devices or simulators before real-world reliance.

## 12. Future phone access

- [ ] S124 — Design: Choose remote-access and authentication architecture.
- [ ] S125 — Build: Provide a phone interface to the same persistent Synth instance.
- [ ] S126 — Build: Continue conversations across PC and phone.
- [ ] S127 — Build: Expose ongoing work and briefings remotely.
- [ ] S128 — Build: Support remotely authenticated permission decisions.
- [ ] S129 — Build: Deliver appropriate notifications.
- [ ] S130 — Build: Handle disconnects without lost work or duplicate actions.
- [ ] S131 — Verify: Test remote access, revocation and communication security.

## 13. Learning, experimentation and self-maintenance

- [ ] S132 — Design: Define automatic improvements versus proposals for review.
- [ ] S133 — Build: Compare predicted and actual outcomes.
- [ ] S134 — Build: Learn strategies from verified results.
- [ ] S135 — Build: Identify recurring agent/workflow failures.
- [ ] S136 — Build: Run controlled experiments in isolated environments.
- [ ] S137 — Build: Evaluate improvements before adopting them.
- [ ] S138 — Build: Maintain a capability and health inventory.
- [ ] S139 — Build: Diagnose failing internal components.
- [ ] S140 — Build: Repair recoverable problems within authority.
- [ ] S141 — Build: Preserve rollback and evaluation evidence for self-maintenance.
- [ ] S142 — Verify: Prevent unmeasured behavioral drift during improvement.

## 14. Packaging and release reliability

- [ ] S143 — Fix: Establish dependency hashes and trusted bootstrap provenance.
- [ ] S144 — Build: Produce a reproducible, traceable runtime payload.
- [ ] S145 — Verify: Run the payload with its embedded Python, not a substituted interpreter.
- [ ] S146 — Verify: Launch the packaged prototype against a synthetic profile.
- [ ] S147 — Verify: Confirm packaged profile, secret-store and resource isolation.
- [ ] S148 — Verify: Test shell/sidecar lifetime and child cleanup.
- [ ] S149 — Verify: Test install, upgrade, uninstall and interruption with synthetic data.
- [ ] S150 — Verify: Resolve the app-data deletion-path discrepancy.
- [ ] S151 — Build: Automate appropriate installer and packaged-runtime checks in CI.
- [ ] S152 — Design: Establish update, artifact-integrity and rollback strategy.
- [ ] S153 — Build: Maintain accurate setup, run, test and release instructions.

## 15. Code quality with behavior preservation

- [ ] S154 — Verify: Identify required aliases and compatibility layers.
- [ ] S155 — Refactor: Remove or consolidate genuinely obsolete paths.
- [ ] S156 — Refactor: Split large modules along tested responsibility boundaries.
- [ ] S157 — Refactor: Reduce initialization-order and cross-layer coupling.
- [ ] S158 — Fix: Replace silent failures with meaningful outcomes where needed.
- [ ] S159 — Build: Improve independent testability of model, persistence, tool and runtime interfaces.
- [ ] S160 — Fix: Remove stale documentation links and misleading completion claims.
- [ ] S161 — Organize: Separate generated files, experiment artifacts and authoritative source.
- [ ] S162 — Verify: Check performance, resource use and long-running stability.
- [ ] S163 — Verify: Repeat relevant end-to-end checks after structural changes.

## 16. Future operating environment

- [ ] S164 — Design: Define what an OS-like Synth should own.
- [ ] S165 — Design: Separate persistent intelligence from replaceable interfaces.
- [ ] S166 — Design: Define applications, devices and agents as native capabilities.
- [ ] S167 — Design: Define environment-wide resource, process and permission ownership.
- [ ] S168 — Explore: Compare an intelligent Windows layer with a dedicated environment/distribution.
- [ ] S169 — Prototype: Test selected architectural ideas without prematurely rebuilding the project.
- [ ] S170 — Evaluate: Select directions that improve continuity, capability and autonomy.

## 17. Companion interaction overlay

- [ ] S171 — Design: Define a non-intrusive floating Companion for interaction while the owner uses the computer for other work.
- [ ] S172 — Build: Support voice and text interaction in the Companion without a persistent chat history, task list or dashboard.
- [ ] S173 — Build: Show captions that track Synth's spoken output in real time, including the words currently being spoken. Keep the visual surface to a cute robot avatar and caption; do not show explanatory text, controls or status clutter.
- [ ] S174 — Build: Use presence/voice state to communicate idle, listening, thinking, speaking and unavailable without distracting from the owner's work.
- [ ] S175 — Build: Keep the Companion movable, interruptible and compatible with the owner's current task focus, including relevant window click-through behavior.
- [ ] S176 — Build: Represent permission requests or urgent outcomes through the Companion while routing detailed task and evidence inspection to the Control Center.
- [ ] S177 — Verify: Test the packaged Companion window lifetime, captions, input, speech output, drag, mic permissions and interruption in a synthetic profile.

## 18. Reasoning-processor architecture (owner clarification, 2026-11-09)

Reference: [SYNTH_REASONING_ARCHITECTURE.md](SYNTH_REASONING_ARCHITECTURE.md).
The owner-set principle is that **Synth is the persistent computing system and
the LLM is its reasoning processor (CPU analogy)**. Synth controls scheduling,
context, memory, goals, authority, effects and evidence, independently of any
particular model invocation. The analogy is conceptual, not a mandate to
implement an OS kernel or to keep model inference continuously active.
Items below remain open; writing this design does not demonstrate acceptance
or implementation. Avoid duplicating work already covered by S051-S063
(orchestration), S068-S075 (authority), S076-S086 (initiative), and S087-S096
(memory).

- [ ] S178 — Design: Specify a versioned, model-independent reasoning-request/result contract, explicitly distinguishing reasoning, state transitions, permission and evidence (supports S001, S002, S051).
- [ ] S179 — Design/Build: Define the context-working-set contract for task/goal/session scope, provenance, freshness, uncertainty and cancellation; verify isolation using actual model-message construction (supports S014, S088-S095).
- [ ] S180 — Design: Define an event-driven reasoning dispatcher that can select direct procedure, model invocation, waiting or idle without busy model calls (supports S039-S046, S076-S086).
- [ ] S181 — Build: Make work identity and commitments survive model timeout/provider change, conversation disconnect and runtime restart without duplicate consequential effects (supports S042, S051-S063).
- [ ] S182 — Verify: Prove Assistant/direct-task/goal entry points preserve the same trust, policy, evidence and recovery invariants when reasoning results hand off to execution (supports S018, S051-S063, S068-S075).
- [ ] S183 — Evaluate: Measure reasoning requests, latency/cost, unnecessary idle calls, context relevance and verified useful work; reject activity-only success measures (supports S086, S095, S142, S162).
- [ ] S184 — Recovery admission: Require separate owner-approved, ownership-fenced reconciliation before resuming a task with unresolved inference or uncertain external effects. Never treat an expired lease as proof an action did not occur (supports S068-S075, S181).
- [ ] S185 — Recovery verification: Execute the goal preflight and resume-boundary regressions against the real SQLite/TaskOwnership schema, restart/process-kill scenarios, and Windows runtime; prove no unverified effect replay (supports S181-S182).
- [ ] S186 — Review/reconciliation UX: Provide an authenticated, evidence-backed workflow to resolve blocked resumes after uncertain reasoning, active/expired ownership or prior mutation, without treating a status flag as approval or automatically replaying effects (supports S068-S075, S184-S185).
- [ ] S187 — Recovery review authorization boundary: Establish verified profile/task/session authority and authenticated owner review before displaying detailed uncertainty evidence or writing reconciliations. The existing uncertainty-inspection foundation intentionally returns no data without this boundary (supports S068-S075, S184-S186).
- [ ] S188 — Frontend recovery acceptance: Prove all task-resume entry points (Assistant, Security, Dashboard and Task drawer) are guarded by fresh preflight, rendered blockers are non-authorizing, and the installed Windows UI never mistakes resume for consequential approval; run full TypeScript/Vitest and packaging tests (supports S184-S187).
- [ ] S189 — Native human-recovery attestation: Define and implement a separate trusted OS/Windows principal and explicit human-presence verification boundary; bind any eventual audited reconciliation to a fresh, ownership-fenced evidence revision and independent external-effect evidence. Control tokens, UI flags, model statements and snapshot hashes are never consent (supports S184-S188).
- [ ] S190 — Recovery evidence integrity acceptance: Verify real task-event sequencing and corrupt/orphaned reasoning receipt containment through writer-locked transitions, API endpoints and crash/restart; reconcile goal-level status reporting with the authoritative resume gate (supports S181-S185, S189).
- [ ] S191 — Native recovery host integration: Implement and Windows-verify the HWND-owned Windows Hello prompt, trusted per-launch host/backend attestation-key channel, task-scoped challenge issuing, and transactional revision revalidation. A host-message proof alone cannot approve effects or resume tasks (supports S187-S190).
- [ ] S192 — Attested recovery acceptance: Test real OS success/cancel/unavailable, challenge replay/expiry, stale task or owner fence, missing/compromised host channel, process restart, and concurrent task changes under the true Windows/Tauri/sidecar trust boundary; preserve fail-closed default (supports S185, S189-S191).
- [ ] S193 — Goal actionability: Show truthful next work/blocked dependencies/backoff from persisted plan state, and ensure GET/scheduler eligibility queries do not commit readiness changes; selection and execution must agree (supports S014, S051-S063, S178-S183).
- [ ] S194 — Goal end-to-end verification: Run full ORM/API/React/Vitest/Windows goal lifecycle, repeated selection, dependency unlock, lease-protected step starts, verified outcomes and honest progress display, including concurrent changes and damaged plans (supports S182, S193).
- [ ] S195 — Goal Execution Vertical Slice V1 acceptance: Run the 25 parameter-expanded, REAL TaskRunner/Planner/SQLite integration cases in a full Sythion checkout and fix any failures before claiming end-to-end success; ensure exactly-once plan-step completion across separate sessions, failure accounting, and no nonterminal continuation (supports S182-S194).
- [ ] S196 — Real-tool vertical slice acceptance: Extend V1 with approved disposable workspace tool-adapter execution and independent artifact validation, budget/permission/ownership enforcement, and process-kill/restart tests. Scripted worker tests do not establish real-tool or native Windows correctness (supports S185, S194-S195).
- [ ] S197 — Default goal-worker binding acceptance: Run full-backend tests verifying explicit/background goals pass the registered ToolExecutor into the real kernel, scripted worker tests remain isolated, unavailable executor starts no tasks, and file-backed two-connection SQLite observations agree. Confirm with the actual installed application (supports S194-S196).
- [ ] S198 — Worker-handoff and real read-only tool acceptance: Run the new TaskRunner worker/recorder exception regressions and real ToolExecutor FileRead/FileHash disposable-workspace tests; verify cancelled work propagates, unknown outcomes do not retry, outside-root access and unauthorized writes remain blocked, and report only independently checked artifacts (supports S195-S197).
- [ ] S199 — Goal restart no-replay acceptance: Run process-kill and new-session SQLite tests to verify interrupted/cancelled/missing tasks stay unresolved, no new goal task or retry is created, repeated startup is idempotent, and terminal verified outcomes remain reconciled (supports S184-S198).
- [ ] S200 — Multi-session verified progress acceptance: Execute the three-step goal across two independent SQLite connections and an actual process restart; verify persisted evidence, exactly-once step events and UI recovery blockers, then run the installed Windows app (supports S194-S199).
- [ ] S201 — Failed-task effect-aware retry acceptance: Run real ORM/TaskRunner tests for uncertain, mutating, incomplete-reasoning and safe read-only failures; validate explicit/background retry stops and Windows process-restart behavior (supports S195-S200).
- [ ] S202 — Unique goal outcome evidence acceptance: In real ORM/TaskRunner/Windows tests, reject foreign goal tasks, superseded plans, mismatched task links and duplicate evidence bindings before any terminal step event or next-work execution (supports S195-S201).
- [ ] S203 — Active-plan completion and mandatory kernel verifier acceptance: Run real ORM tests for replan completion isolation, missing/duplicate active plan refusal, and optional callback unable to waive runtime action evidence. Verify full Windows goal flow (supports S195-S202).
- [ ] S204 — Terminal goal restart binding acceptance: Run real ORM/TaskRunner and process-kill tests proving superseded plans, duplicate evidence tasks and multiple active plans cannot reconcile terminal success/failure or create retries; verify read-only repeated startup and goal UI review consistency (supports S195-S203).
- [ ] S205 — Reproducible goal integration acceptance: Run scripts/run_goal_acceptance.py on a clean Windows checkout with real ORM goal tests, actual process-kill tests, backend/frontend suites, archived commit-bound JSON evidence, and installed Tauri UI/manual resume checks. Do not close on isolated unit tests alone (supports S194-S204).
- [ ] S206 — Verify Windows Tauri backend parity refresh using the explicit isolated staging and retained rollback backup; confirm release payload source equality and installed Windows app acceptance without bundling secrets or stale services.

## Current status and next moves

Achieved (synthetic/focused evidence unless stated otherwise):

- Scoped memory retrieval, verified/confidence/relevance ranking
- Scoped wiring behind disabled continuity flag (typed access context, cache
  isolation, adapter seams; default paths unchanged)
- Narrow backend baseline re-verified after release-payload mirror sync:
  release payload + canonical drift 71/1 skipped; clean build/install 15/7;
  data dir 15; conversation memory 30; scoped memory 11; assistant turns 44;
  goals 46; wake 35/3; tools 27/3 — total 294 passed, 14 skipped, 0 failed.
- Full default backend run completed: 3743 passed / 45 skipped / 10 xfailed /
  1 failed — the only failure was the generated-mirror drift for
  `backend/core/auth.py`; after syncing the one file, the mirror test passes.
  The narrow confirmations remain green: release-payload 60 passed, verify
  mirror OK, and the previously-isolated marker suites are unaffected.
- Browser→API chain verified with synthetic profile; real-provider health and
  conversation/memory recall verified (blue keyboard, synth-prototype-check
  previously; keyboard recall this run); provider turn created and parked task
  blocked by permission at memory_store (by design).
- Focused suites: assistant/memory/wake 98; execution/E2E 50; daily tools 96/3;
  frontend 230; scoped memory 52.

Still the running live dev setup (designed for normal-user browser testing):
- Backend on 127.0.0.1:8000 with isolated dev identity `SYTHION_CONTROL_TOKEN=live-dev-token-instructions`, `SYTHION_DATA_DIR=…sythion-live-dev-restart`, development mode with 127.0.0.1:3001 trusted (explicit auth edit committed).
- Vite dev server on http://127.0.0.1:3001/ serving the Companion/Assistant app.
- 2026-11-09: full rendered onboarding completed: key accepted, `beam` model tested OK, text mode selected, #/assistant shows conversation, turn sent via Enter, Synth answered live from the configured provider.
- Dev caches/pids isolated; `.wite` none.
- 2026-11-09 (later pass), live browser-driven (tab served from 127.0.0.1:3001,
  backend on 127.0.0.1:8000, live provider `beam`):
  - User-granted `C:\Sythion\sythion` as a persisted file root via the actual
    Settings UI ("Add file grant" + "Save settings"); `GET /settings` confirmed
    `persisted_file_roots=["C:\\Sythion\\sythion"]` and
    `effective_file_roots` updated immediately, no restart.
  - The parked README background task was **unclassifiable** by design:
    the model reached `request_user_input`; `pending_action` stayed NULL;
    the typed chat reply failed closed with TaskInstructionRevision.CHANGED
    ("That task changed or stopped…"). The wait cannot be answered in chat
    (test_model_questions_are_unknown_waits_and_inference_excluded).
  - Fresh request completed end-to-end in the browser:
    `Read C:\Sythion\sythion\README.md…` → task `4112e63f-5255-4e4e-9832-d2eabbdea661`
    `state=completed`; answer matched the on-disk file: title
    "Sythion (Synth) - Trusted Runtime Core for a Larger Intelligence System",
    first paragraph "This repository is the trusted local runtime core of a
    larger intelligence system. It is not the whole intelligence stack by itself."
  - Stale parked task `945ef86e-cb05-4bd3-92d4-6bc5384dd961` interrupted from the
    Operations UI (`#/overview` → task → Interrupt task); API confirms
    `state=cancelled`, `terminal_reason=cancelled_by_user`.
  - The Assistant UI renders background tasks as linked pairs appended to the
    main thread (request + final result), e.g. the cancelled pair shows
    "This message was cancelled." — noisy but truthful; not a data-pairing bug.
  - Environment note: machine-level `SYTHION_FILE_ROOTS` in this live profile
    points at `C:/Users/Administrator/{Documents,Downloads,Desktop}` while the
    session user is `orcha` — stale/wrong-user inherited env values; the
    per-profile persisted grant (`C:\Sythion\sythion`) is independent of them.
  - System note: model hallucinated `C:\Ai\sythion\README.md` after `file_search`
    matched that string in repo test fixtures
    (`backend/tests/test_phase14a_action_classification.py`); observed, not fixed
    (model-behavior issue, not a code defect), and the safe boundary held.

Tool inventory (63 registered, live `GET /api/v1/tools`; proven = executed
end-to-end by a provider turn in a live/isolated profile, unproven = registered
but never executed by the provider):
- Proven this session: system_time, system_battery, system_file_roots,
  file_list, file_read, file_search, file_hash, system_disk,
  system_processes, system_active_window, window_list, network_status,
  app_list, package_search, package_list, memory_stats, memory_recall,
  memory_export, memory_recall_related, activity_recall, system_capabilities,
  device_status, objective_get_plan, browser_tabs — i.e. every guarded-visible
  read-only tool has been executed successfully by the live provider at least
  once (file_read/file_hash/README round trip verified against disk;
  injection available/relevant proven; memory persisted across a real
  restart). The browser bridge is live: the provider enumerated 2 real tabs
  across Chrome and Brave ("Mentra Project Context" active). browser_read_page
  and browser_interact are routine-visible but were NOT exercised: the active
  tab is the owner's private browser context, and sending its content to the
  cloud provider is beyond the mandate — decision deferred to the owner.
- Exercised but correctly blocked by the permission/consent boundary:
  objective_create, memory_store (parked at approval; by design).
- Unproven live by deliberate design constraint — browser bridge content
  tools and all mutation class (unreachable without owner decision; see the
  consent boundary blocker below):
  browser_read_page, browser_interact, browser_switch_tab, browser_open_tab,
  browser_close_tab, browser_screenshot, brightness_control,
  clipboard_read, file_create, file_write, file_move, file_copy, file_delete,
  memory_store (direct), memory_forget, memory_link, objective_add_step,
  objective_complete_step, objective_update_summary, system_volume,
  system_power, system_power_control, app_launch, app_focus, app_close,
  gui_observe, gui_focus, gui_click, gui_type, gui_hotkey, gui_paste,
  gui_invoke, gui_set_value, gui_screenshot, gui_batch_form, package_install,
  package_uninstall, tool_propose (review-only tool authoring; deliberately
  not polluted with test proposals).

Still blocked/not done:
- **Owner decision required (unblocks the whole app-internal/consequential
  mutation class):** this web dev runtime has no permission-consent path at
  all (no HTTP endpoint, no UI Allow/Deny control); every mutating or
  app-internal tool classifies `Importance.CONSEQUENTIAL` and is denied
  without out-of-band trusted consent. In the packaged app either the Tauri
  host produces consent receipts or no mutating tool can ever execute.
  Decision needed: (a) approve that the packaged host is the consent
  boundary and accept web-runtime non-execution as correct, or (b) approve
  adding an explicit consent UX (per-request or per-category, never silent
  auto-approve) plus its authenticated receipt path. Until then,
  `file_create/file_write/file_*`, `memory_store` (direct), `objective_create`,
  `app_launch`, `package_*`, GUI and browser-mutation tools are permanently
  unverifiable here.
- **Backend wedge after budget-failed task** (process alive, listener gone
  silently; no log trace; manual restart recovered). Not yet reproduced or
  root-caused — a real runtime-behavior gap to investigate (native tool
  paths vs. background-task failure handling).
- Provider reliability: repeated "empty response"/formatted-answer flakes
  and Ollama Cloud 503s; simple retries recover, tasks get honestly failed.
- Voice mic/speaker: not selected by user, remains untested.
- A live provider round trip succeeded in the isolated `sythion-e2e-inventory`
  profile: background system_time task completed and persisted across sidecar
  restart; memory entry `verification_word=flintstone-771` (USER scope,
  source USER_CONVERSATION, confidence 0.9) was created by post-task
  consolidation, persisted across restart, and recalled by a fresh provider
  turn after a restart ("flintstone-771"); transient 503/parse failure
  occurred on one turn then recovered on the next. `MEMORY_REPORT` showed
  the entry and injection counters present; memory lookup still UNVERIFIED
  quality-wise but functionally present in browser-adjacent runtime.
- Voice mic/speaker: not selected by user, remains untested.
- Direct live coverage of the remaining tools above (browser bridge, GUI, app
  launch, package install/uninstall, file mutations, objective planner tools)
  is unproven; the only proven mutating-boundary items are the fail-closed
  denials (memory_store, objective_create) which behaved as designed.
- `/setup` key UI uses a browser click-through; polish/persistence to be verified on next live session.
- Local intermittent flakiness in `test_phase95_hardening::TestAdmissionAtomicity` remains uncorrelated with this session.
- Full default backend marker-isolated suite rerun after mirror sync: **3744 passed, 45 skipped, 10 xfailed, 0 failed**.

## Next moves (in order):

1. Commit current coherent batch, rerun the continuity/E2E evidence after save.
2. Wire continuity-kernel memory access and entry adapters one at a time behind
   disabled default.
3. Verify baseline with exact-file batches and quarantine intermittent failure.
4. Prove goal-backed delegation and recall through real runtime.
5. Rebuild/verify staged payload provenance.

Every future report must preserve this achieved/remaining/next sequence rather
than restart from vague claims.

No implementation is activated by creating this checklist. Populate before
starting each bounded work unit; reference earlier investigation only with its
limits (source, synthetic, provider, installed-runtime).

| IDs | Status | Scope / acceptance | Dependencies | Owner / files | Evidence / blocker |
| --- | --- | --- | --- | --- | --- |
| S001–S004, S160 | researching | Architecture research and documentation audit; acceptance requires a reviewable target model, not agent consensus | S005–S006 baseline | Coordinator; `SYNTH_PROJECT_UNDERSTANDING.md`, `README.md` | Agent research exposed entity ambiguities and README/dangling-link issues; no design accepted |
| S005–S006 | researching | Baseline and version reconciliation; acceptance requires exact current counts and environment evidence | none | Coordinator/verifiers; `pytest.ini`, CI, frontend configs | Targeted setup/data-dir tests passed; broad collection timed out at 120s; local Python 3.14/Node 24 differ from CI 3.11/Node 20; packaged prototype/runtime remains not verified |
| S039–S050, S087–S096, S024–S038 | implementing | Durable wake reconciliation, scoped relevance memory, and Assistant→task→evidence verification work units | none | Coordinator agents; exact file ownership in delegated briefs | Three agents running; targeted tests required, no broad suite reruns |
| — | queued | Select next bounded unit after baseline and dependency review | — | Coordinator | Initial checklist recorded; no items accepted as done |

## Live dev pass 4 — normal-user interaction sweep, five bugs fixed (2026-11-09)

Driven exclusively through the browser at `http://127.0.0.1:3001` (backend on
:8000 after a controlled restart to pick up source fixes; provider `beam`).
Natural-language user turns were sent as a normal user, and every screen was
visited. Findings, fixes and verification, oldest to newest:

1. **Casual chat failed dead-end for any established session.** The ordinary-
   prose safety path ("I could not produce a properly formatted answer this
   time…") fired for *any* turn whose model output wasn't the JSON contract
   when the session already contained task-context descriptors — i.e. every
   session that ever ran a background task. Small talk failed ~100% of the
   time ("Hi! I'm brand new here…"). Fixed in
   `backend/services/conversation_turn_engine.py`: the answer-only prose
   fallback now also applies when every descriptor is `task_context` (inert
   semantic-reference data — dispatches nothing, answers nothing).
   Clarification/permission descriptors stay strictly non-prose. Live
   verification: same message now returns the model's real answer (with the
   explicit "No background task … informative only" disclosure appended).
   Unit tests added (context-only accepted, mixed clarification stays strict);
   `test_conversation_turn_engine.py` 76/76, assistant-turn and wait-kind
   suites 90/90 remain green.
2. **System health permanently DEGRADED.** `run_health_checks` rolled ANY
   non-HEALTHY statuses into the aggregate — including the provider check
   that is *deliberately* UNKNOWN when the remote probe is disabled
   (opt-in, never a failure). The UI then showed "DEGRADED 6 checks · 1 not
   healthy" forever. Fixed: deliberate disabled checks report `disabled`,
   stay visible as UNKNOWN in the evidence list, and are excluded from the
   rollup; System & Integrations "Health" stat now buckets checks
   ("6 checks · 5 healthy · 1 unknown"). Backend health tests green;
   API re-checked after restart: overall HEALTHY.
3. **Raw JSON evidence rendered to normal users.** The Health checks panel
   printed `JSON.stringify(evidence).slice(0,160)` — raw machine output in a
   user-facing list. Now humanized key/value summaries (nested objects as
   `k=v` lists; empty arrays as "none"). Verified live in browser.
4. **Demo screens claimed LIVE in the sticky topbar.** `#/life`, `#/devices`,
   `#/comms` carry "LIVE" in the topbar while their content is explicitly
   demonstration data (design badge inside the screen says DEMO DATA).
   TopBar now honors the route's declared `data` mode: demo routes show the
   Demo tag. Verified: Assistant/Settings stay LIVE; Life/Devices/Comms show
   DEMO DATA. Preview mode unchanged; TopBar test green.
5. **Operations "Attention" filled with routine completed work.** Every
   casual completed task (no goal to verify against) produced a
   "Completed, not goal-verified" Attention item forever — 7 items in a
   daily-use profile, contradicting the rail's own "Routine work produces
   nothing here" spec. Split: items still surface for goal-linked-unverified
   and for missing-final-result tasks; routine tasks with a recorded result
   stay quiet. Live verified: Attention rail now "2 items" (the two genuine
   failures). Frontend 232/232 (two new lib tests; the one encoding insight:
   "VERIFICATION MISSING RESULT" is still unreachable for goalless tasks, by
   design).

Plus:

6. **Emoji bytes are fine end-to-end.** Apparent mojibake was my PowerShell
   decoding, not the app: API returns U+1F984 intact and the provider
   answered "Dragon" to the emoji prompt. Cosmetic only.
7. **Composer lag after navigation:** after `WaitState=load` it can take
   ~1s for the composer to mount; the earlier "Composer missing" reading
   was sequencing, not a bug.
8. En-dash mojibake em dashes in console prints confirmed PowerShell-only;
   API payloads intact.
9. Operations drawer: open failed task → timeline (51 events) renders all
   events honestly; Escape closes the drawer. Confirmed.
10. Health API `GET /api/v1/agent/health` re-verified after restart:
    `overall HEALTHY`, provider UNKNOWN with `disabled: true`.
11. Frontend remains green: 232/232 (230 before); `npm run build` OK; vite
    mirrors — `src-tauri/resources/backend` synced for `system_state.py`
    (drift suite 65/3 green) and `conversation_turn_engine.py`.

## Live dev pass 2 — backend wedge discovered, injection proven, tool evidence logged (2026-11-09)

Live profile `sythion-live-dev-restart` (backend :8000, UI :3001, provider
`beam`). Exact outcomes; nothing here is inferred.

- **Backend wedge:** after the multi-tool bundle task `c385db99-3499-42f9-b2c3-3263fb36e027`
  terminated on `CLOUD_REQUEST_LIMIT_EXCEEDED` at 09:24:43, the HTTP listener
  silently stopped accepting connections (ECONNREFUSED) while the python
  process stayed alive (low CPU, lingering conhost child). No shutdown trace
  in out.log/err.log/sythion.log. Manual restart recovered the profile; no
  data loss (SQLite WAL intact). Cause unidentified — possible contributors:
  a sync subprocess/native path inside tools (window_list/app_list/package_search)
  blocking the event loop, or the background-task failure path. **Runtime
  reliability finding; not yet reproduced.**
- **Persistence after wedge/restart:** task row returned `failed`,
  `terminal_reason=Exceeded the 50-request per-task budget`; memory entries
  (verification_word, sythion_repo_summary), conversation turns and grants all
  survived; post-restart provider answer to "What is my verification word?" was
  exactly "Your verification word is flintstone-771."
- **Live memory injection in task context is now proven (≥ available=1, relevant≥1):**
  log lines show `Memory injection … available, 0 links` and `2 relevant + 0
  linked of 2 available` for every decision of the bundle task and the bc80ab44
  system-time task; `/memory/report` per_run rows for bc80ab44 show
  `available=1 relevant=1`.
- **Tools executed successfully by the live provider** (as evidenced by
  `tool.executed success=true` lines; the task itself hit the request budget and
  failed honestly by repeating some calls): `app_list`, `activity_recall`,
  `system_disk`, `system_processes`, `network_status`, `file_hash`,
  `package_search`, `memory_stats`, `memory_recall`, `system_active_window`,
  `window_list`. Also proven this session: `system_time`, `system_battery`,
  `system_file_roots`, `file_list`, `file_read`, `file_search` (completed
  task that answered the README title/first paragraph), memory consolidation
  into 2 entries (replace + create). `package_list` and `device_status`,
  `objective_get_plan`, `memory_export`, `memory_recall_related` remain
  untested by a provider turn.
- **Consequential/permission boundary makes these permanently unprovable in
  this web dev runtime — by design:** every mutating or app-internal tool
  (`file_create`, `file_write`, `file_delete`, `file_move`, `file_copy`,
  `memory_store` (direct), `objective_create`, `objective_add_step`,
  `objective_complete_step`, `objective_update_summary`, `app_launch`,
  `app_focus`, `app_close`, `package_install`, `package_uninstall`, all
  `gui_*`, `browser_open_tab`, `browser_close_tab`, `browser_switch_tab`,
  `browser_interact`, `system_power_control`, `brightness_control`,
  `system_volume`, `clipboard_read`, `browser_screenshot`, package tools)
  classifies as `Importance.CONSEQUENTIAL` — environment/`SYTHION_AUTONOMY_LEVEL`
  is explicitly NOT authenticated consent (fail-closed by design), and the
  dev HTTP API and the UI have **no permission-decision endpoint or
  Allow/Deny control at all**. Parked examples observed live:
  `objective_create` and `memory_store` tasks hold `waiting_for_user`
  forever. Consequence: in the packaged runtime, either the trusted host
  produces consent receipts (verify in the Tauri host) or no mutating tool
  can ever execute. **Owner decision required** (see "Still blocked/not done").
- **Two provider flakes observed, auto-recovered:** an
  "informative only — I could not produce a properly formatted answer" turn
  (retried, succeeded) and two "Model returned empty response" attempts
  within one task; plus earlier Ollama Cloud 503s. Provider reliability
  remains the operational risk; turns against Beam recover on retry.
- **The `file_create` attempt failed NOT because of roots** — `system_capabilities`
  is accurate: in `guarded` autonomy mode mutating non-`app_internal` tools are
  not model-visible at all (`file_create`/`file_write` flagged medium +
  `requires_confirmation`, correctly hidden). The model's conclusion was
  correct for the inventory it can see.



- 2026-11-09: Captured all 170 initial conversation items with stable IDs;
  established evidence-based completion and coordinator ownership.
- 2026-11-09: Recorded the first research status update: entity architecture,
  baseline/version review, and README/documentation alignment entered
  "researching" without any item being accepted as done.
- 2026-11-09: Corrected project-understanding notes on the generated backend
  mirror, legacy quarantined voice module, and stale README wording.

- 2026-11-09: Live dev pass 2 recorded exact evidence: backend wedge after a 50-request-budget-failed bundle task (process retained, listener silently dropped; manual restart recovered; persistence verified), live memory injection in task context proven (available/relevant >= 1), per-tool provider execution evidence for 19 read-only tools, the CONSEQUENTIAL/consent boundary (no web-runtime consent path for mutating or app-internal tools; owner decision recorded), and two auto-recovered provider flakes.
- 2026-11-09: Live dev pass 3 — fixed and committed Memory screen inconsistencies found during normal-user browser testing (STORED ITEMS and CONTEXT PER RUN always rendered "-" because the screen read demo-only report fields while the live /memory/report contract is counts/relation_vocabulary/memory_context_injection{runs,totals,per_run}; now reads both shapes with fallback; Stores tab derives from counts and renders a curated injection panel, no more "[object Object]" garbage). Frontend 230/230, vite build ok, verified live (Stored 2, Context per run 2.0).
- 2026-11-09: Wedge timeline established from the Vite dev-server proxy log: healthy until 09:23:19 local; first failure `read ECONNRESET /api/v1/agent`, then all subsequent connections refused while the process and background tasks kept running (bundle task c385db99 finished its budget-fail path at 09:24:43). The listening socket closed inside the process; nothing in the app closes it deliberately. A cheap forced-budget repro (SYTHION_MAX_CLOUD_REQUESTS_PER_TASK=2, isolated profile) did NOT wedge the listener. Cause remains unidentified; keep this as an open runtime finding.
- 2026-11-09: Live dev pass 4 — normal-user browser sweep found and fixed five defects, all verified with evidence in the browser: (1) casual chat returned the "could not produce a properly formatted answer" dead-end for every established session (ordinary-prose fallback now allowed with context-only task descriptors; unit+integration suites green); (2) System health permanently showed DEGRADED because the deliberately-disabled provider probe counted as a failure — disabled checks are now excluded from the rollup (API: overall HEALTHY, provider still shown UNKNOWN disabled); (3) Health-checks rows dumped raw JSON to normal users — now humanized key/value summaries; (4) Life/Devices/Communications showed a LIVE sticky topbar badge while serving demonstration data — demo routes now show DEMO DATA; (5) Operations "Attention" rail filled with routine completed non-goal tasks — routine results now produce nothing, goal-linked-unverified and missing-result cases still surface (frontend 232/232).

- 2026-11-09: Recorded the owner's CPU analogy as a system-level architectural principle, added the reasoning-processor design reference, and appended open architecture acceptance items S178-S183. Documentation-only; no product code or test results changed.

- 2026-11-09: First implementation slice of the LLM-as-reasoning-processor architecture: introduced `backend/services/reasoning_dispatch.py` (immutable bounded model-call job metadata, shared one-shot dispatch) and routed Assistant-turn and task-decision model calls through it. Existing runtime remains the owner of validation, authorization, execution and persistence. Verification performed in this conversation's isolated Python environment: `python -m pytest backend/tests/test_reasoning_dispatch.py -q` — 20 passed; `python -m compileall -q` for new files succeeded. The full private repository was not available to the local test runner, so integration/full-suite tests and Windows packaged behavior are **not** claimed; no GitHub Actions were invoked. S178-S183 remain open pending actual integration acceptance.

- 2026-11-09: Added the first bounded event-driven *attention* integration, keeping the existing MainAgent wake policy, durable wake deduplication, RunnableGoalSelector fairness/backoff, task leases, executor and evidence gates as the authorities. The orchestrator now obtains an immutable RUN_GOAL/IDLE decision under its existing lease and skips model/tool work when no goal is runnable. Also removed duplicate release_lease calls in its idle and eligibility-race paths (the finally block remains the single release). Local isolated verification: 40/40 focused reasoning + attention unit tests passed; Python compilation of the three new Python files passed. Added four repository integration regressions for idle, eligibility race, unavailable lease, and invalid goal references; these could not be executed here without the full private checkout. No GitHub Actions were invoked manually; this does not prove end-to-end autonomous scheduling. S180 remains partial and S178-S183 remain open.

- 2026-11-09: **Reasoning continuity slice (partial S181):** task-decision provider invocations now emit privacy-minimal, per-task durable receipts (`reasoning.started`, `reasoning.response_received`, `reasoning.timed_out`, `reasoning.failed`, `reasoning.cancelled`) through the existing TaskRunner task-event ledger. Writes require active task ownership/fencing and RUNNING state; unauthorized, malformed, or caller-transaction-overlapping writes are refused. Model output, prompts, credentials, reasoning traces, and approval claims are never included. A failed mandatory receipt is non-repairable and blocks adopting the untracked provider result; cancellations still propagate. The read-only `unresolved_reasoning_calls` recovery view reports missing terminal receipts as *uncertain inference*, not permission to repeat tools or evidence of completion. Live Assistant chat calls remain non-task and are not journaled as work. **Local verification:** 55 focused unit + standalone SQLite persistence tests passed; new Python files compiled. Seven real TaskRunner/SQLite integration tests were added but **not executed** because this environment lacks a full private repo checkout; no full-suite, installed Windows, or live-provider validation is claimed. Commit uses `[skip ci]` to avoid GitHub-hosted Actions while credits are unavailable. No background autonomy setting or action policy changed.

- 2026-11-09: **Goal-level reasoning continuity view (S181/S182 partial):** added read-only `GET /api/v1/goals/{goal_id}/continuity?limit=50` (1-100) plus `goal_reasoning_continuity.py`. The report projects only committed reasoning-call receipts and current persisted task states for tasks directly associated with the goal or explicitly bound as goal-step evidence; foreign-goal task IDs are excluded. It separates `not_recorded` legacy tasks, settled call receipts and unresolved receipts; prioritizes paused/terminal goals without auto-resume, and states when the task window is truncated. It does not fetch prompts/model output, invoke providers, run tools, mutate goal or task state, or authorize retry. **Locally executed**: 81 passing focused pure reasoning/attention/journal/projection tests and Python compilation, including new goal-level cases. **Added but unexecuted**: 10 full-ORM SQLite integration cases testing cross-goal isolation, plan evidence linkage, truncation and read-only behavior; full repository/Windows/API runtime not available here. This is observability, **not** automatic crash reconciliation or verified end-to-end autonomy. Changes committed with `[skip ci]` to avoid GitHub Actions minutes.

- 2026-11-09: **Read-only ownership-aware recovery preflight (S181 partial):** existing `GET /api/v1/goals/{goal_id}/continuity` now combines bounded committed task reasoning receipts with task-scoped ownership snapshots (ACTIVE/current vs expired; RELEASED/RECOVERED/EXPIRED; missing/indeterminate), task lifecycle state, and unresolved external-action flags. It exposes a `recovery_preflight` summary and per-task `recovery` classification, without disclosing task-owner nonces, worker identities or uncertain action IDs. A current lease means *possibly running*, not proven alive; an expired lease means *review required*, not proven dead. Historical missing receipts remain unknown, and a partial task window is explicitly marked incomplete. This slice **does not change the /tasks/{id}/resume route**, reclaim leases, auto-resume, schedule model work, or authorize/replay actions. Added follow-up acceptance items S184-S185. **Local Python tests:** 140 passed (120 focused pure tests and 20 SQLAlchemy/SQLite smoke tests using a temporary synthetic schema); compilation passed. Real-repository ORM, API and Windows installed-runtime tests remain unexecuted. `[skip ci]` avoids GitHub Actions usage.

- 2026-11-09: **Guarded explicit resume admission (S184 partial):** added `backend/services/resume_admission.py` and read-only `GET /api/v1/tasks/{task_id}/resume-preflight`. The existing `POST /api/v1/tasks/{task_id}/resume` now refuses ambiguous tasks *before* taking the runner lease. Crucially, `TaskRunner.transition_state` repeats admission under the runner's writer lock for **any** paused/interrupted→running transition, so a stale preview or a different event label cannot bypass the gate. The policy blocks unresolved/corrupted reasoning receipts, pending actions, uncertain external action evidence, prior `action.started` or potentially mutating `tool.executed` receipts (even when reported successful), and unreconciled ACTIVE/EXPIRED/RECOVERED ownership. Read-only receipts explicitly labeled `mutating:false`, or clean legacy tasks without such evidence, can pass preflight for an **explicit** resume, but still require the existing lease, ownership, action and permission gates. No auto-resume, force-recovery, approval minting, replay, or user-interface clearance flow was added. **Local verification:** 192 passing focused unit and synthetic-schema SQLite tests; compilation passed for all four new Python files. Added seven real TaskRunner/API SQLite regression tests, **not run** because the complete private checkout is not locally available. S184-S186 remain open for owner-reviewed reconciliation, real-repository integration and installed runtime proof. `[skip ci]` avoids GitHub Actions usage.

- 2026-11-09: **Sanitized recovery-review packet (partial S186, no clearance):** added `backend/services/recovery_review.py`, a deterministic fixed-template review guide for each known resume-preflight blocker, and included the `review` object in the existing read-only `GET /api/v1/tasks/{task_id}/resume-preflight` response. It returns only coarse task/lease/reasoning counts already available to the preflight plus evidence *source classes* and verification steps. It never echoes model/tool text, raw event data, action IDs, owner nonces, credentials, or untrusted reviewer claims. Unknown, contradictory and malformed input fails closed. All cases explicitly set `approved=false`, `authorization_issued=false`, `uncertainty_cleared=false`, `execution_started=false` and `automatic_replay_allowed=false` (including clean preflights). No approval, authenticated resolution, action replay, or UI workflow is added. The previous read-only `uncertainty_inspection.py` foundation remains deliberately fail-closed due to the unproven profile/session boundary; this work does **not** circumvent it or introduce raw introspection. Local isolated/synthetic-schema verification: **228 passed** (34 new pure packet tests and two new SQLite composition tests), Python compilation passed. Existing real-repository API/TaskRunner tests were extended with two review assertions, but could not be run without the full checkout; the Windows runtime and authorization integration remain unverified. S186/S187 remain open. Commit uses `[skip ci]` to avoid GitHub Actions minutes.

- 2026-11-09: **Guarded recovery UX (S186/S188 partial):** TaskDetailDrawer now fetches bounded `GET /tasks/{id}/resume-preflight` only for paused/interrupted tasks, renders the server's sanitized recovery-review guidance, and disables its Resume control when preflight is missing, loading, inconsistent, failed or review-required. The action rechecks immediately before request; `taskService.resume` also enforces a fresh, fail-closed preflight for all other UI paths (Assistant, Security, legacy Dashboard) before POST; the backend still performs writer-locked admission. The Security screen's previous **“Authorize”** label on ordinary task resume was corrected to **“Resume if safe”** with a non-approval notice. No uncertainty clearing, authenticated human approval, grant issuance or automatic replay exists. Changes: `frontend/src/lib/recoveryPresentation.ts`, `types/index.ts`, `services/api.ts`, `hooks/useBackend.ts`, `components/TaskDetailDrawer.tsx`, Security and Assistant copy, plus pure/frontend component/API regression tests. **Local execution:** 16 standalone TypeScript guard assertions and `tsc --strict` compilation for an extracted behavioral guard succeeded; the existing isolated backend/synthetic-SQLite harness was rerun: 228 passed. The new full React/Vitest suites and complete frontend typecheck were **not executed** because the full frontend checkout/dependencies were unavailable locally. S186-S188 remain open for authenticated review, full-suite tests and Windows-installed behavior. Commit uses `[skip ci]` to avoid GitHub Actions minutes.

- 2026-11-09: **Recovery evidence revision (partial S186/S187/S189, no clearance):** added `backend/services/recovery_review_binding.py` and read-only `review_binding` to `GET /api/v1/tasks/{task_id}/resume-preflight`. This SHA-256 snapshot *change detector* covers server-read task identity, version/state/update time, max task-event sequence, task-ownership version/fence/status/expiry, action-presence booleans and coarse preflight classification. It does **not** hash, return, or expose prompts, task text, tool output, action IDs, owner nonces, or credentials. The reader refuses uncommitted ORM mutations; inconsistent/stale preflights fail closed. All returned authority/clearance flags remain false. The task drawer optionally displays a short revision and evidence watermark, clearly marked as non-authorizing. **Local verification:** 246 passing focused backend tests, including 38 new pure+synthetic SQLite binding tests; source compiled successfully. Added real TaskRunner/API assertions and a frontend component test but **did not execute the complete private repository or React/Vitest/Windows suite**. The revision is *not* a signature, event-content integrity proof, authenticated human identity or permission to resume/replay; full owner verification and transactional attested reconciliation remain open S186-S189. GitHub commit uses `[skip ci]` to avoid Actions minutes.

- 2026-11-09: **Fail-closed reasoning receipt integrity (S184/S190 partial):** added `backend/services/reasoning_receipt_integrity.py` to audit durable reasoning-call history before manual resume. Unlike the prior unresolved-start count, this auditor rejects duplicate starts, reuse of settled call IDs, orphan/duplicate terminals, malformed or extra receipt metadata (including forged approval claims), invalid or out-of-order sequence numbers, and unknown `reasoning.*` kinds; gaps caused by unrelated task events remain legal. `read_task_resume_admission` reads all `reasoning.*` event types and includes each committed event sequence; a corrupt history is explicitly refused as `corrupt_reasoning_receipt_review_required`. Findings expose only coarse anomaly categories, not job IDs/raw event data. The existing writer-locked `TaskRunner.transition_state` and POST resume route inherit this gate. **Local execution:** 290 focused and synthetic-SQLite tests passed (36 new pure-auditor cases, eight new SQLite admission cases); Python compilation passed. Three real TaskRunner/API integration regressions were added but **not run** without the full private checkout; Windows/Tauri native human verification and audited clearance remain unimplemented. No new approval path, automatic restart, replay, model call or external side effect is introduced. Commit uses `[skip ci]` to avoid GitHub Actions minutes.

- 2026-11-09: **Native recovery presence challenge protocol (S189/S191 foundation, NOT an active verifier):** added `backend/services/recovery_native_challenge.py` (default-disabled, one-time, 256-bit random task+revision challenges with monotonic expiry, capacity and attempt limits, strict domain-separated host HMAC assertion check, fresh-revision rejection and no authority flags). The contract requires a distinct per-launch host/backend secret, not the exposed local control token. It verifies only *host-message integrity*, not independently authenticated human identity or external effect outcome. **No Tauri Windows Hello integration, key provisioning, API endpoint, task clearance, authorization, auto-resume or replay** was added. Added 39 pure offline challenge tests + 4 synthetic SQLite binding tests (fake native signer only); the selected isolated backend suite ran **353 passed** and Python compilation passed. Full private-repo, Rust/Tauri Windows, and real OS verification remain unexecuted; native authenticity and trust-channel security remain open S189-S192. Added `docs/RECOVERY_NATIVE_ATTESTATION_PROTOCOL.md` with explicit host, HWND, ACL, revision-fencing and effect-reconciliation prerequisites. Commit uses `[skip ci]` to avoid GitHub Actions.

- 2026-11-09: **Goal next-work preview + side-effect-free selection (S193 implementation; integration unverified):** added `backend/services/goal_work_preview.py`, read-only `GET /api/v1/goals/{goal_id}/next-work`, and a new "Next work" panel in `GoalDetailDrawer`. The deterministic projection reports the next eligible step, virtual readiness when BLOCKED dependencies are complete, in-progress/failed/backoff states and unmet dependency keys; malformed/unknown/cyclic plan dependencies fail closed. `RunnableGoalSelector` now uses the same preview rather than calling `Planner.refresh_readiness` during a GET/selection query (which previously could commit `plan_step.ready` audit events). `Planner.select_next_step` uses the same dependency-safe projection and only persists readiness when a leased execution path actually selects the blocked step. The preview **never executes a tool, invokes an LLM, starts a task, approves an action, or claims the goal completed**. Local verification: 22 pure + four disposable-schema SQLite checks **26 passed**, Python compilation passed. Added seven real-ORM selector/planner tests, three API tests, and two frontend Axios contract tests but **could not execute** the full repository/React/Vitest/Windows suite in this environment. Existing recovery and approval policy unchanged; Windows Hello/recovery work intentionally paused. See S194 for end-to-end acceptance. Commit uses `[skip ci]` to conserve Actions credits.

- 2026-11-09: **Goal Execution Vertical Slice V1 authored, integration NOT yet executed (S195):** introduced `backend/services/goal_execution_outcome.py` and 24 locally passing pure unit tests. In `TaskOrchestrator`, the old `already_applied` label is replaced by canonical evidence adjudication: refresh goal, step and task from persisted ORM state after a worker returns; require matching `evidence_task_id` and a clean kernel `CompletionVerifier` before reporting `completed`; distinguish pre-applied `failed` outcomes from success; stop explicit/background continuation with `outcome_unverified` for missing, nonterminal, stale or contradictory evidence. `apply_task_outcome` now defers instead of manufacturing step failure from a nonterminal task. Background service accounting does not treat unverified work as terminal failure or success. Removed double lease release from the explicit no-work path. Added 11 full-ORM scripted-worker tests in `backend/tests/test_goal_execution_vertical_slice.py`: a three-dependent-step temp-file goal, independently checked report, newly opened SQLite session, already-applied success and failure, middle failure and retry exhaustion, interrupted/nonterminal stop, background accounting, unverified completion criteria, broken evidence link, single lease release and separate worker ORM session. Updated existing regression expectations formerly interpreting `already_applied` as a success category. **Locally executed:** 24/24 pure unit tests passed and compiled; **NOT executed here:** 11 real-ORM integration tests, full backend suite, real ToolExecutor, live model, Tauri/Windows application. Repository is private and GitHub DNS is unavailable from the container, so do not treat tests added as tests passed. See `docs/GOAL_EXECUTION_VERTICAL_SLICE_V1.md`. Commit uses `[skip ci]` to avoid Actions minutes.

- 2026-11-09: **Default goal-worker tool binding fixed; acceptance still open (S195-S197):** discovered that both goal execution loops previously called the real `run_task` with `tool_executor=None`. Added `backend/services/goal_worker_binding.py`, now resolving the application's existing `get_tool_executor` provider before any task starts, and passing the registered executor to production goal workers; explicit injected scripted workers retain a null test-only tool argument and bypass provider resolution. Missing/failed provider returns `tool_executor_unavailable` with zero executions and no task. No permission or tool policy changed. **Executed in a real local Python runtime:** 15/15 isolated binding tests passed and module/test source compiled. Added 6 full-repo executor-forwarding/unavailable-path cases in the V1 suite (**17 parameter-expanded vertical-slice cases total**) and upgraded the separate-worker-session scenario from in-memory StaticPool to file-backed SQLite with distinct connections. Full real-ORM V1 tests, production tool invocation, full backend suite and Windows packaging **could not be executed** in this container (no full private checkout; GitHub DNS blocked). See `docs/GOAL_EXECUTION_VERTICAL_SLICE_V1.md`. Committed with `[skip ci]` to avoid Actions credit usage.

- 2026-11-09: **Goal worker failure containment + real read-only tool acceptance (S195-S198 partial):** added `backend/services/goal_worker_handoff.py` and wired explicit/background cycles to stop with `worker_or_outcome_error` and an `unverified` step when a real worker or outcome recorder raises; no automatic retries or fabricated terminal state. Background cycles give a fairness slot but do not record a terminal failed session. The helper logs only the exception type (never error text/tool payloads) and propagates `asyncio.CancelledError`. Added 14 isolated handoff tests, all locally passing; combined with the previous 15 worker-binding tests **29 passed**. Added four real-ORM exception/cancellation cases and one real read-only-tool three-step case to `backend/tests/test_goal_execution_vertical_slice.py` (**22 parameter-expanded V1 cases**); added five ToolExecutor/FileRead/FileHash/blocked-FileCreate temporary-workspace tests in `backend/tests/test_goal_readonly_tool_adapter.py`. The latter **10 acceptance cases were not executed** here: full private repository unavailable in the container. No live LLM, Windows app, real file mutation approval, verified cross-process recovery or background-autonomy default change. See `docs/GOAL_EXECUTION_VERTICAL_SLICE_V1.md`. Commit uses `[skip ci]`.

- 2026-11-09: **Goal Restart V1 no-replay fix (S199/S200 partial):** discovered startup `recover_step` requeued INTERRUPTED/CANCELLED plan steps as READY and missing-task steps as PENDING, bypassing the old task's conservative resume gate by permitting a brand-new goal task. Added `backend/services/goal_restart_policy.py` to classify terminal tasks for existing verifier-driven reconciliation while keeping missing/interrupted/cancelled/nonterminal tasks RUNNING and deferred; no implicit retry, event append, new task or permission. Added exact evidence-task/linked-task and foreign-goal guards in terminal recovery and removed raw exception text from startup recovery result/logging. `project_goal_work` now withholds all new eligible steps while any step is RUNNING, including independent steps; `execute_session` blocks retry on unresolved/invalid plans; read-only goal preview displays `recovery_review_required` for interrupted, cancelled, unlinked, missing or foreign evidence without raw task data. Local isolated Python tests **42 passed**, including **13 new pure restart-policy cases**, with compileall. Updated true process-kill, isolated file-backed SQLite, ORM scheduler and goal preview assertions; added three-work-step across two physical DB connections and mixed-status no-retry regressions. Those full-ORM/OS tests are **not executed** in the partial local checkout. Recovery/consent permissions unchanged; details in `docs/GOAL_RESTART_NO_REPLAY_V1.md`. Commit uses `[skip ci]` to avoid GitHub Actions.

- 2026-11-09: **Failed-task effect-aware retry preflight (S201 partial):** new read-only retry evidence gate inspects task ownership, pending/active action, mutating/uncertain receipts and reasoning integrity before failed goal retries. Explicit/background review-required stops added. Local 24 pure tests passed and source compiled; six real ORM/TaskRunner regression cases added but not executed. No full Windows/process-kill run, policy relaxation or GitHub Actions invocation. Commit uses `[skip ci]`.

- 2026-11-09: **Unique active-plan goal-task binding (S202 implementation only):** added pure goal_execution_binding check shared by Planner direct completion, TaskOrchestrator outcome application and post-worker observation. Duplicated/foreign/stale evidence is unverified, never completion. 19 standalone pure tests passed locally (43 together with existing retry tests), but four new real ORM outcome checks and full Windows/process-kill acceptance were not run in the partial checkout. `[skip ci]` avoids Actions usage.

- 2026-11-09: **S203 partial**: Planner and GoalManager completion now scope to one active plan; direct Planner completion always calls kernel verifier before optional caller veto. 23 new pure tests passed (66 focused combined), source compilation succeeded. Added seven full ORM tests but not run; Windows/process-kill acceptance pending. Commit uses [skip ci].

- 2026-11-09: **S204 partial terminal-recovery binding:** requires one active plan, exact goal/task associations and single evidence-task reference before reconciling terminal goal steps. Fixed stale no-replay assertion; 69 locally passing focused pure tests, Python compilation succeeded, and 7 full-ORM parameter-expanded test cases authored but NOT executed in this partial checkout. Windows/process-kill acceptance and full suite remain open. Commit uses [skip ci].

- 2026-11-09: **S205 test harness authored, acceptance NOT run:** added `scripts/run_goal_acceptance.py` with opt-in Windows process kill/full backend/frontend matrix and fail-closed commit-bound JSON evidence, `backend/tests/test_goal_acceptance_runner.py` parser regressions and docs runbook. Local partial checkout executed 10 new runner checks plus 69 focused policy checks = 79 passing, and compiled the new script/tests. Complete ORM, actual Windows process-kill and installed Tauri acceptance remain unexecuted; no GitHub Actions invoked. Commit uses [skip ci].

- 2026-11-10: **S205 Windows newline test repair (revalidation pending):** owner-provided focused-suite evidence shows 220 passed / 2 failed on Windows, both from comparing FileReadTool's normalized text to raw CRLF bytes. Fixed two test assertions without weakening raw SHA-256/byte-count checks, added LF/CRLF/CR regression parameters, and updated the acceptance runbook. Full Windows rerun and process-kill/backend/frontend/installed-app acceptance remain OPEN; do not claim a passing Windows suite from this commit alone. [skip ci].

- 2026-11-10: **S205 Windows full-run diagnosis (acceptance OPEN):** clean e590bf8 Windows run: 225 focused goal tests pass, 3 actual process-kill tests pass, frontend build passes. Full backend native exit 3221225477 in Phase 9.5 shared-StaticPool concurrency test plus an earlier unidentified F; Vitest 255 pass/2 fail from missing useGoalNextWork mock. Switched only failing concurrency test to real file-backed independent per-thread connections and repaired mock. Windows reruns/installed app checks remain OPEN; no production code changes, commit [skip ci].

- 2026-11-10: **S205 second Windows run follow-up (acceptance OPEN):** owner's clean 3e60555 Windows test passed Phase 9.5 concurrent SQLite (1/1), and full backend reached 1,471 passed / 9 skipped / 4 xfailed before failing goal preview dirty-session guard test. Root cause reproduced: expired SQLAlchemy goal.id lookup in the test call can autoflush a staged goal.title change before entering the preview; production preview guard unchanged. Updated regression caches goal ID before mutation and expands to dirty goal/step, new task and deleted step with no-flush assertions. Goal UI targeted suite 1 passed/1 failed due missing useTaskResumePreflight mock; fixed the non-authorizing test mock. Full suite Windows rerun and installed-app acceptance pending; commit [skip ci].

- 2026-11-10: **S205/S206 packaged backend drift:** clean 6db6135 Windows run proved goal preview 14 passed, goal UI 2 passed; full backend reached 3,369 passed/57 skipped/4 xfailed then found 27 stale or missing source files in Git-ignored `src-tauri/resources/backend`. Added explicit `scripts/refresh_staged_backend.py` read-only default, opt-in verified promotion, retained backup, rollback and 5 synthetic-tree smoke tests; added acceptance preflight blocking expensive full-backend runs on stale mirror. Windows refresh/complete backend/installed Tauri checks remain OPEN; [skip ci].

- 2026-11-10: **S205/S206 owner Windows automated suite PASS; installed application acceptance STILL OPEN:** owner-supplied clean-commit 09cc363 (Windows 11/Python 3.14.7) report: 18 refresh/runner tests passed; 27 stale mirror paths corrected via explicit verified promotion with retained backup; 60 release-payload tests passed; 225 focused goal ORM/policy tests passed; 3 real Windows subprocess process-kill cases passed; backend mirror preflight passed; full backend 4,392 passed/60 skipped/10 xfailed; frontend build passed; Vitest 257 passed across 29 files. All six runner suites status passed with exit 0; installed_windows_acceptance_performed=false. No Actions used. Record and manual installed-app plan: docs/evidence/GOAL_WINDOWS_TEST_MATRIX_2026-11-10.md. S194–S206 remain unchecked until their installed app acceptance clauses are exercised; known warnings require separate prioritization. [skip ci].

- 2026-11-10: **OpenCode browser acceptance D-001/D-004 first repair (Windows/browser rerun pending):** user-shared summary at clean 09cc363 reports 16 defects and read-only Control Center verdict; report/screenshots remain untracked on owner Windows checkout and were not read by this GitHub-based patch. D-001 code-confirmed: CompanionSurface submitted `/assistant/turns` without required client_request_id and had an unsafe legacy fallback to POST /tasks. It now generates a caller identity, reuses it after an uncertain same-draft retry, never auto-retries, removes execution fallback, and displays a bounded uncertainty message without raw backend errors. D-004 code-confirmed: historical abandoned/completed blocked plan steps no longer populate current Operations attention. Added regression tests for both. D-002 (how visible chat creates authorized task) is an explicit separate product/architecture decision; no autonomous behavior or backend policy changed. No full checkout/Vitest/browser run performed in this environment; S194-S206 native acceptance remain open. [skip ci].

- 2026-11-10: **Sythion OpenCode browser verification accepted / D-002 UI prototype (NOT yet rerun):** owner-supplied report at clean 4d62756 verifies D-001 live POST /assistant/turns 200 with nonempty request ID, caption/draft/console, and safe offline handling; D-004 active blocked-step positive control then abandoned-goal attention removal with API-confirmed status. Live mid-flight retry, native installed app, and 15s objective freshness remain unverified. Added explicit Create task confirmation in Assistant only, routing through existing POST /tasks with caller idempotency_key; normal Send remains pure conversation. The backend retains its intent classifier, lease and permission rules; the UI treats backend CONVERSATION as no Task and uncertain transport as unknown with no auto retry. Tests added for confirmation, idempotency identity, failure, disabled backend and API payload. No code was run in owner's environment for this new feature. [skip ci].

- 2026-11-10: **D-004 query freshness/D-007 confirmation wording (browser rerun pending):** after successful goal pause/resume/abandon, GoalDetailDrawer now invalidates the shared `objectives` and `goals` queries so Operations/Goals list do not show the old goal up to the stale-time window; success labels use correct paused/resumed/abandoned forms. Owner-supplied prior browser D-001/D-004 PASS evidence and D-002 task-entry test plan summarized in docs/evidence/SYTHION_UI_BROWSER_VERIFICATION_2026-11-10.md; actual owner screenshots/local untracked report not committed. No backend permission changes. [skip ci].

- 2026-11-10: **D-002 first Windows test-fix follow-up (rerun pending):** owner-provided clean 4008bc5 Windows Vitest: 98/100 passed; the 6 WorkEntry, 21 API and 2 Goals targeted tests passed. Two Assistant assertions failed, with one unhandled `window is not defined` from the real TaskDetailDrawer opened in a Node-only React test; TypeScript build blocked by 3 `Mock<any[], unknown>` vs `Promise<Task>` test typing errors. Added a per-test window listener stub for the drawer, narrowed the outdated global 'Execution' assertion to the completed reply bubble, and typed the WorkEntry mock against CreateWorkTask. This is test-harness repair only; backend/autonomy/runtime permissions unchanged. Windows rerun plus OpenCode live D-002 verification remain pending. [skip ci].

- 2026-11-10: **D-008 and D-003 partial remediation (owner tests pending):** `relTime` now returns complete phrases `just now`/`in a moment` rather than `just now ago`/`in a moment from now`; added deterministic Vitest UTC/future regression. ConversationRuntime and ConversationTurnEngine prompts now explicitly distinguish conversational continuity from durable memory writes, prohibit unverified `I'll remember` claims, and require an actual memory-write receipt before asserting persistence; prompt-contract tests added. D-003 NOT CLOSED: prompt instructions do not guarantee model compliance and no synchronous memory-save receipt is implemented. No autonomous writes, permissions, task execution or backend storage behavior changed. [skip ci].

- 2026-11-10: **D-005/D-006 notification behavior (Windows rerun pending):** topbar bell now clears unread count on opening tray, retains items, and marks arrivals viewed while the tray is open; no stale unread badge or negative count on dismiss. Ordinary successful conversation no longer creates a redundant `Sent` notification, while queued associated background work still creates `Work queued`. Added TopBar + Assistant regressions, no backend permissions or task execution changes. [skip ci].

- 2026-11-10: **Targeted Windows UI test repair (rerun required):** owner-supplied fa53b7a run reported 105/107 Vitest passes, with two TopBar failures (notification subscription not active at initial unwrapped React mount and open Popover requiring DOM event listeners in Node tests); frontend build had 7 TypeScript errors from `Mock<CreateWorkTask>` instead of Vitest 1.x `Mock<Parameters<...>, ReturnType<...>>` and from `afterEach` returning VitestUtils. Backend memory-prompt tests passed 2/2. Adjusted only test setup/types/teardown, retaining all assertions and production guards; installed UI and D-002 browser acceptance remain unverified. [skip ci].

- 2026-11-10: **Owner Windows targeted UI gate GREEN, commit c5e3874:** Fast-forward pull; Vitest 107/107 (TopBar 3, WorkEntry 6, Assistant 73, Goals 2, API 21, format 2); `tsc && vite build` green (191 modules). Recorded owner-supplied evidence in docs/evidence/SYTHION_UI_BROWSER_VERIFICATION_2026-11-10.md. D-002 live browser task creation and installed Tauri acceptance NOT performed; do not close them based on unit tests. [skip ci].

- 2026-11-10: **D-002 browser evidence BLOCKED upstream (owner OpenCode, c5e3874):** isolated live backend admitted and deduplicated a read-only task (`POST /tasks`, same task ID on key replay), but Ollama Cloud HTTP 402 wrapped in inference 502 prevented onboarding/Assistant UX, no tools ran. Three inference attempts, provider UNAVAILABLE and readiness false; no live UI success claim. Evidence summarized in docs/evidence/SYTHION_UI_BROWSER_VERIFICATION_2026-11-10.md, original local untracked report and screenshots not copied. Added specific sanitized provider-402 feedback for onboarding, without bypassing inference/readiness gates; regression tests in frontend modelTestFailure and Setup. Tests for this follow-up are pending. [skip ci].

- 2026-11-10: **Ollama Cloud policy change confirmed by owner email / green setup gate:** Email states Free Cloud usage ended Nov 9 5:40PM PT for listed models incl. gpt-oss:20b; pay-as-you-go credits required. Ollama official https://ollama.com/pricing corroborates credit pricing and free local inference. Individual account balance NOT verified. Owner Windows on 39bddf4: modelTestFailure+Setup 14/14 Vitest, TypeScript/Vite build green (192 modules). D-002 remains BLOCKED by upstream HTTP 402; restore authorized genuine inference for browser acceptance, do not bypass setup/permissions. Summary added to docs/evidence/SYTHION_UI_BROWSER_VERIFICATION_2026-11-10.md; no account, key, billing or runtime changes. [skip ci].

- 2026-11-10: **Universal Inference V1 foundation (not active routing):** added fixed-endpoint OpenAI-compatible transport and compatibility probe for OpenRouter, NVIDIA NIM and Groq. Separate write-only, build-scoped provider keys; HTTP error redaction, no redirects, no automatic provider retries or failover, bounded payload, strict tool/arguments acceptance, 60s negative probe cache; unit tests use httpx.MockTransport and synthetic keys. Existing Ollama default, onboarding, task permission policy, and live D-002 blocker unchanged. No provider accounts/keys configured or live calls performed. Next step is independent Windows test/adapter capability proof before guarded provider selection. [skip ci].

- 2026-11-10: **Universal Inference exploratory provider probe (awaiting owner execution):** added an opt-in CLI `python -m scripts.probe_cloud_provider` for exact model discovery and harmless model-only tool-call compatibility checks; all network actions require `--allow-network`, API key entered via hidden prompt or build-scoped vault, no model tool execution/task creation. Added offline tests for no-network default, model requirement, free catalog filtering, fail-closed probe. Default Sythion Ollama still unchanged; no OpenRouter account credentials accessed. [skip ci].

- 2026-11-10: **Cloud probe diagnostic correction (Windows rerun pending):** Owner reports two model capability probes failed for Gemma 4 26B and Nemotron 3.5 Lightning and suspects Windows hidden-key paste. Prior OpenRouter model listing worked but does not authenticate a key. Added OpenRouter authenticated GET /api/v1/key (`--verify-key`) with no inference or printed account metadata; CLI explicitly reports when nonempty hidden input is received. Probe errors now retain only safe HTTP status/category instead of collapsing all cases, with mocked 401/429/400 and no-tool cases. No retries, key exposure, fallback, execution or onboarding bypass. [skip ci].

- 2026-11-10: **Owner OpenRouter probe 429, not invalid credential:** key verified AUTH PASS; 30/30 targeted provider/probe tests green; Gemma 4 :free probe returned provider HTTP 429. Official OpenRouter caps: 20/min, 50/day free on unfunded account; failed attempts count; exact exhaustion cause not verified. CLI now warns against immediate retries after 429. Added opt-in CompatibleCloudModelClient (OpenAI-compatible tool-response path) and removed accidental Ollama adapter construction with an injected client; Ollama remains unchanged as default. Tests pending Windows verification; D-002 remains blocked. [skip ci].

- 2026-11-10: **Owner Windows cloud client gate 60/60 PASS (2826 deprecation warnings):** backend/test_cloud_model_client, test_openai_compatible_provider, test_probe_cloud_provider_cli, test_model_visible_tool_contract, test_phase6_model_selection. Following phase introduces inert provider staging in build-isolated profile via authenticated `/inference-preview` endpoints, with only allowlisted providers/models and no network or execution. Staged choice never changes legacy Ollama default or readiness; new tests pending Windows. [skip ci].

- 2026-11-10: **Owner Windows provider staging gate 39/39 PASS**, 2037 deprecation warnings, commit e9205ac. Added an opt-in `/inference-preview/probe` API requiring explicit authorization, exact staged model/provider and build-scoped key; one synthetic function call, no active inference or task execution. Safe sanitized 429/auth/malformed outcomes; recheck staging after request. No durable capability evidence or readiness changes yet. New offline route tests pending Windows verification. [skip ci].

- 2026-11-10: **Owner Windows provider preview gate GREEN, 59/59 tests** at acaadb8. Added optional first-run cloud provider preview for credential setup, exact model staging and an explicit one-request tool-call probe. Uses existing build-scoped vault and authenticated staging/probe backend routes, preserves legacy Ollama onboarding and readiness; no execution authorization. Frontend preview tests/build remain pending Windows validation. [skip ci].

- 2026-11-10: **Owner Windows UI build green** at 04cb6e8 (`tsc && vite build`, 193 modules). Added inert, provider/model/generation/credential-scoped HMAC evidence helper for staged synthetic tool-call success, TTL 1h. Every restage creates a fresh random generation; rotated vault key, expired or edited evidence fail closed. Not yet hooked to preview route or readiness; no default/active provider change. Evidence tests await Windows gate. [skip ci].

- 2026-11-10: **Scoped cloud evidence route/UI integration (Windows gate pending):** `/inference-preview/probe` snapshots key/model/generation, does one real synthetic tool call, rejects in-flight key or stage changes, persists one hour MAC-bound nonsecret result in SQLite after actual probe only. `/inference-preview/providers` returns `tool_probe_verified` for matching still-current evidence, never execution readiness. UI explicitly labels expiry and non-activation. Existing Ollama readiness remains sole onboarding authorization; no fallback. Added DB failure, key race, expiry and UI tests. [skip ci].

- 2026-11-10: **Owner verification** at 0a476ec: 55/55 targeted backend tests and 47/47 frontend tests PASS; `npm run build --prefix frontend` PASS (193 modules). Added explicit cloud conversation-shape bridge supporting existing conversation string and memory `message.content` contracts, sharing fixed-endpoint provider adapter with tool-decision client. No active model switch, no automatic retries, no existing readiness bypass or task execution. New offline bridge tests await Windows run. [skip ci].

- 2026-11-10: **Owner Windows bridge gate 64/64 PASS** at e97328a (2,240 deprecation warnings). Added isolated opt-in `/inference-preview/chat` one-message text-only endpoint for exact staged provider/model and secure key, without requiring tool capability; no task/memory/conversation persistence or real assistant execution. Fixed response-shape/429/auth/race fail-closed tests. Frontend preview integration pending. Backend tests pending Windows. [skip ci].

- 2026-11-10: **Isolated text-preview frontend** linked to `/inference-preview/chat`; one-shot consent, user-written text only, unverified reply notice, no tool requirement or task/execution/normal chat integration. Key/model changes clear local state; 429 does not trigger retry. Frontend UI/API tests added. No live provider calls by author; Windows test/build pending. [skip ci].

- 2026-11-10: **Owner Windows verification 74 backend / 52 frontend / production build PASS** on 965e233. Implemented standalone *text-only capability evidence* binding successful plain-text preview to exact provider/model/stage/key via separated HMAC, 1-hour expiry; independent of tool evidence, no prompt or conversation storage and no active routing change. `/inference-preview/providers` now exposes text_probe_verified. Targeted new tests and Windows gate pending. [skip ci].

- 2026-11-10: **Owner Windows text-evidence gate 76 backend / 31 frontend / build PASS** at b2d2182. Introduced dedicated `/cloud-conversation/turn` bounded answer-only request requiring exact current staged model+key+text attestation. Max 3 preceding turn pairs, no command interpreter, tasks, memory, normal composer, auto retries or fallback. API + tests pending Windows verification; UI entry lane to follow. [skip ci].

- 2026-11-10: **Cloud conversation-only experimental first-run lane:** explicit entry from cloud preview after a recent text proof. Independent screen, bounded in-memory transcript and cloud endpoint, never the main Assistant composer or task controller; refuses stale verification and restores setup on exit. Ollama setup untouched. Frontend tests/build pending Windows verification. [skip ci].

- 2026-11-10: **Owner Windows 65/65 frontend suite PASS** after test-only d0c9225 fix. Added dedicated Cloud Text Chat navigation available in the full app after Ollama onboarding; same protected preview and answer-only lane, no task/memory/tool routing changes. Added synchronous in-flight guard against duplicate cloud sends and regression test. Windows test/build pending; no live provider calls. [skip ci].
