# Reasoning-processor boundary: acceptance evidence (2026-11-19)

This records observed evidence for the principle "Synth is the persistent
system; the LLM is a replaceable reasoning processor, not the brain". It is a
**partial** acceptance record: the items listed as open are not claimed.

## Environment

- Windows 11, CPython 3.14.7, branch `fix/production-provider-selection-contract-2026-11-15` at `3dbec7e` with uncommitted work.
- Tests used an isolated `LOCALAPPDATA` unless a live provider E2E was being run (those use the build-scoped write-only vault).
- The staged backend mirror was regenerated from canonical `backend/`; `python scripts/refresh_staged_backend.py --check` → `[backend-mirror] clean: staged backend matches source`.

## Fresh full backend baseline

```
$env:LOCALAPPDATA = <isolated>
python -m pytest backend/tests -q
4739 passed, 61 skipped, 10 xfailed, 0 failed, 0 errors in 2092.42s (0:34:52)

This is the latest measurement, after the S179 context contract, bounded
scheduler, NVIDIA-only E2E scope, context-provenance changes, wake-path
integration, contract versioning, and the event-bus reset fix. Earlier runs
recorded 4729 passed (before the reset fix, 1 failure), 4729/61/10 green, and
4692/64/10 before the later changes.
```

Earlier polluted/aborted runs (49 failures, then 1 failure + 1 error) were
traced to an acceptance fixture that permanently mutated a module global and to
a mirror refresh performed while a suite was running. Both were fixed; the
final mirror test and the previously erroring module pass in isolation.

## Shared contract across all reasoning entry paths

Implemented and tested:

- `ReasoningJob` + authority-free `ReasoningResult` (`job_id`, `purpose`, `provider_id`, `work_id`, `value`).
- Single admission factory `admit_reasoning_job`, job-bound `handoff_reasoning_result`.
- Bounded attention `decide_attention` with `DISPATCH` / `DEFER` / `DO_NOTHING` and `AttentionSignals` derived only from runtime facts.
- Single work-selection contract `WorkItem` / `select_next_work` / `reasoning_signals_for`, used by conversation, task decision, goal planning **and** the leased wake path (`decide_background_attention`).
- Enforceable cognitive hard constraints; fail-closed tool-argument schema validation at `ModelDecisionEngine._parse_decision`.

Observed commands:

```
pytest [work-selection + wake + reasoning contract suites]  → 198 passed
pytest [reasoning/attention/result/conformance suites]      → 167 passed
pytest backend/tests --collect-only -q                      → 4754 tests collected,
                                                             no marker-contract violations
```

Terminal task states and completed goals admit **zero** model calls; a pending
mutating action defers admission regardless of model output.

## Provider neutrality

Offline conformance parametrizes all registered providers
(`ollama-cloud`, `openrouter`, `nvidia`, `groq`) over all three online paths and
asserts one result type, exact identity binding, accepted own-job handoff,
rejected cross-provider handoff, and absence of authority fields.

Live, opt-in E2E (real vault credentials, no key in chat or source):

```
$env:SYTHION_RUN_REASONING_E2E = "1"
python -m pytest backend/tests/test_reasoning_provider_e2e.py -q -rs
1 passed
```

- Live scope narrowed by owner direction: **NVIDIA only**, no Ollama Cloud or Groq live testing.
- Nemotron models preferred; the default pin is `nvidia/nemotron-3-ultra-550b-a55b` (override with `SYTHION_E2E_MODEL_NVIDIA`). Safety/embed/guard Nemotron variants are excluded.
- The test still probes a small bounded set of fallbacks if the preferred model is unavailable.

## Context working set and scheduler (partial S179/S180)

```
pytest test_reasoning_scheduler.py test_context_working_set.py \
       test_reasoning_effectiveness.py -q
17 passed
```

`ContextWorkingSet` binds context to exact task/goal/session scope, rejects
foreign items, labels each item with provenance and epistemic status, expires,
and renders no model messages when cancelled or stale. `ReasoningScheduler` is
a bounded asyncio queue with one sequential worker and bus subscription; it
does not call models or execute tools. `ReasoningMetrics.record_context`
measures relevance ratio and estimated context tokens (token count is a cost
proxy, not a currency claim).

## Measurement and event-driven dispatch decision (partial S183/S180)

```
pytest test_reasoning_effectiveness.py test_reasoning_dispatch.py \
       test_model_engine_result_contract.py test_work_selection_contract.py -q
72 passed
```

`ReasoningMetrics` records admitted/model calls, idle, deferred and direct
procedures, latency and tokens. `EffectivenessReport.effective` is false for
model activity without verified useful work, and `record_verified_work`
requires an evidence reference. `ReasoningEventBus` carries identity and coarse
outcomes only — no prompts, model output or credentials. The attention decision
selects model / direct procedure / waiting / idle; idle and direct-procedure
paths make zero model calls.

## Consent / attestation boundary

```
pytest test_reasoning_consent_boundary.py \
       test_consequential_authenticator_regressions.py \
       test_recovery_native_challenge.py -q
56 passed
```

Model-shaped approval JSON cannot alter the attention verdict, cannot add an
authority field to `ReasoningResult`, and cannot make `ConsequentialApprovalService`
approve without an injected authenticator. `NativePresenceObservation` defaults
every authority flag to false and the unconfigured verifier denies.

## Wake-path integration and contract version (2026-11-19)

```
pytest test_wake_scheduler_integration.py test_reasoning_dispatch.py \
       test_bounded_wake_scheduler.py -q
56 passed
```

`ReasoningResult.contract_version` is explicit; handoff rejects an unknown
version. Real wake outcomes emit `IDLE`/`ADMITTED` events on the shared bus and
are consumed by `ReasoningScheduler`; the bus sink grants no authority.
`BoundedWakeScheduler` is opt-in through `WakeWorker.run_bounded_wake_cycle`
and is not the default event-driven worker loop.

## Native host compile verification (2026-11-19)

After installing VS BuildTools 2022 (VCTools) and Windows SDK 10.0.26100 with
owner approval, `cargo check --offline` in `src-tauri` exits **0**. Three
compile errors found on the first successful link setup were fixed (async
operation import, `?` in `main()`, and a `!Send` COM pointer across `await`).
`cargo test --offline` also exits 0: **31 passed**, including four new native
attestation tests (big-endian canonical message, one-shot signing, task/revision
binding, invalid-scope refusal).

## Goal lifecycle acceptance and cross-language conformance (S193-S204 partial)

```
pytest [goal execution / restart / preview / continuity / orchestration / work state] -q
→ 207 passed

pytest test_native_canonical_conformance.py test_trusted_host_attestation_boundary.py -q
→ 16 passed
```

The Python `NativePresenceChallengeBroker._canonical_message()` output matches
byte-for-byte the vector emitted by the Rust host test
`print_canonical_vector_for_cross_language_conformance`, so both sides sign and
verify the same domain-separated, big-endian-length-prefixed message. Goal
lifecycle suite covers real ORM vertical slices, evidence-gated completion,
no-replay restart policy, and side-effect-free next-work selection. Installed
app and Windows UI clauses remain unexercised.

## Real process-kill acceptance (partial S184/S185/S199)

```
$env:SYTHION_RUN_PROCESS_KILL = "1"
python -m pytest backend/tests -q -m process_kill -rs
→ 8 passed, 0 failed, 0 skipped (480.25s)
```

Covers real Windows `TerminateProcess` during atomic budget commit,
multi-process concurrency limits, specialist kill/recovery, and admission
survival across process kill. The env-isolation fixture now preserves the opt-in
`SYTHION_RUN_PROCESS_KILL` flag for `process_kill`-marked tests; the normal
suite is unaffected (sanity subset: 66 passed). Installed-app and full Windows
goal-lifecycle clauses of S184–S206 remain unexercised here.

## Reconciliation suite (2026-11-19 final pass)

```
pytest [17 reasoning / context / scheduler / attestation / goal / conversation suites] -q
294 passed
SYTHION_RUN_REASONING_E2E=1 pytest test_reasoning_provider_e2e.py -q
1 passed   (NVIDIA, Nemotron preference)
```

## Not proven / still open

- **Native trusted-host attestation is not implemented.** `backend/services/recovery_native_challenge.py` is an offline, default-deny host-message integrity verifier. Real human-presence proof requires a trusted Tauri host with a per-launch signing key never exposed to the webview, HTTP, model or control-token channel. No backend can fabricate this.
- **Live E2E is NVIDIA-only by owner direction** (Nemotron preferred); Ollama Cloud and Groq are deliberately excluded from live testing. Offline conformance still covers all registered providers.
- **S183 remains partial**: request counts, latency, tokens, context relevance, token-based cost proxy, idle/deferred/direct-procedure decisions and verified useful work are measured, but currency cost attribution and a production scheduler subscriber are not implemented.
- **No time-based background scheduler**: `ReasoningScheduler` is a bounded event queue; `select_next_work` and the attention dispatcher are deterministic contracts. Neither is a time-driven background autonomy scheduler.
- Checklist S178/S180/S182 remain unchecked with partial annotations; S179, S181, S183–S206 remain as previously recorded.
