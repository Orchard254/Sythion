# Brief: research - 002 - attempt 1

- Item: 002 Record the remaining newly-discovered defects as research candidates
- Role: researcher (continuing the cycle-1 research)
- Goal (the slice that matters): Sythion's "better" includes **verification
  before claims**, **honest debt reporting** and **release and operator
  readiness** (`goal.md`, `docs/QUALITY_GATES.md:34-44`, `:45-59`, `:88-97`).
  Several real defects were discovered DURING this run's implementation and
  review stages. They are not in the research queue yet, so the Scout cannot
  rank them and the run cannot schedule them. Your job is to record them
  properly, with the evidence that already exists.

## Read

- `C:\Sythion\sythion\.orchestra\research.md` - **your file.** R-001..R-018
  from cycle 1, plus **R-019** which you added in your last pass. Append-only,
  newest at the top.
- `C:\Sythion\sythion\.orchestra\critiques.md` - read these blocks, they carry
  the findings:
  - `2026-10-20 19:10 - implementer - 001 - ACCEPT`
  - `2026-10-20 19:45 - reviewer - 001 - ACCEPT`
  - `2026-10-20 19:50 - implementer - 004 - ACCEPT`
  - `2026-10-20 20:20 - reviewer - 004 - ACCEPT`
  - `2026-10-20 20:35 - verifier - 001 - ACCEPT`
- `C:\Sythion\sythion\.orchestra\reviews\001-r1.md` - the Reviewer's findings.
- `C:\Sythion\sythion\.orchestra\reviews/004-r1.md` - the Reviewer's findings.
- `C:\Sythion\sythion\.orchestra\reports/004-implement-1.md` - the implementer's
  reported defect D-1.
- `C:\Sythion\sythion\.orchestra\reports/research-002.md` - **your own last
  pass.** Its "Findings" section 5 contains a candidate that you deliberately
  did NOT make, and which now must become one.
- `C:\Sythion\sythion\.orchestra/follow-ups.md` - what is already queued for
  the human, so you do not duplicate it as a candidate.
- The repository files each finding names, to confirm the file:line references
  before you write them down.

## Write

- `C:\Sythion\sythion\.orchestra\research.md` - APPEND-ONLY, newest blocks at
  the TOP, above R-019. **Do not renumber, reorder, edit or delete any
  existing block** - the Scout's adopt/reject decisions live in them and the
  orchestrator's critiques cite them by number.
- `C:\Sythion\sythion\.orchestra\reports/research-003.md` - your report.

Do not modify any other file, do not create branches, do not commit.

## Context

- Current status: items 001 and 004 are in their last stages; 002 is being
  implemented.
- Branch: `master` (read-only; do not switch branches)
- Plan: n/a
- Previous findings: your last pass added R-019.

**You already have the evidence for these. Your job is to write them up
correctly, confirm the file:line references, and identify anything that is NOT
yet established.** Do not re-run the full suite.

### Candidate 1 (R-020) - the acceptance suite writes to and DELETES the developer's real Windows Credential Manager entry

From your own last pass, Finding 5:
`backend/tests/acceptance/test_secret_storage.py::test_credential_manager_round_trip`
uses `secrets_module.CredentialManagerStore()` (`:139`),
`store.set(SECRET_NAME, "sk-roundtrip-zz")` (`:140`, target
`Sythion (Synth)/ollama_api_key`, `backend/core/secrets.py:32-34`) and
`store.delete(SECRET_NAME)` (`:146`). It does not request the
`memory_secret_store` fixture, and `win32cred` IS importable on this machine,
so `_default_store()` (`backend/core/secrets.py:112-121`) returns the REAL
vault whenever the store has been reset to `None`. This contradicts
`docs/ARCHITECTURE.md:358` ("A non-persistent in-memory backend is used for
tests so the real vault is never touched").

The orchestrator checked the CURRENT state, read-only, without touching the
secret: `win32cred.CredEnumerate('Sythion (Synth)/ollama_api_key', 0)` ->
**error 1168, "Element not found"** - the entry does not exist right now. The
fallback in `backend/.env` (a 57-character `SYTHION_OLLAMA_API_KEY`) is still
present. You cannot tell from here whether the entry ever existed or whether a
test run deleted it. **Say that honestly in the candidate.**

This is the most severe finding of the run: it is an effect OUTSIDE the
repository, it has already been triggered by every full-suite run performed in
this session, and it destroys a user's stored credential. Confirm the
file:line references, confirm the fixture's absence, confirm that
`_default_store()` can return the real store in a test context, and write the
candidate. Note in the candidate whether the FULL SUITE actually executes this
test (i.e. whether it is collected and run, or skipped) - that determines
whether the exposure is "every suite run" or narrower.

### Candidate 2 (R-021) - `DbEventLedger.append_event` loses concurrently-appended events

From implementer 004's reported defect **D-1**:
`backend/runtime/events.py:64` allocates the sequence number by read-then-write
(`sequence = max(last.sequence_num if last else 0, pending_max) + 1`) against
a unique index on (task_id, sequence). Two sessions appending to the same
`task_id` collide; the worker thread's `IntegrityError` is swallowed by
`threading`; the test still reports "1 passed" while up to **50%** of the
task's events are lost. Measured by the implementer: 5 or 10 of 10 events
surviving across 8 rounds. The event ledger is the repository's replay and
audit substrate, so this is a durability defect, not a test defect. Confirm the
code and the unique index, and write the candidate.

### Candidate 3 (R-022) - the `tauri-build` CI job's binary assertion is a no-op

From the Reviewer of item 001, finding 3:
`.github/workflows/ci.yml` - the step named "Assert the shell binary was
produced" runs `Test-Path "src-tauri/target/release/sythion.exe"`. `Test-Path`
is a PowerShell cmdlet, does not set `$LASTEXITCODE`, and the step exits **0
whether or not the file exists**. The orchestrator verified this by
reproducing the GitHub pwsh wrapper exactly
(`$ErrorActionPreference='stop'; Test-Path <missing>; if ((Test-Path
-LiteralPath variable:\LASTEXITCODE)) { exit $LASTEXITCODE }` -> step exit code
**0**). So the one CI job that builds the desktop shell does not gate on the
shell binary being produced. Confirm the current line and write the candidate.

### Candidate 4 (R-023) - the readiness route bypasses the sanctioned key-resolution point

From your own last pass, Finding 4:
`backend/api/routes.py:2261` (`setup_readiness`) and `:2007`
(`onboarding_status`) read `settings.ollama_api_key` directly, while
`backend/core/secrets.py:174-175` ("This is the single resolution point for the
key; callers must not read Settings.ollama_api_key directly") and
`docs/ARCHITECTURE.md:357` ("only resolvable at the one sanctioned point
(`resolve_ollama_api_key`)") name one sanctioned resolution point. Behaviourally
the two are equivalent (`has_secret(X) or bool(env)` is exactly
`bool(resolve_ollama_api_key(env))`), so this is a contract/architecture defect,
not a behavioural one - but it is in the secrets and public-API area, so the
severity and the sensitivity of a fix both need stating. Write it as a
candidate with that nuance explicit.

### Candidate 5 (R-024) - a dead branch of the same shape as the tautologies, in production code

From implementer 004: `backend/services/capabilities.py:219` has the same
dead-branch shape that item 004 removed from the test suite. Read the code,
confirm what the dead branch makes unreachable, and write the candidate.

## Constraints

- **Read-only.** The only files you may write are `research.md` (the new
  blocks at the top) and `reports/research-003.md`.
- **NEVER print a credential value.** Listing key names and lengths is fine.
  **Do NOT write to, delete from, or read the value of the Windows Credential
  Manager** - a read-only existence check (`CredEnumerate`) is the only
  credential-vault operation you may perform, and the orchestrator has already
  done it.
- Do not run the full suite. Do not run `test_secret_storage.py` deliberately.
- Do not run destructive commands, migrations, `make clean`,
  `scripts/stage_python_runtime.py`, or `npx tauri build`.
- Do not push, deploy, publish, or touch real data.
- Do not ask the user anything.

## Output contract

- Append to `C:\Sythion\sythion\.orchestra\research.md`, at the TOP, the
  blocks R-020, R-021, R-022, R-023, R-024 - **in that id order, each
  directly above the previous** - in exactly the existing shape:

```
### R-0NN <Short title>
- Source: local
- Source detail: <file and line, and the command you ran>
- Evidence: <what is wrong, concretely, and how you know>
- Files: <paths it touches>
- Severity: high | med | low
- Direction: <the shape of a fix, not the fix itself>
- Status: queued
```

- The heading pattern must be exactly `### R-0NN <title>`; `status.py` parses
  these.
- Write `C:\Sythion\sythion\.orchestra\reports\research-003.md` with the usual
  shape (What I did / Evidence / Findings / **Not checked**).
- Reply with a short summary of the five candidates and, for each, anything you
  could NOT establish. The FILES are the deliverable.

## Previous attempt (if any)

- Cycle 1 (R-001..R-018) was critiqued and ACCEPTED. Your follow-up pass added
  R-019 and was not yet critiqued at the time this brief was written. This pass
  adds five more candidates, all of which were discovered by other roles
  during implementation and review, with evidence already in hand.
