# Protected Runtime Contract

**Status:** Active (Phase 0). This document is the architectural contract for
every current and future component of Sythion. Each invariant listed here is
enforced by a code boundary; the enforcing code is cited inline. Future
intelligence layers (see `TARGET_INTELLIGENCE_ARCHITECTURE.md`) may sit *above*
this runtime but must never weaken or bypass these guarantees.

---

## 1. Authentication

- The sidecar binds to loopback only (`core/config.py: Settings.host` defaults
  to `127.0.0.1`; launcher passes an explicit loopback port).
- Every `/api/*` request (HTTP and WebSocket) is authenticated with the
  per-launch control token issued by the Tauri shell handshake
  (`core/auth.py::LocalControlBoundaryMiddleware`, `core/handshake.py`).
- Origin validation: dashboard origins only (`tauri://localhost`, packaged
  variants; dev origins opt-in). Browser-extension origins are accepted
  exclusively on browser-bridge paths and only together with the token
  (`core/auth.py::_origin_allowed`, `EXTENSION_ORIGIN_SCHEMES`).
- Non-loopback peers are rejected regardless of credentials
  (`core/auth.py`, client check).
- WebSocket handshakes authenticate via query-string token because browsers
  cannot set WS headers (`core/auth.py::extract_websocket_token`). There is
  deliberately no unauthenticated path, including `/api/v1/browser/*`.

## 2. State ownership

- `TaskRunner` is the sole authority for task-state transitions
  (`services/task_runner.py::transition_state`, `ALLOWED_TRANSITIONS`).
  No other component writes `tasks.state`.
- All state changes append immutable, sequence-numbered events
  (`TaskEvent`); the event history is the authoritative execution record.
- Single-runner semantics via the profile-wide lease
  (`RunnerLease`, `acquire_lease`), plus the process-level profile lock at
  startup (`main.py` lifespan).

## 3. Execution

Every tool action passes this fixed pipeline inside `ToolExecutor.execute_tool`
(`tools/executor.py`) before any OS call:

1. Deterministic policy gate (`_policy_error`: no elevation, direct
   instruction required for confirmation-classed actions).
2. Input schema validation (Pydantic or declared JSON schema).
3. Deterministic target validation (`BaseTool.validate_targets`), including
   OS-handle-based file-root containment re-checks and reparse-point refusal.
4. Cancellation check immediately before the OS action.
5. Execution.
6. Post-execution verification (`BaseTool.verify`) — a failed read-back flips
   the result to failure even if the OS call "succeeded".

The importance classifier may additionally park a consequential action for
explicit user approval (`core/importance_classifier.py`,
`task_execution_loop._execute_tool`).

**Execution-scoped context (Phase 1 clarification):** the cancellation check
and task identity travel in an execution-scoped `PolicyContext` installed via
a ContextVar for the duration of each tool call (`tools/executor.py::
execution_context`). Tool instances hold no per-task state, so one task's
cancellation state can never affect another task — including under future
concurrent workers. Kernel structure is documented in
`RUNTIME_KERNEL_ARCHITECTURE.md`.

## 4. Completion

A model cannot declare success without durable evidence:

- `_completion_precondition_error`
  (`services/task_execution_loop.py`) refuses completion when uncertain
  actions exist, when the final mutating tool failed, or when its verification
  did not pass.
- Repeated refused completions fail the task honestly
  (`COMPLETION_BLOCKED`).
- Budget exhaustion fails honestly with a specific code; it is never retried,
  mutated past, or locally papered over (`core/budgets.py`).

## 5. Audit

- Consequential actions record `action.started` durably *before* the OS call
  (`TaskRunner.record_action_started`).
- Every execution appends an immutable `tool.executed` event with evidence,
  duration, mutation flag, and verification status.
- Event history is replayable from any sequence number
  (`GET /api/v1/tasks/{id}/events?after_sequence=N`).

## 6. Secrets

- Secrets live in Windows Credential Manager behind the write-only secret
  store (`core/secrets.py`); values are never returned by any endpoint.
- Secrets never enter `.env`, SQLite, ordinary logs, task events, exports, or
  model context. Only non-secret settings persist
  (`core/settings_store.py` whitelist).

## 7. Process isolation

- **The core process never executes Python that originates from database
  records, model-authored tool proposals, or untrusted external content.**
  Restricted builtins are explicitly NOT considered a sandbox.
- Proposal implementations are stored verbatim as inert review data. The
  authoring pipeline validates them with static AST analysis only
  (`services/tool_authoring.py::static_interface_check` — no compile/exec).
- Startup counts enabled proposals but never loads or executes them
  (`main.py` lifespan, "PROTECTED RUNTIME CONTRACT" block).
- Approval records an owner review decision only
  (`tool_authoring.approve_tool_proposal`); nothing is registered into the
  executor.
- Executable enablement of custom tools requires a genuine out-of-process
  isolation boundary and is deferred to a later phase. If/when introduced,
  the boundary must be documented with exactly what isolation it does and
  does not provide; restricted-builtins `exec()` is not acceptable.

## 8. Browser

- Browser control (extension bridge + UIA observation tools) cannot bypass
  authentication: bridge routes sit behind the same middleware as all API
  routes; the extension authenticates with the per-launch token and an
  extension-scheme origin (`core/auth.py`, `api/browser_bridge_routes.py`).
- Loopback-only exposure; LAN/network clients are unreachable by binding and
  rejected by peer check.
- Extension-side permissions stay minimal for function
  (`extension/manifest.json`); no static secrets are committed — the token is
  configured per launch and stored only in local extension storage.

## 9. Recovery

- An interrupted or uncertain action remains **uncertain** until re-observed
  or otherwise verified: stale-lease takeover marks the running task
  `interrupted` and records `action.uncertain` for any active action
  (`task_runner._mark_task_interrupted`); resume requires explicit user
  action (`POST /tasks/{id}/resume`) and re-observation before mutations.
- Uncertainty blocks completion (`_completion_precondition_error`).

## 10. Model authority

The LLM is **never** the source of truth for:

| Concern | Actual authority |
|---|---|
| Task state | `TaskRunner` + task/event ledger |
| Permissions | Executor policy gate + importance classifier + owner approvals |
| Security | Middleware, executor pipeline, secret store |
| Verification | `BaseTool.verify` read-backs recorded as events |
| Persistence | SQLite ledger / stores |
| Audit history | Immutable `TaskEvent` stream |

Model decisions are proposals that deterministic code validates, gates,
executes, verifies, and records. A model claim of success without recorded
verification is refused, not trusted.

---

## Lease timing (governing constants)

Single source of truth: `core/config.py`.

- Expiry / stale-takeover threshold: `settings.lease_timeout_seconds`.
- Heartbeat interval: `settings.lease_heartbeat_seconds` (must be strictly
  smaller than expiry; enforced by `ensure_lease_contract()` at import).
