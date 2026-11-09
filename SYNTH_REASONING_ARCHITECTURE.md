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

## 8. Ownership-aware restart preflight (2026-11-09 implementation slice)

The read-only `GET /api/v1/goals/{goal_id}/continuity` response now adds a
`recovery_preflight` summary and a `recovery` classification on each returned
task. This reconciles recorded reasoning-call lifecycle with the current task
state and a snapshot of its task-ownership lease. A current ACTIVE lease means
the worker **may** still be running; it is not proof of liveness. An expired
lease triggers review rather than proving worker death. Unresolved inference
receipts and uncertain external actions remain explicit, never silently
converted into completed work, a clean restart, or an execution permission.

Only goal-scoped task IDs are considered, and the report discloses whether its
bounded task window is complete. It never returns ownership nonces, worker IDs,
or external action IDs. The preflight reads existing records; it **does not**
reclaim owners, replay tools, resume interrupted tasks, enable background
orchestration or issue consequential consent. Restart admission/recovery and
end-to-end Windows execution remain open acceptance work (S184-S185). Focused
local tests and synthetic-schema SQLite smoke tests do not establish complete
production integration.

## 9. Guarded manual resume admission (2026-11-09 implementation slice)

A new read-only `GET /api/v1/tasks/{task_id}/resume-preflight` reports whether
an explicit resume appears clear from committed task state, ownership and
reasoning/action receipts. It is never a permission or liveness guarantee.
The existing `POST /api/v1/tasks/{task_id}/resume` refuses blocked tasks before
it acquires a runner lease. More importantly, `TaskRunner.transition_state`
rechecks the same policy **under its writer transaction** on every
paused/interrupted→running transition, regardless of which entry path or event
label requested it. A changed event between preflight and transition therefore
forces refusal instead of silently restarting work.

Resume preflight blocks potentially live or unreconciled owners, incomplete or
corrupt reasoning receipts, pending actions, outstanding uncertainty and
historical mutating tool evidence. A recorded successful mutation does **not**
prove it is safe to repeat. Prior `action.started` and `tool.executed` entries
without an explicit `mutating:false` marker require review, even when the
model says they succeeded. This is intentionally more conservative than the
former resume behavior. No UI workflow for authenticated owner review,
uncertainty clearance, action reconciliation or re-entry currently exists;
some interrupted tasks will remain blocked pending S184-S186. Avoid manual
database status flips as a workaround.

The local verification covers pure policy tests and temporary synthetic-schema
SQLite tests, **not** the actual Sythion TaskRunner/API or installed Windows
runtime. The seven new repository integration regressions are retained as
unexecuted acceptance work. Background autonomy remains off by default.

## 10. Non-authorizing recovery review packet (2026-11-09)

The task resume preflight now returns a `review` packet generated by
`backend/services/recovery_review.py`. This packet is **deterministic,
read-only and advisory**. It maps the existing blocked-resume reason to
source categories requiring independent verification and a fixed checklist of
checks. It cannot grant an approval, clear uncertainty, resume a task, replay
an external effect or assert that an owner inspected the evidence.

The packet deliberately contains no raw action/event contents, prompt text,
tool parameters, IDs of potentially sensitive external actions, ownership
nonces, model-provided instructions or user-authentication claims. Unknown or
contradictory preflight records fail closed. Even `preflight_clear` is only
a time-sensitive observation: the TaskRunner's writer-locked admission,
existing leases and consequence policy are still authoritative.

Sythion already has an intentionally no-data `uncertainty_inspection.py`
foundation because authenticated profile/task/session access cannot yet be
established from the available schema. This new packet does **not** change
that: it adds sanitized guidance to the pre-existing task-scoped preflight
endpoint, not a detailed inspection or an authenticated review submission.
A genuine owner-led reconciliation and UI needs a verifiable principal/scope
boundary, independent target-state evidence, a durable audited decision
and an authorization policy that is distinct from model reasoning
(S186–S187). Until those gates exist, blocked tasks remain blocked.

## 11. Recovery review in the desktop task interface (2026-11-09 slice)

The task drawer now requests and shows a read-only `resume-preflight`
snapshot when a task is paused or interrupted. It displays fixed-template
verification guidance and a conservative resume control that is disabled if
preflight is absent, malformed, in flight, errored or blocked. Successful
preflight is **not** owner approval; all UI resume entry points perform a
fresh preflight before posting a resume request, and the TaskRunner repeats
the decision while holding the task-state writer lock.

The Security screen previously mislabeled its task resume action
`Authorize`; that action now reads `Resume if safe` and explicitly warns
that resuming is not permission approval. Independent authenticated human
consent, a trustworthy profile/session principal and effect reconciliation
remain *unimplemented*. A per-launch local API token is a control boundary,
**not** human identity. The work does not enable auto-resume or bypass
pending-action/uncertain-effect safeguards.

The new UI unit and Axios interaction tests are committed, but React/Vitest,
full TypeScript build and Windows packaged behavior remain unverified in this
environment. A small isolated TypeScript behavioral check is not equivalent
to those integration tests (S186-S188).

## 12. Evidence revision for later trusted recovery review (2026-11-09)

The read-only task `resume-preflight` report includes a `review_binding`
revision computed from server-read task version, state/update time, task-event
watermark, ownership version/fencing token/status/expiry, action-presence
markers and the current coarse recovery-blocker classification. Its digest is
a stable change detector for this selected metadata, **not** a signature or
proof that all event contents are authentic. Raw request text, model output,
tool parameters, action IDs, owner nonces and credentials are never included
in the revision input. A changed ledger position, task version or owner fence
changes the stamp and can invalidate a previously observed review snapshot.
The reader refuses sessions with pending ORM writes rather than autoflushing
them as a side effect of this read-only review.

The task drawer may show an abbreviated snapshot revision to help a person
recognize a changed observation. The API explicitly reports
`authenticated_human=false`, `authorization_issued=false`,
`uncertainty_cleared=false` and `automatic_replay_allowed=false`.
The local API launch token and a system-generated conversation session are
**not** authenticated human consent. No clearance, native Windows verifier,
replay authorization or automatic resume is implemented by this slice.

A future human attestor must be independently verified via a native trusted
boundary, scoped to exact work/effects, and must bind its audited decision
to a freshly recomputed revision **inside the task writer transaction**.
The external system's actual effect outcome also requires independent proof.
The snapshot alone is never sufficient to reopen uncertain work (S186-S189).

## 13. Fail-closed reasoning receipt chronology (2026-11-09)

Before an interrupted/paused task is considered for explicit manual resume,
`backend/services/reasoning_receipt_integrity.py` now verifies the committed
task reasoning-event stream, rather than relying only on a count of unmatched
`reasoning.started` records. A terminal call receipt must pair with one
previously observed start, each job ID is single-use, metadata must match the
actual journal contract, and reasoning event sequence numbers must be strictly
increasing. Event-sequence gaps are legal when non-reasoning events interleave.
Unknown `reasoning.*` event types, orphan terminals, duplicates, malformed
payloads and extra authority claims all make the evidence **corrupt**. They
never count as authorization or silently erase uncertainty.

The manual resume preflight reads every `reasoning.*` type (including unknown
ones) and returns the existing
`corrupt_reasoning_receipt_review_required` blocker on anomalies. The
writer-locked TaskRunner rechecks this policy before paused/interrupted→running
transitions. The review UI remains read-only, and the checker returns only
fixed, privacy-minimal anomaly labels, never raw job IDs or model output.

This is **not** trusted human authentication, effect reconciliation or an
audited clearance process. The Tauri per-launch control token and system
session lineage are not proof of a human approval. Actual Windows-integrated
recovery identity/consent needs a separately verified native host, a fresh
transaction-fenced evidence binding, and independent proof of external-action
outcomes. The real repo integration and Windows tests remain open (S185,
S189-S190); local synthetic SQLite tests do not establish end-to-end safety.

## 14. Native recovery presence handshake: offline protocol only (2026-11-09)

`backend/services/recovery_native_challenge.py` defines a **default-deny,
offline** one-shot challenge/host-message verification contract. The
challenge is bound to an exact task and the existing evidence revision,
expires quickly, is single use, and does not survive process restart. A
separately provisioned per-launch host secret is needed to authenticate the
fixed, domain-separated HMAC assertion. The webview, LLM, API control token
or a caller's claimed `approved: true` flag cannot satisfy it without that
host secret.

Importantly, host-message integrity is **not** Windows user verification,
authenticated owner consent, verification of external effects, or a grant.
Every observation produced by this foundation keeps
`authenticated_human`, `independent_effect_verified`,
`recovery_cleared`, `authorization_issued`, and
`automatic_replay_allowed` false. There is no API/TaskRunner integration:
native host key provisioning, a window-owned OS verifier, audited approvals
and lock-fenced clearance have not been built.

Detailed requirements and test limitations are in
`docs/RECOVERY_NATIVE_ATTESTATION_PROTOCOL.md`. Windows desktop
verification must use the HWND-owned UserConsentVerifier interop path;
do not use the UWP overload or assume a generic Tauri modal proves human
identity. Existing uncertain-effect resume blockers remain in place
(S189-S192).

## 15. Goal actionability and pure readiness (2026-11-09)

Goal work now has a deterministic `goal_work_preview.py` projection and
read-only `GET /api/v1/goals/{goal_id}/next-work` endpoint. The Goal detail
drawer shows the next eligible step, which dependencies are outstanding,
whether failure backoff applies, and when a failed/running/completed plan
requires review. These are **observations**, not a claim of completion or
authorization. The LLM does not decide or approve eligibility.

Previously, `RunnableGoalSelector.list_runnable_goals()` called the
mutating `Planner.refresh_readiness()` just to assemble a read-only
eligibility listing. That helper commits step-state changes and ledger events.
The selector now evaluates *virtual readiness* without writes. When a blocked
step's recorded dependencies are all completed, the preview may show it as
eligible while the durable row remains BLOCKED. The Planner owns the only
actual readiness transition, inside its existing runner-lease-protected
step-start path. The Planner uses the same projection to prevent a stale
READY row with unmet dependencies from being started. Malformed, unknown,
duplicated, self-referential, and cyclic dependency data blocks plan
selection rather than producing unjustified work.

The UI does not introduce an automatic run button or background scheduler.
Existing user opt-in, agent wake, task ownership, tool permission and result
verification remain the authorities. Local pure/synthetic-SQLite tests verify
the projection and nonmutating query surface; the full repository ORM/API,
React and installed Windows runtime remain acceptance work (S193-S194).

## 16. Outcome-adjudicated Goal Execution Vertical Slice V1 (2026-11-09)

The goal continuation orchestration now treats a worker's return value or
`{"updated": false}` response as **non-authoritative**. That response may
mean the kernel post-task pipeline has already applied an outcome, but does
not specify whether it was verified completion, failure or unresolved work.
`TaskOrchestrator._observed_step_outcome` explicitly refreshes the goal,
plan step and task from committed ORM state (including changes made in a
different worker session), requires the exact evidence-task link, and
rechecks the kernel's completion verifier for a success result.

The output vocabulary is `completed`, `failed`, or `unverified`.
An unverified outcome halts explicit/background continuation; it is not
reported as a failed or successful terminal service outcome. A worker
that returns while the task is still running, interrupted or waiting
does not have its plan step forcibly failed by `apply_task_outcome`.
A pre-applied failed step is still a failure and may undergo existing
bounded retry/goal pause rules. The no-eligible-step branch also no
longer releases the same runner lease twice.

See `docs/GOAL_EXECUTION_VERTICAL_SLICE_V1.md` for the 11-real-ORM
scripted-worker scenarios (3-step dependency chain, file/report checks,
actual DB session restart, exact audit/event counts, failure and mismatch
cases). **Only 24 pure classification tests were run locally in this
conversation;** the full integration suite needs the complete private
repository and is not claimed passed. OS-facing worker behavior in these
new tests is scripted; no live tool adapter, model or Windows installed
runtime was exercised. Real execution acceptance remains open S195-S196.

## 17. Registered executor binding for goal workers (2026-11-09)

The ordinary API installs one registered ToolExecutor through
`backend.api.dependencies.get_tool_executor`. Goal execution previously
passed `None` as the third argument of the production `run_task`, even
though that worker constructs the real `TaskExecutionLoop` and
`ModelDecisionEngine` with the supplied executor. As a result, the
goal planner could start real tasks without a usable tool registry.

The bounded `bind_goal_worker` adapter now resolves the application
executor once before a real goal task starts and forwards the same object
to default workers in both explicit and background cycles. If the
provider fails, the session stops with `tool_executor_unavailable`
without creating a task. An explicitly injected deterministic worker
keeps the existing test seam and does not contact the production tool
provider.

This is *composition-root repair*, not an enlargement of the LLM's
permissions. All runtime and action-approval gates remain authoritative.
The binding helper passed 15 local pure tests. Six full-ORM regression cases
were committed but not executed here; the separate-worker-session
acceptance fixture now uses a genuine file-backed SQLite database with
different physical connections. Live ToolExecutor behavior, installed
Windows, and the full application goal lifecycle remain unverified
(S195-S197).

## 18. Fail-closed goal worker handoff and read-only tool acceptance (2026-11-09)

`run_goal_worker_handoff` isolates one awaited worker and its canonical
post-task outcome application. An exception from either results in the
fixed `worker_or_outcome_error` stop reason (logging only the exception type) rather than an implicit retry,
verified success or terminal failure. The explicit or bounded background
session reports the attempt as `unverified`; the background fairness
counter still accounts for a started attempt without treating it as a
failed terminal session. Cancellation propagates. No subsequent dependent
step executes because of a worker/recorder exception.

The real file-tool acceptance tests now check `ToolExecutor.execute_tool`
with Sythion's exact reviewed `FileReadTool` and `FileHashTool`
implementations, SHA-256 independent reads, approved disposable roots,
out-of-root refusal, and denial of file creation despite a claimed
direct instruction. A new three-step acceptance scenario combines read-only
tool dispatch with the real Planner/TaskRunner and a scripted temporary
artifact writer. The script is **not** a real mutating tool authority.
The 29 local pure binding/handoff tests passed; full-ORM and file-tool
adapter scenarios remain unexecuted pending a complete checkout
(S195-S198).

## 19. Goal restart does not replay uncertain work (2026-11-09)

Startup `TaskOrchestrator.recover_step` previously promoted INTERRUPTED
and CANCELLED task-bound plan steps from RUNNING to READY, and missing-task
steps to PENDING. This could let goal scheduling create a *new* task for the
same uncertain effect, bypassing the original task's strict resume gate.
The `goal_restart_policy.py` classification now defers those cases
without changing plan status, attempts, task ownership or audit history.
Only durable terminal COMPLETED/FAILED tasks are eligible for the existing
verified reconciliation path; task-step evidence linkage and goal scope
are checked before doing so.

The pure goal readiness projection and planner now block new steps when
any active-plan step is RUNNING, including unrelated READY work and retries
of FAILED sibling steps. The explicit bounded goal session stops with
`unresolved_step_review_required` instead of scheduling a retry in that
state. The goal next-work API distinguishes persisted interrupted/cancelled,
missing, mismatched and foreign-bound evidence as
`recovery_review_required` without leaking task IDs or prompt text.

This slice retains the previous manual task-resume and consequence
permission boundary. It does not clear uncertain effects or automatically
start replacement work. The 13 new pure restart-policy tests passed locally
(42 total focused Python tests); true ORM, process-kill, separate-connection
and Windows packaged acceptance remains pending S199-S200. See
`docs/GOAL_RESTART_NO_REPLAY_V1.md`.
