# Project goal

> Derived by the orchestrator from the repository itself. Nobody was asked.
> Everything below is a hypothesis with a source. Corrections can arrive at any
> time and apply from the next cycle onwards - they never stop the run.

## Purpose

Sythion is the **trusted local runtime core** of a larger intelligence system
(`README.md:1-21`). It ships as a Windows 10/11 desktop application: a Tauri
shell wrapping a React dashboard, driving exactly one fixed Python/FastAPI
sidecar over an authenticated loopback control boundary. The runtime core owns
the durable task/event ledger, policy-gated tool execution, evidence capture
and verification, cancellation, replay, retention and recovery. Higher
intelligence layers are meant to sit *above* this runtime, never around it
(`README.md:23-27`, `docs/ROADMAP.md:104-114`).

## Who it serves

- The operator of the packaged desktop app: they run one local process, they
  trust the app with file, GUI, app, package and voice actions, and they need
  to know what actually happened.
- Contributors and future agents maintaining the runtime: the repo's own
  quality gates demand that docs match the code and that claims are backed by
  executed evidence (`docs/QUALITY_GATES.md:34-44`, `:61-67`).

## What "better" means

- **Runtime boundary integrity**: the trusted loopback boundary, policy gating,
  target validation, cancellation and verification stay strict; nothing in this
  run may weaken them (`docs/QUALITY_GATES.md:10-21`).
- **Verification before claims**: the backend suite stays green, docs state the
  *real* baseline rather than a stale one, and every behaviour change is
  pinned by a test (`docs/QUALITY_GATES.md:34-44`, `:88-97`).
- **Release and operator readiness**: the confidence that a tagged build
  behaves like the repo claims - CI, packaging gates, runbooks
  (`docs/ROADMAP.md:50-55`).
- **Architecture clarity and honest debt reporting**: mixed runtime/intelligence
  modules get split when the boundary is clear, and known gaps stay documented
  rather than papered over (`docs/QUALITY_GATES.md:23-32`, `:99-109`).

## Derived from

- README: `README.md` (repo purpose, boundary, source-of-truth doc list, claimed
  baseline of 692 passed / 32 skipped and 196 acceptance passed / 2 skipped).
- Manifest / package metadata: `backend/requirements.txt` (FastAPI 0.109.0,
  SQLAlchemy 2.0.40, pydantic 2.10.6, uvicorn 0.27.0, httpx 0.26.0),
  `frontend/package.json` (React 18, Vite 5, Tauri CLI 2.12, eslint with
  `--max-warnings 0`, vitest), `backend/requirements.lock.txt`.
- Recent git history: `git log --oneline` on `master` - `d2a24e5` "Fix Sythion
  startup defects and clean the release payload", `b9152fa` "start", then the
  checkpoint series `9347612`..`db10f86` (read-back verification hardening for
  mutating tools, durable work-state layer, Phase A-E hardening). The history is
  a sequence of *verification* and *boot-correctness* checkpoints, which is
  itself evidence of where the project's risk concentrates.
- Open TODOs / FIXMEs: 4 raw `TODO|FIXME|XXX|HACK` matches across the backend
  tree - the codebase is not TODO-driven; the unfinished work is described in
  `docs/ROADMAP.md` and in `C:\Sythion\SYTHION_TODOS.md` instead.
- Test suite state: `backend/tests` contains 120 indexed test files including a
  dedicated `backend/tests/acceptance/` suite; the README and
  `docs/ROADMAP.md:20-37` disagree on the baseline (README says 692 passed /
  32 skipped, ROADMAP says 659 passed / 32 skipped as of 2026-08-16). There is
  **no `pytest.ini`, no `pyproject.toml` and no `setup.cfg`** anywhere in the
  repo, so pytest runs with `inifile: None` and no registered markers.
- CI / lint config: `.github/workflows/ci.yml` - 4 jobs (backend-tests on
  `windows-latest` with Python 3.11, a credential-gated `provider-e2e`
  workflow_dispatch job, `frontend` lint+build, `tauri-build`). The workflow
  triggers on `push: branches: [main]`, but the repository's only branch is
  `master` (`git branch -a` -> `master`, `origin/HEAD -> origin/master`). No
  Python linter is configured (`Makefile` `lint:` target is an echo). Frontend
  lint is `eslint --max-warnings 0`.
- Other: `docs/ROADMAP.md` (Now / Next / Later / Deferred / out-of-scope),
  `docs/QUALITY_GATES.md` (9 gates), `docs/ARCHITECTURE.md`,
  `docs/CODEBASE_BOUNDARIES.md`, `docs/PROJECT_STATUS.md`,
  `docs/acceptance-test-map.md`, `C:\Sythion\SYTHION_TODOS.md` (Phases A-F,
  with exactly one open box: "Run all mandatory acceptance tests on a clean
  interactive Windows machine" - a manual release gate).

## Assumptions

| # | Assumption | Why | How to check |
|---|---|---|---|
| 1 | The runtime core in `backend/` is the product; the intelligence layer above it is scaffolding | `README.md:10-27` and `docs/ROADMAP.md:104-114` both say so explicitly | Read `docs/CODEBASE_BOUNDARIES.md` and check that changes stay inside the runtime core |
| 2 | `master` is the base branch that merges must target | `git branch -a` shows only `master` and `origin/HEAD -> origin/master` | `git rev-parse --verify master` and check `origin/master` |
| 3 | The backend suite is the release-confidence signal, and a green suite is the acceptance bar | `docs/QUALITY_GATES.md:88-97`, `Makefile` `test:` target, `ci.yml` backend-tests job | Run `python -m pytest backend/tests -q` and compare the count with the README/ROADMAP claims |
| 4 | Windows-only runtime: pywin32, UIA/MTA, winget and SAPI5 paths are intentional, not legacy to be deleted | `docs/ROADMAP.md` and `SYTHION_TODOS.md` Phase C/D describe them as the chosen platform | Check `backend/tools/gui_tools.py` and `backend/tools/package_tools.py` for Windows-only guards |
| 5 | The live sidecar DB in `%LOCALAPPDATA%\Sythion (Synth)` and `backend/sythion.db` are real user data and must not be reset | `docs/ROADMAP.md`, `backend/core/storage.py`, and the guardrails in this skill | Inspect what a test command does before running it; tests must use temp DBs |
| 6 | The single open TODO (clean-machine interactive acceptance run) genuinely needs a human | It requires an interactive Windows desktop session | `SYTHION_TODOS.md:111` and `docs/acceptance-test-map.md` |
| 7 | The missing pytest config file is a real gap and not a deliberate choice | There is no `pytest.ini`/`pyproject.toml`/`setup.cfg` anywhere, yet the Makefile and CI select tests by marker (`-m "not slow and not flaky ..."`), and the markers those targets depend on are applied to almost no test | `python -m pytest backend/tests -q` prints `inifile: None`; `backend/tests/conftest.py::pytest_configure` declares eight markers, but `gui`, `network`, `acceptance`, `flaky` and `integration` are applied to ZERO tests (see R-003) and `concurrency`/`learning_soak` are applied but never declared (see R-005) |

## Off-limits (never change without asking)

- Authentication and the local control boundary: `backend/core/handshake.py`,
  `backend/core/auth.py`, `backend/core/profile_lock.py`, token issuance and
  origin checking.
- Secret storage and DPAPI / Windows Credential Manager paths:
  `backend/core/secrets.py`.
- Database schema and migrations: `backend/models/database.py`,
  `backend/migrations/`, and anything that changes the on-disk schema.
- Public HTTP API shape: `backend/api/routes.py`, `backend/api/schemas.py`
  (request/response contracts consumed by the React frontend).
- Safety gates that deny an action: file-root grants and canonical handle
  rechecks, elevated-window / secure-desktop denial in `backend/tools/gui_tools.py`,
  fixed-argument winget restrictions in `backend/tools/package_tools.py`,
  budget enforcement in `backend/core/budgets.py`.
- Release, publishing, signing, installer and distribution
  (`docs/release-plan.md`).

These are not off-limits to *read*, to *test*, to *document*, or to improve with
tests and observability. They are off-limits to change *behaviourally* unless
the sensitive-change gate passes.

## Base branch

master

## Run order and dependencies

Fixed by the orchestrator. The order is not a preference: three of the
dependencies are HARD, and each was found by running the thing rather than by
reasoning about it.

1. **001 (CI) and 004 (tautologies) in parallel** - independent. Both merged.
2. **002 (test-selection truth)** before 003 - item 003's tables must describe
   the merged `Makefile` / `pytest.ini` / `ci.yml`.
3. **002 and 006 CONFLICT** at `backend/tests/conftest.py`'s last line.
   Cause: master's file ends WITHOUT a trailing newline, so both branches
   modify the same final line and then append their own block. The Committer
   resolves it at merge time by keeping both blocks and adding the newline.
   **The orchestrator originally asserted these hunks did not overlap and that
   the merge would be clean. That was wrong, and the Reviewer of 006 caught
   it by attempting the combination.** The corrected statement is in
   `critiques.md`.
4. **007 (credential vault) before any full-suite measurement.** Item 003's
   measurement script runs the full backend suite, and until 007 lands that
   includes the test that writes to and deletes the developer's REAL Windows
   Credential Manager entry. Safe before 007 merges: `--collect-only` (no
   test bodies execute), the D1/D2 sandbox Makefiles outside the repository,
   and `check_doc_paths.py` (pure file reading).
5. **006 adds 3 tests; 002 and 007 add 0.** So the merged tree collects
   **1614** where `master` collects **1611**. Any measurement taken on a tree
   without 006 is stale by exactly those two, and item 003's own `--check`
   mode is designed to say so.

## Commands
- \python -m pytest backend/tests -q\ (full backend suite, from the repository root)
- \python -m pytest backend/tests/acceptance -q\ (acceptance sub-suite)
- \cd frontend && npm run lint\ (eslint, --max-warnings 0)
- \cd frontend && npm test\ (vitest run, 49 tests)
- \cd frontend && npm run build\ (tsc + vite build)
- \python -c "import backend.main; import backend.api.routes"\ (backend import gate, from the repository root)

## Budget for this run

**6 merged items.** (Raised from the default 5 on 2026-10-20, with a reason:
implementing item 001 exposed a pre-existing defect - `backend/.env` is
gitignored, so `test_setup_readiness.py::test_readiness_states` FAILS in a
clean checkout, and a GitHub Actions runner is a clean checkout. The pipeline
item 001 repairs would therefore go RED on its first run for a reason that has
nothing to do with item 001. The plan for item 001 explicitly forbids fixing
such a pre-existing failure inside that item, so it becomes its own item (006).
Shipping a knowingly-red pipeline would defeat the purpose of item 001. The
raise is one item, not an open-ended extension.)

## Baseline notes

- Baseline captured by the orchestrator at the start of this run is recorded in
  `log.md` and in `reports/research-001.md`. Any pre-existing failure is
  listed there so it is not blamed on later work.
- The suite runs with **no pytest configuration file**: pytest 8.4.2's header
  prints `rootdir: C:\Sythion\sythion` and **no `configfile:` line** (the
  string "inifile: None" is pytest's *usage-error* wording, not its normal
  header - corrected by the Scout, see `critiques.md`).
  `backend/tests/conftest.py::pytest_configure` declares eight markers
  (`slow`, `flaky`, `integration`, `acceptance`, `gui`, `network`,
  `process_kill`, `provider_e2e`); the two that are applied but never declared
  are `concurrency` and `learning_soak`. The markers the Makefile selects on are
  applied to almost no test: `gui`, `network` and `acceptance` to zero (R-003).
  pytest-asyncio currently reports `asyncio: mode=Mode.STRICT`.
- The suite is NOT deterministic on this machine. Across six observed full-suite
  runs there were **2 native crashes of two different kinds** (R-007):
  - **Fault A** - a BURST of 294 native COM faults (277x `0x80040155`
    CLASS_E_CLASSNOTAVAILABLE, 18x `0x8001010d` RPC_E_INVALID_REFERENCE) on the
    single `sythion-uia-mta` thread inside `_UIADriver._find_window`
    (`backend/tools/gui_tools.py:417`/`:426`, reached via `:403` observe from
    the dispatch lambdas at `:1537` and `:1749`). It does **not** kill the
    process; the captured run continued another 23 percentage points.
  - **Fault B** - `Windows fatal exception: access violation` (`0xC0000005`)
    inside native sqlite3, in the two `tfn` threads of
    `backend/tests/test_phase105_concurrency.py::TestBudgetAtomicity::test_concurrent_budget_does_not_exceed`
    -> `backend/agents/coordination.py:1127 atomic_commit_spent`. It **does**
    kill the interpreter, with no pytest summary.
  - The smallest reproducer is the FULL SUITE. The single test that reaches
    Fault A's frame was clean in 30/30 separate-process runs and Fault B's
    test in 25/25, so a single-test loop will report "not reproducible" and
    send the next agent down the wrong path.
  - `CLSID_CUIAutomation` is registered `ThreadingModel = Both`, so the
    apartment-mismatch explanation for `CLASS_E_CLASSNOTAVAILABLE` is refuted.
- **Measured baseline** (CPython 3.14.7, Windows): full suite `1564 passed,
  45 skipped`, `1609 collected`, wall time **389-436 s** (two clean runs;
  the Researcher's `533 s` is refuted). Acceptance suite `191 passed,
  2 skipped` (the documented `196` is refuted; the claim that the two skips
  ARE the symlink-privilege skips is confirmed). Frontend lint exit 0; vitest
  5 files / 49 tests passed.
- **The warning count is NOT a stable baseline** (407,733 / 406,252 / 407,719
  across three runs of an unchanged tree). Do not quote it in a document.
- A faulthandler dump is a snapshot of ALL threads. The faulting thread must
  be identified from the dump, not from the main thread's stack - attributing
  a crash to "the test the main thread was running" is a misreading.
- The local interpreter is CPython **3.14.7**; CI pins **3.11**. Differences
  between the two are a plausible source of local-only failures and must not be
  reported as project defects without a check.
- `C:\Sythion` (the parent directory) is a *separate* git repo holding the
  CogniGraph memory database (`C:\Sythion\.cognigraph\memory.db`), `AGENTS.md`
  and `opencode.json`. It contains an untracked `sythion/` directory, which is
  this repo. The orchestrator works in `C:\Sythion\sythion` only.
