# Follow-ups for the human

Things the team cannot or must not do itself: deploys, publishes, migrations
against real data, secret rotation, live config, manual checks.

Format: `- [ ] (item-id) [deferred|required] what to do`

- `[deferred]` - safe to do later, the run did not wait on it and nothing is
  broken until it happens.
- `[required]` - must happen before the change is live. The in-repo work is
  still merged; this only says when the outside action has to land.

Nothing here blocks the run. The orchestrator merges the in-repo part and
keeps going.

- [ ] (001) [required] Observe the repaired workflow on GitHub after the merge. The pipeline has never gated this repository, so its first real run is unobserved: confirm the `backend-tests` job passes on Python 3.11 (note `whispercpp==0.0.17` has no cp311 wheel and may need a source build), the `frontend` job's new vitest step passes, and the `tauri-build` job still succeeds.
- [ ] (001) [required] Before ANY manual `workflow_dispatch` of the CI workflow, confirm the GitHub repository variable `RUN_PROVIDER_E2E` is NOT set to `1` and that the `provider-e2e` environment / `OLLAMA_API_KEY` secret state is understood. Item 001 adds `workflow_dispatch`, which makes that credentialed job reachable by hand for the first time. This is not visible from the checkout.
- [ ] (001) [deferred] Consider whether the repository should be renamed to `main`, or whether `ci.yml` should keep both `master` and `main` in its push list. The workflow now accepts both, so the rename is optional.
- [ ] (000) [deferred] The backend suite is not deterministic on this machine: 2 native crashes in 6 observed full-suite runs, of two different kinds - a burst of COM faults in the `sythion-uia-mta` worker thread (`0x80040155`, does not kill the process) and a native sqlite3 access violation (`0xC0000005`) in `test_phase105_concurrency.py::TestBudgetAtomicity::test_concurrent_budget_does_not_exceed` (does kill the process). Neither reproduces in isolation; the smallest reproducer is the full suite. Neither root cause was established. This is the next research cycle's first candidate.
- [ ] (000) [deferred] The release payload work rejected from this run needs a release owner: `backend/requirements.lock.txt` does not pin `vosk` although the shipped runtime contains it (`scripts/stage_python_runtime.py` installs from the lock); the vendored runtime and `build/staging` carry ~253 MB of tracked binaries including two byte-identical duplicate archives; the installer ships the pytest/coverage stack; and `backend/sythion_backend/services/voice_service.py` is a 611-line dead copy that was deleted once on purpose and reintroduced by a bulk checkpoint commit. The only rebuild path (`scripts/stage_python_runtime.py`) `rmtree`s the committed runtime, so this is release/distribution work and was deliberately left alone.
