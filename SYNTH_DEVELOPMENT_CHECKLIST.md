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
