# Cognitive Architecture

**Status:** Active (Phase 8). Builds on all prior phase contracts. The
Runtime Kernel remains the sole authority over execution; this document
describes the cognitive layer that decides *what to propose*.

> **The LLM supplies reasoning; the cognitive architecture supplies
> structure; the Runtime supplies authority.**

---

## Pipeline

```text
UNDERSTAND → CHECK_CONTEXT → REASON → EVALUATE → DECIDE → ACT/ASK/WAIT
                ▲                                          │
                └──────────── OBSERVE (bounded) ◄──────────┘
```

`services/cognitive pipeline (backend/cognitive/pipeline.py)` runs ONE
bounded cycle per invocation: `MAX_REASONING_CALLS = 2` per engine, no
recursive model calls, no infinite self-reflection.

## CognitiveState

A structured dataclass (`cognitive/state.py`) — never a prompt blob:

- `user_input`, `intent` (IntentResult), `context_sources` (labeled:
  USER_INPUT / GOAL_CONTEXT / PERSONAL_CONTEXT / MEMORY_DATA …), each with an
  `EpistemicLabel` (KNOWN / OBSERVED / REMEMBERED / INFERRED / ASSUMED /
  UNKNOWN)
- `active_goals`, `relevant_memory`, `capabilities_available`
- `uncertainty` (AmbiguityLevel), `candidates`, `selected`

Context sources stay individually labeled — never merged into
indistinguishable prose before reasoning sees them.

## IntentInterpreter

Deterministic rule-based first pass producing `IntentResult`
(category/confidence/ambiguity/interpretations). Categories: COMMAND,
INFORMATIONAL, SOCIAL, GOAL, FOLLOW_UP, CLARIFICATION, CORRECTION, FEEDBACK.
Interpretations are *inferences with confidence*, not facts; materially
different readings are retained as ambiguity data. An optional LLM refinement
(`refine_with_llm`) may ADD candidate interpretations but cannot override a
confident deterministic category.

### Ambiguity policy

Levels: CLEAR < LOW_AMBIGUITY < AMBIGUOUS < HIGH_IMPACT_AMBIGUITY.
Clarification is preferred when ambiguity is material AND impact is
non-trivial; it is suppressed when interpretation confidence is high or the
action is safe/reversible. Tested both directions.

## ReasoningEngine

Provider-neutral via Phase 6 ModelSelector + ModelClient. Produces a
`ReasoningArtifact` — a concise structured RESULT (problem_statement,
relevant_facts with epistemic labels, unknowns, constraints, candidate
options, tradeoffs, recommended_direction, uncertainties) — **not**
chain-of-thought. Malformed/unavailable model output falls back to a
deterministic artifact. An epistemic guard downgrades unverifiable "KNOWN"
claims to ASSUMED.

Reasoning NEVER: executes tools, writes Goal/Memory state, changes
permissions, or bypasses Runtime.

## DecisionSystem

Deterministic evaluation of CandidateActions:

1. HARD constraints veto instantly (documented semantics: check(candidate)
   returns True = violated). The LLM cannot redefine hard constraints.
2. Missing required capabilities veto (SelfModel/capability honesty).
3. Weighted scoring (documented constants): risk 0.30, evidence 0.25,
   reversibility 0.20, capability_fit 0.15, cost 0.10 (unknown cost = 0.5).
4. Selected decision carries ConfidenceBreakdown(evidence, interpretation,
   decision) — LLM confidence ≠ decision reliability; unknown stays None.

Decision types: ANSWER, ASK_CLARIFICATION, CREATE_GOAL, UPDATE_GOAL,
EXECUTE_STEP, DEFER, REFUSE, WAIT, REQUEST_PERMISSION, NO_ACTION.

Risk levels NONE→CRITICAL consider reversibility, data impact, external side
effects and scope. Risk estimation here does NOT duplicate runtime
authorization — CRITICAL selections still pass through executor
policy/approval gates.

## SimulationEngine

Dry-run only: predicts effects from candidate metadata (expected outcome,
reversibility warnings, capability usage). It can never invoke real tools or
mutate state; candidates are unmodified by evaluation.

## Goal vs immediate action

Deterministic category rules decide: questions → ANSWER; imperative machine
actions → EXECUTE_STEP proposal; long-horizon markers → CREATE_GOAL; social →
ANSWER. The LLM cannot create endless goals — goal creation remains a
decision type gated by GoalManager validation.

## Memory / SelfModel / Selector integration

- Context assembly pulls PERSONAL_CONTEXT (REMEMBERED epistemic),
  GOAL_CONTEXT (KNOWN), USER_INPUT — labeled sources.
- Reasoning requests specify ModelRequirements; the Phase 6 selector picks
  the model. No provider names in this package (source-scanned).
- Learning hooks: every decision emits `cognitive.decision.made` with input/
  intent/ambiguity/decision/risk/confidence into the existing ledger — clean
  evidence for future learning without exposing chain-of-thought.

## Security summary

Cognitive modules contain no tool-execution surface, no adapter imports, no
GoalManager/MemoryService writes (source-scanned). A command-intent cycle
creates no Task rows — execution belongs exclusively to Runtime paths.
Model metadata can influence scoring but can never grant permissions or
bypass verification.
