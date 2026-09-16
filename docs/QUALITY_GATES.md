# Sythion Quality Gates

## Purpose

These gates define what "professional" means for this repository.

They are intentionally practical. A change does not need to be perfect, but it
must respect the runtime contract, verification discipline, and project memory.

## Gate 1 — Runtime boundary integrity

A change must not:

- bypass policy gating;
- bypass target validation;
- bypass cancellation boundaries;
- let model narration replace verified outcomes;
- let intelligence-layer helpers silently become runtime truth.

If a feature needs broader intelligence, layer it **above** the runtime rather
than weakening the runtime.

## Gate 2 — Architectural clarity

A change should preserve or improve the distinction between:

- trusted runtime core;
- runtime/intelligence bridge modules;
- intelligence-layer scaffolding.

If a module is mixing multiple responsibilities and the split is clear,
prefer extracting the boundary over deepening the monolith.

## Gate 3 — Verification before claims

Do not claim a behavior is correct unless it was verified through one or more
of:

- targeted tests;
- full backend suite;
- import/boot validation;
- direct code-path inspection where runtime execution is not possible.

When validation is partial, say so explicitly.

## Gate 4 — Docs must reflect reality

Authority docs must match the codebase's verified state.

At minimum, keep aligned:

- `ARCHITECTURE.md`
- `INTELLIGENCE_ARCHITECTURE.md`
- `CODEBASE_BOUNDARIES.md`
- `PROJECT_STATUS.md`
- this file, when the gates themselves materially change

Do not leave stale milestone language or historical phase claims as if they are
current truth.

## Gate 5 — Shared memory continuity

After a meaningful verified change:

- write the new fact to project memory immediately;
- supersede stale facts when direction changes;
- do not rely on future LLMs to infer architectural intent from diffs alone.

## Gate 6 — Public-surface stability during refactors

When splitting internals:

- preserve stable import surfaces where that reduces risk;
- prefer compatibility facades for high-traffic modules;
- change public behavior only when there is a clear, justified reason.

## Gate 7 — Minimal, high-value scope

Do not pad the repo with speculative files or duplicate documents.

New files should exist because they:

- reduce ambiguity;
- preserve decision continuity;
- improve operator/developer execution;
- or encode a stable boundary worth keeping visible.

## Gate 8 — Testing baseline discipline

The backend suite is a release-confidence signal.

Before closing meaningful backend work:

1. run focused tests around the changed area;
2. run the broader backend suite when the change touches shared contracts,
   architecture, tool execution, or model/runtime boundaries;
3. report actual results, not assumptions.

## Gate 9 — Honest handling of debt

Do not disguise known debt as solved.

Examples:

- if diagnostics remain noisy, say so;
- if a manual release gate still exists, say so;
- if an abstraction is only partially cleaned up, document the remainder.

Professionalism here means truthful state reporting, not cosmetic certainty.

## Exit criteria for a strong change

A strong change in this repo usually means:

- the runtime boundary is preserved or strengthened;
- code structure is clearer than before;
- tests still pass;
- docs still match the code;
- shared memory was updated;
- future agents are less likely to get confused.
