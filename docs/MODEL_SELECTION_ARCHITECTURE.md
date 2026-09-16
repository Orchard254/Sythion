# Model Selection Architecture

**Status:** Active (Phase 6). Builds on the provider-neutral layer from
`CAPABILITY_AND_MODEL_ARCHITECTURE.md`. All runtime authority contracts are
unchanged.

> **The selector recommends. The Runtime executes.**
> Evidence comes only from recorded evaluation runs; unknown stays unknown.

---

## ModelProvider / ModelRegistry

- `runtime/model_provider.py`: `ModelProvider` Protocol (`provider_id`,
  `selected_model_id`, `list_models`, `health`, `chat`) + `ModelHealth`.
  Neutral accessor `get_default_provider()` — higher layers import THIS,
  never the adapter module (enforced by source-scan tests).
- Sole implementation: `adapters/ollama_adapter.OllamaCloudProvider`
  wrapping the adapter singleton. No routing/fallback inside it.
- `ModelRegistry` (services/system_state.py): registered models, selected
  flag, availability, health. Truthfulness contract unchanged:
  `evaluated=False` until real evaluations exist; `quality_claims="none"`.

## ModelRequirements

Deterministic requirement generation from task context
(`requirements_for_task_class(task_class)`): fields `tool_calling,
structured_output, reasoning, vision, long_context, task_class`. The LLM may
*inform* requirements later; deterministic code remains the generator of
record.

## Capability schema (triad)

Every model capability is `{supported, available_now, evaluated}`:

```text
reasoning:  supported=true   available=true    evaluated=false
```

`supported=false` is a HARD compatibility reject in selection;
`supported=unknown` passes but is flagged "unverified" in the rationale.
No capability claim is empirically meaningful until `evaluated=true`.

## ModelEvaluator

`services/model_selection.ModelEvaluator` — fixed-prompt, deterministic
task classes over the provider `chat` boundary ONLY (no tools, no files, no
credentials, no arbitrary code):

| Task class | Status |
|---|---|
| simple_reasoning | implemented (arithmetic exact-match) |
| structured_output | implemented (strict JSON shape check) |
| context_following | implemented (verbatim token) |
| error_recovery | implemented (format correction) |
| planning | implemented (3-step JSON plan) |
| tool_calling / coding | NOT implemented — honestly skipped |

Each run persists a versioned row (`model_evaluations`): model/provider,
task_class, evaluation_version, score 0..1, passed (None=inconclusive),
latency_ms, usage_tokens, input_ref, summaries. Errors record
`passed=None` (inconclusive), never failure-by-assumption.

## ModelSelector — deterministic policy

`select(requirements, available_models=None, exclude=None)`:

1. **Capability filter**: required cap with `supported=False` → rejected
   `lacks <cap>` (hard; e.g. vision-required never falls back to text-only).
2. **Availability filter**: not in available set / registry says
   unavailable → rejected `unavailable`.
3. **Configured primary** first when compatible+available.
4. **Evidence**: among remaining candidates, prefer positive pass-rate for
   the requested task class (pass_rate desc → avg_score desc → attempts → id).
5. **Tie-break**: no evidence anywhere → stable first candidate by id,
   flagged `"no evaluation evidence … (unverified)"`.

Every decision persists a `model_selections` audit row
(requirements/selected/rejected/reason/fallback_of). `explain` output is the
SelectionResult dict itself.

## Fallback policy

Bounded by `settings.model_max_attempts` (default 2 = primary + ONE
fallback) per engine instance:

1. decision call fails (provider error / timeout);
2. failed attempt **consumes cloud-request budget** (recorded before any
   fallback decision — BudgetExceededError blocks fallback);
3. failure recorded as ledger event `model.provider_error` /
   `model.timeout` under scope `model:<model_id>`;
4. selector picks next compatible candidate excluding the failed one
   (capability-aware — incompatible models can never be chosen);
5. retry through the SAME protected decision path (repair/budgets intact);
6. loop bounded by attempts and by candidates exhausting → honest
   CLOUD_ERROR/TIMEOUT to the task loop.

Transient vs permanent: single transient errors fall back immediately;
persistent failures exhaust the bound quickly. There is no blacklist and no
automatic quality downgrade from raw failures — failures are *evidence*
(`model.failure` events), and future routing policy may consume them.

## Health vs Quality

HEALTH (reachability: probe/status) and QUALITY (evaluated evidence) are
separate axes. A healthy model with no evaluations is "unverified"; an
evaluated-but-down model is excluded until healthy again. Neither axis can
bypass Runtime policy or budgets.

## Future learned-routing boundary

A learned router replaces ONLY the ranking function in step 4 (evidence
scoring); filters, fallback bounds, budget accounting, and audit stay fixed.
