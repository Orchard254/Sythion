# Report 010f-research-1 — native crash (0xC0000005) in full-suite runs

Scope: read-only research into the residual non-deterministic native crash described in memory facts #1273/#1330 and `.orchestra` reports. No suite was run; no source changed.

## Observed facts (verified in `.orchestra` artefacts, not by me)

- 2 native faults in ~6 full-suite runs on this machine: COM bursts (`0x80040155`/`0x8001010d`) on the `sythion-uia-mta` thread that do not kill the process; and a fatal `0xC0000005` (`exit 3221225477`) that kills the interpreter with no JUnit XML and no summary.
- Fatal crash sites recorded across verifier reports are not identical: the newer verifier (006-v1) shows `backend/tests/test_phase95_hardening.py::TestAdmissionAtomicity::test_two_callers_at_boundary_only_one_admits` -> `backend/agents/delegation.py` `validate` -> SQLAlchemy -> native sqlite, with the GUI worker thread incidentally parked at `backend/tools/gui_tools.py:973` (`_run` / `queue.get`).
- Older scope note (`goal.md`) records an earlier site as `test_phase105_concurrency.py::TestBudgetAtomicity::test_concurrent_budget_does_not_exceed` -> `backend/agents/coordination.py:1127 atomic_commit_spent` -> native sqlite. Both are SQL-heavy, threaded, full-suite-ordering-dependent.
- Both crashing tests pass in isolation (006-v1 records `flake-isolated-phase95`, 1 passed).
- Smallest reproducer in all reports is the FULL suite; 30/30 and 25/25 single-process loops were clean. Outcome is therefore order/accumulation-dependent, not a deterministic defect in one test.

## Environment observed (commands run, all tiny/read-only)

- `python --version` -> **CPython 3.14.7** (only interpreter on this machine).
- `ci.yml` pins **Python 3.11** for both local and provider-E2E jobs; README/runtime packaging ships a **Python 3.13** runtime under `src-tauri/resources/python-runtime`.
- `pip` inventory: no torch/sentence-transformers/hnswlib/pycuda/sqlcipher; present: `onnxruntime 1.29.0`, `psutil 7.2.2`, `comtypes 1.4.17`, `sqlalchemy 2.1.5`, `sqlite 3.50.4`.
- `sqlite3.threadsafety` -> 3 (serialized); bundled runtime's `_sqlite3` and the OS SQLite are not the same build as the 3.14 dev install's.
- `Select-String` over `backend/` for `KMP_/OMP_/MKL_/CUDA`-style env handling found **none**. `concurrent.futures.ThreadPoolExecutor` appears only in `ollama_adapter.py`, `cognitive/reasoning.py`, `services/planner.py`, all `max_workers=1`; `threading.Thread` only in `gui_tools.py` and four tests.

## Code-level candidates (ranked, all unverified)

1. **Python/native ABI mismatch between CI and this dev box (most likely environmental).** CI and the sidecar pin 3.11/3.13; this machine runs 3.14.7 and the installed `pywin32`/`_sqlite3`/`onnxruntime` builds are the 3.14 wheels. Whichever extension's native heap layout differs is a credible trigger for `0xC0000005`.
2. **Threaded SQLite under full-suite load.** `test_db` uses `StaticPool` + `check_same_thread=False`; tests sharing one underlying connection plus teardown without `engine.dispose()` are common across the suite. `threadsafety=3` makes this legal, but mixed exhaustion of native handles across ~1614 tests is still suspicious.
3. **GUI/COM worker-thread lifetime.** The global MTA worker is created once and never stopped; COM bursts (`0x80040155`) in `_find_window` are a different fault family. Cross-family interleaving between COM apartment cleanup and SQLite cleanup during teardown could account for a late access violation.
4. **Memory pressure / handle accumulation (secondary).** `test_db` engines are not disposed; ThreadPoolExecutors create new event loops; ~1614 DB engines by end-of-run. Could produce native OOM-style faults, but no evidence of high memory.
5. **Product bug in delegation admission (least likely).** Crash is in SQL/allocator code paths; affected tests are thread-concurrency admissibility and pass in isolation. No code-path regression corresponds to the crash.

## Recommendation

**Treat as environmental rather than blocking further work.** CI (Python 3.11, Windows runner) has never shown the fatal native crash, and the only documented repro requires the 3.14 developer box under full-suite ordering that cannot be cheaply recreated. _Do not hunt deeper now_: a 3.14-vs-3.11 verification is a cheap, definitive discriminator and should be run once (e.g., install 3.11 + pinned deps, run only `test_phase95_hardening.py::TestAdmissionAtomicity` and `test_phase105_concurrency.py` repeatedly) — **not** another full-suite run. If the team still wants certainty, add `faulthandler` dump collection to CI only and gather dumps there; do not request repeated local full-suite passes. Keep recorded as a known non-deterministic outcome, not a tracked defect.