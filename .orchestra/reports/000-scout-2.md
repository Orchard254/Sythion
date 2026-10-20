# Report: scout - 002 - attempt 1

## What I did

- Read the brief (`.orchestra/briefs/002-scout-1.md`), `goal.md` (budget 6,
  off-limits list, baseline notes), **all** of `critiques.md` (every report so
  far), `follow-ups.md`, the current `backlog.md` and `board.json`, and the six
  new candidate blocks R-019..R-024 in `research.md`.
- Checked the state of the repository that the brief's premises depend on
  (`git log`, `git branch --contains 7c478d1`) and confirmed that item 001's
  merge commit is on `master`.
- Read `backend/core/secrets.py:23-121` once, to check that the fix shape I
  wrote into item 007's acceptance criteria is actually reachable without
  changing a protected file. I did not enumerate, read, write or delete any
  credential, and I did not run the test suite.
- Wrote four files: `.orchestra/backlog.md` (overwritten),
  `.orchestra/board.json` (overwritten), `.orchestra/research.md` (edited in
  place - only the six new candidates' `- Status:` lines changed, plus two
  `- Rejected reason:` lines), and this report.

Commands run (all read-only):

- `git log --oneline -8`, `git branch -a`, `git branch --contains 7c478d1`,
  `git show --stat 7c478d1`
- `git diff --stat -- .orchestra/research.md` and `git diff -U0` over the same
  file (to prove R-001..R-018 were not touched)
- `git show HEAD:.orchestra/backlog.md` / `HEAD:.orchestra/board.json`
  compared block-by-block against the working tree (to prove items 001, 002
  and 004 are unchanged)

## Evidence

- **The budget arithmetic.** `goal.md:127-137`: "**6 merged items.** ... The
  raise is one item, not an open-ended extension." `critiques.md:119-134` and
  the brief: item 001 is merged (`7c478d1`, confirmed on `master` by
  `git branch --contains 7c478d1`), items 002 and 004 are in flight and their
  acceptance criteria are frozen. 6 - 1 merged - 2 in flight = **3 slots**.
- **R-020 is built first, and it is the only candidate with an effect outside
  the repository.** `research.md` R-020: `backend/tests/acceptance/test_secret_storage.py:139`
  constructs the production `CredentialManagerStore()`, `:140` writes
  `Sythion (Synth)/ollama_api_key`, `:141-146` deletes it; the file never
  requests the `memory_secret_store` fixture (`:27-33`), and
  `backend/core/secrets.py:112-121` `_default_store()` returns the real vault
  whenever `win32cred` imports. It contradicts `docs/ARCHITECTURE.md:358`.
  **The operational consequence I acted on:** every remaining full-suite run
  of this run - the verifier's run for item 004, the verifier's run for item
  002, item 003's own measurement script - executes the defect. Building it
  first is therefore the only ordering that stops the bleeding during the run.
- **R-020's fix shape is reachable without touching a protected file.** I read
  `backend/core/secrets.py:29` (`_CM_TARGET_PREFIX = "Sythion (Synth)/"`),
  `:32-33` (`_cm_target` returns `_CM_TARGET_PREFIX + name`) and `:50-87`
  (`CredentialManagerStore.get/set/delete` each call `_cm_target(name)` at
  CALL time). The module global is therefore readable at call time, so a test
  can pin a test-only prefix inside the test process and still drive the real
  `win32cred` store. That is what item 007's criterion 2 requires and what
  criterion 4 forbids changing `backend/core/secrets.py` to achieve.
- **R-019 is the item `goal.md` already reserved.** `goal.md:129-137` names it:
  "it becomes its own item (006). Shipping a knowingly-red pipeline would
  defeat the purpose of item 001." `follow-ups.md:16` tells the human to
  EXPECT the first GitHub run to be red for this reason. The Researcher's 2x2
  matrix (R-019 evidence) proves the `.env` file and the env var are
  alternatives and that exactly one test depends on them; the fix shape is
  proven in a scratch tree.
- **R-021 is a real durability defect and is preserved, not dropped.**
  `research.md` R-021 and `critiques.md:95,98`: `backend/runtime/events.py:64`
  allocates the sequence by read-then-write against the unique index at
  `backend/core/db_manager.py:128-131`; up to 50% of a task's events are lost
  across 8 measured rounds while the suite stays green. It is already a
  `[required]` follow-up (`follow-ups.md:24`). I wrote it up as item 008 with
  full acceptance criteria and marked it **deferred**, so the criteria survive
  for a later run.
- **R-022 is already covered by a follow-up.** `follow-ups.md:20` carries it
  as `[deferred]` with the identical one-line fix shape
  (`if (-not (Test-Path "...")) { throw "..." }`). Filing an item would
  duplicate an assignment that already exists - which the brief explicitly
  warns against.
- **R-023 lands in two protected files.** `backend/core/secrets.py` (secret
  storage) and `backend/api/routes.py` (public HTTP API shape) are both on
  `goal.md:91-106`'s off-limits list, and the Researcher's own evidence says
  the fix "needs the sensitive-change gate rather than being taken as a
  routine refactor". The two expressions are exactly equivalent today, so the
  defect is a contract violation with no behavioural consequence. Deferred as
  item 009 with criteria, not built.
- **R-024 needs a decision nobody in the repository can make.** Its evidence
  states it plainly: "the intended predicate is not stated anywhere in the
  repository", and the two candidate readings differ observably - a developer
  configured through `backend/.env` would be told to configure something they
  already configured. A literal `or True` -> `or False` swap would be wrong.
- **Item 003's numbers are now confirmed** (`goal.md:173-178`): full suite
  `1564 passed, 45 skipped`, `1609 collected`, 389-436 s; acceptance `191
  passed, 2 skipped`; and the warning count is explicitly NOT a baseline
  ("407,733 / 406,252 / 407,719 across three runs of an unchanged tree. Do
  not quote it in a document"). I put that number set and that prohibition into
  item 003's `Why` line without touching its acceptance criteria.

## Acceptance criteria

| # | Criterion | Result | Evidence |
|---|---|---|---|
| 1 | Every one of R-019..R-024 ends `adopted -> <item-id>` or `rejected` + reason | PASS | Status map: R-019 -> 006, R-020 -> 007, R-021 -> 008, R-023 -> 009, R-022 rejected, R-024 rejected; `Select-String` -> 0 `queued` remaining in the file, 9 `Rejected reason:` lines (7 pre-existing + 2 new) |
| 2 | R-001..R-018 are untouched | PASS | `git diff -U0 -- .orchestra/research.md` -> 8 insertions / 6 deletions, all of them `- Status:` lines or new `- Rejected reason:` lines in the six new blocks; no other byte of the file changed |
| 3 | Items 002 and 004 keep their acceptance criteria, `- Likely files:` and `- Sensitivity:` EXACTLY | PASS | Block-by-block comparison against `git show HEAD:.orchestra/backlog.md`: item 002 IDENTICAL (3095 chars), item 004 IDENTICAL (2487 chars), item 001 IDENTICAL (2710 chars) |
| 4 | Item 001 stays as it is with `- Status: merged` | PASS | `git show HEAD:.orchestra/backlog.md` already carries `- Status: merged` for 001; my file matches it byte-for-byte. I also corrected `board.json`, which still said `accepted` while `7c478d1` was already on `master` - the two files now agree |
| 5 | `backlog.md` and `board.json` agree on every item's status and sensitivity | PASS | My own parser: 9 items, ALL 9 AGREE (status + sensitivity), `owner/branch` preserved for the in-flight items, `round/verifyRounds/conflicts` = 0, `roles` block copied verbatim |
| 6 | Deferred items still carry acceptance criteria a later run can execute | PASS | Items 005, 008 and 009 each have 6 numbered criteria; 005's criteria are byte-identical to the ones it had before this re-rank |
| 7 | No item behaviourally changes auth, secrets, the DB schema, the public API or a safety gate | PASS | Items 006 and 007 change only files under `backend/tests/`. Item 009 touches `backend/core/secrets.py` and `backend/api/routes.py` but is DEFERRED and pins the behaviour-preserving invariant (criterion 2: byte-identical response bodies). Item 008 forbids a schema change (criterion 3) and forbids the DB-assigned-sequence route (criterion 4). Item 003 is docs + two read-only scripts |
| 8 | Nothing outside `.orchestra/` touched; no branch, no commit | PASS | `git status --short` shows only `.orchestra/` entries; no `git checkout -b`, no `git commit`; no credential was enumerated, read, written or deleted |

## Findings

1. **The ranking rule I applied**, so a human can disagree with it: *severity
   of the consequence first (does it destroy data outside the repository, does
   it silently lose runtime data, does it make the release signal lie), then
   whether the defect is already burning a slot or a follow-up, then size and
   risk, then verifiability - a candidate whose acceptance criteria cannot be
   checked by an outsider loses to one whose can.* Under that rule the three
   slots went to 007 (007 > 006 > 003), and 005, 008 and 009 were deferred
   behind them with their criteria intact.
2. **R-020 is built first, ahead of R-019, even though R-019 is cheaper.** The
   reason is not severity ranking in the abstract: item 004's verifier run,
   item 002's verifier run and item 003's own measurement script are all
   full-suite runs, and each of them executes the credential deletion. The
   defect is not a thing that will be true later; it is true now, on every
   run the rest of this run performs.
3. **The board was stale on item 001.** `board.json` said `accepted` with
   `owner: committer-001` while `git log` shows the merge commit `7c478d1` on
   `master` and `HEAD:.orchestra/backlog.md` already said `merged`. Since the
   brief requires the two files to agree, I set both to `merged` and kept the
   owner/branch values. The committer is still marked `working` in `roles` -
   I left the roles block untouched, as instructed, so that block is now
   slightly behind the item state.
4. **Item 006 must be built after item 002.** Both touch
   `backend/tests/conftest.py` (item 002 adds the marker hooks, item 006 adds
   the autouse `.env` neutralisation). I said so in item 006's `Why` line and
   in item 003's (criterion 7 already required it to follow 001 and 002).
5. **What I did NOT do with R-019.** Its Direction also asks for
   `backend/api/routes.py:2261`/`:2007` to be routed through
   `resolve_ollama_api_key`. That is R-023, it lands in two protected files,
   and it is out of scope for a test-isolation item - so item 006 explicitly
   excludes it and item 009 carries it as deferred.
6. **Why 005 was deferred rather than dropped.** Its defect is real and
   unfixed: `scripts/smoke_test.py:21` and `cli.py:26` still hard-code port
   8000 while the sidecar binds an ephemeral port, so the tools can send the
   control token to whatever else owns that port. It lost the third slot to
   003 on value-per-slot (003 fixes 22 documented falsehoods against a
   confirmed number set; 005 needs a foreign process on port 8000 to cause
   harm, and it touches token handling). Its criteria are unchanged and its
   `Why` now carries the deferral reason, so a later run picks it up without
   re-deriving anything.
7. **Why 008 (R-021) was deferred rather than built.** It is the most severe
   *runtime* defect on the table - up to 50% of a task's event history lost
   while the suite stays green - and it is preserved as a deferred item with
   criteria. It lost the slot because of verifiability under this run's
   conditions: one of its three fix shapes (a database-assigned monotonic
   sequence) needs a schema change and is off-limits, the remaining two touch
   the concurrency-critical write path of the audit ledger, and the assertion
   it needs (exact event count under concurrency) is the kind that makes a
   suite intermittently red - in a suite that already crashes in roughly one
   run in three (Fault A and Fault B, `goal.md:154-170`). Adding a racy
   assertion in the last slots of a run whose baseline is already unstable was
   the wrong trade.
8. **R-022's rejection is bounded, not a dismissal.** The `npx tauri build`
   step itself fails the job when the build fails, so the no-op assertion is a
   redundant net rather than the only gate - but it IS the same silent-green
   pattern item 001 exists to eliminate, and the one-line fix belongs either to
   whoever next touches `.github/workflows/ci.yml` or to the human acting on
   `follow-ups.md:20`.
9. **R-024 is a decision, not a bug I could fix.** The dead `or True` branch
   at `backend/services/capabilities.py:219` makes
   `REQUIRES_CONFIGURATION` unreachable for `reasoning_model`, and
   `backend/services/self_model.py:87-90` therefore under-reports what the
   operator must configure. The repository nowhere states the intended
   predicate, and the two readings disagree observably for a `.env`-configured
   developer. I flagged it in the report as needing a product decision and
   coupled it to R-023 in item 009's `Why`.

## Not checked

- **I did not run the backend suite, any test file, or vitest.** The
  baseline figures I quote in item 003's `Why` (`1564/45/1609`, `191/2`,
  389-436 s) are the baseline agent's, taken from `goal.md:173-178`; I did not
  re-measure them and I did not re-measure the warning-count instability.
- **I did not touch the Windows Credential Manager in any way** - no
  enumeration, no read, no write, no delete. The "the entry does not exist
  right now" fact in item 007's `Why` is the Researcher's and the
  orchestrator's read-only check, which I took as given. Item 007's criterion
  1 makes the re-check an obligation of the implementer, with an explicit
  read-only method, and criterion 5 keeps the operator action in
  `follow-ups.md`.
- **I did not re-derive the six candidates' evidence** beyond reading it:
  the 2x2 matrix, the 8-round event-loss measurement, the `CredEnumerate`
  result, the pwsh-wrapper reproduction of the `Test-Path` no-op, and the
  `win32cred` importability check are the Researcher's, the Reviewer's and
  the orchestrator's, as the brief instructed.
- **I did not run the R-019 fix shape in a scratch tree.** I read the
  mechanism (`backend/core/config.py:173-176`, `:179`) and the test file's
  own helper, and I wrote item 006's criteria against the Researcher's proven
  shape, but I did not execute it.
- **I did not check Python 3.11 / CI-runner parity** for anything, did not
  check whether `win32cred` imports in a clean CI environment, and did not
  check the GitHub-side settings of any kind.
- **I did not verify that the orchestrator's `status.py` / `verify.py` parse
  my file the way my parser does.** I kept the parse-safe header comment from
  the first pass, verified with my own parser that the file yields exactly
  nine items whose status and sensitivity match `board.json`, and confirmed
  with `git diff` that items 001/002/004 are byte-identical to HEAD.
- **I did not check the remaining ~38 documents for further drift**, and I
  did not check whether other acceptance tests write to the vault (item 007's
  criterion 3 exists precisely because that is unverified).
