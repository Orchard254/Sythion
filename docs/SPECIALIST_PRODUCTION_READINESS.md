# Specialist Production Readiness (Phase 9.75)

Status of the three Phase 9.5 blockers and the resulting Phase 10 readiness
of the bounded specialist architecture. Historical phase reports are not
rewritten; this document is the authoritative readiness statement.

## 1. Admission correctness

**Mechanism.** `DelegationService.validate()` admits via one conditional SQL
statement (`backend/agents/delegation.py:_ADMIT_SQL`):

```sql
UPDATE delegations SET status='VALIDATED', updated_at=:now
WHERE id=:did AND status='PROPOSED'
  AND (SELECT COUNT(*) FROM delegations
       WHERE status IN ('VALIDATED','ASSIGNED','RUNNING','RESULT_READY','VERIFIED')
         AND ((:tid IS NOT NULL AND task_id=:tid)
           OR (:gid IS NOT NULL AND goal_id=:gid))) < :limit;
```

- The invariant `admitted ≤ max_specialists_per_task` is established at the
  write boundary. SQLite's writer serialization makes it hold across
  processes; no post-commit rollback is involved in correctness (the
  post-commit recount remains as defensive validation only).
- Slots are consumed by ADMITTED states only; `PROPOSED` rows never block.
- Duplicate idempotency is equally cross-process: partial UNIQUE index
  `uq_delegations_fingerprint_active` on `delegations.fingerprint`
  (active states only) + `IntegrityError → duplicate_delegation`.

## 2. Concurrency evidence

`test_phase975_admission_multiproc.py` spawns independent OS processes
against a shared file DB:

| test | result |
|---|---|
| exact boundary (2 processes, limit 2) | both admit, total = 2 |
| above-boundary contention (6 processes, limit 2) | ≤ 2 admitted; refusals terminal `FAILED` with `max_specialists_per_task`; zero residual `PROPOSED`; `PRAGMA integrity_check = ok` |
| duplicate objective across processes | exactly one active delegation survives |
| terminal accounting | every proposal ends admitted or explicitly FAILED |

Kill-during-admission (`test_phase975_admission_recovery.py`, gated by
`SYTHION_RUN_PROCESS_KILL=1`): process terminated inside the uncommitted
admission transaction ⇒ row stays `PROPOSED`, zero phantom slots, integrity
ok; the existing recovery pass plus re-validation admits cleanly; duplicate
proposal resolves to the same delegation id.

## 3. Capability correctness

Canonical split (`docs/DELEGATION_ARCHITECTURE.md` §16,
`docs/AGENT_ARCHITECTURE.md` §24):

```
SYSTEM_OBSERVATION (read-only)
    ≠
SYSTEM_CONTROL (HIGH risk, permission-gated:
                system_power_control, brightness_control, system_volume)
```

All 13 capability specs audited (`test_phase975_capabilities.py`):
observation capabilities are pinned to read-only allowlists, mutating tools
are enumerated per spec, HIGH-risk specs require permission, all tools
resolve to registered executor tools, and no role binds `system_control`.
RESEARCH provably cannot reach control tools — neither through role binding
nor through the kernel boundary.

## 4. Provider-backed validation

Gated real-provider E2E (`SYTHION_RUN_PROVIDER_E2E=1`,
`test_phase975_provider_e2e.py`) proves, with a real model through the real
`ModelSelector → ModelProvider → SpecialistReasoner` path:

1. **reasoning-only** (ANALYSIS): substantive findings, explicit limitations,
   confidence separation, non-fallback `specialist.reasoning` ledger event,
   verified result, `integrate(ACCEPT)`;
2. **action-producing** (CODING + harmless `fixture_observation` fixture):
   model-proposed action validated → SpecialistRuntime → ToolExecutor →
   fixture tool executed, `specialist.action:1` evidence matches result refs;
3. budget integrity in both: model calls/actions counted against specialist
   budgets and recorded on the delegation row; parent remains authoritative.

Deterministic failure counterparts run ungated
(`test_phase975_provider_failures.py`): unavailable provider, malformed JSON
(one bounded repair), timeout, budget exhaustion (model-call and step).

## 5. CI strategy

- Default CI: `provider_e2e` tests SKIP — no credentials, no cost.
- Explicit integration: credentialed `workflow_dispatch` job `provider-e2e`
  injects `OLLAMA_API_KEY` secret / selected-model variable at runtime only.
- Process-kill suite unchanged (`SYTHION_RUN_PROCESS_KILL=1`), now including
  kill-during-admission.

## 6. Evidence requirements

A specialist result is only `VERIFIED` when its evidence refs match real
ledger events (`specialist.action:N`, `specialist.reasoning`,
`delegation.context.built`, `runtime.action`). Provider-backed tests assert
non-fallback reasoning events and seq-matched action events — fabricated or
template results cannot pass verification.

## 7. Security posture

The expanded specialist surface stays bounded: specialists still act only
through `SpecialistRuntime` (binding check → budget check → policy-checked
ToolExecutor); `system_control` is HIGH-risk + permission-gated and bound to
no role; fixture tools used in E2E are inert and isolated from user data;
secrets never enter the repo (runtime env injection only).

## 8. Remaining limitations

- External provider calls cannot be deterministically process-killed;
  provider outage behavior is covered by deterministic failure tests and
  Phase-6 bounded fallback instead.
- Specialist execution remains serialized behind the profile-wide lease
  (concurrent Runtime execution is Phase 10 scope).
- Legacy delegation rows keep `NULL` fingerprints (index ignores them);
  cross-process duplicate protection applies to newly proposed rows.

## 9. Phase 10 readiness

**YES** — conditional on nothing further in this phase:

1. **Atomic admission**: multi-process contention at the SQLite boundary
   respects the configured limit with no visible over-limit `VALIDATED`
   state and no corruption (tests above).
2. **Capability integrity**: `SYSTEM_OBSERVATION ≠ SYSTEM_CONTROL`, roles
   receive only intended capabilities, control tools unreachable from
   observation-bound specialists.
3. **Real specialist work**: full
   `Main Agent → DELEGATE → ModelSelector → ModelProvider →
   SpecialistReasoner → structured result → verification → integration`
   chain demonstrated with a real provider for both a reasoning-only and a
   harmless action-producing task, with evidence and budget assertions.

Phase 10 may build multi-specialist coordination on this foundation; the
explicitly deferred items (unrestricted concurrent Runtime, recursive
delegation, debate/RL/self-modification, multi-device) remain deferred.
