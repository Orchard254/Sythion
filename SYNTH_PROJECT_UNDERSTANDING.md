# Synth: mental model for development agents

Audience: an LLM taking responsibility for understanding, designing or changing
Synth. This is a synthesized engineering model, not a conversation transcript,
feature wish list, or claim that the intended system already exists. Read the
product model first, then the implementation map: neither alone is sufficient
to make sound changes. Source and tests establish current behavior, while the
product model explains why that behavior exists and what it should grow toward.
Revalidate technical findings before acting;
the source observations below were made against application commit
`14148888607887a677256cf16026e0c7f1c379bc`.

Last revised: 2026-11-09. Earlier test results below are historical session
evidence, not a fresh baseline. Some were reported by delegated verifiers;
the coordinator inspected selected source/test assertions and independently
reran the Assistant HTTP-to-worker test. Do not represent all reports as
independently reproduced or sum overlapping test runs into a coverage claim.

## 1. What system are we building?

**Synth is a persistent agentic computing system organized around continuity,
initiative and useful action.** Its identity is the continuing system, not one
model invocation, conversation, desktop window or specialist. The user interacts
with that system while it maintains context and ongoing work between interactions.

The core design problem is turning intermittent reasoning into reliable,
long-lived agency. Models supply interpretations and proposals; memory supplies
context; goals organize intent; agents distribute work; tools produce effects;
the runtime owns execution and persistence; evidence determines what the system
can truthfully say happened. Adding more tools or a better model alone does not
solve missing connections between these responsibilities.

Think of Synth as having two coupled loops:

1. **Interaction loop:** perceive a request or event, understand its context,
   converse or redirect work, and communicate the result.
2. **Continuity loop:** maintain commitments and knowledge, choose useful work,
   execute or delegate it, evaluate outcomes, update state and decide what next.

These loops describe the intended design, not two already implemented schedulers.
A conversation can influence ongoing work without becoming a task. A task can
finish without ending a project. A specialist can contribute without becoming
the authority over the larger goal. Disconnecting a client should eventually
mean loss of an interface, not loss of the intelligence's continuity.

### Product trajectory, not a chatbot feature expansion

The initial host is a Windows PC. Voice is the primary desired interaction style;
the Companion provides a minimal presence/input surface while the owner works on
the computer, and the Control Center exposes operational detail. Phone access is
another possible client of the same continuing system. Permitted devices extend
the environment Synth can observe and act on. An eventual OS-like form would make
that environment more integrated; it does not currently select a kernel,
distribution, or replacement for Windows.

Independent initiative includes originating beneficial projects, not just
executing assigned work. An internal agenda therefore needs to relate work to
the user's interests and remain inspectable and redirectable. Research, coding,
overnight work and briefings are example applications of this machinery, not
the product boundary. A briefing is a presentation of durable activity and
pending decisions, not the intelligence's central purpose.

### Meaning of independence

Persistence does not imply consciousness. Initiative does not imply unrestricted
authority. Availability does not require constant model calls or purposeless
activity. Standalone operation does not establish an offline-only or local-model
requirement. The desired system can retain its identity and workflow while using
external model providers and cooperating agents.

### Settled product constraints

- Natural, interruptible live voice is the first interaction priority; current
  dictation and completed-result playback are not equivalent to that experience.
- The Companion is the continuous presence/input surface used while the owner is
  using the computer for something else. Its selected visual direction is simple
  and minimal: a cute robot avatar plus live caption, without explanatory text
  or visible controls cluttering the surface. It is not a persistent chat
  history, task list or dashboard; those belong in the Control Center.
- Recoverable actions are intended to proceed autonomously. Most unrecoverable
  actions require explanation and permission; unavailable-user requests can wait
  while other work proceeds. The exact classification mechanism is unfinished.
- Purchases are forbidden. Existing AI-service costs were not equated with
  purchases. No general operating-budget cap was requested for now; that does
  not remove the need for termination, cancellation or fault-containment bounds.
- Device access is permission-scoped. Phone access and an OS-like environment
  are future directions, not existing capabilities.
- Synth owns its runtime memory. Development CogniGraph is an external aid to
  engineering this project, not part of Synth's personal memory architecture.

## 2. How to reason about the building blocks

The following is the engineering decomposition to use when evaluating a change.
It defines responsibilities, not approved new services or a fixed class diagram.

| Responsibility | Owns | Must not be confused with |
| --- | --- | --- |
| Interaction | Voice/text input, conversational context, feedback | Lifetime or authority of ongoing work |
| Reasoning | Interpretation, proposals, uncertainty, choice of approach | Permission or proof of execution |
| Memory | Durable knowledge, provenance, retrieval scope, correction | Merely saving every transcript |
| Goal management | Desired outcomes, priorities and completion criteria | A single task's status |
| Planning | Dependencies and candidate steps toward outcomes | Evidence that steps happened |
| Initiative | Selecting useful work and reacting to relevant events | A timer or endless reasoning loop |
| Agent coordination | Delegation contracts, context, integration | Trusting every specialist result |
| Capabilities | Tool/application/device interfaces | Permission to use every available tool |
| Execution authority | Admission, ownership, consent, cancellation | A model-generated approval flag |
| Recovery | Durable transitions, reconciliation and restoration | Retrying every uncertain action |
| Evaluation | Outcome evidence, quality and feedback | HTTP success or schema validity alone |
| Communication | Briefings, explanations and requests for decisions | A substitute for actual progress |

An autonomous improvement should close a loop across these responsibilities.
For example, discovering a useful project requires a reason to pursue it,
durable intent, prioritization, an executable plan, permitted actions, evidence
of progress and a way for the user to redirect it. Merely generating an idea
does not implement initiative. Similarly, self-improvement requires measured
outcomes and controlled changes; a model saying it learned is insufficient.

Useful future research directions include opportunity discovery, an environment
model, experiments/simulation, attention management and self-maintenance. They
are proposals for filling responsibility gaps, not approved feature commitments.

## 3. Historical vision anchors

This short section preserves the interpretation's origin. The mental model
above is the operational briefing; analogies below are not architecture.

Synth (Sythion) is intended to be a standalone, always-active intelligent
system running on the owner's PC. It is inspired by fictional systems such as
Jarvis, the Algorithm and the AI in *Silo*, but is not intended to be human.
The owner describes it as having a life of its own **as an agent**: continuing
to exist, work, learn and pursue activity even when nobody is conversing with
it. In the longer term the owner is considering whether Synth could become an
operating system or inhabit an environment of its own. That is a future
direction, not an approved OS implementation plan.

Synth is not merely a chatbot, secretary, project manager or fixed set of
workflows. Those are analogies or examples of a much broader, open-ended
intelligence. It should be able to use and coordinate agents to accomplish a
wide variety of work, with capabilities growing over time. Do not mistake an
example for the boundary of the product.

"Standalone" does not establish an offline-only requirement or independence
from model providers. "Always active" describes continuity and initiative, not
a requirement to burn compute when there is nothing useful to do. "Its own
life" does not imply consciousness, human needs or unrestricted authority.
The OS possibility does not yet choose between a new kernel, a distribution,
or an intelligent layer on an existing operating system.

### Concrete examples and preferences stated by the owner

- Synth can work on projects or research overnight and give a morning brief
  covering what it did, what remains in progress and what needs attention.
- It can identify and start projects it judges beneficial to the owner, not
  only work from an explicitly assigned queue.
- Natural live spoken conversation is the first priority: the owner wants an
  experience closer to Gemini Live or ChatGPT Live than dictation and playback.
- Synth should control devices it is allowed to access. Phone access is a
  desired future capability; Synth currently is intended to run on this PC.
- Synth may act autonomously when an action is recoverable. For most
  unrecoverable actions it should explain and ask permission first; if the
  owner is unavailable, it can include the request in a morning brief.
- Purchases are not allowed. The owner clarified that this is what they meant
  by spending money; do not equate it automatically with existing AI service
  costs. There are no general operating budget limits requested for now.
- Synth has its own runtime memory. CogniGraph is development/project memory
  for people and agents working on Synth, not Synth's runtime memory.

The exact definition of recoverability, device authorization, unattended
initiative, phone access and other boundaries remains to be designed with the
owner. Do not infer permission to change policy or enable risky actions from a
high-level vision.

## Design implications and distinctions

The following are engineering interpretations, not additional owner-approved
requirements: Synth may need enduring identity and state, attention, an
environment model, an internal agenda, reflection, self-maintenance, an agent
and capability ecosystem, and a coherent relationship with the owner. A task
or conversation is one activity within that persistent system, not necessarily
its center. Design for an expandable intelligence rather than freezing today's
UI, tool inventory or architecture as its permanent limits.

Potential building blocks include interaction, intelligence and model access,
memory and knowledge, goals and projects, initiative, agent coordination,
tools and devices, authority and recovery, runtime continuity, communication,
connectivity, and learning/evaluation. These are conceptual boundaries, not a
decision to build separate services or implement everything at once.

Additional proposals worth exploring are opportunity discovery, experimentation
and simulation, an evolving model of the owner's priorities, environment/device
awareness, and measured self-improvement. The owner asked for original ideas,
not merely a restatement of existing code. These remain proposals: do not
silently convert them into requirements or implementation commitments.

### Important distinctions for design

- Conversation, goal, task, plan step, agent and tool call are different units.
  A conversational answer need not create work; a task need not be a goal;
  specialist success need not finish a larger goal.
- Continuous process availability, event-driven initiative and useful autonomous
  progress are separate capabilities. A running worker proves neither the
  presence of suitable goals nor the quality of its decisions.
- Permission, recoverability, successful execution and result quality are
  separate checks. A successful action is not proof of rollback, authorization
  or a correct model conclusion.
- Persistent storage is not proof of relevant recall. A model receiving a
  memory is not proof that it should have received it or understood it. The
  primary goal is not token reduction for its own sake; it is giving the model
  the right context for the current task and discussion, and excluding
  irrelevant or out-of-scope context even when that costs more capability.
- A synthetic provider proves orchestration under controlled responses, not
  real-model judgment. A source backend launch does not prove packaged behavior.
- Existing code budgets and fail-closed policies are current implementation,
  not the owner's final autonomy design. His preference for no general budget
  caps does not authorize removing concurrency, cancellation or fault bounds.

## Repository and current architecture

- The application repository is `C:/Sythion/sythion`. The outer
  `C:/Sythion` workspace contains development tooling and CogniGraph; the
  owner said not to commit the outer repository.
- `README.md` describes this repository as the trusted local runtime core of a
  larger intelligence system, not proof of the complete vision.
- A Tauri Windows shell starts a local Python/FastAPI sidecar. A React and
  TypeScript frontend provides Companion and Control Center surfaces. The
  backend contains conversation, models, memory, planning, agent, execution,
  tool, voice and persistence components.
- `backend` is authoritative source. `src-tauri/resources/backend` is a
  gitignored staged payload mirror and is absent in a clean checkout until
  staging is performed. The configured Tauri `beforeBuildCommand` invokes
  `verify_release_payload.py --check-backend-mirror`; clean-build tooling also
  verifies staged payloads. This is a configured verification step, not a
  guarantee supplied by every Rust/Cargo invocation or by `build.rs`. Generated
  payloads and the owner's profile are not disposable source files.
- The runtime has durable task events, goals and plan steps, a main-agent wake
  worker, specialist delegation, a tool executor and permission foundations.
  Existence of a component does not establish that it is wired into the
  user-facing path or has been proven in an installed application.

## What investigation has established

### Current execution map: three entry paths, not one pipeline

```text
Assistant UI -> /assistant/turns -> conversation engine
  -> answer + persisted exchange
  OR valid start_task -> TaskRunner -> run_task -> outcome/events

/tasks -> cognitive routing
  -> specialist delegation -> validation/execution/verification/integration
  OR other routing -> possible objective attachment -> normal execution

/goals -> GoalManager + Planner -> advance -> TaskOrchestrator
  -> goal-linked task -> run_task -> plan outcome projection
```

Do not mentally splice these paths into one request that always creates a goal,
delegates and advances a plan. The inspected delegation branch returns before
the separate objective-attachment branch. Goal advance invokes the worker,
not the task route's cognitive gate. Delegation can accept goal linkage at the
service level, but the production caller inspected does not supply it.

This explains a major product/implementation mismatch: substantial machinery
exists, yet a user-facing goal is not automatically a coordinated multi-agent
project. The design question is how to connect the responsibilities without
duplicating execution authority, losing evidence, or misreporting completion.

These are bounded observations, not blanket claims about every path:

1. An isolated in-process Assistant HTTP scenario exercised the real worker
   with fake model clients: a task was created, completed and persisted. That
   particular task did not create a goal or delegation.
2. An isolated goal API scenario created and replanned a goal, advanced a
   step, and persisted links between the completed task, step and evidence.
3. A separate task API scenario delegated to a specialist, passed a reviewed
   read-only `file_read` through executor policy against a synthetic file and
   integrated the result. A fake model supplied the proposed action.
4. Direct service-level synthetic testing showed that a delegation explicitly
   given a goal ID can link a specialist task to that goal. The inspected
   production delegation caller did not pass a goal ID; the tested
   goal-advance path did not invoke specialist delegation. The service also
   accepted a nonexistent goal ID during proposal/validation in a probe; this
   needs further investigation before making authority claims.
5. `MemoryService` applies active-status and allowed-scope filtering when an
   access context is supplied. A synthetic assertion test reproduced a task-A
   scoped memory appearing in task-B model context through
   `ModelContextBuilder`. No real provider disclosure or cross-user leak was
   demonstrated. Inactive-memory inclusion was also observed in synthetic
   context construction.
   The relevant difference is in `ModelContextBuilder.select_memory_context`:
   its initial query filters ACTIVE entries but does not apply an access
   context; the FTS query and linked-neighbor query do not themselves filter
   status or scope. Its selection interface accepts an anchor and session ID,
   not `MemoryAccessContext`. The scoped `MemoryService` contract therefore
   cannot be assumed to protect this separate model-message path. Inspect
   callers, cache behavior and trusted context propagation before designing a
   correction; a session label alone is not scope enforcement.
6. The wake worker is event-driven, not a periodic scheduler of its own.
   Background orchestration defaults off; actual installed settings are unknown.
   Real child-process-kill tests passed for selected backend recovery paths;
   installed shell close, automatic relaunch, sleep and reboot remain unproven.
7. Voice capture, segmentation, transcription and speech routes have passed
   synthetic tests with fake components. The Assistant uses an editable draft
   and turn-based flow. Natural live voice, interruption and actual packaged
   microphone/speaker behavior are not established.
8. Consequential-action approval foundations are not the same as an active
   trusted consent path. The inspected main execution and tool paths fail
   closed for confirmation-required or unsafe actions. Do not weaken these
   safeguards merely to increase apparent autonomy.
9. A prototype payload was staged in a temporary development copy. Its
   manifest records `prototype/unverified`: dependency and pip-bootstrap
   release provenance were not verified. An isolated source-backend startup
   succeeded using system Python; this was not a packaged prototype or Tauri
   lifecycle test.

Prior audit reports and passing tests cover selected paths only. They do not
prove a complete user request through goal, agents, real provider, tools,
memory, installed UI and restart. Do not claim otherwise.

### Evidence and navigation leads

Paths below are relative to the application repository. Inspect current symbols
rather than relying on historical line numbers. The
[SYNTH_DEVELOPMENT_CHECKLIST.md](SYNTH_DEVELOPMENT_CHECKLIST.md) is the director's
working backlog; this document is the mental model, not a task queue. Treat the
README's claim that it is the only Markdown document as stale now that both
synthesized documents exist.

| Concern | Primary source leads | Existing test leads |
| --- | --- | --- |
| Assistant submission and worker handoff | `frontend/src/screens/Assistant.tsx`, `backend/api/routes.py`, `backend/services/task_execution_loop.py` | `backend/tests/test_assistant_turn_api.py` |
| Goal creation and advancement | `backend/services/goal_manager.py`, `planner.py`, `task_orchestrator.py`, `work_state.py` | `backend/tests/test_goal_planning.py`, `test_work_state.py` |
| Specialist delegation | `backend/services/cognitive_runtime.py`, `backend/agents/delegation.py`, `coordination.py`, `specialist.py` | `backend/tests/test_phase9_live_delegation.py`, `test_phase10_coordination.py` |
| Memory scope versus model context | `backend/services/memory_service.py`, `model_context_builder.py`, `model_engine.py` | `backend/tests/test_memory_access_context.py`, `test_conversation_memory.py` |
| Voice | `frontend/src/hooks/useVoiceCapture.ts`, `backend/services/voice_pipeline.py`; legacy note: `backend/sythion_backend/services/voice_service.py` is quarantined material, not the active pipeline | `backend/tests/test_voice_pipeline.py`, `frontend/src/screens/Assistant.test.tsx` |
| Wake and process recovery | `backend/services/wake_worker.py`, `main_agent.py`, `bootstrap.py`, `backend/main.py` | `backend/tests/test_process_crash_recovery.py`, `test_phase45_hardening.py` |
| Desktop and payload | `src-tauri/src/main.rs`, `sidecar.rs`, `scripts/clean_source_build.py`, `stage_clean_runtime.py` | `backend/tests/test_data_dir_contract.py`, `scripts/tests/test_clean_source_build_workflow.py` |

Session-specific synthetic probes were retained under
`C:/Users/orcha/AppData/Local/Temp/opencode/`, including
`synth-wholepath-investigation/test_wholepath.py`,
`synth-goal-investigation/scenario.py`,
`synth-delegate-investigation/test_real_builtin_http.py`,
`goal-delegation-verify/test_goal_delegation.py`, and
`test_task_scope_leak.py`. They are not repository regression tests, may be
deleted, and must be reviewed for isolation before reuse. The cross-task probe
contained a vacuous neighbor assertion; its meaningful failing assertion was
task A's scoped entry appearing in task B's model messages, not the presence
of a GLOBAL neighbor. Preserve that distinction when promoting evidence.

## Guidance for future development

- Start with the owner's broad vision; do not reduce it to the latest example
  or assume every idea in this document is an approved requirement.
- Trace the actual user-facing path before changing a subsystem. Distinguish
  implemented, connected, enabled, tested and observed in the installed app.
- Research before delegating implementation; give agents bounded, non-overlapping
  work and independently integrate and verify their results.
- Protect personal data, live profiles, credentials and unfamiliar work. Use
  isolated synthetic profiles for tests; never disclose secrets.
- Preserve the no-purchase direction and existing fail-closed boundaries until
  an explicitly designed and verified alternative is authorized.
- Prefer evidence from realistic behavior over component names or passing
  mocks. Record failures and unknowns honestly, including real-model and
  installed-runtime limits.
- Do not commit or push the outer workspace. Any application delivery requires
  the appropriate user authorization.

## Areas still needing deeper understanding

Real live voice; installed Windows lifecycle and reboot/sleep behavior; real
model judgment and grounding; goal-to-agent integration in a user-facing path;
memory scope and recall quality; recoverability mechanisms and device policy;
learning, skills and browser integration; and the future phone/OS direction.
This list is not exhaustive.
