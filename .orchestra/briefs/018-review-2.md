# Brief: review - 018 - round 2 (re-review of the documentation correction)

- Item: 018 The vault-write guard must not be swallowed by the FastAPI global exception handler
- Role: reviewer (review only - no source edits)
- Reviewed commit: `3359610` (branch `orchestra/018-vault-guard-surface`)
- Previous review: `reviews/018-r1.md`, VERDICT IMPROVE, findings F1 (blocking) and F2 (low),
  both documentation-only. Round 2 was to fix exactly those two.

## Worktree

- Create `C:\Users\orcha\AppData\Local\Temp\opencode\wt-018-review2`, detached at `3359610`.

## Read

- `C:\Sythion\sythion\.orchestra\reviews\018-r1.md` (F1, F2 - the exact required text)
- `C:\Sythion\sythion\.orchestra\reports\018-implement-2.md`
- `git diff 70010b3..3359610` (the round-2 diff) and `git diff master..3359610` (the whole item)

## Check

1. **F1 closed**: the conftest "Known constraint" paragraph now states the two
   client modes accurately and separately, and the claim is TRUE against
   Starlette 0.35.1's `ServerErrorMiddleware` (which sends the response and then
   always re-raises). Read the paragraph and confirm it no longer claims both
   outcomes co-occur.
2. **F2 closed**: the new file's module docstring no longer presents the
   opt-in sentinel as an always-on proof.
3. **Nothing else changed**: `git diff 70010b3..3359610` must touch ONLY prose
   inside those two docstrings. No logic, no assertion, no `backend/main.py`,
   no change to the file set (`git diff --name-only master..3359610` must still
   be exactly the three files).
4. **Nothing broke**: run the new test file with the sentinel armed
   (`$env:SYTHION_VAULT_SENTINEL='1'`) plus `backend/tests/test_vault_guard_root_scope.py`
   - both must pass. Do NOT run the new file without the sentinel.
5. Confirm the four tests are still present and still assert what they asserted.

## Write

- `C:\Sythion\sythion\.orchestra\reviews\018-r2.md` with `- Reviewed commit: 3359610`,
  the tier, the per-finding judgement (F1, F2, and the untouched F3-F8), and
  `VERDICT: ACCEPT` / `IMPROVE` / `REJECT` with the reason.

## Constraints

- No source edits. If you need a probe, create it, use it, delete it, and say so.
- Do not write to the real Windows Credential Manager. Arm the sentinel.

## Output contract

The FILE `.orchestra/reviews/018-r2.md` is the deliverable. Reply with a short
summary.