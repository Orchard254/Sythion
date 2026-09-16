# Production Learning Observation & Real Evidence Verification (Phase 12.8)

**Date:** 2026-08-27  
**Status:** VALIDATED  
**Mode:** `planning_learning_mode = LEARNING_ONLY` (authoritative production execution remains strictly deterministic; no learned policy activation)

---

## 1. Executive Summary

Phase 12.8 verifies that real-world Sythion usage produces genuine `REAL_EXECUTION` planning-learning evidence, establishes the precise boundary between conversational single-turn tasks and multi-step planned Goals, documents the safe owner-triggered observation pathways, validates the conservative legacy migration fix for short step descriptions (`"hi"`), and inspects the live readiness of the system without enabling Phase 13 or fabricating evidence.

---

## 2. Production Evidence Audit (`sythion.db`)

An authoritative audit of the live Sythion production database (`%LOCALAPPDATA%/Sythion (Synth)/sythion.db`) reveals:

| Metric | Production Count |
|---|---|
| `planning_outcomes` Total | **3** |
| `REAL_EXECUTION` Outcomes | **3** |
| `SYNTHETIC_TEST` Outcomes | **0** |
| `IMPORTED` Outcomes | **0** |
| `SIMULATED` Outcomes | **0** |
| `planning_shadow_decisions` Total | **0** |
| `planning_policies` (Active/Candidate) | **0** |
| `tasks` Total | **835** |
| `objectives` (Goals) Total | **450** |
| `goal_plans` Total | **3** (pre-migration) / **447** (post-migration) |
| `delegations` Total | **0** |

### Newest Real Planning Outcome Records

1. **Outcome ID 1**:
   - Timestamp: `2026-08-26 11:00:05.328433`
   - Task Class: `goal`
   - Strategy: `MINIMAL_PLAN`
   - Goal ID: `b6fb0dfa-7b43-4d96-9598-d2fc4f5125ed`
   - Plan ID: `2`
   - Outcome: `SUCCESS_VERIFIED` (Verified Success: `1`, Retries: `0`, Replans: `0`)
   - Provenance: `REAL_EXECUTION`
   - Fingerprint: `ef89a74d43bb968d`
   - Evidence Refs: `["goal:b6fb0dfa-...", "plan:2", "task:8547bd73-...", "plan_step:471"]`

2. **Outcome ID 2**:
   - Timestamp: `2026-08-26 11:06:23.560308`
   - Task Class: `goal`
   - Strategy: `MINIMAL_PLAN`
   - Goal ID: `5b31ba75-d674-49ee-b9e7-9c50850b569d`
   - Plan ID: `3`
   - Outcome: `SUCCESS_VERIFIED` (Verified Success: `1`, Retries: `0`, Replans: `0`)
   - Provenance: `REAL_EXECUTION`
   - Fingerprint: `bf3d89f42d120346`
   - Evidence Refs: `["goal:5b31ba75-...", "plan:3", "task:227fd14a-...", "plan_step:472"]`

3. **Outcome ID 3**:
   - Timestamp: `2026-08-26 16:51:09.169183`
   - Task Class: `goal`
   - Strategy: `MINIMAL_PLAN`
   - Goal ID: `a9d2d561-1003-4211-aeba-e61ec3e5110f`
   - Plan ID: `1`
   - Outcome: `FAILED` (Verified Success: `0`, Retries: `0`, Replans: `0`)
   - Provenance: `REAL_EXECUTION`
   - Fingerprint: `63a1a877db1a6e9e`
   - Evidence Refs: `["goal:a9d2d561-...", "plan:1", "task:83b1e8c6-...", "plan_step:470"]`

---

## 3. Conversational Tasks vs. Goal Planning Architecture

### Why Conversational Tasks Do Not Produce `PlanningOutcome`

Normal conversational user traffic (e.g. *"how have you been old friend"*, *"so far so good..."*, *"hey"*, *"hi"*, *"or kb"*, *"5mbs into mb"*) traverses the **Cognitive Runtime**:
1. `CognitiveDecisionAdapter.route_new_input` classifies the request (e.g. `IntentCategory.SOCIAL`, `IntentCategory.INFORMATIONAL`, `DecisionType.ANSWER`).
2. The runtime processes the single-turn answer directly through the tool/model execution loop.
3. `PostTaskPipeline.record_learning_outcome` records a **`StrategyOutcome`** (Phase 11 strategy selection learning).
4. `PostTaskPipeline.apply_plan_outcome` queries `PlanStep` linked to the task. Because no `GoalPlan` was created, `step` is `None` (or `step.plan_id` is `None`). It returns `None` (*"not a plan-backed execution; nothing to do"*).
5. **No `PlanningOutcome` is generated**, which is architecturally correct.

### Expected Capture Boundaries

| Interaction Type | Planner Invoked? | GoalPlan Created? | PlanningOutcome Captured? | StrategyOutcome Captured? | Classification |
|---|---|---|---|---|---|
| Conversational Chat ("hi", "how are you") | No | No | No | Yes | `not applicable` |
| Direct Answer / Information Retrieval | No | No | No | Yes | `not applicable` |
| Single-turn Tool Execution | No | No | No | Yes | `not applicable` |
| Multi-Step Planned Goal | **Yes** | **Yes** | **Yes** (`REAL_EXECUTION`) | Yes | `applicable` |
| Goal Step Execution / Advance | **Yes** | **Yes** | **Yes** (`REAL_EXECUTION`) | Yes | `applicable` |
| Goal Step Retry / Replan | **Yes** | **Yes** | **Yes** (`REAL_EXECUTION`) | Yes | `applicable` |

---

## 4. Safe Owner-Triggered Production Observation Path

When observing real planning learning in production without impacting live environment state, the owner can trigger harmless, reversible observation workflows.

### Safe Observation Task Catalog

1. **Task A — Simple Planning (`MINIMAL_PLAN` / `DIRECT_PLAN`)**:
   - *Prompt*: *"Create a 2-step plan to list the current directory contents and verify its existence."*
   - *Reversible / Harmless*: Read-only filesystem operations.
2. **Task B — Dependency Planning (`DEPENDENCY_AWARE_PLAN`)**:
   - *Prompt*: *"Plan a 3-step sequence where Step 2 depends on Step 1 output and Step 3 summarizes both."*
   - *Reversible / Harmless*: Pure data transformation / read pipeline.
3. **Task C — Delegated Planning (`DELEGATED_PLAN`)**:
   - *Prompt*: *"Structure a goal delegating analysis of standard library math constants to an inert reasoning specialist."*
   - *Reversible / Harmless*: Inert reasoning-only specialist execution.
4. **Task D — Controlled Retry / Replan (`REPLAN_AFTER_FAILURE` / `REPLAN_BEFORE_RETRY`)**:
   - *Prompt*: *"Execute a step against a non-existent fixture with deterministic fallback to an existing fixture."*
   - *Reversible / Harmless*: Controlled recovery path.

### Real vs. Synthetic Provenance Rule
- An observation task that executes through `CognitivePipeline` → `GoalManager` → `Planner` → `TaskOrchestrator` → `Runtime Kernel` is classified as **`REAL_EXECUTION`**.
- It is classified as `REAL_EXECUTION` because the provenance reflects **how the outcome was generated** (full live production stack execution, true latency, true token cost, live completion verification), not why the owner initiated it.
- Direct fixture injection or synthetic mock test suites are classified as **`SYNTHETIC_TEST`**.

---

## 5. Startup Migration Resolution (`CandidateStepModel.description >= 3`)

### Root Cause Analysis
During startup bootstrap, `migrate_legacy_objectives` converts unplan-backed legacy objectives into canonical plan-backed goals. Legacy objectives in `sythion.db` contained 14 legacy step records with descriptions such as `"hi"` or `"."` (length < 3).

`CandidateStepModel` in `backend/services/planner.py` enforces:
```python
class CandidateStepModel(BaseModel):
    description: str = Field(min_length=3, max_length=500)
```
When `migrate_legacy_objectives` instantiated `CandidateStepModel(key='step_1', description='hi')`, Pydantic raised `ValidationError: String should have at least 3 characters`. Because candidate generation was outside a per-objective exception handler, the exception escaped to `bootstrap.py`, skipping migration of all 450 objectives.

### Conservative Resolution
1. **Schema Invariant Maintained**: `CandidateStepModel.description` min_length=3 is **preserved unchanged**.
2. **Conservative Normalization**: Legacy descriptions are sanitized deterministically:
   - If `len((s.description or "").strip()) >= 3`: preserved verbatim (`s.description.strip()[:500]`).
   - If `len((s.description or "").strip()) < 3` (e.g. `"hi"`, `"."`, `""`, `None`): normalized to `f"Legacy step {idx}"` (length >= 13 characters, valid).
3. **Auditability & Provenance**: Original legacy step rows in `plan_steps` with `plan_id IS NULL` remain intact in the database as audit records. No user claims are fabricated.
4. **Resilience**: Candidate generation and plan creation are wrapped in a per-objective try/except handler so that a single corrupted record will log a skipped audit report rather than aborting bootstrap migration for remaining objectives.

---

## 6. Production Live Readiness & Endpoints

Queries against the actual live production database (`sythion.db`) confirm:

### `GET /planning/live-readiness`
- `phase13_ready`: `False` (gate requires >= 5 real samples and >= 3 real verified successes)
- `policy_readiness`: `"LOW_REAL_EVIDENCE"`
- `promotion_eligible`: `False`
- `real_sample_count`: `3`
- `real_verified_successes`: `2`
- `shadow_sample_count`: `0`
- `shadow_validated`: `False`

### `GET /planning/coverage`
- Total database outcomes: `3`
- Real samples: `3` (100%)
- Synthetic samples: `0`
- Verified success rate: `0.6667` (2/3)

### `GET /planning/shadow/quality`
- Total shadow decisions: `0`
- Expected advantage: `None` / `UNKNOWN`

---

## 7. Security & Non-Promotion Guarantees

1. **Strict LEARNING_ONLY Mode**: `planning_learning_mode` remains strictly `LEARNING_ONLY`. Learned policies cannot be activated without explicit owner action.
2. **Readiness Gate Integrity**: `phase13_ready` will strictly remain `False` until genuine real evidence satisfies the calibration thresholds.
3. **Audit Trail**: Every real planning outcome is anchored by an immutable SHA256 evidence fingerprint and linked ledger events.
