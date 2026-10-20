# Brief: verify - 006 - attempt 1

- Item: 006 — readiness tests independent of gitignored backend/.env.
- Role: independent Verifier. Goal: trustworthy clean-checkout test results without changing production behavior.
- Repository/base: C:\Sythion\sythion, master. Local work only; budget remains six merged items, two currently merged.
- Candidate: orchestra/006-readiness-test-isolation at f1445575655261351989953ccf3d4247cde40ca1; review 006-r2 ACCEPT, tier SENSITIVE.

## Mandatory protocol and isolation

Follow C:\Sythion\AGENTS.md: memory_stats, memory_query, memory_prepare_task before file work; get file context as required. If memory tools are unavailable, STOP and report the blocker rather than bypassing the instruction. Do not inspect the memory database directly as a substitute.

Create your OWN detached worktree pinned at the candidate SHA under C:\Users\orcha\AppData\Local\Temp\opencode. Do not change wt-006 or another agent's worktree. Do not alter branch refs, master, or production files. Scratch probes may live in your own disposable tree; preserve evidence and report exact modifications. Never reset/clean/delete broad paths, never push/deploy/migrate, never access developer .env contents or real credential values.

## Read

Read .orchestra/goal.md; item 006 in backlog.md; plans/006.md; reports/006-implement-2.md; reviews/006-r1.md and reviews/006-r2.md; verification-template.md. Inspect the two-file candidate diff and actual tests. The review is a map, not independent verification evidence.

## Verify

1. Reproduce original readiness failure on base with no .env and every SYTHION_* scrubbed from CHILD environment; candidate readiness module passes. Generate a DUMMY .env in your scratch tree (never copy the user's file) with all three ambient fields and compare outcomes for readiness, file roots, settings store/API with and without it.
2. Name and verify the SENSITIVE invariant: effective test settings match a clean-file-disabled Settings instance, each test gets independent mutable values, and readiness reports the effective credential in both directions without exposing it. Use your own probes, including sequential tests mutating file_roots, and sentinel-induced assertion failure checked in console AND JUnit. Do not print any real credential. Distinguish per-test-copy regression from isolation removed entirely; the latter is not detected in an otherwise clean tree.
3. Cover the review's explicit gap: exact CI backend-working-directory invocation with pinned pytest 7.4.4/pytest-asyncio 0.23.3 environment at C:\Users\orcha\AppData\Local\Temp\opencode\venv-pinned. Report actual interpreter version; do not label this Python 3.11 unless it really is. Collection plus targeted readiness execution is required.
4. Independently check provider_e2e carve-out boundaries using dummy fixtures/probes, not real providers; NEVER opt in to provider calls. Confirm no production files changed and no tests removed/skipped/re-marked to force green.
5. Full-suite execution is SAFE ONLY with product-target writes/deletes blocked before delegation. Candidate 006 alone lacks 007's guard. Use an isolated integration tree including 007 tip 468a17a and, optionally, 002 tip cc03a7b, or a test-only non-delegating blocker plugin outside the repository. Do NOT run unguarded base or candidate full/acceptance suite against the real vault. Report exact tested tree, overlays and SHAs separately from candidate reviewed SHA. The combined final tree is available at b0127b0 (orchestra/003-final-merged), but use your own checkout. Expected collection is 1614; measure, do not assert by arithmetic. Native-fault attempts are not green evidence: record them separately and retry in fresh processes within a reasonable bound.
6. Check all six backlog criteria against evidence, qualifying the obsolete 1564 baseline and count-preservation wording: accepted change adds three tests; review measured 1569 passed/45 skipped. No warning totals should be published as baseline.

## Write/output

ONLY durable output: C:\Sythion\sythion\.orchestra\verifications\006-v1.md. Do not edit board, backlog, follow-ups, code or another report. First line exactly VERDICT: FIXED, VERDICT: NOT FIXED, or VERDICT: REGRESSED. Include both '- Reviewed commit: <full SHA>' and '- Verified commit: <full SHA>', '- Tier: SENSITIVE', actual tested-tree identities, acceptance table with met/not met/not verified, reproducible commands and outputs, named invariant/negative controls, CI-shape result, environment outcome comparison, vault safety method, native-fault attempts, findings and mandatory Not checked section.

Do not claim merged/landed; no actual merge is authorized in this stage. Return concise summary. If memory or safe execution is blocked, report that explicitly rather than weakening safeguards.
