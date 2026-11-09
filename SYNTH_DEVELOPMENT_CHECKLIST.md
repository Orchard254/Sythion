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
- Assistant-turn persistence and receipt finalization
- Conversation task state/result exposure
- Durable terminal-task wake reconciliation
- Companion and Control Center foundations
- Focused suites: assistant/memory/wake 98 passed; execution/E2E 50 passed;
  daily-use tools 96 passed/3 skipped; frontend 230 passed; memory scope/ranking
  regressions passed; real-provider conversation and memory recall succeeded.

Still to verify or complete:

- Complete backend green baseline.
- Successful real-provider/installed-runtime end-to-end run.
- Live daily-use tools and devices.
- True semantic relevance memory quality.
- Staged payload dependency/build verification.

Next moves (in order):

1. Finish marker-isolated backend batches without broad reruns.
2. Verify a real-provider/installed-runtime scenario.
3. Prove live daily-use tools.
4. Improve relevance-based memory quality.
5. Rebuild/verify staged payload provenance.

Every future report must preserve this achieved/remaining/next sequence rather
than restart from vague claims.

## Work register

No implementation is activated by creating this checklist. Populate before
starting each bounded work unit; reference earlier investigation only with its
limits (source, synthetic, provider, installed-runtime).

| IDs | Status | Scope / acceptance | Dependencies | Owner / files | Evidence / blocker |
| --- | --- | --- | --- | --- | --- |
| S001–S004, S160 | researching | Architecture research and documentation audit; acceptance requires a reviewable target model, not agent consensus | S005–S006 baseline | Coordinator; `SYNTH_PROJECT_UNDERSTANDING.md`, `README.md` | Agent research exposed entity ambiguities and README/dangling-link issues; no design accepted |
| S005–S006 | researching | Baseline and version reconciliation; acceptance requires exact current counts and environment evidence | none | Coordinator/verifiers; `pytest.ini`, CI, frontend configs | Targeted setup/data-dir tests passed; broad collection timed out at 120s; local Python 3.14/Node 24 differ from CI 3.11/Node 20; packaged prototype/runtime remains not verified |
| S039–S050, S087–S096, S024–S038 | implementing | Durable wake reconciliation, scoped relevance memory, and Assistant→task→evidence verification work units | none | Coordinator agents; exact file ownership in delegated briefs | Three agents running; targeted tests required, no broad suite reruns |
| — | queued | Select next bounded unit after baseline and dependency review | — | Coordinator | Initial checklist recorded; no items accepted as done |

## Change log

- 2026-11-09: Captured all 170 initial conversation items with stable IDs;
  established evidence-based completion and coordinator ownership.
- 2026-11-09: Recorded the first research status update: entity architecture,
  baseline/version review, and README/documentation alignment entered
  "researching" without any item being accepted as done.
- 2026-11-09: Corrected project-understanding notes on the generated backend
  mirror, legacy quarantined voice module, and stale README wording.
