# Implement item 023 continuation — verification and commit prep

You are Implementer, role exception after a stalled background task. Work in existing worktree `C:\Users\orcha\AppData\Local\Temp\opencode\sythion-023-implement`, branch `orchestra/023-onboarding-discovery`, base `7a9c2f5ea5cefa67edde14465890145ce9b74b47`. Do scoped continuation only, no new product edits except making tests robust if clearly necessary. Read plan and briefs. The current worktree already contains:

- `backend/api/routes.py` change: sync provider discovery now run through `run_in_threadpool`.
- `frontend/src/services/api.ts`: model IDs normalized.
- `frontend/src/screens/Setup.test.tsx`: 30-line updated selector regression.
- new tests `backend/tests/test_onboarding_model_discovery.py` and `frontend/src/services/modelDiscovery.test.ts`.

Run:
1. Inspect the complete files/diffs and ensure allowed-files only.
2. Checkids/branch status.
3. Run backend isolated regression command with specific files.
4. Run frontend vitest command and tsc.
5. Report errors; do not weaken tests to make green. If event-loop/asynchronous offload test is absent, list as gap rather than fabricating.
6. Do not commit until all required gates pass; if committed, report SHA. Preserve main worktree. Avoid killing unrelated processes. Use background for commands and wait notifications rather than polling.
7. Write main report `C:\Sythion\sythion\.orchestra\reports\023-implement-1.md` (canonical), preserving current report if absent; include negative bases if applicable, exact commands and outcomes, all failures. No final verdict inflation. Do not start item 024.

Stay in sequential/orchestrator support mode and mark work as `- self-directed: yes` later. Do not ask user.