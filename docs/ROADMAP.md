# Sythion Roadmap

## Purpose

This roadmap tracks the development direction of **this repository**:
Sythion as the **trusted runtime core** of a larger intelligence system.

It is intentionally operational, not aspirational. It should answer:

- what this repo is for;
- what is already built;
- what is next;
- what is deferred;
- what belongs outside this repo.

For the runtime contract, see [`ARCHITECTURE.md`](ARCHITECTURE.md).
For the larger layered vision, see
[`INTELLIGENCE_ARCHITECTURE.md`](INTELLIGENCE_ARCHITECTURE.md).

## Current verified baseline

- Full backend suite: **1569 passed, 45 skipped** (1614 collected)
- Acceptance suite: **191 passed, 2 skipped**

Measured on CPython 3.14.7 / Windows by
`python scripts/measure_test_baseline.py` on 2026-10-20.
CI pins Python 3.11.

Also true of the tree the baseline above was measured on:

- runtime/intelligence boundaries have been clarified in code and docs;
- `skill_engine.py` has been split into runtime vs proposal concerns;
- memory tooling has been split into durable memory vs runtime activity recall;
- conversation-guidance wiring has been extracted behind an explicit adapter;
- the `BaseTool` contract has been corrected at the executor layer;
- a durable work-state layer exists with `Objective` + `PlanStep`,
  task linkage, plan APIs, and prompt-context injection;
- **objective completion criteria + verification** (completion_criteria, criteria_verified, parent_objective_id, verify-criteria endpoint);
- **sequential multi-call tool execution** (several tool calls in one model
  turn are executed one after another, not in parallel);
- **memory retrieval quality** (stale edge filter, bidirectional traversal, fallback scoring);
- **executor static-analysis cleanup** (modern imports, types, exception handling, protected member access).

## Now

These are the highest-value near-term priorities for this repo.

### 1. Runtime-core hardening

- keep the trusted loopback boundary strict;
- preserve one-task-at-a-time lease safety;
- keep tool execution bounded, cancellable, and verified;
- maintain target validation and policy gating as deterministic runtime truth.

### 2. Release and operator readiness

- tighten packaging and clean-machine validation;
- improve operator/developer runbooks;
- make release readiness obvious from docs and tests;
- reduce ambiguity around what is manual vs automated.

### 3. Architecture professionalization

- continue splitting mixed runtime/intelligence modules when the boundary is
  clear and the public API can stay stable;
- keep docs aligned with actual code;
- reduce stale or misleading internal references.

## Next

These are the likely next implementation tracks after the current cleanup wave.

### Runtime-facing quality work

- improve test ergonomics and developer verification guidance;
- continue making contracts explicit rather than implicit.

### Intelligence-layer integration

- deeper memory retrieval quality without letting memory override live truth;
- richer self-modeling / capability modeling;
- broader intelligence orchestration that still routes through trusted runtime contracts.

### Documentation and operations

- keep roadmap, decisions, and quality gates current;
- maintain a scan-friendly authority surface for future contributors and LLMs.

## Later

These are valid directions, but not the current execution priority.

- stronger planning modules layered above the executor loop once the new
  objective/plan layer has matured beyond its initial minimal implementation;
- richer self-modeling / capability modeling;
- broader intelligence orchestration that still routes through trusted runtime
  contracts;
- additional verified tool surfaces where they fit the safety model.

## Deferred

These items may be valuable, but should not distract from runtime quality and
release discipline right now.

- broad cleanup of every repo-wide static-analysis warning;
- speculative personality expansion not tied to execution quality;
- major refactors without a clear boundary or verification benefit.

## Intentionally out of scope for this repo

This repo should **not** become the whole intelligence system.

Out of scope here:

- unbounded autonomous agency;
- remote-control bypasses around the runtime boundary;
- multi-device orchestration that does not go through trusted runtimes;
- hidden sources of truth that override live tool evidence;
- vague AGI claims unsupported by runtime guarantees.

## Rule for updating this file

Update this roadmap when one of these changes:

- the verified baseline materially changes;
- the near-term priorities change;
- a deferred item becomes active work;
- scope shifts between this repo and higher intelligence layers.

Do not use this file as a changelog. Keep it short and current.