# Brief: review - 019 - round 2 (re-review of the wording corrections)

- Item: 019 Tautology detector must also catch nested and bare `or True`
- Role: reviewer (review only - no source edits)
- Reviewed commit: `f0fc3c6` (branch `orchestra/019-tautology-detector`)
- Previous review: `reviews/019-r1.md`, VERDICT IMPROVE, findings F1 (required),
  F2 (required), F3 (required) - all wording - plus non-blocking F5/F6/F7/F8.
  Round 2 was to fix exactly those.

## Worktree

- Create `C:\Users\orcha\AppData\Local\Temp\opencode\wt-019-review2`, detached at `f0fc3c6`.

## Check

1. **F1 closed**: the comment above the site-1 assertion no longer says the log
   message is "implicitly tested by no exception", and the F5 clarification
   (autoflush, not a persistence check) and the F8 note are present.
2. **F2 closed**: the failure message now names the bare-literal position
   ("or the assertion's own test").
3. **F3 closed**: the docstring's opening no longer claims such an assertion
   "verifies nothing", and no longer contradicts its own third paragraph.
4. **F7 applied**: the `zip` in `_source()` is guarded against silent truncation.
5. **F6 applied**: the site-2 comment states that the comparison pins persisted state.
6. **Nothing else changed**: `git diff 76c5c1d..f0fc3c6` must be text only - no
   change to the detector's RULE, the probes, the self-test structure, any
   assertion's semantics, or any test id. `git diff --name-only master..f0fc3c6`
   must still be exactly the three files.
7. **Nothing broke**: run
   `python -m pytest backend/tests/test_no_tautological_asserts.py backend/tests/test_conversation_memory.py backend/tests/test_phase12_planning_learning.py -q`
   and `python -m pytest backend/tests --collect-only -q` (must be 1628), plus a
   whole-tree scan (must be 0 violations). The full suite is the Verifier's
   job - you do not need to run it.
8. **Re-read the fixed text for a NEW inaccuracy**: the rewrites are claims
   about the rule; check each new sentence against the implementation. In
   particular, does "does not check the property its shape suggests" hold for
   all four flagged shapes, and is the F5 comment about the session/autoflush
   accurate for that test?

## Write

- `C:\Sythion\sythion\.orchestra\reviews\019-r2.md` with `- Reviewed commit: f0fc3c6`,
  the tier, the per-finding judgement (F1, F2, F3, F5, F6, F7, F8), and
  `VERDICT: ACCEPT` / `IMPROVE` / `REJECT` with the reason.

## Constraints

- No source edits. Probe scripts in TEMP only, deleted after.
- Do not push.

## Output contract

The FILE `.orchestra/reviews/019-r2.md` is the deliverable. Reply with a short
summary.