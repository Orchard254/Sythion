# Implement item 024 — honest safe onboarding

You are Implementer. Base: C:\Sythion\sythion master at 29d87986a259dd27637a581a105cc7ce166b29ae. Worktree: C:\Users\orcha\AppData\Local\Temp\opencode\sythion-024-implement owns feature branch `orchestra/024-safe-onboarding` detached at reviewed tip. Implement .orchestra/plans/onboarding-repair-20261023.md item 024 EXACTLY, no 025.

Allowed product/test files only:
- backend/api/routes.py — modify only existing safe-settings and readiness functions and at most small shared private predicate for guarded authority.
- backend/tests/test_onboarding_safe_settings.py — new.
- backend/tests/test_setup_readiness.py — additive expected.
- frontend/src/screens/Setup.tsx — safe-settings Continue handler and removal of now-unused settingsService import.
- frontend/src/screens/Setup.test.tsx — replace obsolete forbidden-update contract and add failure/stale/text-first gates.

Do not edit any other file, including settings_store, config/authority consumers, auth/secrets, schema/migrations, retention service, companion architecture, routes beyond allowed functions. Do not broadly allow autonomy. Do not invent or cite unverified behavior. Implement authorized staged persistent transactional write and validation. Effective guarded authority = runtime-visible persisted env/settings truth as specified. Env autonomous => 409; env guarded + settings autonomous => success; marker alone, malformed/different retention => false readiness. Keep full_ready existing and text-first. Frontend calls existing applySafeSettings, checks ok===true and refresh, requires canStartChat(report) before advance; never mark readiness optimistically. No settings generic writes. Optional microphone remains optional. First step succeeds and reaches step 2. Independent review and verifier will own proving.

Before code, append planner adjudication critique in main C:\Sythion\sythion\.orchestra/critiques.md canonical fields for 024. Reserve IDs? Track a bounded onboarding run in .orchestra/onboarding-run-20261023.md. Full suites after focused tests; reports/tests must show base vs fixed where feasible. No builds (no source rebuild). Use original frontend mock patterns. No app process or live secret. No prod/client mutation beyond allowed worktree.

Run focused backend and frontend gates; retain failing logs if any; if failure, report and don't weaken from planned invariants. Return branch tip and exact commands/file list. Write canonical report main .orchestra/reports/024-implement-1.md.

Do not merge. Do not ask.