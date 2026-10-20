# Report: research - 002 - attempt 1

> Third research pass. **Five** candidates added to `.orchestra/research.md` as
> **R-020, R-021, R-022, R-023, R-024**, at the top of the queue, in that id
> order, each placed directly above the previous one. R-001..R-019 were **not**
> renumbered, reordered, edited or deleted - verified: 24 `### R-NNN` blocks, 24 of
> each required field, the Scout's 11 `adopted` and 7 `rejected` status values and
> all 7 `Rejected reason:` lines intact.

## What I did

### Read

- `.orchestra/briefs/002-research-1.md`, `.orchestra/research.md` (my own file),
  `.orchestra/critiques.md` (the blocks `2026-10-20 19:10 implementer-001`,
  `19:45 reviewer-001`, `19:50 implementer-004`, `20:20 reviewer-004`,
  `20:35 verifier-001`), `.orchestra/reviews/001-r1.md` (finding 3),
  `.orchestra/reviews/004-r1.md`, `.orchestra/reports/004-implement-1.md`
  (defect D-1 and finding 5), `.orchestra/reports/001-implement-1.md`,
  `.orchestra/reports/research-002.md` (my own last pass, Finding 5),
  `.orchestra/follow-ups.md`.
- The repository files each finding names, to confirm the `file:line` references
  against the **current** `master` (which now includes the merged item 001):
  `backend/tests/acceptance/test_secret_storage.py`,
  `backend/tests/acceptance/conftest.py`, `backend/core/secrets.py`,
  `backend/core/db_manager.py`, `backend/runtime/events.py`,
  `backend/api/routes.py`, `backend/services/capabilities.py`,
  `backend/services/self_model.py`, `backend/tests/conftest.py`,
  `.github/workflows/ci.yml`, `docs/ARCHITECTURE.md`, `.gitignore`,
  `backend/.env.example`.

### Method

The brief was explicit: the evidence is already in hand; my job is to write it up
correctly and **confirm the `file:line` references**, not to re-derive or re-run.
So I did exactly that - and, where a reference could have drifted, I confirmed it
rather than trusting the brief.

Two things needed confirming beyond reading:

1. **R-020's exposure scope** - whether the full suite actually *executes*
   `test_credential_manager_round_trip`, or collects-but-skips it. I answered this
   with **collection only** (`--collect-only`), which never runs a test body.
2. **R-022's step semantics** - I reproduced the runner's `pwsh` wrapper by hand so
   the candidate carries a first-hand result, not only a quotation.

### Commands run (all read-only; no file in the repository was created, edited or deleted)

- `python -m pytest backend/tests/acceptance/test_secret_storage.py --collect-only -q`
  -> `10 tests collected` (**collection only - nothing executed**).
- `grep` over the full-tree collection list for `test_secret_storage` -> the ten
  collected ids, including `::test_credential_manager_round_trip`.
- A read-only vault existence check (`win32cred.CredEnumerate`, outside the
  repository) that reports only the error code and the match count - **no blob is
  read, printed, written or deleted**.
- The GitHub runner's exact `pwsh` step wrapper, executed by hand with the relative
  path absent from the working directory.
- `Select-String` / `Get-Content` sweeps for every cited symbol and line.
- `git status --short`, `git log --oneline -3`.

### Files I wrote

- `C:\Sythion\sythion\.orchestra\research.md` - the R-020..R-024 blocks.
- `C:\Sythion\sythion\.orchestra\reports\research-003.md` - this file.

No branch, no commit, no push, no `make clean`, no `scripts/stage_python_runtime.py`,
no `npx tauri build`, no migration, no database reset. The full suite was **not**
run. `test_secret_storage.py` was **not** executed - only collected.

## Evidence

### E1 - R-020: the vault write and delete, and the confirmed current state

`backend/tests/acceptance/test_secret_storage.py:128-148`:

```
128: def test_credential_manager_round_trip(tmp_path):
134:     try:
135:         import win32cred  # noqa: F401
136:     except ImportError:
137:         pytest.skip("pywin32 not available")
139:     store = secrets_module.CredentialManagerStore()
140:     store.set(SECRET_NAME, "sk-roundtrip-zz")
141:     try:
142:         assert store.get(SECRET_NAME) == "sk-roundtrip-zz"
143:         assert secrets_module.has_secret(SECRET_NAME) is True
144:         assert SECRET_NAME in secrets_module.list_secret_names()
145:     finally:
146:         store.delete(SECRET_NAME)
147:     assert store.get(SECRET_NAME) == ""
148:     assert secrets_module.has_secret(SECRET_NAME) is False
```

The target it writes to is the **product's own**: `backend/core/secrets.py:29`
`_CM_TARGET_PREFIX = "Sythion (Synth)/"`, `:32-34` `_cm_target`, `:66-76`
`CredentialManagerStore.set` -> `win32cred.CredWrite`, `:78-87` `.delete` ->
`CredDelete`. The product writes the same target through `:143-147` `set_secret`.

**Exposure scope, confirmed by collection only:**

```
$ python -m pytest backend/tests/acceptance/test_secret_storage.py --collect-only -q
10 tests collected in 0.47s
```

and the full-tree collection list contains
`backend/tests/acceptance/test_secret_storage.py::test_credential_manager_round_trip`.
The file's only skip is `:137` (the `win32cred` ImportError); `win32cred` imports
here, and `backend/tests/acceptance/conftest.py` adds no skip. **So the full suite
executes it - the exposure is "every suite run", not something narrower.**

**Current vault state, read-only, reproduced independently:**

```
CredEnumerate('Sythion (Synth)/ollama_api_key') -> raised error
  winerror = 1168
existence: ABSENT (no matching record)
```

winerror 1168 is `ERROR_NOT_FOUND`. This reproduces the orchestrator's finding.
**What I cannot establish, stated plainly:** whether the entry ever existed, and
therefore whether one of this session's many full-suite runs deleted it. The
repository records no vault history, and the observed absence is equally consistent
with "the developer never used the vault" and "a test run destroyed the entry". The
`backend/.env` fallback (`SYTHION_OLLAMA_API_KEY`, 57 characters) is still present.

### E2 - R-021: the read-then-write sequence allocation

`backend/runtime/events.py:41-73`, with `:64`:

```
47:         last = (
48:             self._session.query(TaskEvent)
49:             .filter_by(task_id=task_id)
50:             .order_by(TaskEvent.sequence_num.desc())
51:             .first()
52:         )
56:         pending_max = max(
57:             (
58:                 e.sequence_num
59:                 for e in self._session.new
60:                 if isinstance(e, TaskEvent) and e.task_id == task_id
61:             ),
62:             default=0,
63:         )
64:         sequence = max(last.sequence_num if last else 0, pending_max) + 1
```

The unique index that rejects the colliding commit - `backend/core/db_manager.py`,
`CREATE UNIQUE INDEX IF NOT EXISTS uq_task_events_task_sequence` at **`:129`**,
`ON task_events (task_id, sequence_num)` at `:130`, the statement block `:128-131`.

Reproduction and measurement are implementer 004's, at
`.orchestra/reports/004-implement-1.md:315-358`: six runs of
`backend/tests/test_phase105_concurrency.py::TestEventLedgerConcurrency::test_no_duplicate_sequence_and_no_cross_contamination`,
every one reporting **`1 passed`** while the worker thread printed
`sqlalchemy.exc.IntegrityError: (sqlite3.IntegrityError) UNIQUE constraint failed: task_events.task_id, task_events.sequence_num`
(four of the six attempts); and eight measurement rounds with
`survived [10, 5] / [10, 10] / [10, 5] / [5, 5] / [10, 10] / [10, 5] / [10, 10] / [10, 5]`
- up to 50% of a task's events lost, every test green.

### E3 - R-022: the CI step is a no-op (reproduced first-hand)

`.github/workflows/ci.yml:132-133`, current post-merge lines:

```
132:       - name: Assert the shell binary was produced
133:         run: Test-Path "src-tauri/target/release/sythion.exe"
```

The runner's `pwsh` wrapper is `$ErrorActionPreference = 'stop'; <script>; if ((Test-Path -LiteralPath variable:\LASTEXITCODE)) { exit $LASTEXITCODE }`.
Executed by hand, with the relative path absent from the working directory:

```
=== exact runner wrapper, relative path ABSENT here ===
False
wrapper exit code:    (0 = the GitHub step would PASS with the binary missing)
```

`Test-Path` printed `False` and left `$LASTEXITCODE` **unset**, so the wrapper's
`exit $LASTEXITCODE` never runs and the step's status is 0. The orchestrator
reproduced the same result independently.

### E4 - R-023: the two sites that bypass the sanctioned resolver

```
backend/api/routes.py:2007:     api_key_ok = has_secret(OLLAMA_API_KEY) or bool(settings.ollama_api_key)
backend/api/routes.py:2261:     key_ok = has_secret(OLLAMA_API_KEY) or bool(settings.ollama_api_key)
backend/core/secrets.py:167: def resolve_ollama_api_key(env_override: str = "") -> str:
backend/core/secrets.py:175:     Settings.ollama_api_key directly.
docs/ARCHITECTURE.md:357: only resolvable at the one sanctioned point (`resolve_ollama_api_key`).
```

Every other reader of the key already goes through the sanctioned point:
`backend/core/config.py:211`, `backend/main.py:255`,
`backend/adapters/ollama_adapter.py:58` and `:437`,
`backend/api/routes.py:2130` and `:2179`. Only these two bypass it. The two forms
are exactly equivalent in behaviour (`has_secret(X) or bool(env)` ==
`bool(resolve_ollama_api_key(env))`, from `backend/core/secrets.py:157-159` and
`:167-179`) - so the defect is contractual, not behavioural.

### E5 - R-024: the dead branch and what it makes unreachable

`backend/services/capabilities.py:215-223`:

```
215:         if spec.id == "reasoning_model":
216:             from backend.core.config import resolve_selected_model
217:             from backend.core.secrets import has_secret, OLLAMA_API_KEY
218:
219:             if not (has_secret(OLLAMA_API_KEY) or True):
220:                 return REQUIRES_CONFIGURATION
221:             if not resolve_selected_model():
222:                 return REQUIRES_CONFIGURATION
223:             return AVAILABLE  # reachability is Health's job, not availability
```

`has_secret(...) or True` is always True, so `:220` is unreachable. The consumer is
`backend/services/self_model.py:87-90`:

```
87:         "requires_configuration": [
88:             {"id": c["id"]} for c in caps
89:             if c["status"] == REQUIRES_CONFIGURATION
90:         ],
```

The spec is `backend/services/capabilities.py:138-143` (`reasoning_model`,
`requires_configuration=True`). The `has_secret` import at `:217` feeds only the
dead branch. The tests that call `resolve_status`
(`backend/tests/test_phase5_capabilities.py:70`,
`backend/tests/test_phase95_hardening.py:176`,
`backend/tests/test_phase975_capabilities.py:97-98`) never exercise
`reasoning_model`.

## Findings

1. **R-020 is the most severe finding of the run**, and the confirmation that made
   it so is the collection check: the test is not skipped, it is in the full-suite
   collection, and it writes to and deletes the **product's own** credential target
   in the developer's real Credential Manager. The effect is outside the
   repository, it destroys user data, and it fires on every suite run. What I
   could **not** establish is whether it has already destroyed this machine's
   entry: the entry is absent now (winerror 1168, read-only), and the repository
   keeps no vault history, so "never existed" and "deleted by a test run" are
   indistinguishable from here. That question is stated as open in the candidate.

2. **R-021 is a production durability defect, not a test defect.** The ledger's
   sequence allocation is a read-then-write against a unique index, so concurrent
   appends to one task collide; the worker thread's `IntegrityError` is swallowed
   by `threading` and the suite stays green while up to 50% of a task's events
   vanish. It is already `[required]` in `follow-ups.md:24`; the candidate is the
   repo-side item that the human entry waits on. The fix and the count-assertion
   test are a **pair** - the assertion alone would make the suite intermittently
   red.

3. **R-022 is a gate that does not gate.** Reproduced first-hand, not quoted: the
   step prints `False` and exits 0 with the binary missing. It is the same
   silent-green pattern item 001 exists to eliminate, in the file item 001 changed.
   Already `[deferred]` in `follow-ups.md:20`.

4. **R-023 is a contract defect, not a behavioural one**, and the candidate says so
   explicitly - because the two candidate fixes (routing through the sanctioned
   resolver, and R-024's predicate) are both behaviour-preserving and both land in
   `backend/core/secrets.py` and `backend/api/routes.py`, which `goal.md` puts
   off-limits. The candidate states the sensitivity so the Scout can route it
   through the sensitive-change gate rather than treating it as a routine refactor.

5. **R-024 is the tautology shape in production, and it is also a wrong answer
   shown to the operator**: a `reasoning_model` capability with no credential is
   omitted from the self-model's `requires_configuration` list. Two extra facts I
   established that the implementer's note did not: the bare `has_secret(...)`
   reads the **vault only**, so making the branch live literally would report
   `REQUIRES_CONFIGURATION` to a developer whose credential comes from
   `backend/.env` - which is why the fix belongs with R-023; and the intended
   predicate is **not stated anywhere in the repository**, so I have written the
   candidate as a decision-plus-shape rather than as a mechanical edit.

6. **Two of the five already have a human-side follow-up** (`follow-ups.md:20` for
   the CI no-op, `:24` for the ledger defect). That is not duplication: those
   entries record the *operator* action, while the candidates give the Scout the
   *repo-side* work to schedule. I noted the overlap in both candidates so the
   Scout can see it and does not file two items for the same change.

7. **The `file:line` references all still hold on the current `master`**
   (`86c975d`, with item 001 merged as `7c478d1`). One reference in the brief
   needed care: the brief cites `backend/core/secrets.py:32-34` for the credential
   target, which is `_cm_target`; the constant itself is at `:29`. I cited both.
   The `ci.yml` lines are unchanged by the merge (`:132-133`).

## Not checked

- **I did not execute `test_secret_storage.py`.** I used `--collect-only`, which
  never runs a test body. I did not write to, delete from, or read the value of
  any Windows Credential Manager entry; the only vault operation I performed is a
  read-only `CredEnumerate` existence check that reports the error code and the
  match count.
- **I could not determine the vault's history.** Whether the
  `Sythion (Synth)/ollama_api_key` entry ever existed, and whether a test run in
  this session deleted it, is not recoverable from the repository or from the
  present state (the entry is absent now). The candidate states this as open
  rather than guessing.
- **I did not re-run the full suite**, as the brief instructed, so the scope claim
  for R-020 rests on the collection list plus the absence of any skip, not on a
  fresh execution.
- **I did not re-derive R-021's measurement.** The 8-round event-loss figures and
  the six-run `IntegrityError` reproduction are implementer 004's, quoted with
  their file:line; I confirmed the **code** and the **unique index** that they
  depend on, and did not repeat the measurement.
- **I did not execute the `pwsh` wrapper under PowerShell 7** - `pwsh` is not
  installed on this machine, so my first-hand reproduction of R-022 ran under
  Windows PowerShell 5.1. The semantics relied on (`Test-Path` is a cmdlet and
  does not set `$LASTEXITCODE`) are identical in both, and the orchestrator
  independently reproduced the runner's wrapper.
- **I did not run GitHub Actions**, so I cannot say what a real `tauri-build` job
  reports; the claim is about the step's exit status, which I reproduced.
- **I did not check whether the GitHub-side repository settings or secrets are
  configured**, and I did not read any credential value - only key names and the
  length of the `.env` key.
- **I did not decide R-024's intended predicate.** The repository does not state
  it, and it is a product decision about what the capability health report should
  mean; the candidate presents the options and the constraint rather than
  pretending the answer is derivable.
- **I did not examine whether `backend/tests/acceptance/` has other tests that
  touch the real vault.** I read that one file in full and swept
  `backend/tests/**` for `set_secret_store_for_testing` call sites; the only other
  file that touches the store is `backend/tests/acceptance/test_secret_storage.py`
  itself (its `memory_secret_store` fixture, which resets the store to `None` at
  teardown - which is precisely how the real vault becomes reachable again).
