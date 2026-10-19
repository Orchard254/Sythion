# Control Center Observation Contract

Date: 2026-10-18  
Status: Specification only. No implementation changes.  
Repository scope: `C:\Sythion\sythion` (primary backend); `sythion/src-tauri/resources/backend` is byte-identical aside from Python bytecode caches.

## 1. Executive recommendation

The Control Center should be built as an **observer of durable backend state and durable events**, not as an interpreter of the process or UI state. Sythion already has a trustworthy core:

- task lifecycle is persisted with explicit state constraints;
- state transitions are authority-gated, validated, and append-only auditable;
- task/event replay supports catch-up after disconnect;
- cancellation, interruption, resume, and lease ownership have durable semantics;
- delegated agents and multi-specialist plans are persisted with their own state machines;
- workflow assets exist as objectives, plans, steps, criteria, and linked tasks.

Recommended approach:

1. Report **persisted states and sequences** as facts.
2. Derive **progress, health, stall, and completion** only from persisted entities, with explicit missing-evidence language.
3. Use polling + event replay as the first transport contract.
4. Do not introduce task-level `blocked` state; represent blocked work at the step level.
5. Keep UI semantics subordinate to backend authority.
6. Add backend/provider allowlist changes only where existing information cannot safely answer an operator question.

### 1.1 What can be confidently reported today

- Task state, version, request text, timestamps, linked objective, result, and terminal reason.
- Full ordered task event timeline and exact catch-up by `sequence_num`.
- Task list filtered by state only in memory after fetching records.
- Task cancellation state and later actions blocked after cancellation.
- Interrupted/paused resumption eligibility.
- Runner lease presence and ownership, including process id and expiration.
- Objective status, criteria, verification, plan versions, and individual plan-step status.
- Delegation lifecycle, parent relationship, dependencies, result availability, and integration outcome.

### 1.2 What cannot be truthfully reported today

- Whether a running process is making meaningful progress.
- Whether a plan step is stalled; only whether it is not in a terminal/completed state.
- Whether a specialist agent is actively executing rather than merely recorded.
- Whether the model is about to recover or is genuinely stuck.
- Whether every event's described operation actually succeeded at the OS or tool boundary.
- Whether a task is "blocked" unless the reference is to its plan step.
- Whether global stop or pause succeeded against an active tool executing at the exact moment of intervention; the backend can only guarantee durable cancellation, and future executor checks.

## 2. Canonical state and transition model

### 2.1 Persisted task states

Tasks are stored in `tasks.state` with the database constraint:

```text
created, running, waiting_for_user, completed, failed, cancelled, interrupted, paused
```

Source: `sythion/backend/models/database.py`, `Task.__table_args__`.

Representations derived only from this set. Do not add a task-level `blocked` state solely for UI convenience; `blocked` is a `PlanStep` state.

### 2.2 Permitted transitions

Source: `sythion/backend/services/task_runner.py`, `ALLOWED_TRANSITIONS`.

| From | To |
|---|---|
| created | running, waiting_for_user, cancelled, interrupted |
| running | waiting_for_user, completed, failed, cancelled, interrupted, paused |
| waiting_for_user | running, cancelled, interrupted |
| interrupted | running |
| paused | running, cancelled |
| completed | terminal, no transitions |
| failed | terminal, no transitions |
| cancelled | terminal, no transitions |

Note: `transition_state` rejects invalid transitions. `force_cancel` enforces the same transition rule but does not require lease ownership.

### 2.3 Plan-step states

Steps are persisted in `plan_steps.status`:

```text
pending, ready, running, in_progress, blocked, completed, failed, cancelled, skipped
```

Legacy statuses `in_progress` and `skipped` remain readable for older rows. Source: `sythion/backend/models/database.py`, `PlanStep.__table_args__`.

Dependencies are recorded as JSON step keys in `depends_on`, and `Delegation` dependency readiness is separate and stricter during orchestration.

### 2.4 Operational meaning of each state

| State | Meaning |
|---|---|
| created | Task exists as a durable execution request but has not yet started execution. |
| running | Task is actively executing or has been resumed into execution. |
| waiting_for_user | Task has durable pending question, authorization, or reply requirement and cannot progress without user input. |
| paused | Task execution is intentionally halted, but remains resumable later. |
| interrupted | Task has lost normal execution continuity, usually due to crash/restart/lease loss, and must re-observe state before continuing. |
| completed | Task reached a terminal successful outcome and recorded final result. |
| failed | Task reached a terminal error. |
| cancelled | Operator or system durably cancelled the task. It is terminal and cannot run again. |

### 2.5 Persisted versus derived state

| Value | Type |
|---|---|
| `tasks.state` | Persisted fact |
| `tasks.version` | Persisted audit counter |
| `tasks.updated_at` | Persisted timestamp |
| `tasks.terminal_reason` | Persisted fact |
| `PlanStep.status` | Persisted fact |
| Task blocked presentation condition | Derived, from step statuses |
| Stall suspected | Derived |
| Stall confirmed | Derived or unknown if evidence insufficient |
| Progress completeness phrase | Derived presentation |
| Process healthy | Derived from lease expiration; with explicit unknown boundaries |

### 2.6 Representing blocked work without misclassifying a running task

Control Center rule:

- A running parent task may have one or more blocked plan steps.
- Blocking must be explicitly labeled as **step-level blocked condition**, not as task cancellation/failure and not as task-level blocked status.
- Parent task remains `running` unless transitioned by backend authority.

If the UI needs "Task contains blocked sub-work," derive it from linked step statuses and clearly label it as derived. Do not generate a misleading summary that implies the parent itself is blocked.

A new task-level state for blocked behavior is **not justified currently**. The cases can be explained by:
- `TaskState` terminal/non-terminal semantics;
- `PlanStep.status='blocked'`;
- pending dependencies;
- waiting-for-user state.

## 3. Progress and health semantics

### 3.1 Distinct semantic meanings

| Concept | Definition | Evidence |
|---|---|---|
| Process/runner health | The runner actually holds an unexpired lease. | `runner_lease.owner_nonce`, `process_id`, `heartbeat`, `expires_at`; `last_task_id`. |
| Last recorded event | Highest committed sequence for task. | `TaskEvent` rows and sequence. |
| Last meaningful progress | Last event that reflects real advancement or a real state change, excluding repeated heartbeats, stores of context, or purely descriptive events. | event type plus event payload. |
| Current step | The plan step whose status is `ready` or `running` / `in_progress`, if any. | Plan-step row. |
| Step completion | Terminal step and evidence proving it. | `PlanStep.result_summary`, `evidence_task_id`, corresponding task terminal state. |
| Waiting | Task state plus durable pending question/action. | `pending_question`, `pending_action`. |
| Suspected stall | No meaningful progress for an operation-specific expected window without being known terminal/legit wait. | Derived from timestamps and operation class. |
| Confirmed failure | Terminal `failed` state and failure reason. | `tasks`, events. |
| Verified outcome | Requires terminal task + required task evidence + `criteria_verified` where applicable. | objective/task/plan evidence; delegation integration. |

### 3.2 Meaningful progress

Meaningful progress includes evidence such as:

- task transitions;
- action started/finished events, if present in event data or persisted;
- plan-step status changes;
- delegation assigned/verified results;
- user reply/resume events;
- completion verification;
- objective mutation.

Not meaningful progress:

- runner lease heartbeat;
- polling API fetch;
- model prompt generation alone, unless recorded as a durable state or action event;
- event descriptions that say something happened but lack corresponding durable state/evidence.

If evidence is insufficient, Control Center must show `unknown / unverified`, not infer completion or progress.

### 3.3 Stall detection

Stall detection must be operation-class aware:

- use duration expectations based on tool type and backend-reported task; no single universal timeout;
- treat `waiting_for_user`, `paused`, and explicit external-wait events as legitimate non-progress states, not stalls;
- use lack of lease/process health only to indicate possible executor death, not task meaning failure;
- distinguish `runner absent` from `task failed`.

Recommended derived labels:

| Derived state | Basis |
|---|---|
| `executor_might_not_be_running` | expired lease and task not terminal |
| `awaiting_user_input` | task `waiting_for_user` + pending question/action |
| `waiting_on_external_dependency` | task event explicitly records await condition plus nonterminal state |
| `suspected_stall` | no meaningful progress for expected operation window, lease possibly alive, not acknowledged wait |
| `possible_loss_of_control` | expired lease; no heartbeat while task has nonterminal state |
| `stalled_confirmation_unknown` | insufficient evidence; do not mark failed |
| `failed` | backend terminal failure |

Warnings should use tentative language: “No meaningful progress since [timestamp]; not necessarily failed.”

### 3.4 Completion and verification

Task `completed` is an execution terminal state. Verified outcome requires more:

- task is `completed`;
- `final_result` present;
- corresponding evidence exists;
- if linked objective, `criteria_verified` and linked `PlanStep` completion, and no unresolved blocked dependencies;
- delegation outcomes either accepted or consciously resolved.

If any evidence missing, show `completed but verification incomplete or unavailable`.

## 4. Event and evidence contract

### 4.1 Existing durable event ledger

`TaskEvent` implements:
- append-only immutable history;
- monotonic `sequence_num` per task;
- `event_type`, raw JSON `event_data`, `created_at`;
- `DbEventLedger.append_event` participates in caller transaction, so state plus event commit together.

Read-only APIs:
- `GET /api/v1/tasks/{task_id}/events`
- `GET /api/v1/tasks/{task_id}/events?after_sequence=N`

Events are ordered by `sequence_num`; client can replay from last sequence and guarantee no gaps/duplication.

### 4.2 What event creation timestamps mean

`created_at` is transaction commit/append time. It is reliable as ledger order but:
- not necessarily the exact OS action start or end time;
- may not include expected duration;
- not enough to infer success;
- may include batch event lag.
Do not use only timestamps to claim task completion success.

### 4.3 Practical event taxonomy (durable/logical classes)

For existing and future events, classify as:

1. **State transition events**
   - e.g. `task.execution_started`, task approved, started, paused, resumed, cancelled, completed, failed.
   - Source of truth for task state if tied to state mutation transaction.

2. **Planned workflow changes**
   - plan created/superseded, step transitions, dependencies resolved.
   - Relies on objective system.

3. **Actual tool/system operations**
   - e.g. `action.started`, tool execution, or interface call.
   - May indicate the operation was attempted but does not establish success.

4. **Operation outcomes/errors**
   - success/error events, e.g. `action.started`, task cancelled, failure reasons.

5. **Approval/intervention events**
   - user reply, cancel, stop, pause, resume, policy action.

6. **Verification results**
   - `criteria_verified`, integration outcome, specialist result verification.

7. **Descriptions, not facts**
   - free text summaries, model reasoning, event data explanations.
   - Derived, potentially incomplete, never independently authoritative.

### 4.4 Evidence required to display claims

For important Control Center claims:

| Displayed claim | Minimum evidence |
|---|---|
| “Task running” | `tasks.state = running` |
| “Tool action started” | `action.started` event + durable active action id |
| “Tool action succeeded” | result event or resulting persisted state; note that events alone are insufficient |
| “Waiting for authorization” | state + pending question/action content |
| “Blocked” | `PlanStep.status='blocked'` or unresolved dependencies |
| “Completed” | state `completed` + final result |
| “Verified outcome” | `criteria_verified`, linked completed steps, no evidence conflicts |
| “Delegation accepted” | `Delegation.status='INTEGRATED'` + integration outcome |
| “Process healthy” | non-expired lease |
| “Progress since” | newest meaningful progress event sequence |
| “Failure reason” | terminal failure reason + event |
| “Stall” | timestamp gap + no legitimate wait + operational context |

### 4.5 Assumption caution

For any claim: if event data describes an event, it proves attempt/sequence, not necessarily successful underlying system mutation. Event text can be descriptive; backend durable state and evidence refs are authoritative.

## 5. Delegated-agent supervision model

### 5.1 Agent lifecycle states

`AgentRecord.state` for agents:

```text
CREATED, READY, RUNNING, WAITING, COMPLETED, FAILED, CANCELLED, EXPIRED
```

Agent types:

```text
MAIN, SPECIALIST, TEMPORARY_SPECIALIST
```

Source: `models/database.py` (`AgentRecord`), `agents/state.py`.

MAIN identity reuses `main_agents.agent_id`; specialists are bounded workers whose parent should point to MAIN except temporary records where configured.

### 5.2 Delegation lifecycle states

```text
PROPOSED, VALIDATED, ASSIGNED, RUNNING, RESULT_READY, VERIFIED,
INTEGRATED, FAILED, CANCELLED, EXPIRED
```

Source: database `Delegation.status` and component memory references. Evidence is from schema/code, not a re-run of provider tests.

### 5.3 Delegation plan states

```text
DRAFT, VALIDATED, ACTIVE, COMPLETED, FAILED, CANCELLED
```

With completion policies: `ALL_REQUIRED`, `MAJORITY`, optional results semantics.

### 5.4 Parent-task mapping

Delegation connects:
- parent agent ID;
- parent goal ID;
- parent task ID;
- specialist agent ID;
- specialist task ID (kernel execution record);
- plan ID and dependencies;
- result and integration outcome.

Parent task does not automatically become blocked due to a single delegation. Completion assessment must account for:
- required delegation results;
- partial failures;
- retries (`attempt`);
- cancelled/expired delegations;
- integration outcome:
  - ACCEPT, REJECT, REVISE, CONTINUE, ASK_USER.

### 5.5 Continuous activity is not implied

An `AgentRecord` or `Delegation` can exist without active work. Control Center labels:
- `record exists, no execution evidence` if state has no detail/heartbeat;
- `recent activity unknown (last event …)`;
- `running but unverified`;
- fresh data only from state transition and event evidence.

Do not represent agents as alive or actively working merely because they are not completed; expired/cancelled/failed are explicit terminal states.

## 6. Intervention and permission semantics

### 6.1 Existing interventions and limitations

- Create task: `POST /tasks` transitions `created -> running` and starts background execution.
- List/read task and events: read-only.
- Cancel task: `POST /tasks/{task_id}/cancel`; lease must be acquired by API, state validation applies.
- Global stop: `POST /stop`; does not require lease and force-cancels most recent nonterminal task. Useful when lease held by executor.
- Resume: `POST /tasks/{task_id}/resume`; restricted to `interrupted` or `paused`; starts new executor.
- Reply to waiting task: `POST /tasks/{task_id}/reply`; records durable `user.replied`, then resumes execution.
- Objectives may be paused, resumed, abandoned separately.
- Main agent can be paused/resumed.
- Skill disabling may impact paused task before next unstarted step.
- Audit events are required for any operator intervention, including before-state, after-state, and operator reason.

Not implemented:
- explicit retry action for terminal failed task;
- explicit pause by API may only exist at step/objective/main-agent level; task-level pause needs path confirmation;
- no priority change/reconnection policy interface to existing backend, likely must be implemented if needed.
- deferred pause/resume may not block current OS calls already underway; they only prevent next action.

### 6.2 Matrix

| Operation | Backend enforced? | Allowed states | Verified? |
|---|---|---|---|
| create | yes | system | source-inspected |
| cancel | yes | nonterminal | runtime-tested |
| force stop | yes | nonterminal | source-inspected |
| resume | yes | interrupted/paused | source-inspected |
| reply | yes | waiting_for_user | source-inspected |
| pause task | partial/contextual | depends on state | not fully verified |
| retry | no | n/a | not implemented |
| recovery from crash | yes via interrupted state + lease stale takeover | source-inspected | partly runtime-tested |
| resource replenish | not scope | n/a | n/a |

Control Center should only expose implemented operations. Any unimplemented operation must be flagged as future implementation requiring code before UI.

## 7. Update freshness and transport

### 7.1 Existing behavior

- Dashboard tasks: 2s active, 15s idle.
- Event timeline: 5s polling per active task.
- Event replay uses `after_sequence`.

### 7.2 Recommended freshness targets

| Data | Target freshness |
|---|---|
| task status (active) | <= 5s, ideally 2s as current |
| active tool/event updates | <= 5s, exact pending events eventually |
| approval request | <= 2s |
| cancellation/stop ack | immediate read followed by retry; bounded by executor polls |
| failure notifications | <= 5s |
| observer fallback | <= 60s with retries, reconciliation |

### 7.3 Transport recommendation

Prefer retaining polling initially, because:
- repo already implements safe replay;
- introducing streaming requires additional lifecycle and ordering semantics;
- polling exact reconciliation already preserves event sequencing;
- escaping this requires introducing missed-event, reconnect, and ordering handling.

Consider push channel only if:
- required latency demonstrably below 5s for error/cancellation/authorization;
- sustained user experience needs;
- after implementing a canonical reconnect catch-up algorithm (using `after_sequence` fallback);
- costs of added infrastructure become justified.

No streaming infrastructure attempt in this task.

## 8. Frontend-facing contract: persisted, derived, unknown

### 8.1 Persisted truth values

- original request
- state
- version
- timestamps
- terminal reason
- objective link
- final result
- step state
- event sequence/content/timestamps
- delegation lifecycle
- agent lifecycle state
- lease expiration, pid

### 8.2 Derived values

- has active in-flight work flag
- step blocked presence
- UI progress phrase
- task status badge count
- aggregate completion ratio
- stall suspicion
- health phrase
- possible executor stop

### 8.3 Unknown values

- true underlying OS success when event data only describes
- why some steps remain blocked if reason omited
- exact current operation duration estimate
- agent execution liveness from record alone
- future recovery time
- repository-level evaluator performance if not supported

## 9. End-to-end example

Request: “Analyze secure storage migration.”

1. System creates durable task; emits `task.created`.
2. Task transitions `created -> running`; emits execution started event, tools count, background execution.
3. Planner links objective and steps; step status `ready`.
4. Delegation service proposes research task; emits proposed delegation.
5. Delegation validated, assigned, running; results verify and integrate.
6. Parent task execution observes latest event, writes action events and step completion.
7. Follow-up complete: task `running -> completed` after verification conditions are met.
8. Frontend captures list state poll; event replay publishes all durable events with sequences.
9. If interrupted during step, state becomes `interrupted`; operator resumes; re-observes before next action to prevent duplicate effects.
10. If user cancels, final state becomes `cancelled`; any pending action then learns cancellation and stops before next OS action.
11. Control Center can show what is proven versus what is only described.

## 10. Prioritized gaps and dependencies

### 10.1 Blocking detailed UI design

- Event taxonomy needs formal mapping from actual event types to display-friendly certainty classes.
- Define progress interpretation per lifecycle.
- Clarify API batch endpoint for tasks vs events.
- Document missing process liveness beyond lease.

### 10.2 Before release

- expose lease info / task liveness endpoint if needed for operator usage;
- endpoint to get task links to delegation/plan for supervision;
- task-level state only where evidence exists;
- improve task-level status in events (e.g., `task.state_changed`), if current records aren't enough.

### 10.3 Valuable later

- automated stall confirmation using execution family expectations;
- event severity fields;
- metrics overlays;
- websocket immediately if higher latency requirement;
- delegation-specific control APIs.

### 10.4 Not currently justified

- new task `blocked` state
- second event store
- automated retries before root cause clear
- completion score algorithms without evidence baseline
- broad metrics aggregation dashboard if still encouraging unsafe interpretation.

## 11. Verification and source distinction

Source/code evidence cited in prior audit. Runtime-tested explicitly:
- event replay, strict sequences, disconnected catch-up
- cancel behavior
- cancellation eventually blocking next action
- lease expiration stale takeover (referenced in existing acceptance test set; not all re-run)
- provider-backed delegated execution has only gated/non-live test support unless documented by phase reports. Consider those UNVERIFIED for current runtime.

## 12. Unresolved questions

- Whether task-level pause currently occurs via model-loop, and which tool initiates it.
- Whether event payloads generally include `action.started/completed` in all tool runs, or only some.
- Which events are model-generated versus generated after a tool result.
- Whether style-criteria completion needs manual verification or automatic.
- Which additional endpoints are needed to fetch agent/delegation relationship graph efficiently.

## 13. Recommended next task (not performed)

Additive and scoped next step: augment backend API with a deterministic, documented delegation link endpoint and task supervision payload, first through code tests, without UI. This contract would make sidebar relationship supervision explicit and queryable. Programmatic changes only in authorized scope with a new task.
