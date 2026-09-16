# Concurrency Architecture (Phase 10.75 — Hardened)

## 1. Concurrency Model

Sythion's Phase 10 multi-specialist coordination adds controlled parallel execution under a staged gate. The model is **cooperative, DB-backed, and lease-guarded** — not free-threaded shared memory.

```
DelegationPlan (durable, DelegationPlanStatus DRAFT→ACTIVE→COMPLETED)
   │
   ├── Delegation A ──┐
   ├── Delegation B ──┼── controlled concurrent execution (when stage permits)
   └── Delegation C ──┘
             │
             ▼
      independent state (DelegationStatus lifecycle)
      independent budgets (per-delegation spent, plan authoritative)
      isolated cancellation (ContextVar per execution)
      correct event streams (per task_id sequence_num)
             │
             ▼
       ResultAggregator (deterministic ordering, conflicts, confidence)
             │
             ▼
          Main Agent (integration authority)
```

What is truly concurrent:
- **Stage 1 reasoning-only specialists** (system_observation, activity_recall, reasoning_model) may run concurrently as `asyncio` tasks with isolated sessions, isolated budgets, isolated event streams.
- Model calls via `ModelSelector → ModelProvider` are concurrent only when provider client is proven concurrency-safe; otherwise serialized.
- Non-destructive reads (ledger, memory recall) are concurrent per task.

What remains serialized (authoritative):
- Profile `RunnerLease` (id=1) — single instance enforcement.
- GUI automation — dedicated UIA MTA worker, single queue.
- File operations — same-file / same-directory / rename races serialized.
- System-control / package / browser / voice — serialized.
- Plan creation — `_plan_lock` + SQLite writer serialization.
- Delegation admission — conditional `UPDATE ... WHERE < limit` atomic via SQLite writer serialization.

## 2. Execution Ownership

```
RunnerLease (profile, authoritative)
  └─ SpecialistExecutionSlot[] (child ownership under plan, per-delegation)
       plan_id, delegation_id, owner_nonce, expires_at, status ACTIVE/RELEASED
```

- Parent orchestration ownership (`RunnerLease`) is never duplicated. Specialist tasks have independent `tasks.id` (`delegation:<id>:<attempt>` idempotency).
- Slots are **child ownership only**, not competing authority. Failure to acquire slot ≠ lease failure.
- Each specialist task has independent cancellation, budget, event sequence, result, recovery state.

## 3. Slot Allocation

- Policy: `ParallelismPolicy` from `core/config.py` (`parallelism_enabled`, `max_parallel_specialists`, `max_parallel_model_calls`, `max_parallel_runtime_actions`, `stage`).
- Atomic invariant: `actual_active_specialists <= configured limit` at all times, not just post-run.
- Implementation (`coordination.py:acquire_specialist_slot`):
  - Fast-path count check, then re-check under writer serialization, then `INSERT`, then post-insert validation (count after insert > limit → rollback). Two concurrent acquirers cannot both succeed over limit.
  - Global and per-plan limits both enforced.
  - Multi-process contention via separate `DatabaseManager` sessions sharing same WAL file also enforced (SQLite serializes writers).
- Fairness: `DelegationGraph.ready_set()` sorts by `priority asc, created_at, id` deterministically; starvation prevention via stable ordering. Tests prove no permanent starvation, failed specialists release slots reliably, completed specialists release via `release_specialist_slot`.

## 4. Cancellation

- Cancellation scopes use `ContextVar` (`tools/executor.py:PolicyContext` carried by `_EXECUTION_CONTEXT` ContextVar). Each `asyncio` task gets its own context copy; `current_execution_context()` is per-task. No leak.
- Hierarchical:
  - **Parent cancelled → all child specialists cancelled** (`DelegationPlanService.cancel_plan`): iterates delegations, transitions each non-terminal to `CANCELLED`, appends `agent.cancelled` events, marks specialist `tasks` as `cancelled` where present, transitions plan to `CANCELLED`.
  - **Child cancelled → parent continues** (`cancel_delegation`): only `MAIN` may cancel (authority guard `require_main_actor`), isolated event `agent.cancelled {isolated:true}`, no auto-parent cancellation. Completion policy decides plan outcome.
  - Specialist cannot cancel another directly — guard enforces `only MAIN may cancel`.
  - Already VERIFIED/INTEGRATED results remain auditable after parent cancellation; not rewritten as failed (recovery preserves evidence).
- Event-level cancellation: `ToolExecutor.execute_tool` installs `PolicyContext(cancelled_check=...)` before validation/target-check/action; checks before OS call; `_atomic_write_text` checks before `os.replace`.

## 5. Budgets

- No in-memory budget authority. **Database is authoritative** (`DelegationPlan.budget_json`, `Delegation.budget_json.spent`).
- Plan budget: `max_total_steps`, `max_total_model_calls`, `max_total_time` (from `core/config.py`).
- Per-specialist spent: `actions`, `model_calls`, `elapsed_seconds` (from `SpecialistRuntime.cost_snapshot()`).
- Atomicity (Phase 10.75 hardened):
  - `DelegationPlanService._atomic_budget_available()` sums spent across plan inside serialized view.
  - `atomic_commit_spent(delegation_id, spent)` — now `BEGIN IMMEDIATE` writer-serialized read-compute-write; writes spent only if total after write ≤ plan budget; otherwise rollback and returns `False`. Second concurrent writer blocks until first commits, so it sees updated totals and fails correctly. Prevents the classic `read(used=0)` race where both writers think `2 <= 4`.
  - Recovery semantics: `transaction committed` → durable spend (no refund on process death); `transaction rolled back` → no spend; `process death during transaction` → rollback (never partial). DB is ground truth; never refund on death alone.
  - Legacy `_budget_available` retained for readiness gating; hardened path is `atomic_commit_spent`.
- Tool/Mutating budgets still via `SpecialistRuntime._enforce_budget()` (per-specialist caps `max_specialist_steps/time/model_calls`).

## 6. Database Transactions

- SQLite WAL mode (`PRAGMA journal_mode=WAL`) for concurrent reads; writer serialization for admission/budget/slot.
- Deterministic boundaries: `DelegationPlanService.create_plan` is atomic — validates everything before persistence, commits once. No partial graph.
- Delegation admission (Phase 9.75) via conditional `UPDATE delegations SET status='VALIDATED' WHERE id=:did AND status='PROPOSED' AND (SELECT COUNT(*) ...) < :limit` — cross-process atomic.
- Event appends participate in caller transaction; `DbEventLedger.append_event` accounts for `session.new` pending rows to avoid `(task_id, sequence_num)` collisions under `autoflush=False`. Sequence per `task_id` (independent specialists have different task_ids → no global sequence contention).
- Temporary test DBs for stress tests; never user's real DB (`Pragma integrity_check = ok` after each stress class).

## 7. Event Ordering

- Per-task sequence: `DbEventLedger` allocates `max(last_seq, pending_max) + 1` per `task_id`.
- Guarantees: no duplicate `(task_id, sequence_num)`, no lost events, no cross-task contamination (verified by concurrent thread test with 2 tasks × 10 events).
- Replay: `event_history(task_id)` ordered by `sequence_num ASC` produces same per-task history; aggregation sees all required events.

## 8. Capability Concurrency Classes

Machine-readable truth table: `backend/agents/capability_concurrency.py`

| Capability | Class | Stage | Rationale |
|---|---|---|---|
| system_observation | REASONING_PARALLEL | STAGE_1 | Read-only OS queries, stateless |
| activity_recall | REASONING_PARALLEL | STAGE_1 | Immutable ledger reads |
| reasoning_model | REASONING_PARALLEL | STAGE_1 | Provider-isolated; fallback state not leaked |
| persistent_memory | NON_DESTRUCTIVE_PARALLEL | STAGE_2 | Scoped writes, SQLite serialized |
| goal_planning | NON_DESTRUCTIVE_PARALLEL | STAGE_2 | Deterministic validation, no OS mutation |
| file_operations | SERIAL_ONLY | STAGE_0 | Same-file/dir/move races unproven |
| gui_automation | SERIAL_ONLY | STAGE_0 | MTA worker always serialized |
| system_control | SERIAL_ONLY | STAGE_0 | Mutating OS, HIGH risk |
| app_control | SERIAL_ONLY | STAGE_0 | Desktop mutation |
| package_management | SERIAL_ONLY | STAGE_0 | System mutation |
| browser_control | SERIAL_ONLY | STAGE_0 | External bridge |
| voice | SERIAL_ONLY | STAGE_0 | Hardware |
| self_authoring_tools | SERIAL_ONLY | STAGE_0 | DB writes unproven parallel-safe |
| UNKNOWN | UNKNOWN→SERIAL_ONLY | — | Defaults to serialized |

`capability_is_concurrent_safe(capability_id, stage)` consults truth table first; `UNKNOWN` always `False`. Stage gates: `STAGE_0` none concurrent, `STAGE_1` reasoning-only set, `STAGE_2` non-destructive set, `STAGE_3` all except GUI.

## 9. GUI Serialization

- Always serialized: `capability_is_concurrent_safe("gui_automation") == False` at every stage.
- Implementation: single `queue.Queue` + background `threading.Thread` (`_UIAWorker`) running as COM MTA; `run_on_worker` via `asyncio.to_thread` with 10s timeout.
- Gates: `_desktop_gate` (session locked/secure desktop), `_target_window_safety` (visible, not minimized, not secure/elevated), `_require_foreground` before SendInput.

## 10. Filesystem Constraints

- Distinct files: allowed only if capability policy permits; currently `file_operations` is `SERIAL_ONLY`, so distinct-file writes are **serialized** in production. Test proves distinct files succeed sequentially when roots permit.
- Same file: serialized or deterministically rejected (`FILE_EXISTS` without overwrite).
- Same directory, different files: explicitly tested; allowed sequentially but not claimed as general parallel-safe.
- Conflicting rename/move: tested; second move to same destination without overwrite fails deterministically.
- Do NOT claim general filesystem parallel safety if only distinct-file writes work — docs and tests state `SERIAL_ONLY`.

## 11. Provider Concurrency (Phase 10.75 — per-model lock)

- Path: `ModelSelector → ModelProvider → OllamaCloudAdapter`. Adapter previously had single `_capability_profile` / `_capability_failed_at`; now per-model `dict[model_id -> Profile]` immutable replace and per-model `dict[model_id -> failed_at]` negative cache, with per-model `asyncio.Lock` (narrow, no global bottleneck) via `_capability_lock_for(model_id)` under a `threading.Lock` protecting the lock map.
- Guarantees validated in `test_phase1075_hardening.py::TestProviderCacheIsolation` with mocked provider:
  - Success race same-model: 6 concurrent cold starts → 1 probe, not 6.
  - Failure race same-model: cooldown per model, no poison to other models.
  - Mixed success/failure models: `model-a` success concurrent with `model-b` failure — no cross-poison, cache entries stay associated with correct model/provider.
  - Repeated restart/cold start: cached after first probe, bounded (2 probes for list+chat), new adapter instance again bounded.
- `httpx.AsyncClient` concurrent `chat` calls remain isolated per specialist (each call carries its own `model_id`); provider fallback state per specialist, one failure doesn't corrupt another.
- Real-provider E2E (`SYTHION_RUN_PROVIDER_E2E=1`, 2 reasoning specialists, shared parent budget) still gated but now asserts no cache corruption.

## 12. Recovery

- Desired persisted state mid-crash:
  ```
  A = COMPLETED/VERIFIED (preserved)
  B = RUNNING (recovered from task evidence)
  C = READY (VALIDATED) (stays READY)
  D = BLOCKED (depends on B, remains BLOCKED until B VERIFIED)
  ```
- `DelegationPlanService.recover_plans()` for `ACTIVE` plans:
  - `RUNNING` without `specialist_task_id` → `ASSIGNED`
  - `RUNNING` with missing task → `ASSIGNED`
  - `RUNNING` with `task.state == completed` → `RESULT_READY` (evidence reconstructed)
  - `RUNNING` with `task.state in (failed,cancelled,interrupted)` → `ASSIGNED`
  - Otherwise leave (lease takeover path).
- Guarantees: no duplicate task (idempotency `delegation:<id>:<attempt>`), no forged result (verification requires ledger evidence), no lost event, no dependency corruption, no double budget charge (spent only committed atomically).

## 13. Rollout Stages (Phase 10.75 production policy)

```
Stage 0 = safe default (parallelism_enabled=false)
Stage 1 = opt-in if validated (SYTHION_PARALLELISM_ENABLED=1, per-model provider lock proven, ToolExecutor audit clean)
Stage 2 = disabled unless independently validated (DB safety proven for same-key/different-key but semantic merge insufficient — see docs/CONCURRENCY_HARDENING.md §4)
Stage 3 = disabled
```

| Stage | Meaning | Status |
|---|---|---|
| STAGE_0_SERIALIZED | Coordination only, serialized via profile lease | **SUPPORTED** (default) |
| STAGE_1_REASONING_ONLY | Parallel reasoning-only (system_observation, activity_recall, reasoning_model) | **SUPPORTED_WITH_RESTRICTIONS** — opt-in via `SYTHION_PARALLELISM_ENABLED=1`, provider per-model lock validated, file/GUI remain SERIAL_ONLY, budget atomic via `BEGIN IMMEDIATE` |
| STAGE_2_NON_DESTRUCTIVE | Parallel non-destructive runtime (persistent_memory, goal_planning + Stage 1) | **DISABLED** — DB corruption closed but semantic merge still last-wins; do not enable on DB test alone |
| STAGE_3_FULL | Broader capability parallelism (except GUI) | **DISABLED** — not validated |

No auto-enable of new stages; operator must set env explicitly. See `docs/CONCURRENCY_HARDENING.md` for hardening evidence.

## 14. Stress Tests (exact workloads — Phase 10.75 extensions)

- Stage 1 real parallelism: `N = max_parallel_specialists` (2) asyncio tasks with separate sessions, separate task_ids, separate budgets/events.
- Budget atomicity: 3 threads each `atomic_commit_spent` with `{actions:2}` against plan budget `max_total_steps=4`; invariant `sum ≤4`; now `BEGIN IMMEDIATE` serialized.
- Budget crash: gated `SYTHION_RUN_PROCESS_KILL=1` writer `TerminateProcess` mid-`atomic_commit_spent` → fresh reader `integrity_check=ok`, no partial spend, total ≤ limit.
- Recovery semantics: committed vs rolled-back tests assert DB authoritative, no refund on death alone.
- Cancellation isolation: 3 concurrent RUNNING, cancel only A → A CANCELLED, B/C RUNNING.
- Parent cancellation: plan with VERIFIED + RUNNING + VALIDATED, cancel parent → plan CANCELLED, no new READY, existing result auditable.
- Dependency: A,B → C (C depends_on ["0","1"]); only VERIFIED unlocks C.
- Fairness: 4 READY, `max_parallel_specialists=2` → 3rd acquire fails, release → acquire succeeds, long-run 5-slot round-robin.
- Event ledger: 2 tasks × 2 threads × 5 events = 20 events, verified no duplicate seq, no loss, replay stable.
- SQLite stress: 4 threads × 5 cycles of state/budget/event/slot transitions; `PRAGMA integrity_check=ok`.
- File: distinct files, same file, same-dir different files, conflicting move.
- Model: 3 concurrent `SpecialistReasoner.analyze()` with distinct model_ids; failure isolation.
- Provider cache isolation: 4 mocked tests (success race / failure race / mixed / restart bounded) proving per-model lock, no cross-poison.
- Memory same-key 6 writers / different-keys 10 writers → `integrity_check=ok`, exactly one ACTIVE per key, no duplicate ACTIVE, no lost unrelated, provenance preserved.
- Recovery: A VERIFIED preserved, B RUNNING→RESULT_READY, C READY, D BLOCKED.
- Partial failure: SUCCESS/FAILED(TIMEOUT)/FAILED(VERIFICATION)/CANCELLED under ALL_REQUIRED → not ready.
- Aggregation: 3 VERIFIED with conflicting X vs Y → conflict detected, confidence meaningful.
- Multi-process slot limit: 4 processes contending for 2 slots → `actual_active ≤2` verified via DB count.
- Stage 1 process-kill: gated 2 reasoning specialists → `TerminateProcess` → restart → `integrity_check=ok`, no duplicate tasks, no forged VERIFIED, budget correct, dependency intact, `recover_plans` sane.
- Provider E2E parallel: gated `SYTHION_RUN_PROVIDER_E2E=1`, 2 reasoning specialists with real provider, shared budget, attribution check.
- Soak: 55 cycles × 2 specialists = 110 specialists mixed outcomes → `integrity_check=ok`, no negative budgets, no impossible states, no duplicate ids.

## 15. ToolExecutor & Registry Hardening (Phase 10.75)

Shared-state audit (`docs/CONCURRENCY_HARDENING.md` §2) found one load-bearing shared mutable: `ToolExecutor._tools` dict. Fix: `RLock` around `register_tool`/`get_tool`/`list_tools`; `execute_tool` lock-protected lookup then unsynchronized `tool.execute` (tools are stateless). Registry mutation remains serialized by policy — no concurrent `register_tool` during active execution; tests prove concurrent `list_tools`+`get_tool` while `execute_tool` active does not corrupt. `CapabilityRegistry` is stateless recomputation from `list_tools()` snapshot. No new shared mutable execution state introduced.

## 16. Observability

Machine-readable `logger.info("concurrency.* ...")` records `specialist_id`, `task_id`, `delegation_id`, `model_id`, `execution_slot`, `parallel_stage`, `budget_before/after`, `provider_request_id`, `start/end`, `recovery_state` where available. No secrets, no CoT, no new event store.
