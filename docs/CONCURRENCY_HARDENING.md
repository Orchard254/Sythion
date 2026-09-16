# Concurrency Hardening — Phase 10.75

> Closes remaining Stage 1 shared-state and crash-safety risks before learned delegation. Validation only for Stage 2.

## 1. Provider Synchronization

**Problem:** `OllamaCloudAdapter` held single `_capability_profile` / `_capability_failed_at` on a singleton. Concurrent specialists cold-starting the same model raced on `list_models` + `POST /api/chat` probe; different models could poison each other's cache.

**Fix (`backend/adapters/ollama_adapter.py`):**
- Per-model cache: `_capability_profiles: dict[model_id -> Profile]` immutable replace; `_capability_failed_at_map: dict[model_id -> float]` per-model negative cache (60 s cooldown).
- Per-model keyed `asyncio.Lock` via `_capability_lock_for(model_id)` under a `threading.Lock` protecting the lock map. Only same-model cold starts serialize; different models never block (no global bottleneck).
- `ensure_capabilities(model_id=None)` resolves `target_model = model_id or selected_model`; double-checked locking inside `async with lock:` prevents duplicate probes.
- Probe `POST` uses `target_model`, not hard-coded selected; failure sets per-model `failed_at` only; success does immutable `profiles[target_model]=profile`; legacy `_capability_profile`/`_capability_failed_at` kept synced for selected model for backward compatibility.

**Tests (`test_phase1075_hardening.py::TestProviderCacheIsolation`):**
- `success_race_same_model`: 6 concurrent `ensure_capabilities()` → 1 `list_models` + 1 `chat` probe (per-model lock, not 6).
- `failure_race_same_model`: 4 concurrent failing probes → both raise, cooldown prevents extra probes, different model (`model-ok`) unaffected.
- `mixed_success_failure_models`: concurrent `model-a` success + `model-b` failure → `model-a` succeeds, `model-b` raises, no cross-poison; second `model-a` hits fast-path cache.
- `repeated_restart_cold_start_bounded`: first cold start 2 probes (list+chat), next 5 calls cached (still 2), new adapter instance again 2 — bounded, not unbounded.

## 2. ToolExecutor Shared-State Audit

| Component | Mutable? | Shared? | Concurrently Safe? | Action |
|-----------|----------|---------|--------------------|--------|
| `ToolExecutor._tools` dict | Yes — written at startup via `register_all_tools`, read via `get_tool`/`list_tools`/`execute_tool` | Yes — singleton shared by all specialists | **Reads concurrent, writes serialized** via `RLock` + policy "registry mutation remains serialized" | Added `._registry_lock = RLock()`; `register_tool`/`get_tool`/`list_tools` hold lock; `execute_tool` does lock-protected lookup then releases before `tool.execute`. Documented. |
| `Tool instance` (e.g., `FileCreateTool`) | No — `name/description/input_schema` frozen; `execute/verify/validate_targets` pure aside from `self._is_cancelled()` reading ContextVar | Singleton shared | Yes — no instance mutable state | None — verified stateless. |
| `ContextVar _EXECUTION_CONTEXT` | Yes per execution | No — `ContextVar` copy per asyncio task | Yes — isolated | Existing. |
| `PolicyContext` (`task_id`, `cancelled_check`) | Frozen dataclass, per execution | No | Yes | Existing — execution-scoped. |
| `cancellation state` | Via `PolicyContext.cancelled_check` async predicate | Per execution | Yes | Existing. |
| `timing state` | `time.time()` local | No | Yes | — |
| `active-action state` | `Task.active_action_id` per task row | No (per `Task.id`) | Yes — isolated | — |
| `CapabilityRegistry` | Stateless — recomputes `resolve_status` from `executor.list_tools()` + OS + bridge each call | Shared lookup | Yes — `list_tools()` now snapshot under lock | Documented read-only. |
| `CapabilityRegistry tool set` | Determined by `ToolExecutor._tools` | Shared | Reads safe via lock | Mutation remains serialized (see above). |
| `verification state` | `tool.verify` side-effect free | No | Yes | — |
| `class-level state` | Removed in Phase 1 (`_active_cancelled_check` deleted) | — | — | Verified absent. |
| `module-level state` | `_adapter`, `_provider`, `_default_provider` singletons | Yes | `OllamaCloudAdapter` now per-model locked; provider facades are stateless wrappers | Fixed above. |
| `SQLite/WAL` | File shared | Yes | Yes — WAL + writer serialization | Verified. |
| `MemoryService` | `memory_entries` UNIQUE `key` + `status` | Shared file | **DB corruption prevented** via UNIQUE retry + `SUPERSEDED` rename; semantic merge still last-wins | Added `IntegrityError` retry in `create`/`update`; same-key concurrent creates deterministically supersede, exactly one ACTIVE per key. |
| `SpecialistRuntime` | `actions_used`, `model_calls_used`, `started_at` per instance | No — per specialist | Yes — isolated | Plus observability logging added (no secrets). |
| `SpecialistExecutionSlot` | DB row per plan | Shared table | Yes — `BEGIN IMMEDIATE`-style check + post-insert validation | Existing. |
| `GUI MTA` | `_gui_worker` queue + thread, `_uia_driver` | Yes — singleton | **SERIAL_ONLY** — never claimed parallel-safe | Truth table hard `SERIAL_ONLY` at all stages; `capability_is_concurrent_safe` always False. |
| `File tools globals` | `_CTYPES_READY` bool, `_RESERVED_NAMES` frozenset | Shared read-only after init | Yes | — |
| `Budget` | `DelegationPlan.budget_json` + `Delegation.budget_json.spent` | Shared plan row | **Atomic `BEGIN IMMEDIATE` commit** | Hardened `atomic_commit_spent` (see §3). |

**Finding:** No hidden shared mutable execution state remains that is reachable from concurrent reasoning-only specialists without synchronization or SERIAL_ONLY gating. File/GUI/browser/package/system_control remain serialized by truth table.

## 3. Budget Transaction Semantics

**Authoritative:** SQLite DB, `DelegationPlan.budget_json` + per-delegation `budget_json.spent`. In-memory sums are never authoritative.

```
transaction committed  → durable spend (visible after TerminateProcess, no refund)
transaction rolled back → no spend (reader sees 0, remaining budget unchanged)
process death during transaction → rollback (no partial spend, no duplicate)
never "refund" on death alone — DB transaction is the ground truth
```

**Fix:** `DelegationPlanService.atomic_commit_spent` now:
1. `rollback()` any implicit transaction,
2. `BEGIN IMMEDIATE` to acquire RESERVED lock before reading totals (second writer blocks until first commits),
3. Re-reads totals inside the reserved transaction,
4. Checks `used + adding <= limit` for steps/calls/time,
5. Writes `budget_json` or rolls back, then `commit()`.

Prevents read-compute-write race where two writers both read `used=0` and both think `2 <= 4`.

**Tests:**
- `TestBudgetRecoverySemantics::test_committed_is_authoritative_no_refund` — spend committed → fresh reader still sees 3, death does not refund.
- `test_rolled_back_no_spend` — budget 2, first spend 2 succeeds, second 2 fails (rolled back) → second delegation still 0, total 2.
- Gated `TestAtomicBudgetCrash::test_process_kill_during_atomic_commit` — writer `Terminated` mid-transaction → fresh process `PRAGMA integrity_check=ok`, total ≤ limit, spent is 0 or 2 (never 1/partial/corrupt).

## 4. Memory Concurrency Findings

- **Writes:** `MemoryService.create` previously did `SELECT ACTIVE WHERE key=:k` then `INSERT`; concurrent same-key writers could both see no ACTIVE and both INSERT → UNIQUE violation. Same for `update` supersession second INSERT. **Fix:** `IntegrityError` catch → `rollback` → re-query ACTIVE winner → `update` path deterministically supersedes; second INSERT loser returns winner. No duplicate ACTIVE, no lost unrelated keys.
- **FTS5:** `memory_entries_fts` triggers `AFTER INSERT/UPDATE/DELETE` run inside writer transaction; no separate contention.
- **Stress results:**
  - Same-key 6 writers → no errors, `PRAGMA integrity_check=ok`, exactly one ACTIVE for contested key, history chain preserved, no duplicate ACTIVE keys, contradiction state valid, provenance via `superseded_by`.
  - Different-keys 10 writers → no errors, `integrity_check=ok`, 10 distinct ACTIVE, no lost entries.
- **Stage 2 decision:** DB corruption issue closed, but semantic merge is still last-writer-wins `SUPERSEDED` chain, not CRDT/conflict-free. `CapabilityConcurrency.persistent_memory` evidence updated to note this. **Stage 2 remains DISABLED** for production parallelism. `Stage 3` also remains disabled. Do not enable based solely on this test.

## 5. Stage 1 Validated Boundaries

`ParallelismStage.STAGE_0_SERIALIZED` default OFF (`parallelism_enabled=false`).
`STAGE_1_REASONING_ONLY` opt-in (`SYTHION_PARALLELISM_ENABLED=1`) allows concurrent `system_observation`, `activity_recall`, `reasoning_model` only, validated:
- Separate `DatabaseManager` sessions per specialist sharing same WAL file → isolated tasks/budgets/events/ledgers.
- `SpecialistExecutionSlot` per-plan + global limits via `acquire_specialist_slot` (fast-path count + writer-serialized re-check + post-insert validation).
- `DelegationGraph.is_ready` deterministic (deps must be VERIFIED), `ready_set` sorted by priority/created_at/id, `blocked_reason`.
- Cancellation isolated via `ContextVar`; parent cancel propagates, child cancel isolated.
- Budget atomic via `atomic_commit_spent` as above.
- Provider per-model lock as above; no cross-specialist cache poison.
- Recovery preserves per-delegation states independently.

Stage 2/3 remain gated: `persistent_memory`/`goal_planning` validated for DB safety only, not production; `file_operations`/`gui_automation`/`system_control`/`app_control`/`package_management`/`browser_control`/`voice`/`self_authoring_tools` stay `SERIAL_ONLY`.

## 6. Stage 2/3 Reasons for Remaining Restrictions

- Same-key semantic merge policy insufficient: two reasoning writes to same fact produce supersession chain with last-writer-wins; no evidence-weighted merge or user intent resolution. Turning on parallel Stage 2 would allow specialists to clobber each other's facts without detection.
- `file_operations` serialized: atomic `temp+os.replace` per file is safe, but same-file / same-directory / rename/move races have not been proven safe for arbitrary concurrent specialists; current `SERIAL_ONLY`.
- GUI always serialized via single MTA `queue.Queue` thread; secure desktop/elevated/visibility gates not parallel-safe.
- `Stage 3` is superset of above — remains disabled until each capability individually passes its own concurrency validation.

## 7. Process-Kill Results

Gated `SYTHION_RUN_PROCESS_KILL=1` suite (`test_phase1075_hardening.py`):
- Budget writer terminated mid-`atomic_commit_spent` → fresh reader `integrity_check=ok`, total ≤ limit, no partial spend.
- Two reasoning specialists terminated mid-slot+event+budget → fresh reader `integrity_check=ok`, no duplicate `delegation_id` tasks, no forged `VERIFIED` without `result_json`, budget totals correct, `DelegationGraph.validate` no cycles, `recover_plans` returns sane dict.

Pre-existing `test_phase9_process_kill` still passes (2 tests).

## 8. Soak-Test Results

`TestSoakAndObservability::test_soak_100_cycles_integrity` — 55 cycles × 2 specialists = 110 specialists with mixed success/failure/cancelled/timeout/budget-exhaustion/duplicate/restart: workload completes, then `PRAGMA integrity_check=ok`, no negative budgets, no impossible `DelegationStatus` transitions, no duplicate delegation `id`s, no orphaned active slots.

Longer soak (100+ cycle) not run in normal CI; gated `slow` variant can be dispatched manually.

## 9. Observability

Added machine-readable `logger.info("concurrency.* %s", json.dumps({...}))` at:
- `SpecialistRuntime.request_action` — `specialist_id`, `delegation_id`, `task_id`, `tool`, `seq`, `elapsed`, `success`.
- `SpecialistRuntime.request_model_call` — `specialist_id`, `delegation_id`, `task_id`, `model_calls_used`, `elapsed`.
- `DelegationPlanService` helper `_obs` — ready to emit `plan_id`, `delegation_id`, `slot_id`, `stage`, `budget_before/after`, `provider_request_id`, `start/end`, `recovery_state` (not yet wired to every path; minimal set added to budget/slot paths).

No secrets, no chain-of-thought stored; not a new event store.

## 10. Production Policy

```
Stage 0 = safe default (parallelism_enabled=false)
Stage 1 = opt-in if validated (SYTHION_PARALLELISM_ENABLED=1, per-model provider lock proven, audit table clean)
Stage 2 = disabled unless independently validated (DB safe but semantic policy insufficient)
Stage 3 = disabled
```

No auto-enable of new stages; operator must set env explicitly.

## 11. Remaining Risks

- Real Ollama Cloud rate-limit/outage paths still rely on 60 s negative cache; long outage will keep returning `ProviderCapabilityError` per model until cooldown — acceptable.
- `httpx.AsyncClient` connection pool shared across specialists on same loop: believed safe per httpx docs, but not stress-tested beyond mocked concurrency; real-provider soak under high concurrency could still reveal pool starvation — monitor.
- Memory same-key last-wins semantics could surprise learned delegation if Stage 2 were ever enabled without a merge policy — keep disabled.
- Tool registry `RLOCK` makes `list_tools` snapshot consistent but does not prevent a caller holding a stale tool reference across a concurrent `register_tool` — mutation remains serialized by policy; if future hot-reload of tools is needed, add versioned registry.

## 12. Evidence Index

- Provider isolation tests: 4 tests in `test_phase1075_hardening.py::TestProviderCacheIsolation`
- Registry stability: `TestRegistryStability`
- Budget atomic + recovery: `TestBudgetRecoverySemantics`, `TestAtomicBudgetCrash` (gated)
- Memory same-key/different-key: `TestMemoryConcurrency`
- Soak 110 specialists: `TestSoakAndObservability`
- Stage 1 kill: `TestStage1ProcessKill` (gated)
- Provider E2E: `TestProviderE2EGated` (gated `SYTHION_RUN_PROVIDER_E2E=1`)
- Pre-existing Phase 10.5 suite still green: 34 passed, 2 skipped.
