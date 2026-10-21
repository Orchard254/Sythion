VERDICT: ACCEPT

# 016 Implement: doc sweep (006 F3 + 003 F3)

Commit: `05c03f73a89229ce17d83be64408a6fa563f28b4` (branch `orchestra/016-doc-sweep`,
worktree `C:\Users\orcha\AppData\Local\Temp\opencode\wt-016`).

## Scope

Docs-only. Two items.

1. 006 F3 — `backend/tests/test_phase115_soak.py:1` module docstring. Verified first:
   the module docstring already reads
   `"""Learning soak test — realistic synthetic workload for learning substrate."""`
   (post-011 `gated_soak` -> `learning_soak` rename), and `pytestmark` is
   `pytest.mark.learning_soak`. No `Gated soak` occurrence remains in the file,
   so no edit was required for this item.

2. 003 F3 — `docs/TESTING.md` clarifies the cost of `--check-collected`. The
   standalone invocation runs the full measurement first; the cheap form is
   `--measured <json> --check-collected` (67 s measured). One clarifying sentence
   added at the end of the paragraph following the command block.

## Files touched

- `docs/TESTING.md` (+2 / -2 lines; one added sentence + wrap).
- `backend/tests/test_phase115_soak.py` — verified only; not edited.
- `.orchestra/reports/016-implement-1.md` — this report.

No `src-tauri/` or other files touched.

## Verification evidence

- `git diff --stat` (implementation commit): `docs/TESTING.md | 4 ++--`.
- Diff hunk:
  ```
  -added or removed.
  +added or removed. The standalone form runs the full measurement first; the
  +cheap form is `--measured <json> --check-collected` (67 s measured).
  ```
- `backend/tests/test_phase115_soak.py` line 1 contains `Learning soak test`,
  not `Gated soak`.

## Tier classification

Tier: SENSITIVE? No. Doc-only change to `docs/TESTING.md` (plus a verification-only
inspection of a test file); no runtime, test assertion, or safety-relevant code
touched. NORMAL.

## Scope check

Change is XS-sized (one sentence in one markdown file). Allowed paths only. Worktree
clean before report commit.