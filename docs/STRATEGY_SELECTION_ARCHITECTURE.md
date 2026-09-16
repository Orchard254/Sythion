# Strategy Selection Architecture (Phase 11)

**Status:** Active. Deterministic, provider-independent StrategySelector over historical evidence.

---

## Selector Interface

```python
selector.select(task_context, candidates, historical_evidence) -> SelectionResult
# result: strategy, confidence, evidence_summary, alternatives, reason, mode, score
```

`SelectionResult.to_dict()` exposes all fields for audit.

## Candidate Strategies

Derived from CandidateAction decision_types via deterministic mapping:

```
EXECUTE_STEP/EXECUTE_DIRECTLY → DIRECT_EXECUTION
ANSWER                        → DIRECT_ANSWER
DELEGATE (role)               → DELEGATE_{RESEARCH,ANALYSIS,CODING,REVIEW,PLANNING_SUPPORT}
MULTI_SPECIALIST              → MULTI_SPECIALIST
ASK_CLARIFICATION             → ASK_CLARIFICATION
```

Unknown raw strings are rejected; only taxonomy values become candidates.

## Ordering (Section 11)

```
hard constraints
→ capability compatibility
→ safety/runtime eligibility
→ current availability
→ strategy evidence
→ cost/latency preferences
```

A historically successful but currently impossible strategy is vetoed.

## Evidence Matching

Scoped to structured task features:

- task_class (command, informational, goal, general, planning, etc.)
- goal_type / complexity / required_capability / specialist_role / risk_level / estimated_steps

Raw prompt text is not a matching key (audit field only). Phase 11 uses `task_class` as the primary match dimension; finer dimensions are future work.

## Scoring (Cost-Aware, Deterministic)

```
strategy_score =
    0.40 * success_rate
  + 0.20 * verified_success_rate
  - 0.15 * latency_cost        (normalized 0..1, unknown=0.5)
  - 0.10 * cost_component       (normalized 0..1, unknown=0.5)
  - 0.15 * failure_risk

latency_cost = min(1, mean_latency_ms / 5000)
cost_component = min(1, mean_cost / 5.0)
```

Unknown values contribute neutral 0.5, not fabricated zeros. Weights are documented and configurable via `SCORING_WEIGHTS`. Raw evidence is retained alongside the score.

### Direct vs Delegated

For comparable task_classes the selector compares DIRECT vs DELEGATED evidence on verified success, time, model calls, specialist count, failure, user correction. Delegation is not preferred merely for producing more text.

### Specialist Role Learning

Evidence per role (RESEARCH, ANALYSIS, CODING, REVIEW, PLANNING_SUPPORT) is tracked via `StrategyOutcome.role`. The cognitive delegation bridge (`CognitiveDecisionAdapter._select_role`) consults SUPPORTED/ESTABLISHED evidence; otherwise falls back to deterministic hints.

### Multi-Specialist Learning

Single vs multi-specialist statistics include additional cost. Preference requires justified improvement, not just success rate.

## Confidence Separation

```
decision confidence (score-derived, bounded 0..1)
  ≠
learning evidence strength (UNRATED/LOW_SAMPLE/SUPPORTED/ESTABLISHED)
```

A strategy may have high model confidence with low historical evidence, or vice versa. Both are exposed.

## Exploration vs Exploitation

- EXPLOIT: use best-supported strategy (default).
- EXPLORE: deterministic, low-rate alternative trial when cost/risk low.

Limits (configurable):

```
strategy_exploration_rate = 0.10
strategy_max_exploration_cost = 2.0
strategy_min_confidence_to_exploit = 0.65
strategy_high_risk_threshold = HIGH  (exploration disabled at HIGH/CRITICAL)
```

Exploration is a hash of task_class + date bucket (deterministic, no RNG in prod ordering). It never overrides hard constraints, safety, permissions, or runtime policy. High-risk exploration is blocked automatically.

## Determinism & Boundedness

- Sorting is by score desc, sample_count desc, strategy id asc.
- No LLM in the scoring path.
- No Python code / prompts / policies / permissions are modified.
- Recommendations only: the Runtime remains authoritative for execution.

## Flow

```text
Cognitive Analysis
    ↓
Candidate Strategies
    ↓
StrategySelector (historical evidence + scoring)
    ↓
DecisionSystem (hard constraints + evidence tie-breaker)
    ↓
Selected Strategy → Execution → Verified Outcome → StrategyOutcome
                                                → Future Selection
```

## Introspection

Each selection returns `evidence_summary` answering:

```
Which executions support this? How many? Outcomes? Verified? Cost?
Corrections? When collected? (provenance)
```

See `GET /learning/strategies` and `GET /learning/strategies/{id}`.

## Integration

- Cognitive pipeline calls `_strategy_guidance` before `DecisionSystem.evaluate`; evidence is a soft tie-breaker within 0.05 score delta.
- Delegation role selection delegates to the same selector.
- Model selection unchanged: selector may adjust `ModelRequirements.task_class` hint, not provider model id.

## Anti-Patterns Prevented

- Single-success ≠ learned truth (small-sample protection).
- Learning evidence ≠ Runtime authority (strict layering).
- Stale evidence cannot silently dominate (expiration + recent/decayed counts).
- Malicious stored record cannot become instruction (taxonomy validation on ingest).
