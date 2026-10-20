# Brief: scout - 002 - attempt 1

- Item: 002 Re-rank the backlog with the newly discovered defects and decide
  what fits the remaining budget
- Role: scout
- Goal (the slice that matters): Sythion's "better" is defined in `goal.md`:
  the runtime boundary stays strict, **claims are backed by executed evidence
  and docs match the code**, release/operator readiness, and honest debt
  reporting. The run's budget is **6 merged items** and **1 is already merged**
  (item 001), so **5 slots remain**. Your job is to decide what those 5 slots
  buy.

## Read

- `C:\Sythion\sythion\.orchestra\goal.md` - the goal, the off-limits list, the
  **budget (6 merged items, raised from 5 with a recorded reason)**, and the
  baseline notes.
- `C:\Sythion\sythion\.orchestra\research.md` - the candidate queue. **24
  candidates.** R-001..R-018 were decided in the first Scout pass. **R-019,
  R-020, R-021, R-022, R-023, R-024 are NEW and undecided** - you must adopt
  or reject each of them.
- `C:\Sythion\sythion\.orchestra\backlog.md` - the current backlog: items 001
  (merged), 002 (building), 003 (ready), 004 (in-review), 005 (ready).
- `C:\Sythion\sythion\.orchestra\board.json` - the same, machine-readable.
- `C:\Sythion\sythion\.orchestra\critiques.md` - **read all of it.** It is the
  orchestrator's judgement of every report so far and it is where the reasoning
  behind the new candidates lives.
- `C:\Sythion\sythion\.orchestra\follow-ups.md` - what is ALREADY queued for
  the human, so you do not file an item that a follow-up already covers.
- `C:\Sythion\sythion\docs/QUALITY_GATES.md` - the repo's own definition of a
  professional change. This is the yardstick.
- `C:\Sythion\sythion\docs/ROADMAP.md` - the repo's stated priorities.

## Write

- `C:\Sythion\sythion\.orchestra\backlog.md` - OVERWRITE. Re-ranked, with the
  new items added and the existing ones' **acceptance criteria left EXACTLY as
  they are** (002 and 004 are being implemented right now against those
  criteria; 003 and 005 are ready to be planned).
- `C:\Sythion\sythion\.orchestra\board.json` - OVERWRITE, mirroring every item.
- `C:\Sythion\sythion\.orchestra\research.md` - **edit in place**: change only
  the `- Status:` line of the six NEW candidates (R-019..R-024) to
  `adopted -> <item-id>` or `rejected`, adding a `- Rejected reason:` line for
  rejects. Do NOT touch R-001..R-018 - their decisions are already made and the
  orchestrator's critiques cite them by number.
- `C:\Sythion\sythion\.orchestra\reports/scout-002.md` - your report.

Do not create branches, do not commit, do not touch any file outside
`.orchestra/`.

## Context

- Current status: item 001 is MERGED (commit `7c478d1`). Items 002 and 004 are
  in flight. The run has 5 of its 6 merge slots left.
- Branch: `master` at `C:\Sythion\sythion`.
- Plan: n/a
- Previous findings: the first Scout pass ranked R-001..R-018 into items 001-005
  and rejected 7 candidates with reasons. Its report is
  `reports/scout-001.md`.

### The six new candidates, and why they exist

| Candidate | One line | Severity | Where it came from |
|---|---|---|---|
| **R-019** | `test_setup_readiness.py::test_readiness_states` depends on a developer's gitignored `backend/.env`; it FAILS in a clean checkout | med | implementer 001 + the Researcher's mechanism pass |
| **R-020** | the acceptance suite writes to and **DELETES** the developer's real Windows Credential Manager entry | high | the Researcher's mechanism pass |
| **R-021** | `DbEventLedger.append_event` loses up to 50% of concurrently-appended events | high | implementer 004's reported defect D-1 |
| **R-022** | the `tauri-build` CI job's "Assert the shell binary was produced" step is a no-op | med | reviewer 001 |
| **R-023** | the readiness/onboarding routes bypass the sanctioned key-resolution point | med | the Researcher's mechanism pass |
| **R-024** | a dead `or True` branch in `backend/services/capabilities.py:219` makes a health state unreachable | med | implementer 004 |

**Facts already established - do not re-derive them:**

- **R-019.** The mechanism is pinned: `backend/core/config.py:173-176` points
  pydantic-settings at `<repo>/backend/.env` and `:179` builds the settings
  singleton once at import. A 2x2 matrix proves the `.env` file and the
  `SYTHION_OLLAMA_API_KEY` env var are **alternatives** - either alone makes
  the test pass; only the absence of both fails it. Exactly **ONE** test in
  the suite depends on it. The file already contains the correct pattern:
  `_clear_local_env_key` (`test_setup_readiness.py:195-204`) does
  `monkeypatch.setattr(core_config.settings, "ollama_api_key", "")` and the two
  sibling tests call it (`:215`, `:237`); only `test_readiness_states` omits
  it. The fix shape was **proven** in the Researcher's report (pin the
  attribute on the already-imported singleton -> `8 passed`). Consequence:
  **a GitHub Actions runner has no `.env`, so the pipeline item 001 repaired
  goes RED on its first real run.** The orchestrator and the baseline agent
  both reproduced this.
- **R-020.** `backend/tests/acceptance/test_secret_storage.py:139-146` uses
  `secrets_module.CredentialManagerStore()` (the REAL vault), `store.set(...)`
  then `store.delete(...)`, and never requests the `memory_secret_store`
  fixture; `win32cred` IS importable on this machine, so
  `_default_store()` (`backend/core/secrets.py:112-121`) returns the real
  vault whenever the store has been reset to `None`. The file's only skip is
  the `win32cred` ImportError, so **every full-suite run executes it**. This
  contradicts `docs/ARCHITECTURE.md:358` ("the real vault is never touched").
  The orchestrator and the Researcher each checked the vault READ-ONLY:
  `CredEnumerate('Sythion (Synth)/ollama_api_key')` -> winerror **1168,
  "Element not found"**. The entry does not exist now and the `backend/.env`
  fallback is intact; whether it ever existed is **indistinguishable from
  here**. This is an effect OUTSIDE the repository, already triggered by every
  full-suite run in this session.
- **R-021.** `backend/runtime/events.py:64` allocates the event sequence
  number by read-then-write (`sequence = max(last.sequence_num if last else 0,
  pending_max) + 1`) against a unique index on (task_id, sequence). Concurrent
  appends to one task collide, the worker thread's `IntegrityError` is
  swallowed by `threading`, and the test still reports "1 passed" while up to
  50% of the events are lost (measured: 5 or 10 of 10 surviving across 8
  rounds). The event ledger is the repository's replay and audit substrate.
- **R-022.** The step is `run: Test-Path "src-tauri/target/release/sythion.exe"`.
  `Test-Path` is a cmdlet, does not set `$LASTEXITCODE`, and the step exits 0
  whether or not the file exists. Verified by reproducing the GitHub pwsh
  wrapper exactly.
- **R-023.** `backend/api/routes.py:2261` and `:2007` read
  `settings.ollama_api_key` directly, while `backend/core/secrets.py:174-175`
  and `docs/ARCHITECTURE.md:357` name `resolve_ollama_api_key` as the one
  sanctioned resolution point. The two forms are **exactly equivalent**, so it
  is a contract/architecture defect, not a behavioural one. NOTE: `secrets.py`
  and `routes.py` are BOTH on `goal.md`'s off-limits list - a fix there needs
  the sensitive-change gate and a pinned invariant.
- **R-024.** `backend/services/capabilities.py:219` has the same dead-branch
  shape item 004 removed from the tests. The bare `has_secret(...)` reads the
  **vault only**, so making the branch live would wrongly report
  `REQUIRES_CONFIGURATION` to a developer configured via `backend/.env` - which
  is why the Researcher tied its fix to R-023. The symptom is
  `self_model.py:87-90` silently omitting `reasoning_model` from the
  `requires_configuration` list. The INTENDED predicate is nowhere stated in
  the repository.

### The decision you have to make

**5 merge slots remain** and there are more candidates than slots. Items 002
and 004 are already being implemented and MUST be finished - their acceptance
criteria are in flight and you must not weaken or renumber them. That leaves
**3 slots** for: item 003, item 005, and whichever of R-019..R-024 you judge
worth building.

Decide, and justify each choice:

1. **Which of R-019..R-024 become items?** Not all of them need to. Say which
   you adopt and which you reject, with a reason for every rejection.
2. **Which of the resulting items fit the 3 slots, and which are DEFERRED?**
   A deferred item is a legitimate, expected outcome - the skill's stop
   condition includes "everything left is blocked, deferred or dropped". Use
   `- Status: deferred` for those, and put the reason in the item's `Why`
   line.
3. **Order the whole active set by value against effort and risk**, best
   first, and state the ranking rule you applied so a human can disagree with
   it.

**Signals the orchestrator has, stated so you can argue with them:**

- **R-020 is the only candidate with an effect outside the repository that
  DESTROYS user data, and it is already happening** - every full-suite run in
  this session has executed it. The repository's own
  `docs/ARCHITECTURE.md:358` promises the opposite. Whatever you decide,
  this one must not be quietly dropped.
- **R-019 is cheap and unblocks the value of item 001.** The fix shape is
  already proven, and it is the difference between a pipeline that gates the
  repository and one that gates it RED. It is also a TEST-ONLY change.
- **R-021 is a durability defect in the audit substrate** - the ledger the
  runtime replays from. But it is in the data area, it needs a pinned
  invariant (a concurrency test that fails before and passes after), and it
  may be larger than it looks. Judge its size honestly.
- **R-022 is a one-line fix** in a file the run has already touched, and it
  removes a gate that cannot fail - the same disease item 001 exists to cure.
- **R-023 and R-024 are in the secrets / public-API area.** `goal.md` puts
  `backend/core/secrets.py` and `backend/api/routes.py` off-limits for
  behaviour changes without the sensitive-change gate. You may still propose
  ADDING TESTS or DOCUMENTATION there. A behaviour change needs a named,
  pinned invariant.
- **003 and 005 are the two items from the first pass that are not yet
  planned.** 003 is a Gate 4 ("docs must reflect reality") item and now has a
  verified number set to quote (1564 passed / 45 skipped / 1609 collected full;
  191 passed / 2 skipped acceptance; 389-436 s wall time; and the reason NOT to
  quote the warning count). 005 is an operator-tooling item about a hard-coded
  port 8000 and a duplicated handshake path.
- A **[deferred]** item still needs its acceptance criteria written, so that a
  future run can pick it up without re-deriving anything.

## Constraints

- **Do not invent work.** Rejecting a candidate with a clear reason is a good
  outcome, not a failure.
- **Every item must have acceptance criteria someone who was NOT in this
  conversation can check objectively.** "The code is better" is not a
  criterion.
- **Do not weaken items 002 or 004's existing acceptance criteria** - they are
  being implemented against those exact texts right now. You may not renumber
  them, and you may not change their `- Likely files:` or `- Sensitivity:`
  lines. You MAY update their `- Status:`.
- **Item 001 stays exactly as it is**, with `- Status: merged`.
- Respect `goal.md`'s off-limits list. An item that behaviourally changes auth,
  secrets, the DB schema, the public API or a safety gate needs the
  sensitive-change gate; most of those are not worth the slots. Items that only
  ADD TESTS, DOCS or OBSERVABILITY to those areas are fine.
- One item = one branch = one reviewable change. Split anything large.
- Predict the sensitivity for each item (`none`, `payments`, `access`,
  `security`, `data`, `api`, `infra`, `unknown`) and say why.
- Do not push, deploy, publish, or touch real data.
- Do not ask the user anything. If something needs a decision you cannot make,
  say so in the report and stop.

## Output contract

- `backlog.md` - ONE block per item, exactly this shape:

```
### 001 Short title
- Why: <tied to goal.md and to a candidate id in research.md>
- Acceptance:
  1. <objectively checkable criterion>
  2. <objectively checkable criterion>
- Size: S | M | L
- Risk: low | med | high (one line why)
- Likely files: <paths or globs>
- Sensitivity: none | payments | access | security | data | api | infra | unknown
- Status: merged | ready | planned | building | in-review | verifying | accepted | merged | needs-improvement | blocked | deferred | dropped
```

  Blocks appear in value order, best first. `status.py` parses the
  `### <id> <title>` headings and the `- Status:`, `- Size:`, `- Risk:`,
  `- Likely files:` and `- Sensitivity:` fields - keep them on their own lines
  in exactly that spelling.

- `board.json` - mirror every item:

```json
{
  "items": {
    "001": { "status": "merged", "owner": null, "branch": null, "round": 0, "verifyRounds": 0, "conflicts": 0, "sensitivity": "infra" }
  },
  "roles": { ...unchanged... }
}
```

  `board.json` and `backlog.md` MUST agree on every item's status.

- `research.md` - edit in place: the six new candidates each get
  `- Status: adopted -> <item-id>` or `- Status: rejected` plus
  `- Rejected reason: ...`.

- Write `C:\Sythion\sythion\.orchestra\reports/scout-002.md` (shape:
  What I did / Evidence / **Not checked** - the Not checked section is
  required).

- Reply with: the ranked list, which candidates you rejected and why, which
  items you deferred and why, and what you did not check.

## Previous attempt (if any)

- The first Scout pass (`reports/scout-001.md`) ranked R-001..R-018 into items
  001-005 and rejected 7 candidates with reasons. **Its decisions stand** -
  you are ranking the NEW candidates and re-ordering the ACTIVE set, not
  re-litigating it.
