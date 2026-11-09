# Synth architecture: the LLM as a reasoning processor

Status: **architectural direction / design reference**, recorded 2026-11-09.
This is not a statement that the complete architecture has been built, nor
approval to enable autonomous actions or change existing permission boundaries.
Related references: [project understanding](SYNTH_PROJECT_UNDERSTANDING.md),
[development checklist](SYNTH_DEVELOPMENT_CHECKLIST.md), and [README](README.md).

## 1. Identity and principle

**Synth is the persistent computing system. An LLM is a replaceable reasoning
unit used by that system, analogous to a CPU in a conventional computer.**

Synth's identity, goals, memory, commitments, work state, permissions, tools,
and evidence belong to the continuing runtime, not to a model, prompt, agent
conversation, provider connection or UI window. The system can have useful
work in progress while no model call is running. A model call is a bounded
computation on a specific reasoning problem; the runtime decides whether and
when it is necessary.

This analogy is a design tool, **not** a claim that model inference behaves
like deterministic CPU instructions, that a context window is literal RAM,
or that Synth is already a new operating system. Model outputs can be wrong,
inconsistent or maliciously influenced by inputs; they are proposals/evidence
to evaluate, not direct execution authority.

| Computing concept | Conceptual Synth responsibility |
| --- | --- |
| CPU | One or more model calls performing bounded reasoning operations |
| RAM / working set | A task-specific assembled context containing scoped, relevant state |
| Durable storage | Persistent events, goals, project knowledge, memory, and receipts |
| Processes / jobs | Independently tracked conversations, tasks, plans and commitments |
| Scheduler | Selects useful work, priorities, dependencies, and when to request reasoning |
| Interrupts / events | User direction, tool results, wake conditions, deadlines, failures and changes |
| I/O and devices | Tools, applications, interfaces and eventually permitted integrations |
| Kernel / authority boundary | Admission, capabilities, policy, authenticated consent, execution, recovery |
| Verification / telemetry | Outcome evidence, factual checks, provenance, costs and learning signals |

The model may suggest actions or plans, but must not award itself authority,
declare an unobserved tool effect completed, or turn a generated approval flag
into authenticated human consent. The user remains able to interrupt, revoke
and redirect work.

## 2. Control belongs to the system, not the conversation

Conceptual flow (not one existing production pipeline):

```text
user input / environmental event / due commitment / completed tool
        |
        v
durable state + event ledger + attention / job scheduler
        |
        +--> known deterministic procedure (model not required)
        |
        +--> reasoning needed?
                |
                v
       context manager: relevant, authorized, fresh working set
                |
                v
       reasoning dispatcher --> selected LLM/provider
                |
                v
       untrusted reasoning result:
         answer | proposed plan | proposed action | question | defer
                |
                v
       validation + policy + authorization
                |
                +--> communicate response
                +--> execute permitted action(s) via tools
                +--> wait for authenticated approval or information
                +--> reject / retry with bounded policy
                |
                v
       observation + evidence + durable transition
                |
                +--> reschedule, learn from verified outcomes, or idle
```

The scheduler must be allowed to choose **do nothing**. Always available does
not mean always inferring. An idle system can retain commitments, observe
authorized events, serve interfaces and recover from restarts without
continuous LLM activity. Event-driven wakeups are preferable to busy loops;
future deadline-based scheduling would require its own explicit design.

The runtime must own task lifecycle and prevent a provider timeout, crash,
model swap or closed chat surface from losing accepted work. An LLM being
available does not imply an action should be initiated.

## 3. Proposed boundary between system and reasoning unit

The following are **conceptual contracts**, not existing API payloads or
approved wire formats. Design and version them before implementation.

**Reasoning request** should identify:

- A unique request/correlation ID, purpose (interpret, plan, assess, explain,
  diagnose, choose among options), and associated goal/task/commitment IDs.
- Trigger, current work state and expected response type, with only the
  relevant knowledge and observations selected by the context manager.
- Source, provenance, scope, trust level, freshness and uncertainty of that
  context; never silently mix another task's private data into a new request.
- Constraints: allowed *proposals*, available capabilities, policy summary,
  timing/token/tool-call budget, cancellation signal and quality requirements.
- Whether the request is informational or may produce a proposal for a later,
  separately authorized effect.

**Reasoning result** should preserve:

- Correlation ID; answer, candidate plan or proposed action(s); assumptions
  and uncertainty; cited observation/evidence references where available.
- Missing information or requested observation; recommended next work, if any.
- Explicit distinction between *proposal*, *reported observation* and
  *verified effect*. A model's text is never itself a tool receipt.
- Failure/timeout/invalid-output states that the runtime can reconcile
  without pretending progress or reissuing consequential effects.

Prefer small, bounded reasoning jobs with a clear output contract over a
single permanently growing conversation. Multiple providers or specialized
models can eventually serve different reasoning jobs, but model selection is
a resource/quality tradeoff, not a transfer of Synth's identity or authority.

## 4. Context is a managed working set

A context manager must decide **what the reasoning unit needs**, rather than
loading every transcript or blindly trusting model-suggested memories.

- Construct scoped context from durable state, recent observations, goals,
  relevant prior outcomes, and permitted user/project knowledge.
- Preserve source identity, timestamps, uncertainty, access context and
  exclusions. Outdated or conflicting information should be labeled, not
  silently promoted to fact.
- Use retrieval quality and task relevance before token minimization; exclude
  irrelevant or unauthorized material even when it looks useful.
- Distinguish short-lived reasoning scratch context from durable memory.
  Persist only information justified by evidence and retention rules.
- Test task/session isolation, corrections, restarts, context cache invalidation
  and prompt-injection resistance.

A context window is limited and ephemeral. Synth's knowledge must not depend
on the model retaining prior prompts, nor should a model response alone be
proof that a memory is true.

## 5. System loops and execution boundaries

**Interaction loop:** receive input, determine whether it requires a direct
answer, clarification, task creation or existing-work redirection, then report
only what is supported by evidence.

**Continuity loop:** reconcile accepted work and events, prioritize useful
next steps, reason when necessary, execute permitted steps, verify effects,
record results and schedule the next condition. The loop can be idle or paused.

**Learning loop (future design):** compare predictions with verified outcomes,
evaluate whether a strategy was actually better, and only then propose/persist
a reusable procedure or improvement. More stored text or more model calls
alone do not establish learning.

Core invariants:

1. The durable runtime is the source of work state; a model message cannot
   overwrite it without validated transitions.
2. All effects pass the same execution authority, policy and evidence gates,
   regardless of whether they originated from a user, goal, agent or scheduler.
3. A consequential or irreversible action requires the relevant authenticated
   consent; an LLM must never simulate that consent.
4. Respect cancellation, duplicate suppression, idempotency/reconciliation,
   recovery and explicit failure states.
5. Maintain observed vs inferred vs unknown distinctions in environment state.
6. Treat cost, latency, reasoning-call frequency and useful verified outcomes
   as separate measures. Optimize useful results, not busyness.
7. The Companion and Control Center are interfaces to the same continuing
   system, not owners of its lifecycle.

## 6. Relationship to the repository as inspected

Existing building blocks include:

- Model access/decisions: `backend/services/model_engine.py`,
  `backend/services/model_selection.py`, and provider adapters.
- Context and memory: `backend/services/model_context_builder.py`,
  `backend/services/memory_service.py`.
- Durable work and wake mechanisms: `backend/services/main_agent.py`,
  `backend/services/wake_worker.py`, task/goal and event services.
- Tool execution and policy: `backend/services/task_execution_loop.py`
  and `backend/runtime/` plus `backend/tools/`.
- Interaction: `frontend/src/screens/Assistant.tsx`, Companion, Control
  Center, and the Tauri/sidecar runtime boundary.

**Integration gaps, not already-shipped capabilities:**

- Assistant turns, direct `/tasks`, and goal advancement currently take
  different paths; there is not yet one proven shared reasoning/work contract
  covering all three.
- The wake worker is event-driven, but a complete attention-based scheduler
  with independently initiated, goal-linked work is not demonstrated.
  Background orchestration has historically defaulted off.
- The main web development path lacks authenticated Allow/Deny approval
  issuance for consequential actions. Never bypass the fail-closed boundary
  to demonstrate autonomy.
- Context selection and memory isolation require current-code inspection and
  explicit regression tests; older audit notes may predate fixes.
- Persistent multi-agent goal completion, live voice, model/provider failover
  and an installed-runtime recovery loop require end-to-end proof.

Read current source before treating any of these as a permanent defect:
these are design targets informed by the 2026-11-09 inspection, not a
frozen assessment of future commits.

## 7. Incremental engineering plan and acceptance

Do **not** rewrite the existing task runner or invent a second authority
system. Implement small adapters and verify each connection.

1. **Specify the job/result contract.** Map existing conversation, task and
   goal state to an explicit reasoning purpose, scoped context, evidence and
   durable work identity. Document what never becomes a task.
2. **Prove context assembly.** Under synthetic and then live provider tests,
   include required information, exclude another task's scoped memory and
   label stale/conflicting observations.
3. **Unify admission and result handoff.** Exercise Assistant, direct tasks
   and goal steps through consistent policy, state transitions, receipts,
   cancellations and verification, without flattening distinct concepts.
4. **Add bounded attention and reasoning dispatch.** Start with explicit
   events and accepted work; support idle/no-model behavior, priorities,
   backpressure, failure and manual interruption. Do not silently enable
   background autonomy.
5. **Verify continuity.** Demonstrate model timeouts/provider switches,
   client disconnects, process restart, queued dependencies and duplicate
   events without lost work or unauthorized repeated effects.
6. **Evaluate usefulness and efficiency.** Measure reasoning invocations,
   token/latency costs, task progress and genuinely verified outcomes. A
   scheduler that calls the LLM more often but delivers no better results
   has not improved Synth.

Acceptance requires evidence from the actual entry paths, plus negative
tests for permission bypasses and fabricated completion. Synthetic
unit/integration checks and real-provider/installed-runtime evidence must be
reported separately. Keep the detailed tracking in checklist S051-S063,
S068-S075, S076-S096, and the architecture-specific additions S178+.
